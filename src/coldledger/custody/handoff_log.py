"""Append-only custody intake with late-record handling.

A handoff record carries a gapless business *sequence*. Records can arrive
late (out of arrival order), and a late record whose sequence is correct can
still lie on a backwards event time, which would make the custody timeline,
the asset ledger and the audit trail unreplayable.

``HandoffLog`` is the single intake point and commits every decision
atomically across three guards:

1. **Custody chain** -- :func:`coldledger.custody.handoffs.validate`: gapless
   sequences, continuous holder chain, non-decreasing event time, unique
   seals.
2. **Asset changes** -- :func:`coldledger.custody.asset_ledger.project`, a
   pure fold over committed movements whose balances never go negative.
3. **Audit order** -- :class:`coldledger.audit.trail.AuditTrail`, a hash chain
   appended in observation/commit order.

Late-record policy (``LatePolicy``):

* ``REJECT``  -- a record that cannot extend the effective timeline is refused
  immediately; nothing is committed.
* ``SUSPEND`` (default) -- such a record is parked (its sequence is ahead of
  the gap) or quarantined (back-time / invalid) until the gap fills or a
  revision supersedes it. Nothing touches the ledger while suspended.
* ``REVISE``  -- a late record for an already-committed sequence is accepted
  as a *new revision*: original events stay untouched, a compensating
  reversal plus corrected transfer is appended, and the whole revised chain
  is re-validated before commit.

Idempotence: a byte-identical resubmission is a duplicate replay and returns
the same outcome without appending anything, so replaying a feed twice yields
the same committed log, ledger and audit trail.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from threading import RLock
from typing import Callable, Iterable

from . import handoffs
from .asset_ledger import CustodyLedger, project
from ..audit.trail import AuditTrail


class LatePolicy(str, Enum):
    REJECT = "reject"
    SUSPEND = "suspend"
    REVISE = "revise"


class LateRecordError(ValueError):
    """Raised under REJECT when a late record cannot extend the timeline."""


# submission statuses
ACCEPTED = "accepted"        # committed as the next gapless handoff
REVISED = "revised"          # committed as a revision of an old sequence
DUPLICATE = "duplicate"      # identical replay, nothing appended
PARKED = "parked"            # waiting for an earlier sequence (gap future)
QUARANTINED = "quarantined"  # held, needs manual resolution

# committed-log event kinds
EV_OPEN = "open"
EV_HANDOFF = "handoff"
EV_REVISION = "revision"
EV_PARK = "park"
EV_QUARANTINE = "quarantine"
EV_PROMOTE = "promote"


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _fingerprint(frm: str, to: str, at: datetime, seal: str, quantity) -> str:
    q = "" if quantity is None else str(Decimal(str(quantity)))
    raw = "|".join([frm, to, _utc(at).isoformat(), seal, q])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Outcome:
    status: str
    sequence: int
    revision: int = 0
    detail: str = ""
    recorded_at: datetime | None = None
    also: tuple["Outcome", ...] = ()


@dataclass(frozen=True)
class Commit:
    """One append-only commit; the commit log is the sole source of truth."""
    ordinal: int
    kind: str
    sequence: int
    revision: int
    handoff: handoffs.Handoff | None
    quantity: Decimal | None
    actor: str
    recorded_at: datetime
    note: str = ""


class HandoffLog:
    def __init__(self, origin: str, opening_holder: str | None = None,
                 opening_quantity=Decimal("0"), policy: LatePolicy = LatePolicy.SUSPEND,
                 now: Callable[[], datetime] | None = None):
        self.origin = origin
        self.opening_holder = opening_holder or origin
        self.policy = LatePolicy(policy)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        # effective state
        self._handoffs: dict[int, handoffs.Handoff] = {}
        self._revisions: dict[int, int] = {}
        self._quantities: dict[int, Decimal] = {}
        self._fingerprints: dict[int, set[str]] = {}
        self._watermark = 0  # highest gapless sequence accepted
        # late records not yet effective
        self._parked: dict[int, list[dict]] = {}
        self._quarantined: dict[int, list[dict]] = {}
        # append-only commit log and its audit projection
        self.commits: list[Commit] = []
        self._audit = AuditTrail(now=self._now)
        opening = Decimal(str(opening_quantity))
        if opening < 0:
            raise ValueError("opening quantity must be non-negative")
        if opening > 0:
            self._quantities[0] = opening
            self._append_commit(EV_OPEN, 0, 0, None, opening, "system",
                                "opening custody balance")

    # ------------------------------------------------------------------ utils

    def _append_commit(self, kind, seq, rev, hnd, quantity, actor, note) -> Commit:
        commit = Commit(len(self.commits) + 1, kind, seq, rev, hnd,
                        Decimal(str(quantity)) if quantity is not None else None,
                        actor, _utc(self._now()), note)
        self.commits.append(commit)
        # Bind the business payload (event time + seal + quantity) into the
        # audit hash, so tampering with a committed handoff breaks the chain.
        payload = "" if hnd is None else f":{hnd.at.isoformat()}:{hnd.seal}"
        qpart = "" if commit.quantity is None else f":{commit.quantity}"
        subject = f"custody:seq{seq}:rev{rev}{payload}{qpart}"
        self._audit.append(actor, kind, subject,
                           commit.recorded_at, recorded_at=commit.recorded_at)
        return commit

    def _effective_chain(self, overrides: dict[int, handoffs.Handoff] | None = None):
        """Gapless effective handoffs 1..watermark with overrides applied."""
        chain = []
        seq = 1
        while True:
            if overrides and seq in overrides:
                chain.append(overrides[seq])
            elif seq in self._handoffs:
                chain.append(self._handoffs[seq])
            else:
                break
            seq += 1
        return chain

    def _movements(self, extra: Iterable[tuple] = ()) -> list[tuple]:
        """Committed asset movements in commit order, plus trial extras.

        Base handoff moves are committed at the gap head, hence ascending in
        sequence; revisions are appended at their (later) commit position.
        """
        rows = []
        if self._quantities.get(0, Decimal("0")) > 0:
            rows.append(("open", 0, 0, "", self.opening_holder, self._quantities[0]))
        for c in self.commits:
            if c.kind in (EV_HANDOFF, EV_PROMOTE):
                rows.append(("move", c.sequence, 0, c.handoff.from_party,
                             c.handoff.to_party, c.quantity or Decimal("0")))
            elif c.kind == EV_REVISION:
                rows.append(("move", c.sequence, c.revision, c.handoff.from_party,
                             c.handoff.to_party, c.quantity or Decimal("0")))
        rows.extend(extra)
        return rows

    def _guarded(self, hnd: handoffs.Handoff, quantity: Decimal,
                 revision: int = 0) -> None:
        """Run all three guards as if hnd were effective; raise on failure."""
        if revision:
            chain = self._effective_chain({hnd.sequence: hnd})
            other_seals = {e.seal for s, e in enumerate(chain, start=1)
                           if s != hnd.sequence}
            if hnd.seal in other_seals:
                raise ValueError("seal invalid")
        else:
            chain = [*self._effective_chain(), hnd]
        handoffs.validate(self.opening_holder, chain)
        extra = []
        if revision:
            extra.append(("move", hnd.sequence, revision, hnd.from_party,
                          hnd.to_party, quantity))
        else:
            extra.append(("move", hnd.sequence, 0, hnd.from_party,
                          hnd.to_party, quantity))
        # project() enforces entry integrity and non-negative final
        # balances with revision-cascade semantics; no stricter prefix check
        # (compensations transiently dip during the downstream rewrite).
        project(self.origin, self._movements(extra))

    # ---------------------------------------------------------------- intake

    def submit(self, sequence: int, from_party: str, to_party: str,
               at: datetime, seal: str, quantity=None,
               actor: str = "system") -> Outcome:
        """Submit one handoff record. Never partially commits.

        Invalid caller input raises; late-but-unacceptable records are
        reported through the returned status (or :class:`LateRecordError`
        under REJECT) so the feed keeps replaying.
        """
        if not isinstance(sequence, int) or sequence < 1:
            raise ValueError("sequence must be a positive integer")
        qty = Decimal(str(quantity)) if quantity is not None else Decimal("0")
        if qty < 0:
            raise ValueError("quantity must be non-negative")
        stamped = _utc(self._now())
        hnd = handoffs.Handoff(sequence, from_party, to_party, _utc(at), seal)
        fingerprint = _fingerprint(from_party, to_party, hnd.at, seal, qty)

        with self._lock:
            # 1) duplicate replay -> same outcome, nothing appended
            dup = self._duplicate_outcome(sequence, fingerprint, stamped)
            if dup is not None:
                return dup

            # 2) gap future -> park (or reject)
            if sequence > self._watermark + 1:
                return self._suspend(hnd, qty, fingerprint, actor, stamped, PARKED,
                                     f"sequence {sequence} arrived before {self._watermark + 1}")

            # 3) already committed -> revise / quarantine / reject
            if sequence <= self._watermark:
                return self._handle_existing(hnd, qty, fingerprint, actor, stamped)

            # 4) exact gap head
            return self._accept_head(hnd, qty, fingerprint, actor, stamped)

    def _duplicate_outcome(self, sequence, fingerprint, stamped) -> Outcome | None:
        if fingerprint not in self._fingerprints.get(sequence, set()):
            return None
        if any(p["fingerprint"] == fingerprint for p in self._parked.get(sequence, [])):
            return Outcome(PARKED, sequence, 0, "duplicate replay of parked record", stamped)
        if any(q["fingerprint"] == fingerprint for q in self._quarantined.get(sequence, [])):
            return Outcome(QUARANTINED, sequence, 0,
                           "duplicate replay of quarantined record", stamped)
        status = DUPLICATE
        return Outcome(status, sequence, self._revisions.get(sequence, 0),
                       "duplicate replay", stamped)

    def _suspend(self, hnd, qty, fingerprint, actor, stamped, status, reason) -> Outcome:
        if self.policy is LatePolicy.REJECT:
            raise LateRecordError(f"late record at sequence {hnd.sequence}: {reason}")
        record = {"handoff": hnd, "quantity": qty, "fingerprint": fingerprint,
                  "actor": actor, "submitted_at": stamped, "reason": reason}
        bucket = self._parked if status == PARKED else self._quarantined
        bucket.setdefault(hnd.sequence, []).append(record)
        self._fingerprints.setdefault(hnd.sequence, set()).add(fingerprint)
        self._append_commit(EV_PARK if status == PARKED else EV_QUARANTINE,
                            hnd.sequence, 0, hnd, qty, actor, reason)
        return Outcome(status, hnd.sequence, 0, reason, stamped)

    def _handle_existing(self, hnd, qty, fingerprint, actor, stamped) -> Outcome:
        current = self._handoffs[hnd.sequence]
        same_payload = (
            (hnd.from_party, hnd.to_party, hnd.at, hnd.seal)
            == (current.from_party, current.to_party, current.at, current.seal)
            and qty == self._quantities.get(hnd.sequence, Decimal("0")))
        if same_payload:
            self._fingerprints.setdefault(hnd.sequence, set()).add(fingerprint)
            return Outcome(DUPLICATE, hnd.sequence,
                           self._revisions.get(hnd.sequence, 0),
                           "duplicate replay of committed record", stamped)

        reason = (f"sequence {hnd.sequence} already committed at "
                  f"{current.at.isoformat()}; late record at {hnd.at.isoformat()}")
        if self.policy is LatePolicy.REJECT:
            raise LateRecordError(f"late record at sequence {hnd.sequence}: {reason}")
        if self.policy is LatePolicy.SUSPEND:
            return self._suspend(hnd, qty, fingerprint, actor, stamped,
                                 QUARANTINED, reason)

        # REVISE: try a new compensating revision against all three guards
        rev = self._revisions.get(hnd.sequence, 0) + 1
        try:
            self._guarded(hnd, qty, revision=rev)
        except (ValueError, KeyError) as exc:
            return self._suspend(hnd, qty, fingerprint, actor, stamped,
                                 QUARANTINED, f"revision rejected by guard: {exc}")
        self._handoffs[hnd.sequence] = hnd
        self._revisions[hnd.sequence] = rev
        self._quantities[hnd.sequence] = qty
        self._fingerprints.setdefault(hnd.sequence, set()).add(fingerprint)
        self._append_commit(EV_REVISION, hnd.sequence, rev, hnd, qty, actor, reason)
        return Outcome(REVISED, hnd.sequence, rev, reason, stamped)

    def _accept_head(self, hnd, qty, fingerprint, actor, stamped) -> Outcome:
        try:
            self._guarded(hnd, qty)
        except (ValueError, KeyError) as exc:
            reason = f"record rejected by guard: {exc}"
            if self.policy is LatePolicy.REJECT:
                raise LateRecordError(reason) from exc
            return self._suspend(hnd, qty, fingerprint, actor, stamped,
                                 QUARANTINED, reason)

        self._handoffs[hnd.sequence] = hnd
        self._quantities[hnd.sequence] = qty
        self._fingerprints.setdefault(hnd.sequence, set()).add(fingerprint)
        self._watermark = hnd.sequence
        self._append_commit(EV_HANDOFF, hnd.sequence, 0, hnd, qty, actor,
                            "custody handoff")
        primary = Outcome(ACCEPTED, hnd.sequence, 0, "custody handoff", stamped)
        promoted = self._drain()
        if promoted:
            return Outcome(ACCEPTED, hnd.sequence, 0, "custody handoff",
                           stamped, also=tuple(promoted))
        return primary

    def _drain(self) -> list[Outcome]:
        """Promote parked records as gaps close.

        Several records may be parked for the same sequence (duplicate or
        competing late arrivals). The first that passes every guard wins;
        once one is promoted, the losing siblings are quarantined. If none
        passes, all remain in quarantine and the gap stays blocked.
        """
        results = []
        while self._watermark + 1 in self._parked:
            candidates = self._parked.pop(self._watermark + 1)
            winner = None
            failures = []
            for record in candidates:
                if winner is not None:
                    failures.append((record, "superseded while parked"))
                    continue
                hnd, qty = record["handoff"], record["quantity"]
                try:
                    self._guarded(hnd, qty)
                except (ValueError, KeyError) as exc:
                    failures.append((record,
                                     f"parked record failed guards on promotion: {exc}"))
                    continue
                winner = record
            if winner is None:
                for record, reason in failures:
                    record["reason"] = reason
                    self._quarantined.setdefault(record["handoff"].sequence, []).append(record)
                    self._fingerprints.setdefault(record["handoff"].sequence,
                                                  set()).add(record["fingerprint"])
                    self._append_commit(EV_QUARANTINE, record["handoff"].sequence, 0,
                                        record["handoff"], record["quantity"],
                                        record["actor"], reason)
                    results.append(Outcome(QUARANTINED, record["handoff"].sequence,
                                           0, reason, _utc(self._now())))
                break
            hnd, qty = winner["handoff"], winner["quantity"]
            self._handoffs[hnd.sequence] = hnd
            self._quantities[hnd.sequence] = qty
            self._watermark = hnd.sequence
            self._append_commit(EV_PROMOTE, hnd.sequence, 0, hnd, qty,
                                winner["actor"], "promoted after gap filled")
            results.append(Outcome(ACCEPTED, hnd.sequence, 0,
                                   "promoted after gap filled", _utc(self._now())))
            for record, reason in failures:
                record["reason"] = reason
                self._quarantined.setdefault(hnd.sequence, []).append(record)
                self._append_commit(EV_QUARANTINE, hnd.sequence, 0,
                                    record["handoff"], record["quantity"],
                                    record["actor"], reason)
                results.append(Outcome(QUARANTINED, hnd.sequence, 0,
                                       reason, _utc(self._now())))
        return results

    # ---------------------------------------------------------------- views

    def custodian(self) -> str:
        return self.opening_holder if self._watermark == 0 \
            else self._handoffs[self._watermark].to_party

    def effective_handoffs(self) -> tuple[handoffs.Handoff, ...]:
        return tuple(self._handoffs[s] for s in range(1, self._watermark + 1))

    def revision_of(self, sequence: int) -> int:
        return self._revisions.get(sequence, 0)

    def pending(self) -> tuple[tuple[int, handoffs.Handoff], ...]:
        return tuple((seq, r["handoff"])
                     for seq in sorted(self._parked) for r in self._parked[seq])

    def quarantined(self) -> tuple[tuple[int, handoffs.Handoff, str], ...]:
        return tuple((seq, r["handoff"], r["reason"])
                     for seq in sorted(self._quarantined)
                     for r in self._quarantined[seq])

    def movements(self) -> tuple[tuple, ...]:
        return tuple(self._movements())

    def ledger(self) -> CustodyLedger:
        return project(self.origin, self._movements())

    def audit_trail(self) -> AuditTrail:
        return self._audit

    def verify(self) -> bool:
        """Re-derive every projection from the commit log and compare."""
        def rows(ledger):
            return [(r.sequence, r.account, r.reference, r.quantity,
                     r.reason, r.reversal_of) for r in ledger.entries()]
        if rows(self.ledger()) != rows(project(self.origin, self._movements())):
            return False
        # rebuild the audit hash chain straight from commits; if a committed
        # payload was tampered with, the digests diverge
        entries = []
        for c in self.commits:
            payload = "" if c.handoff is None else f":{c.handoff.at.isoformat()}:{c.handoff.seal}"
            qpart = "" if c.quantity is None else f":{c.quantity}"
            entries.append((c.actor, c.kind,
                            f"custody:seq{c.sequence}:rev{c.revision}{payload}{qpart}",
                            c.recorded_at, c.recorded_at))
        rebuilt_trail = AuditTrail.rebuild(entries)
        if [r.digest for r in rebuilt_trail.records] != \
                [r.digest for r in self._audit.records]:
            return False
        handoffs.validate(self.opening_holder, self._effective_chain())
        return self._audit.verify()

    # ------------------------------------------------------------- replay

    @classmethod
    def replay(cls, commits: Iterable[Commit], origin: str,
               opening_holder: str | None = None,
               policy: LatePolicy = LatePolicy.SUSPEND) -> "HandoffLog":
        """Rebuild a log deterministically from committed commits.

        Each commit's own ``recorded_at`` stamps its audit entry, so replaying
        the same commit list always reproduces identical audit digests, ledger
        entries and effective state -- including when the feed is replayed
        twice.
        """
        commits = list(commits)
        stamps = iter([c.recorded_at for c in commits])

        def replay_clock():
            # commit stamps while rebuilding; afterwards behave as a live clock
            try:
                return next(stamps)
            except StopIteration:
                return datetime.now(timezone.utc)

        log = cls.__new__(cls)
        log.origin = origin
        log.opening_holder = opening_holder or origin
        log.policy = LatePolicy(policy)
        log._now = replay_clock
        log._lock = RLock()
        log._handoffs = {}
        log._revisions = {}
        log._quantities = {}
        log._fingerprints = {}
        log._watermark = 0
        log._parked = {}
        log._quarantined = {}
        log.commits = []
        log._audit = AuditTrail(now=log._now)
        for c in commits:
            hnd = c.handoff
            if c.kind == EV_OPEN:
                log._quantities[0] = c.quantity or Decimal("0")
                log._append_commit(EV_OPEN, 0, 0, None, c.quantity, c.actor, c.note)
            elif c.kind in (EV_HANDOFF, EV_PROMOTE):
                log._handoffs[c.sequence] = hnd
                log._quantities[c.sequence] = c.quantity or Decimal("0")
                log._watermark = max(log._watermark, c.sequence)
                log._fingerprints.setdefault(c.sequence, set()).add(
                    _fingerprint(hnd.from_party, hnd.to_party, hnd.at, hnd.seal,
                                 c.quantity or Decimal("0")))
                log._append_commit(c.kind, c.sequence, 0, hnd, c.quantity,
                                   c.actor, c.note)
            elif c.kind == EV_REVISION:
                log._handoffs[c.sequence] = hnd
                log._revisions[c.sequence] = c.revision
                log._quantities[c.sequence] = c.quantity or Decimal("0")
                log._fingerprints.setdefault(c.sequence, set()).add(
                    _fingerprint(hnd.from_party, hnd.to_party, hnd.at, hnd.seal,
                                 c.quantity or Decimal("0")))
                log._append_commit(EV_REVISION, c.sequence, c.revision, hnd,
                                   c.quantity, c.actor, c.note)
            elif c.kind in (EV_PARK, EV_QUARANTINE):
                fp = _fingerprint(hnd.from_party, hnd.to_party, hnd.at, hnd.seal,
                                  c.quantity or Decimal("0"))
                bucket = log._parked if c.kind == EV_PARK else log._quarantined
                bucket.setdefault(c.sequence, []).append({
                    "handoff": hnd, "quantity": c.quantity or Decimal("0"),
                    "fingerprint": fp,
                    "actor": c.actor, "submitted_at": c.recorded_at,
                    "reason": c.note})
                log._fingerprints.setdefault(c.sequence, set()).add(fp)
                log._append_commit(c.kind, c.sequence, 0, hnd, c.quantity,
                                   c.actor, c.note)
            else:  # pragma: no cover - defensive
                raise ValueError(f"unknown commit kind {c.kind}")
        return log
