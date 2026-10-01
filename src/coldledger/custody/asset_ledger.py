from __future__ import annotations
from dataclasses import dataclass
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

class CustodyLedger:
    def __init__(self):
        self._rows: list[Entry] = []
        self._refs: set[tuple[str, str]] = set()
        self._lock = RLock()

    def post(self, account: str, reference: str, quantity, reason: str) -> Entry:
        q = Decimal(str(quantity))
        if not account or not reference or not reason:
            raise ValueError("account, reference and reason required")
        if q == 0:
            raise ValueError("zero custody entry")
        key = (account, reference)
        with self._lock:
            if key in self._refs:
                raise ValueError("duplicate idempotency reference")
            row = Entry(len(self._rows) + 1, account, reference, q, reason)
            self._rows.append(row)
            self._refs.add(key)
            return row

    def reverse(self, sequence: int, reference: str, reason: str) -> Entry:
        with self._lock:
            originals = [r for r in self._rows if r.sequence == sequence]
            if not originals:
                raise KeyError(sequence)
            original = originals[0]
            if any(r.reversal_of == sequence for r in self._rows):
                raise ValueError("entry already reversed")
            key = (original.account, reference)
            if key in self._refs:
                raise ValueError("duplicate reversal reference")
            row = Entry(len(self._rows) + 1, original.account, reference, -original.quantity, reason, sequence)
            self._rows.append(row)
            self._refs.add(key)
            return row

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

    def transfer(self, source: str, target: str, quantity, reference: str, reason: str) -> tuple[Entry, Entry]:
        q = Decimal(str(quantity))
        if q <= 0:
            raise ValueError("transfer quantity must be positive")
        with self._lock:
            if self.balance(source) < q:
                raise ValueError("insufficient source balance")
            debit_ref = reference + ":debit"
            credit_ref = reference + ":credit"
            if (source, debit_ref) in self._refs or (target, credit_ref) in self._refs:
                raise ValueError("duplicate transfer reference")
            debit = Entry(len(self._rows)+1, source, debit_ref, -q, reason)
            credit = Entry(len(self._rows)+2, target, credit_ref, q, reason)
            self._rows.extend([debit, credit])
            self._refs.add((source, debit_ref)); self._refs.add((target, credit_ref))
            return debit, credit

    def audit(self, running_balance: bool = True) -> None:
        """Verify ledger integrity.

        With ``running_balance`` (the default for ledgers posted directly),
        every prefix must keep each account non-negative. Projections built
        from a committed event log instead pass ``running_balance=False`` and
        check final balances separately: revision compensations interleave in
        commit order and may transiently dip negative while the fold rewrites
        the downstream chain.
        """
        seen_sequences = set()
        seen_refs = set()
        prev_sequence = 0
        for row in self._rows:
            if row.sequence in seen_sequences:
                raise ValueError("duplicate sequence")
            seen_sequences.add(row.sequence)
            if row.sequence != prev_sequence + 1:
                raise ValueError("sequence gap")
            prev_sequence = row.sequence
            key = (row.account, row.reference)
            if key in seen_refs:
                raise ValueError("duplicate reference")
            seen_refs.add(key)
            if row.reversal_of is not None and row.reversal_of >= row.sequence:
                raise ValueError("invalid reversal pointer")
        if running_balance:
            running: dict[str, Decimal] = {}
            for row in self._rows:
                running[row.account] = running.get(row.account, Decimal("0")) + row.quantity
                if running[row.account] < 0:
                    raise ValueError("custody balance went negative")

    def final_balances_non_negative(self) -> None:
        accounts = {r.account for r in self._rows}
        for account in accounts:
            if self.balance(account) < 0:
                raise ValueError("custody balance went negative")


def project(origin: str, movements: Iterable[tuple]) -> "CustodyLedger":
    """Pure fold: build a CustodyLedger from committed custody movements.

    Each movement is a tuple ``(kind, seq, rev, frm, to, quantity)``:

    * ``("open", 0, 0, "", holder, qty)`` posts the opening balance.
    * ``("move", n, 0, frm, to, qty)`` posts the transfer for handoff ``n``.
    * ``("move", n, k, frm, to, qty)`` with ``k >= 1`` is a revision of
      sequence ``n``. It reverses every movement currently effective at
      sequences ``n`` through the latest committed sequence in descending
      order, posts the corrected transfer at ``n``, then reposts the
      downstream movements unchanged. When the revised flow cannot be funded,
      an account ends negative and the projection rejects it; the original
      history stays append-only either way.

    References derive from ``(seq, rev, originating_seq, role)`` alone, so the
    output depends only on the committed input: replaying the same events any
    number of times yields identical entry sequences, references and
    balances -- even when distinct revisions both rewrite the same
    downstream sequence.
    """
    ledger = CustodyLedger()
    # seq -> (frm, to, qty, debit_seq, credit_seq) of the effective movement
    effective: dict[int, tuple] = {}

    def undo(origin, seq, rev, tag):
        frm, to, qty, debit_seq, credit_seq = effective[seq]
        base = f"custody:seq{seq}:rev{rev}from{origin}"
        if debit_seq > 0:
            ledger.reverse(debit_seq, f"{base}:{tag}:debit",
                           f"revision {rev} of {origin}: undo handoff {seq}")
            ledger.reverse(credit_seq, f"{base}:{tag}:credit",
                           f"revision {rev} of {origin}: undo handoff {seq}")

    def post_move(seq, rev_tag, frm, to, qty):
        if rev_tag == 0:
            ref = f"custody:seq{seq}"
            reason = f"custody handoff {seq}"
        else:
            ref = f"custody:seq{seq}:{rev_tag}"
            reason = f"{rev_tag} of handoff {seq}"
        debit = ledger.post(frm, ref + ":debit", -qty, reason)
        credit = ledger.post(to, ref + ":credit", qty, reason)
        return debit.sequence, credit.sequence

    for kind, seq, rev, frm, to, qty in movements:
        q = Decimal(str(qty))
        if kind == "open":
            if seq != 0 or q < 0:
                raise ValueError("invalid opening custody movement")
            if q > 0:
                ledger.post(to, "custody:seq0:opening", q, "opening custody balance")
            continue
        if kind != "move":
            raise ValueError("unknown custody movement kind")
        if seq < 1 or rev < 0 or q < 0:
            raise ValueError("invalid custody movement")
        if rev == 0:
            if seq in effective:
                raise ValueError("duplicate movement for custody sequence")
            if q != 0:
                debit_seq, credit_seq = post_move(seq, 0, frm, to, q)
                effective[seq] = (frm, to, q, debit_seq, credit_seq)
            else:
                effective[seq] = (frm, to, q, -1, -1)
            continue
        if seq not in effective:
            raise ValueError("revision precedes original movement")
        latest = max(effective)
        affected = list(range(seq, latest + 1))
        for s in reversed(affected):
            undo(seq, s, rev, "undo")
        if q != 0:
            debit_seq, credit_seq = post_move(seq, f"rev{rev}:fix", frm, to, q)
            effective[seq] = (frm, to, q, debit_seq, credit_seq)
        else:
            effective[seq] = (frm, to, q, -1, -1)
        for s in affected:
            if s == seq:
                continue
            old_frm, old_to, old_qty, _d, _c = effective[s]
            if old_qty != 0:
                debit_seq, credit_seq = post_move(
                    s, f"rev{rev}from{seq}:repost", old_frm, old_to, old_qty)
                effective[s] = (old_frm, old_to, old_qty, debit_seq, credit_seq)
    ledger.audit(running_balance=False)
    ledger.final_balances_non_negative()
    return ledger


def net(entries: Iterable[Entry]) -> Decimal:
    return sum((r.quantity for r in entries), Decimal("0"))


def by_reason(entries: Iterable[Entry]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for r in entries:
        out[r.reason] = out.get(r.reason, Decimal("0")) + r.quantity
    return out
