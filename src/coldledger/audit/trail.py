from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib, json


@dataclass(frozen=True)
class Record:
    index: int
    actor: str
    action: str
    subject: str
    at: datetime
    previous: str
    digest: str
    recorded_at: datetime | None = None


class AuditTrail:
    """Append-only hash-chained audit trail.

    Two distinct clocks are tracked: ``recorded_at`` (when the audit entry was
    written) and the business event time ``at`` supplied by the caller. The
    hash chain commits in record order; both timelines must be non-decreasing
    between consecutive entries, otherwise audit replay would describe a
    different ordering than the one that was observed.
    """

    GENESIS = 'GENESIS'

    def __init__(self, now=None):
        self.records = []
        self._now = now or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def digest(index, actor, action, subject, at, previous, recorded_at):
        raw = json.dumps([
            index, actor, action, subject,
            at.astimezone(timezone.utc).isoformat(),
            previous,
            recorded_at.astimezone(timezone.utc).isoformat(),
        ], separators=(',', ':')).encode()
        return hashlib.sha256(raw).hexdigest()

    def append(self, actor, action, subject, at=None, recorded_at=None):
        at = (at or self._now()).astimezone(timezone.utc)
        stamped = (recorded_at or self._now()).astimezone(timezone.utc)
        if self.records:
            if at < self.records[-1].at:
                raise ValueError('audit event time moved backward')
            if stamped < self.records[-1].recorded_at:
                raise ValueError('audit record time moved backward')
        prev = self.records[-1].digest if self.records else self.GENESIS
        i = len(self.records) + 1
        r = Record(i, actor, action, subject, at, prev,
                   self.digest(i, actor, action, subject, at, prev, stamped), stamped)
        self.records.append(r)
        return r

    def head(self):
        return self.records[-1].digest if self.records else self.GENESIS

    def verify(self):
        prev = self.GENESIS
        last_at = None
        last_recorded = None
        for r in self.records:
            if r.recorded_at is None:
                return False
            if r.previous != prev or r.digest != self.digest(
                    r.index, r.actor, r.action, r.subject, r.at, prev, r.recorded_at):
                return False
            at = r.at.astimezone(timezone.utc)
            if last_at is not None and at < last_at:
                return False
            if last_recorded is not None and r.recorded_at < last_recorded:
                return False
            prev = r.digest
            last_at = at
            last_recorded = r.recorded_at
        return True

    @classmethod
    def rebuild(cls, entries, now=None):
        """Deterministically rebuild a trail from committed audit entries.

        Each entry is ``(actor, action, subject, at, recorded_at)``; the five
        tuple is mandatory for rebuild so replaying the same committed data
        twice always yields byte-identical indexes, links and digests.
        """
        trail = cls(now=now)
        for actor, action, subject, at, recorded_at in entries:
            trail.append(actor, action, subject, at, recorded_at=recorded_at)
        return trail

    def committed(self):
        """Entries in replay form, consumable by :meth:`rebuild`."""
        return [(r.actor, r.action, r.subject, r.at, r.recorded_at) for r in self.records]
