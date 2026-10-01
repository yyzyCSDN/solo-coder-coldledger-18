import unittest
from dataclasses import astuple
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from coldledger.custody import handoffs
from coldledger.custody.handoffs import validate
from coldledger.custody.handoff_log import (
    HandoffLog, LatePolicy, LateRecordError,
    ACCEPTED, REVISED, DUPLICATE, PARKED, QUARANTINED,
    EV_HANDOFF, EV_REVISION, EV_PARK, EV_QUARANTINE, EV_PROMOTE,
)
from coldledger.custody.asset_ledger import CustodyLedger, project
from coldledger.audit.trail import AuditTrail

UTC = timezone.utc
T = lambda h, m=0: datetime(2026, 10, 1, tzinfo=UTC) + timedelta(hours=h, minutes=m)
Q = Decimal


class HandoffValidationTests(unittest.TestCase):
    def _h(self, seq, frm, to, at, seal):
        return handoffs.Handoff(seq, frm, to, at, seal)

    def test_happy_path(self):
        events = [
            self._h(1, "MAKER", "CARRIER", T(8), "s1"),
            self._h(2, "CARRIER", "DEPOT", T(10), "s2"),
        ]
        self.assertEqual(validate("MAKER", events), "DEPOT")

    def test_arrival_order_irrelevant_when_sequence_continuous(self):
        events = [
            self._h(2, "CARRIER", "DEPOT", T(10), "s2"),
            self._h(1, "MAKER", "CARRIER", T(8), "s1"),
        ]
        self.assertEqual(validate("MAKER", events), "DEPOT")

    def test_late_record_with_backward_time_is_rejected(self):
        # sequences look continuous, but seq 2 claims an earlier instant
        events = [
            self._h(1, "MAKER", "CARRIER", T(10), "s1"),
            self._h(2, "CARRIER", "DEPOT", T(9), "s2"),
        ]
        with self.assertRaisesRegex(ValueError, "backward"):
            validate("MAKER", events)

    def test_equal_instant_is_rejected(self):
        events = [
            self._h(1, "MAKER", "CARRIER", T(10), "s1"),
            self._h(2, "CARRIER", "DEPOT", T(10), "s2"),
        ]
        with self.assertRaises(ValueError):
            validate("MAKER", events)

    def test_sequence_gap_and_discontinuity_rejected(self):
        with self.assertRaisesRegex(ValueError, "gap"):
            validate("MAKER", [self._h(2, "MAKER", "B", T(8), "s")])
        with self.assertRaisesRegex(ValueError, "discontinuity"):
            validate("MAKER", [self._h(1, "MAKER", "B", T(8), "s"),
                               self._h(2, "WRONG", "C", T(9), "t")])

    def test_seals_must_be_present_and_unique(self):
        with self.assertRaisesRegex(ValueError, "seal"):
            validate("MAKER", [self._h(1, "MAKER", "B", T(8), "")])
        with self.assertRaisesRegex(ValueError, "seal"):
            validate("MAKER", [self._h(1, "MAKER", "B", T(8), "x"),
                               self._h(2, "B", "C", T(9), "x")])


def _clock(times):
    state = {"i": 0}

    def now():
        value = times[state["i"]]
        state["i"] = min(state["i"] + 1, len(times) - 1)
        return value
    return now


class HandoffLogTests(unittest.TestCase):
    def make(self, policy=LatePolicy.SUSPEND, times=None, opening=Q("100")):
        clock = _clock(times or [T(6)]) if times else (lambda: T(6))
        return HandoffLog("MAKER", opening_quantity=opening, policy=policy, now=clock)

    def test_normal_chain_commits_all_three_guards(self):
        log = self.make()
        o1 = log.submit(1, "MAKER", "CARRIER", T(8), "s1", Q("40"))
        self.assertEqual(o1.status, ACCEPTED)
        o2 = log.submit(2, "CARRIER", "DEPOT", T(10), "s2", Q("40"))
        self.assertEqual(o2.status, ACCEPTED)
        self.assertEqual(log.custodian(), "DEPOT")
        self.assertEqual(log.ledger().balance("MAKER"), Q("60"))
        self.assertEqual(log.ledger().balance("CARRIER"), Q("0"))
        self.assertEqual(log.ledger().balance("DEPOT"), Q("40"))
        self.assertTrue(log.audit_trail().verify())
        self.assertTrue(log.verify())

    def test_out_of_order_record_is_suspended_then_promoted(self):
        log = self.make(times=[T(h) for h in range(6, 12)])
        late = log.submit(2, "CARRIER", "DEPOT", T(10), "s2", Q("40"))
        self.assertEqual(late.status, PARKED)
        # nothing hit the ledger while suspended
        self.assertNotIn("DEPOT", [r.account for r in log.ledger().entries()])
        head = log.submit(1, "MAKER", "CARRIER", T(8), "s1", Q("40"))
        self.assertEqual(head.status, ACCEPTED)
        self.assertEqual([o.status for o in head.also], [ACCEPTED])
        self.assertEqual(log.custodian(), "DEPOT")
        self.assertEqual(log.ledger().balance("DEPOT"), Q("40"))
        self.assertEqual([c.kind for c in log.commits],
                         ["open", "park", "handoff", "promote"])

    def test_backward_time_under_reject_raises_and_commits_nothing(self):
        log = self.make(policy=LatePolicy.REJECT)
        log.submit(1, "MAKER", "CARRIER", T(8), "s1", Q("40"))
        with self.assertRaises(LateRecordError):
            log.submit(2, "CARRIER", "DEPOT", T(7), "s2", Q("40"))
        # only opening + seq1 visible; the refusal appended nothing
        self.assertEqual(log.custodian(), "CARRIER")
        self.assertEqual(len(log.effective_handoffs()), 1)

    def test_backward_time_under_suspend_is_quarantined(self):
        log = self.make(times=[T(h) for h in range(6, 12)])
        log.submit(1, "MAKER", "CARRIER", T(8), "s1", Q("40"))
        outcome = log.submit(2, "CARRIER", "DEPOT", T(7), "s2", Q("40"))
        self.assertEqual(outcome.status, QUARANTINED)
        self.assertIn("backward", log.quarantined()[0][2])
        self.assertEqual(log.custodian(), "CARRIER")

    def test_invalid_late_record_cannot_break_future_chain(self):
        # seq2 arrives "late" with correct numbering but a wrong from-party
        # and a backward timestamp; under REVISE it must not become a revision
        log = self.make(policy=LatePolicy.REVISE,
                        times=[T(h) for h in range(6, 14)])
        log.submit(1, "MAKER", "CARRIER", T(8), "s1", Q("40"))
        log.submit(2, "CARRIER", "DEPOT", T(10), "s2", Q("40"))
        log.submit(3, "DEPOT", "CLINIC", T(12), "s3", Q("40"))
        out = log.submit(2, "CARRIER", "DEPOT2", T(9), "s2b", Q("30"))
        self.assertEqual(out.status, QUARANTINED)
        self.assertEqual(log.revision_of(2), 0)
        # effective timeline and assets untouched
        self.assertEqual(log.custodian(), "CLINIC")
        self.assertEqual(log.ledger().balance("DEPOT2"), Q("0"))

    def test_valid_revision_cascades_through_downstream_and_keeps_chain(self):
        log = self.make(policy=LatePolicy.REVISE,
                        times=[T(h) for h in range(6, 14)])
        log.submit(1, "MAKER", "CARRIER", T(8), "s1", Q("40"))
        log.submit(2, "CARRIER", "DEPOT", T(10), "s2", Q("40"))
        # late correction of seq1's seal and precise time; flow unchanged
        out = log.submit(1, "MAKER", "CARRIER", T(8, 30), "s1b", Q("40"))
        self.assertEqual(out.status, REVISED)
        self.assertEqual(out.revision, 1)
        refs = [r.reference for r in log.ledger().entries()]
        # original history retained, reversal pair, corrected move, repost
        self.assertIn("custody:seq1:debit", refs)
        self.assertTrue(any(r.reference == "custody:seq2:rev1from1:undo:debit"
                            for r in log.ledger().entries()))
        self.assertTrue(any(r.reference == "custody:seq1:rev1:fix:credit"
                            for r in log.ledger().entries()))
        self.assertTrue(any(r.reference == "custody:seq2:rev1from1:repost:credit"
                            for r in log.ledger().entries()))
        balances = {a: log.ledger().balance(a)
                    for a in ("MAKER", "CARRIER", "DEPOT")}
        self.assertEqual(balances, {"MAKER": Q("60"), "CARRIER": Q("0"),
                                    "DEPOT": Q("40")})
        self.assertTrue(log.verify())
        # history is append-only: original commits still present
        kinds = [(c.kind, c.sequence) for c in log.commits]
        self.assertIn((EV_HANDOFF, 1), kinds)
        self.assertIn((EV_REVISION, 1), kinds)

    def test_revision_of_last_handoff_can_change_quantity(self):
        log = self.make(policy=LatePolicy.REVISE,
                        times=[T(h) for h in range(6, 14)])
        log.submit(1, "MAKER", "CARRIER", T(8), "s1", Q("40"))
        out = log.submit(1, "MAKER", "CARRIER", T(8, 30), "s1b", Q("30"))
        self.assertEqual(out.status, REVISED)
        self.assertEqual(log.ledger().balance("MAKER"), Q("70"))
        self.assertEqual(log.ledger().balance("CARRIER"), Q("30"))
        self.assertTrue(log.verify())

    def test_mid_chain_revision_creating_shortfall_is_quarantined(self):
        log = self.make(policy=LatePolicy.REVISE,
                        times=[T(h) for h in range(6, 14)])
        log.submit(1, "MAKER", "CARRIER", T(8), "s1", Q("40"))
        log.submit(2, "CARRIER", "DEPOT", T(10), "s2", Q("40"))
        # seq1 corrected to 30 but CARRIER already forwarded 40: impossible
        out = log.submit(1, "MAKER", "CARRIER", T(8, 30), "s1b", Q("30"))
        self.assertEqual(out.status, QUARANTINED)
        self.assertEqual(log.revision_of(1), 0)
        self.assertEqual(log.ledger().balance("MAKER"), Q("60"))

    def test_revision_that_overdraws_is_refused(self):
        log = self.make(policy=LatePolicy.REVISE,
                        times=[T(h) for h in range(6, 14)])
        log.submit(1, "MAKER", "CARRIER", T(8), "s1", Q("40"))
        out = log.submit(1, "MAKER", "CARRIER", T(8, 30), "s1b", Q("150"))
        self.assertEqual(out.status, QUARANTINED)
        self.assertEqual(log.revision_of(1), 0)
        self.assertEqual(log.ledger().balance("MAKER"), Q("60"))

    def test_duplicate_replay_is_idempotent(self):
        log = self.make(times=[T(h) for h in range(6, 14)])
        first = log.submit(1, "MAKER", "CARRIER", T(8), "s1", Q("40"))
        self.assertEqual(first.status, ACCEPTED)
        again = log.submit(1, "MAKER", "CARRIER", T(8), "s1", Q("40"))
        self.assertEqual(again.status, DUPLICATE)
        entries_one = [astuple(r) for r in log.ledger().entries()]
        audit_one = [r.digest for r in log.audit_trail().records]
        # a third replay changes nothing
        third = log.submit(1, "MAKER", "CARRIER", T(8), "s1", Q("40"))
        self.assertEqual(third.status, DUPLICATE)
        self.assertEqual([astuple(r) for r in log.ledger().entries()], entries_one)
        self.assertEqual([r.digest for r in log.audit_trail().records], audit_one)

    def test_duplicate_replay_of_parked_record(self):
        log = self.make(times=[T(h) for h in range(6, 14)])
        log.submit(2, "CARRIER", "DEPOT", T(10), "s2", Q("40"))
        again = log.submit(2, "CARRIER", "DEPOT", T(10), "s2", Q("40"))
        self.assertEqual(again.status, PARKED)
        # only one park commit, no ledger impact
        self.assertEqual(sum(c.kind == EV_PARK for c in log.commits), 1)

    def test_replay_from_commits_reproduces_everything(self):
        log = self.make(policy=LatePolicy.REVISE,
                        times=[T(h) for h in range(6, 20)])
        log.submit(2, "CARRIER", "DEPOT", T(10), "s2", Q("40"))  # parked
        log.submit(1, "MAKER", "CARRIER", T(8), "s1", Q("40"))   # promotes 2
        log.submit(1, "MAKER", "CARRIER", T(8, 30), "s1b", Q("40"))  # metadata revision
        log.submit(3, "DEPOT", "CLINIC", T(12), "s9", Q("40"))
        bad = log.submit(4, "CLINIC", "X", T(11), "s0", Q("40"))
        self.assertEqual(bad.status, QUARANTINED)
        committed = list(log.commits)

        replayed = HandoffLog.replay(committed, "MAKER", policy=LatePolicy.REVISE)
        # identical audit chain
        self.assertEqual([r.digest for r in replayed.audit_trail().records],
                         [r.digest for r in log.audit_trail().records])
        # identical ledger
        self.assertEqual([astuple(r) for r in replayed.ledger().entries()],
                         [astuple(r) for r in log.ledger().entries()])
        # identical effective state
        self.assertEqual(replayed.custodian(), log.custodian())
        self.assertEqual(replayed.revision_of(1), 1)
        # replay of a replay is stable
        replayed_twice = HandoffLog.replay(list(replayed.commits), "MAKER",
                                           policy=LatePolicy.REVISE)
        self.assertEqual([astuple(r) for r in replayed_twice.ledger().entries()],
                         [astuple(r) for r in log.ledger().entries()])
        self.assertTrue(replayed_twice.verify())

    def test_replayed_log_treats_resubmission_as_duplicate(self):
        log = self.make(times=[T(h) for h in range(6, 14)])
        log.submit(1, "MAKER", "CARRIER", T(8), "s1", Q("40"))
        replayed = HandoffLog.replay(list(log.commits), "MAKER")
        out = replayed.submit(1, "MAKER", "CARRIER", T(8), "s1", Q("40"),
                              actor="system")
        self.assertEqual(out.status, DUPLICATE)

    def test_competing_parked_records_winner_promoted_loser_quarantined(self):
        log = self.make(times=[T(h) for h in range(6, 16)])
        # two different late claims for seq2
        log.submit(2, "CARRIER", "DEPOT", T(10), "s2a", Q("40"))
        # second claim has a backward event time and cannot ever validate
        log.submit(2, "CARRIER", "DEPOT", T(7), "s2bad", Q("40"))
        head = log.submit(1, "MAKER", "CARRIER", T(8), "s1", Q("40"))
        statuses = [o.status for o in head.also]
        self.assertEqual(statuses.count(ACCEPTED), 1)
        self.assertEqual(statuses.count(QUARANTINED), 1)
        self.assertEqual(log.custodian(), "DEPOT")
        self.assertEqual(log.ledger().balance("DEPOT"), Q("40"))

    def test_committed_handoff_tampering_breaks_audit_chain(self):
        log = self.make(times=[T(h) for h in range(6, 14)])
        log.submit(1, "MAKER", "CARRIER", T(8), "s1", Q("40"))
        self.assertTrue(log.verify())
        # tamper with the committed handoff's seal in the append-only log
        commit = log.commits[-1]
        bad = commit.handoff.__class__(1, "MAKER", "CARRIER", T(8), "FORGED")
        log.commits[-1] = commit.__class__(
            commit.ordinal, commit.kind, commit.sequence, commit.revision,
            bad, commit.quantity, commit.actor, commit.recorded_at, commit.note)
        # effective view now disagrees with what the audit hash committed
        self.assertFalse(log.verify())

    def test_concurrent_submissions_are_atomic(self):
        import threading
        log = self.make()
        errors = []

        def holder(i):
            return "MAKER" if i == 1 else f"P{i - 1}"

        def submit_pair(i):
            # arrives late: even sequence first, then its predecessor
            seq = i + 1
            try:
                log.submit(seq, holder(seq), f"P{seq}", T(8 + seq), f"s{seq}", Q("1"))
                log.submit(seq - 1, holder(seq - 1), f"P{seq - 1}",
                           T(8 + seq - 1), f"s{seq - 1}", Q("1"))
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=submit_pair, args=(i,))
                   for i in range(1, 20, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        # no partial commits: every debit has a matching credit
        self.assertTrue(log.audit_trail().verify())
        refs = [r.reference for r in log.ledger().entries()]
        self.assertEqual(sum(r.endswith(":debit") for r in refs),
                         sum(r.endswith(":credit") for r in refs))
        self.assertTrue(log.verify())

    def test_double_feed_replay_is_stable(self):
        log = self.make(policy=LatePolicy.REVISE,
                        times=[T(h) for h in range(6, 30)])
        feed = [
            (2, "CARRIER", "DEPOT", T(10), "s2", Q("40")),
            (1, "MAKER", "CARRIER", T(8), "s1", Q("40")),
            (1, "MAKER", "CARRIER", T(8, 30), "s1b", Q("40")),
        ]
        for args in feed:
            log.submit(*args)
        once = HandoffLog.replay(list(log.commits), "MAKER", policy=LatePolicy.REVISE)
        twice = HandoffLog.replay(list(once.commits), "MAKER", policy=LatePolicy.REVISE)
        def snapshot(l):
            return (
                [(r.sequence, r.account, r.reference, str(r.quantity), r.reason,
                  r.reversal_of) for r in l.ledger().entries()],
                [r.digest for r in l.audit_trail().records],
                l.custodian(),
            )
        self.assertEqual(snapshot(log), snapshot(once))
        self.assertEqual(snapshot(once), snapshot(twice))
        self.assertTrue(twice.verify())


class AssetProjectionTests(unittest.TestCase):
    def test_projection_is_a_pure_fold(self):
        movements = [
            ("open", 0, 0, "", "MAKER", Q("100")),
            ("move", 1, 0, "MAKER", "B", Q("40")),
            ("move", 2, 0, "B", "C", Q("40")),
            # revision corrects seq1 metadata; quantity and parties unchanged,
            # downstream is rewritten through the cascade
            ("move", 1, 1, "MAKER", "B", Q("40")),
        ]
        a = [astuple(r) for r in project("MAKER", movements).entries()]
        b = [astuple(r) for r in project("MAKER", list(movements)).entries()]
        self.assertEqual(a, b)
        ledger = project("MAKER", movements)
        self.assertEqual(ledger.balance("MAKER"), Q("60"))
        self.assertEqual(ledger.balance("B"), Q("0"))
        self.assertEqual(ledger.balance("C"), Q("40"))

    def test_projection_flags_impossible_downstream_after_correction(self):
        # seq1 corrected to 30 but downstream still forwards 40 -> B ends -10
        movements = [
            ("open", 0, 0, "", "MAKER", Q("100")),
            ("move", 1, 0, "MAKER", "B", Q("40")),
            ("move", 2, 0, "B", "C", Q("40")),
            ("move", 1, 1, "MAKER", "B", Q("30")),
        ]
        with self.assertRaisesRegex(ValueError, "negative"):
            project("MAKER", movements)

    def test_projection_rejects_negative_balance(self):
        movements = [
            ("open", 0, 0, "", "MAKER", Q("10")),
            ("move", 1, 0, "MAKER", "B", Q("40")),
        ]
        with self.assertRaisesRegex(ValueError, "negative"):
            project("MAKER", movements)

    def test_zero_quantity_transfer_rejected(self):
        ledger = CustodyLedger()
        ledger.post("A", "r", "10", "x")
        with self.assertRaisesRegex(ValueError, "positive"):
            ledger.transfer("A", "B", 0, "t", "x")


class AuditOrderTests(unittest.TestCase):
    def test_backward_business_time_rejected(self):
        trail = AuditTrail(now=lambda: T(12))
        trail.append("u", "create", "S", at=T(10), recorded_at=T(12))
        with self.assertRaisesRegex(ValueError, "event time moved backward"):
            trail.append("u", "amend", "S", at=T(9), recorded_at=T(13))

    def test_backward_record_time_rejected(self):
        trail = AuditTrail()
        trail.append("u", "create", "S", at=T(10), recorded_at=T(12))
        with self.assertRaisesRegex(ValueError, "record time moved backward"):
            trail.append("u", "amend", "S", at=T(11), recorded_at=T(11, 30))

    def test_rebuild_is_deterministic(self):
        entries = [
            ("u", "create", "S", T(10), T(12)),
            ("q", "release", "S", T(11), T(13)),
        ]
        d1 = [r.digest for r in AuditTrail.rebuild(entries).records]
        d2 = [r.digest for r in AuditTrail.rebuild(list(entries)).records]
        self.assertEqual(d1, d2)
        self.assertTrue(AuditTrail.rebuild(entries).verify())

    def test_verify_detects_reordered_timestamps(self):
        trail = AuditTrail.rebuild([
            ("u", "a", "S", T(9), T(12)),
            ("u", "b", "S", T(10), T(13)),
        ])
        self.assertTrue(trail.verify())
        # tamper: swap business timestamps while leaving digests recomputed
        trail.records[0] = trail.records[0].__class__(
            1, "u", "a", "S", T(10), trail.records[0].previous,
            trail.records[0].digest, trail.records[0].recorded_at)
        trail.records[1] = trail.records[1].__class__(
            2, "u", "b", "S", T(9), trail.records[1].previous,
            trail.records[1].digest, trail.records[1].recorded_at)
        self.assertFalse(trail.verify())


if __name__ == "__main__":
    unittest.main()
