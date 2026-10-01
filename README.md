# ColdLedger Pharmaceutical Cold-Chain Operations Lab

Dependency-free Python reference implementation for temperature-sensitive pharmaceutical distribution. It models inventory expiry, chain-of-custody, telemetry correction, packaging qualification, dry-ice depletion, freezer allocation, customs holds, serialization, recalls, release decisions, vehicle compartment compatibility, returns, SLA calendars and cross-dock state.

Runtime third-party dependencies: none.

## Late custody records

`coldledger.custody.handoff_log.HandoffLog` is the single intake for gapless
handoff sequences. Each submission is checked atomically against three guards:

- the custody chain (`custody.handoffs.validate`): gapless sequences,
  continuous holder chain, strictly non-decreasing event time, unique seals;
- asset changes (`custody.asset_ledger.project`): a pure fold over the
  append-only commit log, with balances never ending negative;
- audit order (`audit.trail.AuditTrail`): a hash chain ordered by intake,
  checking both business-event time and record time.

A late record whose sequence is ahead of the gap is **parked** until the gap
fills; a late record for a committed sequence is **rejected**, **quarantined**
(suspended), or accepted as a new **revision** (`LatePolicy`). Revisions never
mutate history: downstream movements are compensated and reposted, and the
whole revised timeline is re-validated before commit. Replaying committed
records — including the same feed twice — yields identical chains, ledger
entries, audit digests and duplicate-detection state.
