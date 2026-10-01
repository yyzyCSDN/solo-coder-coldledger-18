import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from coldledger.custody.handoffs import (
    Handoff, validate, CustodyChain,
    CustodyMovedBackward, DuplicateCustodyInstant, SequenceGap,
    CustodyDiscontinuity, SealInvalid,
)
from coldledger.custody.asset_ledger import CustodyLedger
from coldledger.custody.ingestion import (
    CustodyIngestor, Policy, Decision,
)
from coldledger.audit.trail import AuditTrail, AuditOrderError

UTC = timezone.utc
T = lambda y, m, d, h=12: datetime(y, m, d, h, tzinfo=UTC)


def h(seq, frm, to, at, seal):
    return Handoff(seq, frm, to, at, seal)


class TestHandoffValidation(unittest.TestCase):
    def test_orderly_chain_validates(self):
        events = [
            h(1, "A", "B", T(2026, 1, 1), "s1"),
            h(2, "B", "C", T(2026, 1, 2), "s2"),
        ]
        self.assertEqual(validate("A", events), "C")

    def test_sequence_gap_rejected(self):
        events = [h(2, "A", "B", T(2026, 1, 1), "s1")]
        with self.assertRaises(SequenceGap):
            validate("A", events)

    def test_discontinuity_rejected(self):
        events = [
            h(1, "A", "B", T(2026, 1, 1), "s1"),
            h(2, "A", "C", T(2026, 1, 2), "s2"),
        ]
        with self.assertRaises(CustodyDiscontinuity):
            validate("A", events)

    def test_same_instant_rejected(self):
        events = [
            h(1, "A", "B", T(2026, 1, 1, 8), "s1"),
            h(2, "B", "C", T(2026, 1, 1, 8), "s2"),
        ]
        with self.assertRaises(DuplicateCustodyInstant):
            validate("A", events)

    def test_late_record_moving_time_backward_rejected(self):
        # Sequences are contiguous but event #2 is earlier than #1.
        events = [
            h(1, "A", "B", T(2026, 1, 3), "s1"),
            h(2, "B", "C", T(2026, 1, 1), "s2"),
        ]
        with self.assertRaises(CustodyMovedBackward):
            validate("A", events)

    def test_duplicate_seal_rejected(self):
        events = [
            h(1, "A", "B", T(2026, 1, 1), "s1"),
            h(2, "B", "C", T(2026, 1, 2), "s1"),
        ]
        with self.assertRaises(SealInvalid):
            validate("A", events)

    def test_arrival_order_does_not_matter_for_validate(self):
        events = [
            h(2, "B", "C", T(2026, 1, 2), "s2"),
            h(1, "A", "B", T(2026, 1, 1), "s1"),
        ]
        self.assertEqual(validate("A", events), "C")


class TestCustodyChain(unittest.TestCase):
    def _two(self):
        chain = CustodyChain("A")
        chain.accept(h(1, "A", "B", T(2026, 1, 1), "s1"))
        chain.accept(h(2, "B", "C", T(2026, 1, 2), "s2"))
        return chain

    def test_accept_advances_state(self):
        chain = self._two()
        self.assertEqual(chain.current, "C")
        self.assertEqual(chain.next_sequence, 3)
        self.assertEqual(chain.watermark, T(2026, 1, 2))

    def test_is_late_detects_only_temporal_regression(self):
        chain = self._two()
        late = h(3, "C", "D", T(2026, 1, 1), "s3")
        self.assertTrue(chain.is_late(late))
        on_time = h(3, "C", "D", T(2026, 1, 3), "s3")
        self.assertFalse(chain.is_late(on_time))

    def test_accept_late_raises(self):
        chain = self._two()
        with self.assertRaises(CustodyMovedBackward):
            chain.accept(h(3, "C", "D", T(2026, 1, 1), "s3"))

    def test_replay_is_deterministic(self):
        events = [
            h(1, "A", "B", T(2026, 1, 1), "s1"),
            h(2, "B", "C", T(2026, 1, 2), "s2"),
        ]
        c1 = CustodyChain.replay("A", events)
        c2 = CustodyChain.replay("A", reversed(events))
        self.assertEqual(c1.timeline, c2.timeline)
        self.assertEqual(c1.current, "C")


class TestAssetLedger(unittest.TestCase):
    def test_zero_transfer_rejected(self):
        led = CustodyLedger()
        led.post("A", "r1", 5, "seed")
        with self.assertRaises(ValueError):
            led.transfer("A", "B", 0, "t1", "xfer")

    def test_post_is_idempotent_on_replay(self):
        led = CustodyLedger()
        e1 = led.post("A", "r1", 5, "in")
        e2 = led.post("A", "r1", 5, "in")  # identical replay
        self.assertIs(e1, e2)
        self.assertEqual(led.balance("A"), Decimal("5"))
        self.assertEqual(len(led.entries()), 1)

    def test_post_conflicting_reference_rejected(self):
        led = CustodyLedger()
        led.post("A", "r1", 5, "in")
        with self.assertRaises(ValueError):
            led.post("A", "r1", 6, "in")

    def test_transfer_idempotent_on_replay(self):
        led = CustodyLedger()
        led.post("A", "seed", 10, "in")
        d1, c1 = led.transfer("A", "B", 4, "t1", "move")
        d2, c2 = led.transfer("A", "B", 4, "t1", "move")
        self.assertIs(d1, d2)
        self.assertIs(c1, c2)
        self.assertEqual(led.balance("A"), Decimal("6"))
        self.assertEqual(led.balance("B"), Decimal("4"))
        self.assertEqual(len(led.entries()), 3)

    def test_revision_appends_compensating_pair(self):
        led = CustodyLedger()
        original = led.post("A", "r1", 10, "in")
        reversal, corrected = led.revise(original.sequence, 7, "fix1", "correction")
        self.assertEqual(reversal.quantity, Decimal("-10"))
        self.assertEqual(corrected.quantity, Decimal("7"))
        self.assertEqual(corrected.revision_of, original.sequence)
        self.assertEqual(led.balance("A"), Decimal("7"))
        self.assertEqual(len(led.entries()), 3)
        led.audit()

    def test_revision_idempotent_on_replay(self):
        led = CustodyLedger()
        original = led.post("A", "r1", 10, "in")
        p1 = led.revise(original.sequence, 7, "fix1", "correction")
        p2 = led.revise(original.sequence, 7, "fix1", "correction")
        self.assertEqual(p1, p2)
        self.assertEqual(led.balance("A"), Decimal("7"))
        self.assertEqual(len(led.entries()), 3)

    def test_replay_stream_rebuilds_identical_ledger(self):
        led = CustodyLedger()
        led.post("A", "r1", 10, "in")
        led.post("B", "r2", 3, "in")
        rows = led.entries()
        rebuilt = CustodyLedger.replay(rows)
        self.assertEqual(rebuilt.entries(), rows)
        self.assertEqual(rebuilt.balance("A"), Decimal("10"))
        rebuilt.audit()

    def test_replay_rejects_noncontiguous(self):
        led = CustodyLedger()
        led.post("A", "r1", 10, "in")
        rows = list(led.entries())
        from dataclasses import replace
        broken = (replace(rows[0], sequence=9),)
        with self.assertRaises(ValueError):
            CustodyLedger.replay(broken)


class TestAuditTrail(unittest.TestCase):
    def test_legacy_append_verifies(self):
        a = AuditTrail()
        a.append("u", "create", "S")
        a.append("q", "release", "S")
        self.assertTrue(a.verify())

    def test_ordered_append_blocks_late(self):
        a = AuditTrail()
        a.ordered_append("u", "one", "S", T(2026, 1, 2))
        with self.assertRaises(AuditOrderError):
            a.ordered_append("u", "two", "S", T(2026, 1, 1))
        with self.assertRaises(AuditOrderError):
            a.ordered_append("u", "two", "S", T(2026, 1, 2))

    def test_revision_is_appended_at_tail_and_verifies(self):
        a = AuditTrail()
        r1 = a.ordered_append("u", "one", "S", T(2026, 1, 1))
        r2 = a.ordered_append("u", "two", "S", T(2026, 1, 2))
        # Late fact about record 1 arrives on Jan 3; never spliced in.
        rev = a.revise("u", "one.corrected", "S",
                       T(2026, 1, 3),
                       corrects=r1.index,
                       occurred_at=T(2026, 1, 1))
        self.assertEqual(rev.index, 3)
        self.assertEqual(rev.corrects, 1)
        self.assertTrue(a.verify())
        # Arrival order preserved: records remain append ordered.
        self.assertEqual([r.index for r in a.records], [1, 2, 3])

    def test_replay_rebuilds_verified_trail(self):
        a = AuditTrail()
        a.ordered_append("u", "one", "S", T(2026, 1, 1))
        a.ordered_append("u", "two", "S", T(2026, 1, 2))
        rebuilt = AuditTrail.replay(a.records)
        self.assertTrue(rebuilt.verify())
        self.assertEqual(rebuilt.records, a.records)

    def test_tampering_breaks_verify(self):
        a = AuditTrail()
        a.ordered_append("u", "one", "S", T(2026, 1, 1))
        a.ordered_append("u", "two", "S", T(2026, 1, 2))
        from dataclasses import replace
        a.records[0] = replace(a.records[0], action="hacked")
        self.assertFalse(a.verify())


def _ingest_scene(policy):
    """A -> B on day 3 arrives first; the late day-1 handoff comes after."""
    ing = CustodyIngestor("A", policy=policy)
    o1 = ing.ingest(h(1, "A", "B", T(2026, 1, 3), "s1"))
    o2 = ing.ingest(h(2, "B", "C", T(2026, 1, 1), "s2"))
    return ing, o1, o2


class TestIngestorReject(unittest.TestCase):
    def test_late_record_rejected_everywhere(self):
        ing, o1, o2 = _ingest_scene(Policy.REJECT)
        self.assertIs(o1.decision, Decision.ACCEPTED)
        self.assertIs(o2.decision, Decision.REJECTED)
        # Chain stops at B; no asset entry for C; audit holds only the accept.
        self.assertEqual(ing.chain.current, "B")
        self.assertEqual(ing.ledger.balance("C"), Decimal("0"))
        self.assertEqual(len(ing.audit.records), 1)
        self.assertTrue(ing.audit.verify())

    def test_rejected_record_replay_is_duplicate_not_error(self):
        ing, _, _ = _ingest_scene(Policy.REJECT)
        again = ing.ingest(h(2, "B", "C", T(2026, 1, 1), "s2"))
        self.assertIs(again.decision, Decision.DUPLICATE)
        self.assertEqual(ing.chain.current, "B")


class TestIngestorSuspend(unittest.TestCase):
    def test_late_record_suspended_then_explicitly_handled(self):
        ing, o1, o2 = _ingest_scene(Policy.SUSPEND)
        self.assertIs(o1.decision, Decision.ACCEPTED)
        self.assertIs(o2.decision, Decision.SUSPENDED)
        self.assertEqual([x.seal for x in ing.pending()], ["s2"])
        # Nothing leaked into asset or audit.
        self.assertEqual(ing.ledger.balance("C"), Decimal("0"))
        self.assertEqual(len(ing.audit.records), 1)

    def test_gap_parks_future_record_until_predecessor(self):
        ing = CustodyIngestor("A", policy=Policy.SUSPEND)
        early = ing.ingest(h(2, "B", "C", T(2026, 1, 2), "s2"))
        self.assertIs(early.decision, Decision.SUSPENDED)
        # Predecessor arrives; both get admitted in order automatically.
        first = ing.ingest(h(1, "A", "B", T(2026, 1, 1), "s1"))
        self.assertIs(first.decision, Decision.ACCEPTED)
        self.assertEqual(ing.chain.current, "C")
        self.assertEqual(ing.ledger.balance("B"), Decimal("1"))
        self.assertEqual(ing.ledger.balance("C"), Decimal("1"))
        self.assertEqual(len(ing.audit.records), 2)
        self.assertTrue(ing.audit.verify())

    def test_suspend_replay_is_idempotent(self):
        ing, _, _ = _ingest_scene(Policy.SUSPEND)
        again = ing.ingest(h(2, "B", "C", T(2026, 1, 1), "s2"))
        self.assertIs(again.decision, Decision.DUPLICATE)
        self.assertEqual(len(ing.pending()), 1)
        self.assertEqual(ing.ledger.balance("C"), Decimal("0"))


class TestIngestorRevise(unittest.TestCase):
    def test_late_record_creates_tail_revision(self):
        ing, o1, o2 = _ingest_scene(Policy.REVISE)
        self.assertIs(o1.decision, Decision.ACCEPTED)
        self.assertIs(o2.decision, Decision.REVISED)
        # Corrected handoff committed after the watermark (day 3 + resolution).
        self.assertEqual(ing.chain.current, "C")
        self.assertGreater(ing.watermark, T(2026, 1, 3))
        # Asset and audit reflect exactly one accepted (revised) entry for C.
        self.assertEqual(ing.ledger.balance("C"), Decimal("1"))
        self.assertEqual(len(ing.chain.timeline), 2)
        actions = [r.action for r in ing.audit.records]
        self.assertEqual(actions,
                         ["handoff.accept", "handoff.revise-request", "handoff.revise"])
        # Original late event time is preserved as evidence.
        self.assertEqual(ing.audit.records[1].occurred_at, T(2026, 1, 1))
        self.assertTrue(ing.audit.verify())

    def test_explicit_corrected_time_used_when_valid(self):
        ing = CustodyIngestor("A", policy=Policy.REVISE)
        ing.ingest(h(1, "A", "B", T(2026, 1, 3), "s1"))
        out = ing.ingest(h(2, "B", "C", T(2026, 1, 1), "s2"),
                         corrected_at=T(2026, 1, 4))
        self.assertIs(out.decision, Decision.REVISED)
        self.assertEqual(out.corrected_at, T(2026, 1, 4))
        self.assertEqual(ing.watermark, T(2026, 1, 4))

    def test_invalid_corrected_time_falls_back_to_suspend(self):
        ing = CustodyIngestor("A", policy=Policy.REVISE)
        ing.ingest(h(1, "A", "B", T(2026, 1, 3), "s1"))
        out = ing.ingest(h(2, "B", "C", T(2026, 1, 1), "s2"),
                         corrected_at=T(2026, 1, 2))
        self.assertIs(out.decision, Decision.SUSPENDED)
        self.assertEqual(ing.chain.current, "B")

    def test_revision_replay_is_deterministic(self):
        stream = [
            h(1, "A", "B", T(2026, 1, 3), "s1"),
            h(2, "B", "C", T(2026, 1, 1), "s2"),
            h(2, "B", "C", T(2026, 1, 1), "s2"),  # duplicate replay
        ]
        r1 = CustodyIngestor.replay("A", stream, policy=Policy.REVISE)
        r2 = CustodyIngestor.replay("A", stream, policy=Policy.REVISE)
        self.assertEqual([e for e in r1.chain.timeline],
                         [e for e in r2.chain.timeline])
        self.assertEqual(r1.ledger.entries(), r2.ledger.entries())
        self.assertEqual(
            [r.digest for r in r1.audit.records],
            [r.digest for r in r2.audit.records],
        )
        self.assertEqual(len(r1.chain.timeline), 2)
        self.assertTrue(r1.audit.verify())


class TestReplayEquivalence(unittest.TestCase):
    def test_repeated_replay_identical_for_all_policies(self):
        # Same logical stream delivered in two different arrival orders.
        logical = [
            h(1, "A", "B", T(2026, 1, 1), "s1"),
            h(2, "B", "C", T(2026, 1, 2), "s2"),
            h(3, "C", "D", T(2026, 1, 3), "s3"),
        ]
        for policy in Policy:
            a = CustodyIngestor.replay("A", logical, policy=policy)
            b = CustodyIngestor.replay("A", list(reversed(logical)) + list(logical),
                                       policy=policy)
            self.assertEqual(
                [e for e in a.chain.timeline], [e for e in b.chain.timeline],
                policy,
            )
            self.assertEqual(a.ledger.entries(), b.ledger.entries(), policy)
            self.assertEqual(
                [r.digest for r in a.audit.records],
                [r.digest for r in b.audit.records],
                policy,
            )
            self.assertTrue(a.audit.verify())

    def test_chain_ledger_audit_replay_consistency_with_revisions(self):
        # Build a ledger with a revision, snapshot rows, rebuild from stream.
        led = CustodyLedger()
        led.post("A", "r1", 10, "in")
        led.revise(1, 7, "fix", "correction")
        rebuilt = CustodyLedger.replay(led.entries())
        self.assertEqual(rebuilt.balance("A"), Decimal("7"))
        rebuilt.audit()


if __name__ == "__main__":
    unittest.main()
