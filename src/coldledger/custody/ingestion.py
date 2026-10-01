"""Coordinated ingestion of custody handoffs.

A handoff record carries a custody *sequence*. Records can arrive late: the
sequence is still the next one expected, but the event time is at or before the
accepted watermark, so splicing it in would write the custody timeline
backwards and make audit and asset ledgers unreplayable.

This module keeps three invariants consistent:

* the custody chain advances continuously in sequence and strictly in time
  (``custody.handoffs``),
* every accepted handoff posts an idempotent asset entry
  (``custody.asset_ledger``),
* every decision is recorded in append-only audit order
  (``audit.trail``).

A late-but-correctly-sequenced record is handled by an explicit policy:

* ``REJECT``  - refused outright, nothing is committed anywhere;
* ``SUSPEND`` - parked in a sequence-ordered pending queue (the default);
* ``REVISE``  - the original event is kept as evidence and a corrected handoff
  is appended at the tail instead of rewriting history.

Re-submitting the same seal is idempotent for every policy, and commit
positions are derived from the event watermark (never the wall clock), so
replaying the same logical stream - in any arrival order - reproduces exactly
the same chain, balances and audit digests.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from .handoffs import (
    CustodyChain,
    Handoff,
    CustodyMovedBackward,
    DuplicateCustodyInstant,
    SequenceGap,
)
from .asset_ledger import CustodyLedger
from ..audit.trail import AuditTrail

UTC = timezone.utc

# Deterministic offset applied when a revision needs a valid (post-watermark)
# commit position and the caller does not supply an explicit corrected_at.
DEFAULT_RESOLUTION = timedelta(seconds=1)


class Policy(str, Enum):
    REJECT = "reject"
    SUSPEND = "suspend"
    REVISE = "revise"


class Decision(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUSPENDED = "suspended"
    REVISED = "revised"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class Outcome:
    decision: Decision
    handoff: Handoff
    reason: str = ""
    corrected_at: datetime | None = None


@dataclass(frozen=True)
class _Parked:
    handoff: Handoff
    reason: str


def _utc(at: datetime) -> datetime:
    return at.astimezone(UTC)


def _same_handoff(a: Handoff, b: Handoff) -> bool:
    return (a.sequence == b.sequence
            and a.from_party == b.from_party
            and a.to_party == b.to_party
            and _utc(a.at) == _utc(b.at))


class CustodyIngestor:
    def __init__(self, origin: str, *, policy: Policy = Policy.SUSPEND,
                 resolution: timedelta = DEFAULT_RESOLUTION,
                 ledger: CustodyLedger | None = None,
                 audit: AuditTrail | None = None):
        if resolution <= timedelta(0):
            raise ValueError("resolution must be positive")
        self._chain = CustodyChain(origin)
        self._ledger = ledger or CustodyLedger()
        self._audit = audit or AuditTrail()
        self._policy = Policy(policy)
        self._resolution = resolution
        self._pending: dict[int, _Parked] = {}
        # seal -> first outcome for every record seen in any state.
        self._seen: dict[str, Outcome] = {}
        self._rejected: dict[str, Outcome] = {}

    # ---- views -----------------------------------------------------------
    @property
    def origin(self) -> str:
        return self._chain.origin

    @property
    def chain(self) -> CustodyChain:
        return self._chain

    @property
    def ledger(self) -> CustodyLedger:
        return self._ledger

    @property
    def audit(self) -> AuditTrail:
        return self._audit

    @property
    def watermark(self) -> datetime | None:
        return self._chain.watermark

    def pending(self) -> tuple[Handoff, ...]:
        return tuple(self._pending[s].handoff for s in sorted(self._pending))

    def outcome_of(self, seal: str) -> Outcome | None:
        return self._seen.get(seal) or self._rejected.get(seal)

    # ---- ingestion -------------------------------------------------------
    def ingest(self, handoff: Handoff, *, corrected_at: datetime | None = None) -> Outcome:
        # Idempotency first: replaying an identical record is a no-op that
        # returns the outcome decided the first time.
        prior = self._seen.get(handoff.seal) or self._rejected.get(handoff.seal)
        if prior is not None and _same_handoff(prior.handoff, handoff):
            return Outcome(Decision.DUPLICATE, handoff, prior.reason, prior.corrected_at)

        if handoff.sequence in self._pending:
            raise ValueError("pending slot already holds a different record")
        if self._chain.accepted(handoff.sequence) is not None:
            raise ValueError(f"sequence {handoff.sequence} already committed")

        if handoff.sequence != self._chain.next_sequence:
            # Future sequence: always park until the gap closes, any policy.
            outcome = self._suspend(handoff, "sequence gap; waiting for predecessor")
            self.drain()
            return outcome

        # Sequence is the expected one. Try to commit it to the chain.
        try:
            self._chain.accept(handoff)
        except (CustodyMovedBackward, DuplicateCustodyInstant) as exc:
            outcome = self._handle_late(handoff, str(exc), corrected_at)
            if outcome.decision is Decision.ACCEPTED:
                self.drain()
            return outcome

        self._commit_accepted(handoff, Decision.ACCEPTED, "")
        self.drain()
        return self._seen[handoff.seal]

    # ---- late-record policy ---------------------------------------------
    def _handle_late(self, handoff: Handoff, reason: str,
                     corrected_at: datetime | None) -> Outcome:
        if self._policy is Policy.REJECT:
            outcome = Outcome(Decision.REJECTED, handoff, reason)
            self._rejected[handoff.seal] = outcome
            return outcome

        if self._policy is Policy.SUSPEND:
            return self._suspend(handoff, reason)

        # REVISE: never rewrite the past. Keep the original (late) event time
        # as evidence in a revision-request record at the tail, then commit a
        # corrected handoff at a valid, strictly-later position.
        at = _utc(handoff.at)
        wm = self._chain.watermark
        effective = _utc(corrected_at) if corrected_at is not None else self._revision_time()
        if effective <= wm:
            # Caller-supplied correction is itself out of order: park it.
            return self._suspend(handoff, reason + "; corrected time not after watermark")

        # Revision request sits just before the corrected commit on the trail.
        request_at = wm + (effective - wm) / 2 if wm is not None else effective - self._resolution / 2
        self._audit.revise("system", "handoff.revise-request", handoff.seal,
                           at=request_at, corrects=self._last_audit_index(),
                           occurred_at=at)
        corrected = Handoff(handoff.sequence, handoff.from_party, handoff.to_party,
                            effective, handoff.seal + ":rev1")
        self._chain.accept(corrected)
        self._post_asset(corrected)
        self._audit_append(corrected, "handoff.revise", effective)
        revised = Outcome(Decision.REVISED, handoff, reason, effective)
        self._seen[handoff.seal] = revised
        self._seen[corrected.seal] = Outcome(Decision.ACCEPTED, corrected,
                                             "accepted as revision")
        return revised

    def _last_audit_index(self) -> int:
        return len(self._audit.records)

    def _revision_time(self) -> datetime:
        wm = self._chain.watermark
        return wm + self._resolution if wm is not None else datetime.now(UTC)

    def _suspend(self, handoff: Handoff, reason: str) -> Outcome:
        outcome = Outcome(Decision.SUSPENDED, handoff, reason)
        self._pending[handoff.sequence] = _Parked(handoff, reason)
        self._seen[handoff.seal] = outcome
        return outcome

    # ---- queue draining --------------------------------------------------
    def drain(self) -> tuple[Outcome, ...]:
        """Admit every parked record whose predecessor is now present.

        Walks the pending queue in sequence order, admitting contiguous
        records; stops at the first still-blocking slot.
        """
        out: list[Outcome] = []
        while True:
            nxt = self._chain.next_sequence
            parked = self._pending.pop(nxt, None)
            if parked is None:
                break
            h = parked.handoff
            if self._chain.is_late(h):
                if self._policy is Policy.REJECT:
                    outcome = Outcome(Decision.REJECTED, h, parked.reason)
                    self._rejected[h.seal] = outcome
                    self._seen.pop(h.seal, None)
                    out.append(outcome)
                    break  # successors can never pass a rejected predecessor
                if self._policy is Policy.REVISE:
                    outcome = self._handle_late(h, parked.reason, None)
                    out.append(outcome)
                    if outcome.decision is Decision.SUSPENDED:
                        break
                    continue
                # Still late under SUSPEND: re-park and stop.
                self._pending[nxt] = parked
                break
            self._chain.accept(h)
            self._commit_accepted(h, Decision.ACCEPTED, "")
            out.append(self._seen[h.seal])
        return tuple(out)

    def settle(self) -> tuple[Outcome, ...]:
        """Admit every record that can ever be admitted from the pending set.

        Drains repeatedly until no further progress is possible, so the
        committed chain/ledger/audit state depends only on the set of records
        received and not on their arrival order.
        """
        results: list[Outcome] = []
        while True:
            batch = self.drain()
            if not batch:
                break
            results.extend(batch)
        return tuple(results)

    # ---- side effects ----------------------------------------------------
    def _commit_accepted(self, handoff: Handoff, decision: Decision, reason: str) -> None:
        self._post_asset(handoff)
        self._audit_append(handoff, "handoff.accept", _utc(handoff.at))
        self._seen[handoff.seal] = Outcome(decision, handoff, reason)

    def _post_asset(self, handoff: Handoff) -> None:
        self._ledger.post(
            handoff.to_party,
            f"handoff:{handoff.sequence}:{handoff.seal}",
            1,
            "custody handoff",
            occurred_at=_utc(handoff.at),
        )

    def _audit_append(self, handoff: Handoff, action: str, at: datetime) -> None:
        self._audit.ordered_append("system", action, handoff.seal, at)

    # ---- replay ----------------------------------------------------------
    @classmethod
    def replay(cls, origin: str, handoffs, *, policy: Policy = Policy.SUSPEND,
               resolution: timedelta = DEFAULT_RESOLUTION) -> "CustodyIngestor":
        """Replay a handoff stream deterministically.

        Records are processed in arrival order; duplicates collapse to no-ops
        and the pending queue is settled at the end. The resulting chain,
        ledger balances and audit digests are identical for the same logical
        stream however it is replayed.
        """
        ingestor = cls(origin, policy=policy, resolution=resolution)
        for hnd in handoffs:
            ingestor.ingest(hnd)
        ingestor.settle()
        return ingestor
