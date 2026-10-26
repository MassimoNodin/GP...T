# Selective opponent processing: locked Luna implementation contract

Prepared 2026-10-11. Workspace: `D:\F1-Engineer`.

## 1. Authority and review gates

The implementation agent must be `gpt-6-luna`. Do not substitute an older model.
The primary agent owns scope, architecture, acceptance and deployment decisions.
Luna may choose normal local syntax, but may not choose different behavior,
limits, storage, dependencies, defaults, test commands or fallbacks. If the
contract is inconsistent, impossible or requires another file, STOP and return
the concrete blocker. Do not invent a workaround. Do not delegate.

This handoff authorizes **Gate A only: opt-in selective live opponent detail**.
It does not authorize implementing the entire earlier conceptual diagram.

| Gate | Deliverable | Advancement authority |
| --- | --- | --- |
| A | Live selection, explicit task demands, safe evidence, recovery, diagnostics | This contract authorizes implementation, not deployment |
| B | Bounded historical materialization from retained raw journal, with immutable provenance | Primary writes another concrete contract after A review |
| C | Reference caching/selection, corner coaching, AI integration and UI | Separate primary-reviewed contract |
| Production | Linux acceptance and live observation, then deployment/enablement | Explicit user authorization |

In A, demands request **future live detail**. Previous unselected laps retain raw
input and metadata, but their detailed traces are deferred, not automatically
reconstructed. Do not implement B/C or claim those capabilities are delivered.
Do not promise zero drops, a percentage speedup, or bounded raw-journal disk use.

## 2. Baseline and protected work

Inspect working tree, applicable AGENTS files and existing diffs before editing.
Record which edits already exist; preserve them rather than replacing files.

Verified code paths:
- `pipeline.py::_process_frames` constructs all-car `CarObservation` records.
- `sessions/car_lap_inventory.py` establishes whole-field tenure/lap metadata.
- `processing/coordinator.py` journals before staging/sealing/publication and
  replays committed and uncommitted journal ranges during recovery.
- `processing/evidence.py` uses immutable evidence and append-only dispositions.
- `processing/runtime.py` has one serialized persistence executor; Linux uses
  the already-added isolated receiver, Windows an independent receive thread.
- `api/app.py` exposes v2 evidence and limits concurrent comparisons to two.
- `analysis/session_comparison.py` already measures on explicit request, not on
  every UDP packet. Do not claim A removes an existing per-packet LLM call.

Protect existing receiver isolation, the 32 MiB evidence-read change and tests,
`docs/migration-baseline.md`, every existing `web/` change, and
`unused-evidence.sqlite3`. Existing dirty files are not permission to rewrite them.

## 3. Frozen scope and configuration

Three distinct layers:
1. Acquisition: retain every successfully admitted raw datagram in the existing
   journal. Never filter cars at the socket or discard whole packets.
2. Field metadata: preserve all-car roster/tenures and existing lap bookkeeping,
   positions, lap times, validity and pit status. Metadata is NOT detailed trace
   analysis. Keep all-car binary decoding and inventory safety rules unchanged.
3. Detail: construct, serialize, stage and seal high-frequency observations only
   for selected cars. Player `CarSample` generation remains mandatory/unchanged.

Add keyword-only `detail_profile` accepting `full` or `demand_v1`, default `full`,
to `TelemetryPipeline`, `SessionCoordinator`, `LiveSessionRuntime`, `create_app`.
Add CLI `api --detail-profile {full,demand_v1}`, default `full`, forwarding it to
`create_app`. No change to existing default callers/import/replay or existing tests.

Default full evidence filename stays unchanged. Demand profile without explicit
`evidence_database_path` uses configured archive path stem plus
`-demand-evidence.sqlite3` in the same directory. An explicit path is respected.
Persist profile and `detail_policy_version="demand-v1"` in generation processor
configuration. Missing profile means legacy `full`. Recovery profile/version
mismatch fails closed with `processing_profile_mismatch_requires_new_generation`.
Preserve profile on every pipeline replacement, including same-UID new occurrence.
No schema migration/version bump, hot profile switch, raw retention change or
production enablement. Rollback stops demand instance and restarts full; never
delete or mutate either database/generation for rollback.

## 4. Exact selection rules

Implement pure selector in `processing/detail_policy.py` using named frozen
dataclasses. Input is current assembled internally consistent frame, context,
verified current active tenures and explicit demands. No stale-frame fallback.
Player uses existing association rules even without opponent roster evidence.
Opponents require current verified tenure/session/format/epoch and assessable
scope. Return deduplicated cars and reasons in stable car-index order.

| Session type | Automatic detailed opponents | Explicit demands |
| --- | --- | --- |
| race/race_2/race_3 | Immediate race-position-order ahead/behind | Add verified targets |
| Every practice and qualifying/shootout enum | Nearest on-track traffic ahead/behind | Add verified targets |
| time_trial | None; no automatic PB/rival detail | Only available verified targets |
| Unknown/missing context | None | Only verified assessable targets |

Race: eligible current-frame cars have verified active tenure, result status 2,
positive position. Pitting/lapped cars remain eligible. Select greatest position
less than player and smallest greater. Duplicate eligible positions, invalid
player position/index, conflicted Lap Data or unassessable scope suppress BOTH
automatic neighbors. Never resolve ambiguity by car index or switch to physical
distance. Missing position numbers do not prevent choosing adjacent eligible
positions. Do not prefer same-lap cars over the actual race-order neighbor.

Practice/qualifying: require known positive track length, finite distance, verified
tenure, result status 2, pit status 0 and driver status 1 or 4. Normalize finite
distances modulo length. Pick smallest positive circular distance in each
direction; wrap at start/finish. Coincident cars are excluded from both directions;
equal nearest candidates suppress that direction. Single other car may occupy
both directions and is deduplicated. Invalid player distance/geometry/conflicted
frame suppresses automatic traffic, not otherwise valid explicit demands. No
distance threshold. Task targets may be pitting/retired when tenure is still
verified; unavailable/synthetic slots never receive fabricated channels.

Session type is authoritative; no GameMode override or online/offline heuristics.
Split-screen preserves existing header-player behavior; no second mandatory player.

## 5. Pipeline and evidence integration

Demand-profile frame order:
1. Preserve lifecycle/association/conflict checks and decode existing packets.
2. Establish current-frame roster/tenures through existing inventory exactly once.
3. Compute detail selection BEFORE making any per-car detailed observations.
4. Construct only selected `CarObservation` rows; preserve player sample path.
5. Publish all field metadata through existing ownership/disposition rules.

Do NOT construct all detailed rows then discard them. Add optional/default-empty
selection fields to output dataclasses as needed; full outputs/order/record bytes
must stay compatible. Selection changes must not close participant tenures or
interrupt player/whole-field metadata lap tracking.

Persist `detail_selection` metadata only at actual scope/selection changes,
including initial player-only selection. Include logical session, epoch, effective
frame ordinal, owning journal sequence, selected cars/bindings, and reasons:
`player`, `race_ahead`, `race_behind`, `traffic_ahead`, `traffic_behind`, `task`.
Keep no unbounded in-memory selection history; use existing persisted metadata.

Use expected metadata attempt count/range plus selection intervals to distinguish
deliberate omissions from real missing/corrupt input. Add additive `detail` to
attempt API metadata: profile, state (`available`, `deferred`, `unavailable`),
reason. Derive it without rewriting existing immutable attempts/manifests.

- Full profile retains all existing disposition/manifest/chunk behavior.
- Demand safely owned completed lap with deliberate omission gets append-only
  readiness state `deferred`, reason `trace_not_selected` for zero rows or
  `trace_selection_interrupted` for partial rows. Retain actual partial chunks and
  metadata; never pad/interpolate missing evidence to make a complete lap.
- Fully selected safe completed attempt keeps existing published readiness.
  Real integrity/ownership/acquisition failures retain quarantine precedence.
- Mid-lap activation/drop/reselection is incomplete; later continuously selected
  lap can publish. Preserve existing start-unobserved qualifications.
- `EvidenceStore.evidence` rejects deferred attempts with explicit reason before
  loading chunks. Existing trace/compare endpoints return existing 422 envelope.
  No silent full-profile fallback or historical reconstruction.
- Old saved comparisons remain immutable; no deferred manifest overwrite.

## 6. Explicit task demand contract

Add `processing/detail_demand.py`. Freeze constants:
`MAX_ACTIVE_DETAIL_TASKS=4`, one opponent binding/task; `MAX_DETAIL_COMMANDS=16`;
`DETAIL_TASK_TTL_S=300`; `MAX_RECENT_DETAIL_TASKS=32` terminal entries evicted FIFO.
Maximum selected detail cars is player + two neighbors + four targets = **7**.
Overlaps deduplicate cars, not task-count budget. No caller-supplied TTL.

POST JSON has exactly `task_id` (1-64 ASCII alphanumeric/underscore/hyphen),
`session_id` (32 lowercase hex characters), `binding_id` (64 lowercase hex
characters). Use exact existing logical session/binding IDs; never
driver name or bare car index as authority. Reject unexpected JSON fields.
State: queued/active/released/expired/rejected. Active coverage is
waiting_for_complete_lap/available, where available requires a published detail
attempt wholly within THIS activation, not any old lap for the binding.

Add endpoints:
- GET `/api/v2/session-evidence/detail-targets?session_id=...`: copied snapshot
  of current verified targets, at most 24 entries: binding_id, car_index, epoch,
  packet_format, is_player. No names. Existing app-level GET auth applies. Unknown
  or noncurrent session returns 404 `detail_session_not_current`. Full/disabled
  runtime returns 409 `detail_demands_unavailable`. Publish this discovery snapshot
  after roster/binding publication, so targeting does not require a completed lap.
- POST `/api/v2/session-evidence/detail-tasks`: existing control-token auth.
- GET `/api/v2/session-evidence/detail-tasks/{task_id}`: bounded copied snapshot;
  existing app-level GET authentication policy applies.
- DELETE same task URL: existing control-token auth.

POST/DELETE return **202 queued**, not applied. GET shows actual applied state,
reason, activation ordinal and effective sequence after owner mutation. Identical
queued POST duplicates coalesce. Active same-session/binding POST renews the lease;
different identity rejects `detail_task_identity_conflict`. A known terminal ID
may start a new activation with incremented ordinal. No exactly-once delivery claim.

Errors: queue full 409 `detail_command_budget_busy` with no mutation; owner task
capacity rejection `detail_task_budget_exceeded`; full/disabled runtime 409
`detail_demands_unavailable`; stale session/binding rejection
`detail_target_not_current`; unknown/evicted GET/DELETE 404
`detail_task_not_found`. Repeat release of known terminal task is no-op returning
terminal state, not another queued command. Evicted IDs are not historical proof
of nonexistence; activation ordinals for evicted IDs restart at 1, with effective
sequence keeping activations unambiguous.

The existing serialized persistence owner validates/applies commands. API threads
only enqueue and read copies. Short locks protect bounded queue/snapshots; never
hold them across database work or reception. No API database writes or pipeline
mutation. Apply at most one command per publication cycle, never drain all commands
ahead of UDP. Existing idle ticks also service one command and expiry.

Before mutation, publish admitted datagrams. Journal validated mutation with
existing checksum/transaction mechanism as `detail_demand`, then apply/acknowledge.
Control entries must NOT inflate runtime received/processed/journaled-datagram
or drop counters. Selection begins at next assembled frame PROCESSED after the
mutation, including delayed frames; do not claim a wire-time cutoff.

Lease expiry uses injected monotonic live clock. Check every owner tick before
new raw admission, including idle ticks; use the expiry mutation barrier below. Replay
uses original journaled mutations, not the current machine clock. Release removes
only that task reason; another task/automatic neighbor can keep the car selected.

Occurrence/session/epoch/format/identity change, flashback/rewind, gap/silence and
shutdown terminate affected tasks: `detail_scope_changed` or
`detail_acquisition_interrupted`. Revalidate queued targets after boundaries;
never retarget slot reuse. Recovery reproduces original demand effects, then
journals restart-clear BEFORE new ingestion: pre-crash leases do not resume.
Reject/drain queued commands on shutdown without retaining a worker. Unjournaled
queued commands are explicitly nondurable. Do not wire legacy engineer/LLM to
the task API in A; it is an explicit integration interface, not an AI feature.

Command/task-state clarification: enqueueing a renewal or release does not replace
an existing active task with queued state. The 202 describes the COMMAND; GET
retains actual task state and additionally reports last_command_state/reason
(queued/applied/rejected). Rejected renewal keeps previous active identity/lease;
new-task rejection becomes task state rejected. TTL/scope/gap/restart termination
uses task state expired with the appropriate reason; explicit release uses released.
Owner rejects a player binding with detail_target_is_player: tasks are opponent
targets. Internally resolve binding IDs using the existing coordinator binding
identity formula, and pass verified tenure identity to the pure selector, never
a mutable car-index-only association. No alternate fingerprint scheme.

Expiry-order clarification: check deadlines on each owner tick BEFORE admitting
the next raw batch. First publish already-admitted pending datagrams with their
current policy, then journal expiry before new admission. This preserves the
mutation barrier and processing-order semantics. A deadline is not retroactive
to buffered/admitted data, and no new batch is admitted under an expired lease.
Expiry is serviced before the single queued command; renewal queued at or after
an expired deadline starts a new activation rather than resurrecting the old one.

## 7. Diagnostics and invariants

Add copied `detail_processing` runtime status: profile/version, current logical
session/epoch, selected cars/reasons, active-task/queued-command counts, cumulative
observation_rows_created and observation_rows_skipped_by_policy. These count
potential vs constructed detailed rows, NOT missing packets/unknown ownership/
decoder failures/kernel drops. Full identifies its profile; disabled shape may
stay disabled. No tokens or participant names in status.

Keep socket buffers, queue sizes, batching/interval, source lock, receiver modes,
SQLite durability, evidence/chunk/read/staging limits, comparison semaphore and
gap accounting unchanged. No new analysis/LLM/upload threads/processes. Bound all
in-memory demand/selection histories independently of lap count. Raw journal
retention/disk growth stays as currently implemented.

## 8. File allowlist and execution order

Existing paths allowed (focused additions preserving dirty work):
- `f1_engineer/pipeline.py`
- `f1_engineer/processing/coordinator.py`
- `f1_engineer/processing/evidence.py`
- `f1_engineer/processing/runtime.py`
- `f1_engineer/api/app.py`
- `f1_engineer/cli.py`
- `f1_engineer/sessions/car_lap_inventory.py` ONLY for non-mutating active-tenure
  access/ordering support; no alteration of whole-field metadata lap rules.

New paths allowed:
- `f1_engineer/processing/detail_policy.py`
- `f1_engineer/processing/detail_demand.py`
- `tests/test_detail_policy.py`
- `tests/test_detail_pipeline.py`
- `tests/test_detail_evidence.py`
- `tests/test_detail_demands.py`
- `tests/test_detail_api.py`
- `docs/selective-processing-implementation-result.md` (result handoff only)

Do not edit this contract, existing tests, web files, receiver implementation,
numeric comparison algorithms, dependency/lockfiles, deployment files or other
paths. Use apply_patch. No inline comments, branches, staging, commits, pushes,
PRs or deployment. No additional agent/delegation.

Execution order:
1. Inspect baseline and run T0 only. If it fails, STOP before editing.
2. Implement pure selector and P01-P10.
3. Integrate pipeline/evidence and P11-P20/E01-E06.
4. Implement demands/runtime/API/config and D01-D12/A01-A07.
5. Run T1 then T2. Fix only task-caused failures, preserving assertions. Maximum
   two reruns of each command after its first run. Then STOP for primary review.
6. Inspect focused diff and write mandatory result handoff.

## 9. Prescribed new tests

Use deterministic synthetic datagrams and temporary databases. The current
admitted_packets fixture has identical car positions: it is NOT a valid unique
race-order policy fixture. Reuse encoding helpers in NEW tests, but explicitly
set unique positions, geometry, identities, context and source/frame sequences.
Use injected clocks and synchronization barriers, not sleeps, for demand tests.
All IDs below are mandatory; parametrization is allowed only within these cases.

### tests/test_detail_policy.py

| ID | Required assertions |
| --- | --- |
| P01 | Race selects position-order neighbors, not physical nearest |
| P02 | First/last race position has one neighbor; single car none |
| P03 | Lapped/pitting race neighbor remains; inactive/absent slot excluded |
| P04 | Duplicate/zero player positions, conflicting Lap Data and unassessable scope suppress both auto neighbors |
| P05 | Parametrize every practice/qualifying/shootout enum; circular traffic and finish-line wrap, not race order |
| P06 | Pit/retired/invalid traffic excluded; single-car dedup; coincident and equal-nearest ambiguity rules |
| P07 | TT/unknown context no automatic opponents/PB/rival |
| P08 | Missing geometry suppresses traffic only; valid task remains |
| P09 | Task/neighbor overlaps dedup cars; invalid tenure never selected |
| P10 | Stable sorted cars/reasons across both 22/24-slot inputs |

### tests/test_detail_pipeline.py

| ID | Required assertions |
| --- | --- |
| P11 | Full profile preserves all-car observation and player sample records |
| P12 | Demand 24-car race creates exactly player+two neighbors; all field metadata/tenures remain |
| P13 | Spy make_car_observation: suppressed car rows never constructed |
| P14 | Current-frame roster/identity change precedes selection; old tenure cannot select replacement |
| P15 | Player record parity full vs demand for identical raw stream |
| P16 | Neighbor swaps apply next assembled processed frame; no stale neighbor |
| P17 | Reorder/malformed/duplicate/conflicting packets preserve diagnostics, player behavior and missing channels |
| P18 | Session/mode/occurrence/epoch/format changes clear stale selection; test 22->24 and 24->22 |
| P19 | 1,000 stable frames: exactly three observations per assessable race frame, one in TT; raw counts equal full; events only on actual selection changes |
| P20 | Four unique demand targets yield at most seven cars; releasing targets never causes all-car fallback |

### tests/test_detail_evidence.py

| ID | Required assertions |
| --- | --- |
| E01 | Full readiness/chunks/records unchanged; additive detail only |
| E02 | Selected full lap publishes; suppressed lap visible/deferred, zero rows; raw payload hashes/datagram counts equal full |
| E03 | Mid-lap activation/drop/reselection deferred, no stitched full lap; later continuously selected lap published |
| E04 | Gap/rewind/ownership quarantine precedence; no unowned detail; saved reports immutable |
| E05 | Fault injection after journal commit, before publication commit, after chunk seal and after manifest insert; recovery selection/chunks/readiness identical, no duplicate publication |
| E06 | Profile mismatch fails; missing legacy profile is full; selection is not counted as acquisition/kernel loss |

### tests/test_detail_demands.py

| ID | Required assertions |
| --- | --- |
| D01 | Queued != active; effective mutation sequence accurate; controls do not inflate datagrams or create sequence-gap events |
| D02 | Same target renewal, identity conflict rejected without modifying active lease; queued command does not overwrite actual task state |
| D03 | Four tasks, 16 commands, 32 terminal statuses bounded; identical queued duplicates coalesce; at most one command per cycle |
| D04 | Release/expiry of overlapping task preserves other task/neighbor selection |
| D05 | Fake clock: expiry at first tick at/after 300 seconds, idle expiry, pending-data barrier, predeadline renewal and postdeadline new activation; no TTL sleep |
| D06 | Activation midway waits for fully selected completed lap; old lap cannot satisfy coverage |
| D07 | Session/occurrence/roster/epoch/format/rewind changes terminate stale tasks, no retarget |
| D08 | Gap/silence/shutdown terminate demands without changing receiver/gap semantics |
| D09 | Recovery replays original demands, then restart-clear before new packets; different replay clock does not change old output |
| D10 | Queued target invalidated before application rejected by owner |
| D11 | Concurrent enqueue/read/release only mutates on owner; bounded snapshots, deterministic queue-order outcomes |
| D12 | Shutdown resolves/rejects queued commands, closes runtime, no retained worker |

### tests/test_detail_api.py

| ID | Required assertions |
| --- | --- |
| A01 | CLI explicit/default forwarding; app default file separation and explicit-path/profile check |
| A02 | Unauthorized POST/DELETE 403 without mutation; configured app-wide GET auth preserved |
| A03 | Invalid task/session/binding IDs/fields/JSON rejected; bare slot/name not accepted; player target rejected |
| A04 | Current-target discovery before first lap, capped 24 and no names; 202 command then actual GET; conflict/stale/capacity reasons exact, no false applied state |
| A05 | Full/disabled unavailable; unknown GET/DELETE 404; repeated terminal release no-op; queue saturation 409 |
| A06 | Deferred trace/compare existing 422 envelope with exact reason; published selected comparisons still work; no silent backfill |
| A07 | Actual bounded selection/task/row status; received/processed/journaled/kernel-drop meanings unchanged |

## 10. Exact authorized Luna validation commands

Run from repository root in PowerShell using existing venv. Missing interpreter
or dependency is a STOP condition, not permission to install/update packages.
Record command output, pass/fail/skip counts and duration for every run.

T0, baseline BEFORE editing:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_pipeline.py tests/test_car_lap_inventory.py tests/test_session_evidence.py tests/test_session_evidence_batching.py tests/test_session_evidence_load.py tests/test_session_comparison_policy.py tests/test_pipeline_output_consumption.py tests/test_cli.py tests/test_api_auth.py
```

T1, new tests AFTER implementation:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_detail_policy.py tests/test_detail_pipeline.py tests/test_detail_evidence.py tests/test_detail_demands.py tests/test_detail_api.py
```

T2, regression acceptance AFTER T1:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_pipeline.py tests/test_car_lap_inventory.py tests/test_session_evidence.py tests/test_session_evidence_batching.py tests/test_session_evidence_load.py tests/test_session_comparison_policy.py tests/test_pipeline_output_consumption.py tests/test_cli.py tests/test_api_auth.py tests/test_isolated_udp_receiver.py
```

Other checks permitted: git diff --check, git diff --stat, git status --short,
read-only diff/file/instruction inspection. No other tests/coverage/formatter,
benchmark, browser, SSH, package install, service control, game traffic generator,
socket-buffer/Wi-Fi change, production action or production database access.

T0 failure: STOP before implementing and report exact baseline failure. Primary
decides disposition. Linux-only isolation skips on Windows are expected but NOT
Linux acceptance. Never weaken assertions or turn failures into skips/xfails.
No changes to existing tests to accommodate changed defaults.

Acceptance requires all prescribed IDs mapped to actual assertions, T1/T2 green
apart from platform-expected skips, focused diff, defaults unchanged and protected
work preserved. Observation reduction is a deterministic count, not speed proof.

## 11. Mandatory handoff from Luna

Write docs/selective-processing-implementation-result.md with:
1. Changed paths and additions to already-dirty files, distinguishing baseline.
2. Every P/E/D/A ID mapped to test function/parametrization; no omitted IDs.
3. Exact T0/T1/T2 results, counts, durations and rerun history.
4. Synthetic API examples: queued, active, expired, rejected and deferred evidence.
5. Evidence of three-row 24-car baseline, seven-car maximum, raw retention parity,
   incomplete-detail safety and deterministic recovery.
6. Limitations: opt-in/not deployed; no historical backfill/UI/LLM integration;
   raw disk growth unchanged; Windows cannot prove Linux losslessness.
7. Confirmation of no out-of-scope edits/tests/dependencies/production actions,
   branches, staging, commits or pushes. No credentials or real telemetry.

Only say Gate A implementation ready for independent primary review if prescribed
tests pass; otherwise report blocker. Do not mark overall architecture or
kernel-drop investigation complete.

## 12. Independent primary verification (not Luna commands)

After handoff the primary will:
1. Audit diff vs contract/baseline: test strength, selection BEFORE construction,
   no alternate all-car staging, association, control auth, receiver invariants.
2. Independently rerun T1/T2 and full Python suite on Windows.
3. Verify isolation tests and full suite in a separate Linux test workspace,
   disposable database and isolated loopback ports, NOT production/game port.
4. Exercise journaled demand transitions/fault recovery/concurrent control during
   synthetic reception; separate kernel/app drops and control sequence effects.
5. Check player numerical parity, whole-field metadata and partial-coverage safety.
6. Check CLI opt-in isolation/rollback and existing web handling of deferred rows.
   If web falsely presents complete detail, STOP production advancement and write
   separate UI contract; do not deploy a knowingly misleading interface.
7. Report independent results and residual risks. Acceptance is not deployment
   permission; user explicitly authorizes deployment.

No finite plan is literally bulletproof. This contract fixes scope, rules, limits,
stop conditions and review boundaries instead of delegating architecture guesses.

## Copyable implementation-agent instruction

> Use gpt-6-luna. Read docs/selective-processing-implementation-plan.md in full
> as a locked contract. Implement Gate A only, within its file allowlist and
> sequence. Preserve existing dirty work. Run only T0/T1/T2 and the listed
> read-only/diff checks. Do not change defaults, existing tests, receiver settings,
> dependencies, this contract or production; do not delegate, create branches,
> stage, commit or push. Any architecture choice, contradiction, missing
> prerequisite, baseline failure or out-of-allowlist change is a STOP condition:
> return the exact blocker to the primary agent. Produce the mandated result
> document and test-ID mapping. The primary performs independent final review
> and verification; you do not authorize deployment or subsequent gates.
