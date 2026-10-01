from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from threading import RLock
from typing import Iterable


@dataclass(frozen=True)
class Entry:
    sequence: int
    account: str
    reference: str
    quantity: Decimal
    reason: str
    reversal_of: int | None = None
    revision_of: int | None = None
    occurred_at: datetime | None = None


class CustodyLedger:
    """Append-only asset ledger.

    Rows are never rewritten: a correction is a *revision* (a compensating
    reversal followed by a corrected entry) appended at the tail. Posting the
    same idempotency reference twice is an error only when the body differs;
    re-submitting the identical command returns the original entry, which is
    what makes repeated replay produce the same ledger.
    """

    def __init__(self):
        self._rows: list[Entry] = []
        self._refs: dict[tuple[str, str], Entry] = {}
        self._lock = RLock()

    @staticmethod
    def _q(quantity) -> Decimal:
        return Decimal(str(quantity))

    def _store(self, row: Entry) -> Entry:
        self._rows.append(row)
        self._refs[(row.account, row.reference)] = row
        return row

    def posted(self, account: str, reference: str) -> Entry | None:
        """Return the entry already stored under an idempotency key, if any."""
        with self._lock:
            return self._refs.get((account, reference))

    def post(
        self,
        account: str,
        reference: str,
        quantity,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> Entry:
        q = self._q(quantity)
        if not account or not reference or not reason:
            raise ValueError("account, reference and reason required")
        if q == 0:
            raise ValueError("zero custody entry")
        key = (account, reference)
        with self._lock:
            existing = self._refs.get(key)
            if existing is not None:
                # Same command replayed: idempotent no-op returning the stored row.
                if existing.quantity == q and existing.reason == reason:
                    return existing
                raise ValueError("duplicate idempotency reference")
            return self._store(Entry(len(self._rows) + 1, account, reference, q, reason,
                                     occurred_at=occurred_at))

    def reverse(self, sequence: int, reference: str, reason: str,
                occurred_at: datetime | None = None) -> Entry:
        with self._lock:
            originals = [r for r in self._rows if r.sequence == sequence]
            if not originals:
                raise KeyError(sequence)
            original = originals[0]
            if any(r.reversal_of == sequence for r in self._rows):
                raise ValueError("entry already reversed")
            key = (original.account, reference)
            existing = self._refs.get(key)
            if existing is not None:
                if (existing.quantity == -original.quantity
                        and existing.reason == reason
                        and existing.reversal_of == sequence):
                    return existing
                raise ValueError("duplicate reversal reference")
            return self._store(Entry(len(self._rows) + 1, original.account, reference,
                                     -original.quantity, reason, sequence,
                                     occurred_at=occurred_at))

    def revise(
        self,
        sequence: int,
        corrected_quantity,
        reference: str,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> tuple[Entry, Entry]:
        """Correct a posted entry without rewriting it.

        Appends a compensating reversal of the original row and then a corrected
        entry; both land at the tail so the audit order stays append-only and
        the revision is itself idempotent (same reference replays the same pair).
        """
        q = self._q(corrected_quantity)
        if q == 0:
            raise ValueError("zero custody entry")
        with self._lock:
            originals = [r for r in self._rows if r.sequence == sequence]
            if not originals:
                raise KeyError(sequence)
            original = originals[0]
            reversal_ref = reference + ":reverse"
            corrected_ref = reference + ":corrected"
            if original.quantity == q:
                raise ValueError("revision is a no-op")
            reversal = self._post_locked(
                original.account, reversal_ref, -original.quantity, reason,
                reversal_of=sequence, occurred_at=occurred_at)
            corrected = self._post_locked(
                original.account, corrected_ref, q, reason,
                revision_of=sequence, occurred_at=occurred_at)
            return reversal, corrected

    def _post_locked(self, account, reference, quantity, reason,
                     reversal_of=None, revision_of=None, occurred_at=None) -> Entry:
        q = self._q(quantity)
        key = (account, reference)
        existing = self._refs.get(key)
        if existing is not None:
            if (existing.quantity == q and existing.reason == reason
                    and existing.reversal_of == reversal_of
                    and existing.revision_of == revision_of):
                return existing
            raise ValueError("duplicate idempotency reference")
        return self._store(Entry(len(self._rows) + 1, account, reference, q, reason,
                                 reversal_of, revision_of, occurred_at))

    def balance(self, account: str) -> Decimal:
        with self._lock:
            return sum((r.quantity for r in self._rows if r.account == account), Decimal("0"))

    def entries(self, account: str | None = None) -> tuple[Entry, ...]:
        with self._lock:
            rows = self._rows if account is None else [r for r in self._rows if r.account == account]
            return tuple(rows)

    def reconcile(self, expected: dict[str, Decimal]) -> dict[str, Decimal]:
        accounts = set(expected) | {r.account for r in self._rows}
        return {a: self.balance(a) - expected.get(a, Decimal("0")) for a in sorted(accounts)}

    def transfer(self, source: str, target: str, quantity, reference: str, reason: str,
                 occurred_at: datetime | None = None) -> tuple[Entry, Entry]:
        q = Decimal(str(quantity))
        if q <= 0:
            raise ValueError("transfer quantity must be positive")
        with self._lock:
            debit_ref = reference + ":debit"
            credit_ref = reference + ":credit"
            d_existing = self._refs.get((source, debit_ref))
            c_existing = self._refs.get((target, credit_ref))
            # Idempotent replay of a previously recorded transfer.
            if d_existing is not None or c_existing is not None:
                if d_existing is None or c_existing is None or d_existing.quantity != -q or c_existing.quantity != q:
                    raise ValueError("duplicate transfer reference")
                return d_existing, c_existing
            if self.balance(source) < q:
                raise ValueError("insufficient source balance")
            debit = self._store(Entry(len(self._rows)+1, source, debit_ref, -q, reason,
                                      occurred_at=occurred_at))
            credit = self._store(Entry(len(self._rows)+1, target, credit_ref, q, reason,
                                       occurred_at=occurred_at))
            return debit, credit

    def audit(self) -> None:
        seen_sequences = set()
        seen_refs = set()
        for n, row in enumerate(self._rows, start=1):
            if row.sequence != n:
                raise ValueError("sequence gap")
            if row.sequence in seen_sequences:
                raise ValueError("duplicate sequence")
            seen_sequences.add(row.sequence)
            key = (row.account, row.reference)
            if key in seen_refs:
                raise ValueError("duplicate reference")
            seen_refs.add(key)
            if row.reversal_of is not None and row.reversal_of >= row.sequence:
                raise ValueError("invalid reversal pointer")
            if row.revision_of is not None and row.revision_of >= row.sequence:
                raise ValueError("invalid revision pointer")

    @classmethod
    def replay(cls, entries: Iterable[Entry]) -> "CustodyLedger":
        """Rebuild a ledger deterministically from a committed entry stream.

        Sequences are restored verbatim and the stream is checked for gaps,
        duplicate references and dangling pointers, so the same stream always
        reconstructs the same balances and rows in the same order.
        """
        ledger = cls()
        expected = 1
        with ledger._lock:
            for row in entries:
                if row.sequence != expected:
                    raise ValueError("replay stream is not contiguous")
                key = (row.account, row.reference)
                if key in ledger._refs:
                    raise ValueError("duplicate reference in replay stream")
                if row.reversal_of is not None and row.reversal_of >= row.sequence:
                    raise ValueError("invalid reversal pointer")
                if row.revision_of is not None and row.revision_of >= row.sequence:
                    raise ValueError("invalid revision pointer")
                ledger._store(row)
                expected += 1
        return ledger


def net(entries: Iterable[Entry]) -> Decimal:
    return sum((r.quantity for r in entries), Decimal("0"))


def by_reason(entries: Iterable[Entry]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for r in entries:
        out[r.reason] = out[r.reason] + r.quantity if r.reason in out else r.quantity
    return out
