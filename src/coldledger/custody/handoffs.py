from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Handoff:
    sequence: int
    from_party: str
    to_party: str
    at: datetime
    seal: str


def validate(origin, events):
    """Validate a gapless, continuous custody chain whose event time never moves backward.

    Sequences must be 1..N with no gaps (arrival order is irrelevant: events are
    sorted by sequence). Each handoff must leave the party currently holding
    custody, timestamps must be strictly non-decreasing along the chain, and
    every seal must be present and unique. Returns the party holding custody
    after the final handoff.
    """
    current = origin
    last_at = None
    seq = 1
    seals = set()
    for e in sorted(events, key=lambda x: x.sequence):
        if e.sequence != seq:
            raise ValueError('sequence gap')
        if e.from_party != current or e.to_party == current:
            raise ValueError('custody discontinuity')
        at = e.at.astimezone(timezone.utc)
        if last_at is not None and at < last_at:
            raise ValueError('custody event time moved backward')
        if last_at is not None and at == last_at:
            raise ValueError('duplicate custody instant')
        if not e.seal or e.seal in seals:
            raise ValueError('seal invalid')
        seals.add(e.seal)
        current = e.to_party
        last_at = at
        seq += 1
    return current
