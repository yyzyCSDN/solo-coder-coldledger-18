from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable


@dataclass(frozen=True)
class Handoff:
    sequence: int
    from_party: str
    to_party: str
    at: datetime
    seal: str


class CustodyError(ValueError):
    """Base class for custody-chain violations."""


class SequenceGap(CustodyError):
    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(f"sequence gap: expected {expected}, got {actual}")


class CustodyDiscontinuity(CustodyError):
    pass


class DuplicateCustodyInstant(CustodyError):
    def __init__(self, at: datetime):
        self.at = at
        super().__init__("duplicate custody instant")


class CustodyMovedBackward(CustodyError):
    def __init__(self, at: datetime, watermark: datetime):
        self.at = at
        self.watermark = watermark
        super().__init__(
            f"custody event time moved backward: {at.isoformat()} <= watermark {watermark.isoformat()}"
        )


class SealInvalid(CustodyError):
    pass


def _utc(at: datetime) -> datetime:
    return at.astimezone(timezone.utc)


def check_handoff(
    handoff: Handoff,
    *,
    expected_sequence: int,
    current_party: str,
    watermark: datetime | None,
    seals: set[str],
) -> datetime:
    """Validate one handoff against the accepted chain head.

    Returns the event time in UTC. Raises a CustodyError subclass on violation.
    Time must be strictly greater than the watermark: a late record carrying a
    correct sequence but an event time at/before the watermark is exactly the
    backwards-timeline case that audit and asset ledgers cannot replay.
    """
    if handoff.sequence != expected_sequence:
        raise SequenceGap(expected_sequence, handoff.sequence)
    if handoff.from_party != current_party or handoff.to_party == current_party:
        raise CustodyDiscontinuity("custody discontinuity")
    at = _utc(handoff.at)
    if watermark is not None and at < watermark:
        raise CustodyMovedBackward(at, watermark)
    if watermark is not None and at == watermark:
        raise DuplicateCustodyInstant(at)
    if not handoff.seal or handoff.seal in seals:
        raise SealInvalid("seal invalid")
    return at


def validate(origin: str, events: Iterable[Handoff]) -> str:
    """Validate a custody chain and return the final holder.

    Events are ordered by sequence; the chain must stay continuous, seals must
    be unique and event time must advance strictly.
    """
    current = origin
    last: datetime | None = None
    seq = 1
    seals: set[str] = set()
    for e in sorted(events, key=lambda x: x.sequence):
        at = check_handoff(
            e,
            expected_sequence=seq,
            current_party=current,
            watermark=last,
            seals=seals,
        )
        seals.add(e.seal)
        current = e.to_party
        last = at
        seq += 1
    return current


class CustodyChain:
    """Append-only, strictly ordered chain-of-custody.

    Acceptance order is forced to equal sequence order and event-time order.
    A late record (correct next sequence, event time at/before the watermark)
    cannot be spliced into the middle; the caller must reject, suspend or
    revise it. Replaying the accepted handoffs always rebuilds an identical
    chain.
    """

    def __init__(self, origin: str):
        self._origin = origin
        self._handoffs: list[Handoff] = []
        self._by_seq: dict[int, Handoff] = {}
        self._seals: set[str] = set()

    @property
    def origin(self) -> str:
        return self._origin

    @property
    def current(self) -> str:
        return self._handoffs[-1].to_party if self._handoffs else self._origin

    @property
    def next_sequence(self) -> int:
        return len(self._handoffs) + 1

    @property
    def watermark(self) -> datetime | None:
        return _utc(self._handoffs[-1].at) if self._handoffs else None

    @property
    def timeline(self) -> tuple[Handoff, ...]:
        return tuple(self._handoffs)

    def accepted(self, sequence: int) -> Handoff | None:
        return self._by_seq.get(sequence)

    def is_late(self, handoff: Handoff) -> bool:
        """True when sequence is the next expected one but event time regresses."""
        if handoff.sequence != self.next_sequence:
            return False
        wm = self.watermark
        return wm is not None and _utc(handoff.at) <= wm
    def accept(self, handoff: Handoff) -> Handoff:
        at = check_handoff(
            handoff,
            expected_sequence=self.next_sequence,
            current_party=self.current,
            watermark=self.watermark,
            seals=self._seals,
        )
        stored = Handoff(
            handoff.sequence,
            handoff.from_party,
            handoff.to_party,
            at,
            handoff.seal,
        )
        self._handoffs.append(stored)
        self._by_seq[stored.sequence] = stored
        self._seals.add(stored.seal)
        return stored

    @classmethod
    def replay(cls, origin: str, handoffs: Iterable[Handoff]) -> "CustodyChain":
        chain = cls(origin)
        for h in sorted(handoffs, key=lambda x: x.sequence):
            chain.accept(h)
        return chain
