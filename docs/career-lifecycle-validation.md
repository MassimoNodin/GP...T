# Career lifecycle correction and live validation

Date: October 11, 2026 (Australia/Sydney).

## Root cause and scope

The Bahrain Driver Career Q1 journal records known Event v1 packets at game
frame 0 with player index 0, alongside Session, Participants, Lap Data and
telemetry packets identifying the actual player at index 7. Previously, the
pipeline treated every packet header as equal ownership evidence. This marked
the frame's player scope ambiguous and kept subsequent frames unassessable,
preventing driver-tenure bindings and quarantining otherwise completed laps.

Player-slot consensus now uses non-Event packet headers consistently across
lifecycle assessment, participant/setup association and car inventory. An
Event-only frame preserves the previously established player scope rather than
inventing a player change. Event format checks and lifecycle decoding remain
active: unknown or malformed events, flashbacks, actual non-Event ownership
conflicts and acquisition gaps still fail closed.

The v2 practice/qualifying diagnostic policy already supports known Career
contexts. The legacy lap payload's `reference_eligible` flag and
`mode_policy_not_implemented` exclusion describe the older Time Trial reference
policy; they are not reasons to override v2 binding or evidence admission. This
fix does not enable Time Trial ranking for Career, race comparisons, coaching,
opponent ranking, strategy, or model-generated causal recommendations.

## Regression validation

- Nine new coordinator cases cover Career practice and Q1, both startup packet
  orders, mixed Event/data player headers and Event-only frames.
- Safety controls cover genuine data-header conflicts, unknown/malformed events
  and flashbacks. Five cases fail on the original pipeline with missing verified
  bindings; all nine pass with the correction.
- Windows focused lifecycle/participant/inventory/history/pipeline tests:
  103 passed. The nine new cases also pass on Windows.
- Isolated Ubuntu focused lifecycle and backend suites: 366 passed, followed by
  nine new Career regression cases passing. No dependency changes were made.
- The first full Windows run reported 1 failed / 1,565 passed / 16 skipped.
  Collection overlapped the subagent finishing the new flashback test; pytest
  executed its earlier assertion while displaying the subsequently edited
  source. The final test file was last edited at 15:54:02 Sydney.
- All ten alphabetically preceding modules plus the finished Career test file
  then passed: 184 tests. A separate lifecycle/Career rerun passed 20 tests.
  No production correction or weaker invariant was needed for the
  collection-time test mismatch.
- The fresh full Windows run with the stable test file passed: **1,566 passed /
  16 skipped**, in 538.80 seconds, with two existing deprecation warnings.
  Compilation and task-specific diff-whitespace checks passed. No frontend
  build, real-model inference, or exhaustive Linux full-suite run was performed
  for this fix; Linux focused and real-game checks are recorded above.

## Real-game validation during continued play

An isolated, low-priority replay on Ubuntu CPUs 2 and 3 read and checksum-verified
120,000 committed journal entries, without another UDP receiver or changes to
the live source ledger. Replay processing took approximately 77 seconds.
The first analysis invocation omitted a required reference argument; that
validator invocation was corrected and analysis ran against the already
completed derived database, without repeating or rewriting source evidence.

The corrected replay publishes the first two Q1 player laps with the same
verified driver binding and no evidence qualifications:

- Lap 1: 102,883 ms (1:42.883).
- Lap 2: 90,564 ms (1:30.564).
- Strict v2 reference selection is available; the measured difference is
  -12,319 ms. Results remain diagnostic-only, not coaching or ranking.

A separate live Q2 analysis request correctly abstained with
`acquisition_gap`; zero receiver drops do not imply uninterrupted game
telemetry. The gap guard was not weakened. Live acquisition remained loss-free
during the replay and analysis workload.

## Activation and autonomous follow-up

After Linux regression validation, a bounded helper waited for two consecutive
safe-lull observations. It activated the source patch at 15:58:08 Sydney time
when telemetry was stale, preserving the prior source and service override as
rollback artifacts. This is base commit `ba35aec` plus an **uncommitted pipeline
patch**, not a newly committed or pushed release.

A fresh NVMe validation database avoids replaying the large previous generation
while the game continues. Previous recordings, journals and original Q1
quarantines remain unchanged; replay-derived Q1 results are separate validation
artifacts, not inserted back into production evidence. UDP 20777 remains owned
by the single existing isolated receiver, with `demand_v1` unchanged.

The helper samples acquisition health every two seconds for one hour after
activation. After a two-minute data-collection period it can run up to three
strict, model-free comparisons when clean comparable Career practice/qualifying
laps exist. It does not compare race laps, invent references, or run ambient AI.
Any receiver loss stops additional workloads, not acquisition. Monitoring ends
automatically; the acquisition service remains enabled and running.

Ubuntu validation artifacts:

- `/mnt/nvme/f1-engineer-migration-checks/career-lifecycle-20261011/results/`
- Replay: `q1-replay-1791694232323609000/result.json` and `derived.sqlite3`.
- Live runs: `live-*/deployment.json`, `monitor.jsonl`, optional analysis reports,
  and rollback copies.
- Active acquisition storage: `/mnt/nvme/f1-engineer/data/career-validation-20261011-155808/`.

The helper source is an ignored operational artifact under
`data/career-lifecycle-20261011/validate_live.py`; it contains no control token.
No UI files or unrelated working-tree changes are included in this fix.

## Fresh live race evidence

The post-activation Driver Career race produced a completed, game-valid lap 2
at 93,834 ms (1:33.834), with an observed start, no pit encounter, verified
driver binding and no evidence qualifications. The authenticated trace endpoint
successfully reads all 5,629 expected observation rows. Lap 1 remains
quarantined with an unobserved start rather than being promoted across the
startup ownership boundary.

At the checkpoint, acquisition had received 72,782 packets with zero
application/kernel drops or socket errors. Across 107 advancing two-second
samples, publication p95 was 16.03 ms and backlog delay p95/max was
45.56/82.29 ms, within the unchanged acceptance thresholds. These are sampled
measurements, not exhaustive per-packet latency guarantees.

The strict reference endpoint rejects the race lap with
`practice_qualifying_unsupported_session_type`, as intended. The Career
ownership/lifecycle fix does not silently admit race comparisons. Checkpoint
artifacts are `fresh-player-summary.json`, `health-checkpoint.json` and
`race-policy-abstention.json` in the live validation directory. Copies of the
replay result, player summary and health checkpoint are retained in the ignored
Windows `data/career-lifecycle-20261011/` directory.

The operator subsequently restarted the race while acquisition stayed active.
The previous race is ended with five completed player laps preserved; the
restarted race has a distinct session UID and evidence session ID, with no
previous player attempts carried into it. At that checkpoint, 350,232 packets
had been received with zero application/kernel drops or socket errors. No
listener restart was required for this game restart. The live directory also
contains `race-restart-checkpoint.json`.
