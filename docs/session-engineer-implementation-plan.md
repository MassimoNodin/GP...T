# Session engineer implementation and migration plan

Date: 2026-10-08

Status: proposed technical design following the specialist architecture audit.
The product requirements and foundation-first delivery order are user-approved;
the contracts and migration details below are proposals, not implemented changes.

Product authority: [agreed decisions](session-engineer-product-decisions.md).

## Objective and delivery order

Replace capture-first product orchestration with one continuously processed
session. Prove live evidence and retrospective evidence are the same before
building contextual radio behaviour or replacing the interface.

1. Shared session/evidence foundation and deterministic live comparison.
2. Session-aware references, metrics, competitive monitoring and tracking tasks.
3. Session-isolated conversation, push-to-talk and scheduled radio delivery.
4. Live Engineer and Session Review interfaces, with legacy archive compatibility.

Existing recordings, stored evidence and diagnostic tools remain available during
migration. No destructive archive rewrite or weakening of integrity checks.

## Current implementation seams

- `pipeline.py` exposes player samples, all-car observations, lap attempts,
  participant tenures, context history and lifecycle events in pipeline outputs.
- `recording/service.py` writes raw input and maintains live acquisition monitors.
  Managed recording selects the observer path rather than the full pipeline.
- `storage/importer.py` owns pipeline-output consumption and durable trace/chunk
  publication. Extract this orchestration instead of copying it into a live loop.
- `storage/database.py` has versioned schemas and WAL-backed reads/writes.
  Archived session identity is tied to a processing run and capture provenance.
- `storage/query.py` correctly requires completed runs for legacy archive reads.
  Add explicit session-evidence readers rather than relaxing that requirement.
- `analysis/car_slot_comparison.py` contains useful bounded comparison primitives
  under a deliberately limited practice/qualifying diagnostic policy. Preserve
  that endpoint; create a separate observed-measurement policy for new evidence.
- The current Engineer service routes a single selected-evidence question. It is
  not a persistent task engine or a conversation store.

## Proposed architecture decisions

### One ordered processor, separate evidence consumers

Live UDP and capture replay use source adapters feeding one deterministic
processing coordinator around `TelemetryPipeline`. Consume results through a
shared evidence sink and live state projection. Keep source timestamps and
ordering distinct from replay delivery/freshness timestamps.

Live input is durably journaled before processing. Explicit source sequence,
segment and gap records make capture loss and recovery observable. A replay
adapter reads existing source evidence without rewriting its original identity.

Rationale: a flag change or a second importer cannot provide durable, queryable
live analysis or guarantee equivalent replay results.

### Separate session lifecycle from evidence readiness

Mint a logical session ID independent of final capture hashes and import jobs.
A session may stay active while individual completed laps have published evidence.
Separate session state, processing-generation state and per-attempt readiness.

Do not mark an active legacy processing run complete to bypass archive guards.
Use additive schema/API contracts and a bounded evidence-provider interface.

Rationale: the product needs completed laps before the capture is finalized, but
that must not imply every active or incomplete artifact is trustworthy.

### Immutable publication and explicit invalidation

Seal bounded chunks when needed for lap publication, rather than only at capture
closure. Publish files before atomically committing their manifest, attempt
revision and processed checkpoint. Readers see committed revisions only.

Retain exact source ranges, checksums, sizes, channel availability and ownership.
Later flashbacks or identity conflicts can supersede/quarantine a published lap
and invalidate its use as an automatic reference. Preserve the old result for
history with a stale-evidence qualification rather than silently changing it.

Rationale: lap completion is not permanent authority, and mutable open files
cannot safely serve simultaneous analysis and retrospective history.

### Evidence-backed driver association

Use session-scoped driver IDs with explicit bindings to observed participant
tenures and slots. Maintain per-driver history across position changes. Names are
a lookup layer, not primary identity; slot indexes alone are not driver identity.
Uncertain association is unresolved rather than merged speculatively.

Rationale: automatic targets follow standings while explicit requests follow a
driver, and neither may inherit data belonging to a replacement slot occupant.

### Measurements and eligibility are separate

Reuse bounded resampling, interval delta, coverage and event-detection primitives
through a provider of published evidence revisions. Add an observed-measurement
policy that supports the agreed session formats without inheriting the legacy
practice/qualifying-only diagnostic authority restrictions.

Publishing evidence does not automatically make a lap an eligible reference.
Partial or unsuitable attempts can remain inspectable. Missing data blocks the
affected metric; unknown conditions qualify measurements, not prove their cause.

Rationale: broaden useful analysis explicitly while preserving source integrity
and the distinction between measurement, ranking and causal explanation.

## Proposed records and state contracts

| Record | Required information |
| --- | --- |
| Logical session | Stable ID, observed game UID/occurrence, format and context history, active/interrupted/ended state, boundary reason |
| Processing generation | Processor/schema/config versions, source identity, committed sequence, publication revision, running/recovering/finished/failed state |
| Source journal | Ordered datagrams and gap/boundary events, segment and sequence IDs, received/recorded/processed/published watermarks |
| Driver binding | Session driver ID, evidence-backed participant/slot tenure association and ambiguity state |
| Attempt revision | Driver binding, lifecycle epoch, source ranges, disposition/validity, observed boundaries, collecting/published/superseded/quarantined state |
| Evidence manifest | Immutable chunk paths/hashes, row/byte counts, schemas and exact attempt-owned ranges |
| Comparison | Exact target/reference revisions, policy version, measurements, support masks and limitations |

Field names and schema layout require review before implementation. Use bounded
metadata and read budgets; no arbitrary caller file paths or unbounded trace loads.

## First milestone slices and acceptance gates

### F1: extract shared processing output consumption

Extract writer/quality/output-draining seams from import orchestration behind a
shared sink. Keep the legacy import adapter and its identities intact.

Gate: identical normalized imported evidence and existing archive API behaviour
for fixtures, including reordered frames, lifecycle events and all-car ownership.

### F2: incremental evidence publication and recovery

Add session/generation contracts, bounded chunk sealing, atomic publication and
idempotent restart recovery. Use one serialized persistence owner and committed
reads. Keep unfinished raw capture state distinct from published lap evidence.

Gate: readers never consume open/uncommitted chunks; injected failures at each
publication step recover without duplicate attempts or dangling readable manifests.

### F3: automatic live acquisition and all-driver retention

Start the listening runtime with the API lifecycle, establish exclusive UDP
ownership, detect sessions and process continuously. Keep raw capture internal.
Do not reserve the existing global import operation merely to publish live laps.
Retain explicit conflict protection for socket ownership and artifact writers.

Persist useful session duration/remaining time, total laps and detected format.
Inactivity marks interruption/staleness, not proof of session end. New session
boundaries reset conversation/task context later; reconnect gaps never imply
uninterrupted lap coverage or unverified driver continuity.

Gate: all observed drivers acquire correctly scoped evidence; completed attempts
become readable while UDP continues, including after disconnect/reconnect tests.

### F4: completed-lap comparison while driving continues

Expose additive session-centric reads and compare two published driver attempts
without a finalized capture, import request, local model or radio dependency.
Return timing/channel differences and basic distance-based brake-onset events
with coverage and uncertainty qualifications.

Do not require manual track authoring for the basic numeric-distance proof.
Named-corner matching needs reliable geometry/model association; until available,
use evidenced distance zones rather than inventing corner names or alignment.

Gate: a player/opponent comparison is produced before session closure and remains
the same result, with the same revisions and policy, after session termination.

### F5: parity, bounded load and legacy migration proof

Replay the same admitted source through the coordinator and compare normalized
evidence, eligibility and measurements, excluding operational IDs/timestamps.
Test sustained all-driver input and concurrent reads; establish and document
measured queue, memory, publication-latency and analysis budgets before release.

Provide an adapter for existing archived sessions, with unavailable metadata
explicit. Avoid duplicate logical sessions and silent merging of reprocessed runs.
Reprocessing produces a new generation and preserves historical report provenance.

Gate: foundation tests pass, legacy safeguards remain, active-session analysis
does not require an import, and memory does not grow with session duration.

## Later milestones

### Session-aware metrics and tasks

Implement agreed race/qualifying/practice/Time Trial reference policies, stable
task references, per-driver derived events and continuous task evaluation.
Provide task creation/cancellation/retargeting/deduplication and useful-result
criteria. Separate task completion from forced lap-end announcements.

Verify Time Trial rival selection and actual evidence early using captured inputs.
Decoded rival/PB indices do not prove rival trace availability. Preserve selection
history and limit timing-only evidence to timing-only answers.

### Conversation and radio

Persist session-scoped turns, tasks, exact report references and evidence links.
Resolve follow-ups using relevant history plus measurements, not prior prose as
truth. Add immediate acknowledgements, push-to-talk and spoken clarification.

Schedule selective radio messages with the agreed busy-driving delay toggle.
Direct answers are prompt; queued findings are revalidated before delivery.
Session changes create fresh context. Cross-session reference selection brings
in chosen evidence without merging conversations. Device bindings and missing
local speech/model dependencies need explicit availability states.

### Two-area interface

Add Live Engineer and Session Review over the new session APIs. Reuse useful
analysis components, but do not remove old routes before saved selections and
archive navigation have migration coverage. Keep controls authenticated and
local-first. Retire recording/import from ordinary navigation only when automatic
acquisition, publication and recovery work end to end.

## Validation and regression map

- Pipeline ordering/reset/ownership: `tests/test_pipeline.py`.
- Driver tenures/epochs/slot changes: `tests/test_car_lap_inventory.py`.
- Flashbacks/supersession: `tests/test_lifecycle_import.py`.
- Capture publication/restart/ownership: `tests/test_recording_controller.py`.
- Source/replay behaviour: `tests/test_capture.py`, `tests/test_replay_controller.py`.
- Archive schema/read bounds: `tests/test_storage.py`, `tests/test_api.py`.
- Context/timing history: `tests/test_session_context.py`, `tests/test_session_history_import.py`.
- Analysis coverage/policies: comparison, region and debrief test suites.
- UI migration: existing pinned-live, comparison identity, Engineer, navigation
  and production-workflow checks under `web/scripts/`.

Add targeted tests for the new session ledger, publication protocol, all-driver
readers and active-session comparisons beside existing suites. Include missing
channels, queue overflow, wraparound, contradictory participants, retired UIDs,
format changes, repeated flashbacks and process restart. Do not claim live-game
verification solely from synthetic fixtures; exercise a real supported session
before declaring the foundation operational.

## Explicit non-goals for the first milestone

No full UI redesign, local-model prompt rewrite, radio delivery, new strategy
engine, save verification or multi-career management. The milestone proves the
data path these later features require. It does not promise Time Trial rival
channel comparisons when the source provides only timing evidence.
