from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json


@dataclass(frozen=True)
class Record:
    index: int
    actor: str
    action: str
    subject: str
    at: datetime
    previous: str
    digest: str
    corrects: int | None = None
    occurred_at: datetime | None = None


class AuditOrderError(ValueError):
    def __init__(self, at: datetime, watermark: datetime):
        self.at = at
        self.watermark = watermark
        super().__init__(
            f"audit event time moved backward: {at.isoformat()} <= watermark {watermark.isoformat()}"
        )


class AuditTrail:
    """Hash-chained, append-only audit trail.

    Each record's ``at`` is its committed position on the trail and must
    advance strictly; the digest chains each record to the previous one, so
    splicing into the middle breaks ``verify``. ``occurred_at`` optionally
    carries the real-world event time (it may predate the trail position) and
    is covered by the digest, so late facts are never silently rewritten.

    A late fact is never inserted in the middle: it is recorded at the tail as
    a revision (``corrects`` pointing at the original index). Because commit
    positions are supplied by the caller rather than read from the wall clock,
    replaying the same decisions reconstructs byte-identical digests.
    """

    GENESIS = "GENESIS"

    def __init__(self):
        self.records: list[Record] = []

    @staticmethod
    def digest(index, actor, action, subject, at, previous,
               corrects=None, occurred_at=None):
        occurred = occurred_at.astimezone(timezone.utc).isoformat() if occurred_at else None
        raw = json.dumps(
            [index, actor, action, subject,
             at.astimezone(timezone.utc).isoformat(), previous,
             corrects, occurred],
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(raw).hexdigest()

    @property
    def watermark(self) -> datetime | None:
        """Highest committed trail position."""
        return self.records[-1].at.astimezone(timezone.utc) if self.records else None

    def is_late(self, at: datetime) -> bool:
        wm = self.watermark
        return wm is not None and at.astimezone(timezone.utc) <= wm

    def append(self, actor, action, subject, at=None,
               corrects=None, occurred_at=None):
        at = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if occurred_at is not None:
            occurred_at = occurred_at.astimezone(timezone.utc)
        prev = self.records[-1].digest if self.records else self.GENESIS
        i = len(self.records) + 1
        if corrects is not None and not (1 <= corrects < i):
            raise ValueError("corrects must point at an earlier record")
        r = Record(i, actor, action, subject, at, prev,
                   self.digest(i, actor, action, subject, at, prev,
                               corrects, occurred_at),
                   corrects, occurred_at)
        self.records.append(r)
        return r

    def ordered_append(self, actor, action, subject, at,
                       corrects=None, occurred_at=None):
        """Append only when the trail position strictly advances.

        A late position (at/before the watermark) raises AuditOrderError; the
        caller must reject, suspend or record a revision instead.
        """
        at = at.astimezone(timezone.utc)
        wm = self.watermark
        if wm is not None and at <= wm:
            raise AuditOrderError(at, wm)
        return self.append(actor, action, subject, at, corrects, occurred_at)

    def revise(self, actor, action, subject, at, *, corrects: int, occurred_at):
        """Record a correction at the tail.

        ``at`` is the revision's commit position (after the watermark); the
        corrected/observed event time goes in ``occurred_at``. Arrival order
        and the hash chain are both preserved while replay keeps the evidence.
        """
        if not 1 <= corrects <= len(self.records):
            raise ValueError("corrects must point at an existing record")
        return self.ordered_append(actor, action, subject, at,
                                   corrects=corrects, occurred_at=occurred_at)

    def verify(self) -> bool:
        prev = self.GENESIS
        for r in self.records:
            if r.previous != prev:
                return False
            if r.digest != self.digest(r.index, r.actor, r.action, r.subject,
                                        r.at, prev, r.corrects, r.occurred_at):
                return False
            if r.corrects is not None and not (1 <= r.corrects < r.index):
                return False
            prev = r.digest
        return True

    @classmethod
    def replay(cls, records):
        """Rebuild a trail from committed records and verify its hash chain."""
        trail = cls()
        prev = cls.GENESIS
        for r in records:
            if r.index != len(trail.records) + 1:
                raise ValueError("audit replay stream is not contiguous")
            if r.previous != prev:
                raise ValueError("audit chain broken during replay")
            if r.digest != cls.digest(r.index, r.actor, r.action, r.subject,
                                      r.at, prev, r.corrects, r.occurred_at):
                raise ValueError("audit digest mismatch during replay")
            if r.corrects is not None and not (1 <= r.corrects < r.index):
                raise ValueError("invalid revision pointer during replay")
            trail.records.append(r)
            prev = r.digest
        return trail
