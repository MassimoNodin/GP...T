# Live engineer facts and events

Implemented October 12, 2026 as the first backend increment on
`feat/working-engineer-v1`. This does not implement standalone speech,
conversation, coaching tasks or the post-pit benchmark itself.

## API and ownership

`GET /api/v2/engineer/live` requires the configured control token in
`Authorization: Bearer <token>`, even when other read routes are unauthenticated.
It returns `{"data": {...}}` with a versioned current snapshot and bounded event
history. Disabled acquisition returns an explicit unavailable result.

The existing acquisition pipeline is the sole producer. Facts cover verified
field drivers independently of detailed-trace selection; no second UDP receiver,
model call or API-time database scan is introduced. The single persistence owner
publishes a cached snapshot after evidence publication. API readers receive
isolated copies and recheck freshness at read time.

## Snapshot contract

- `schema_version`, `status` and `reason` identify availability.
- `generation` and `session_id` identify the logical evidence session.
  `scope` contains the wire session UID, packet format and association epoch.
- `frame_ordinal`, `frame_identifier` and `session_time_s` identify the
  assembled lap-data frame. `captured_at_ns` is receiver wall-clock time;
  `received_monotonic_ns` is the original receiver's monotonic performance-clock
  timestamp, not publication time. `age_s` is computed against the same clock.
- Each driver has a car slot plus verified tenure ordinal and participant
  identity fingerprint. The additive `participant` field exposes bound roster
  metadata: availability/reason, an `identity` equal to the driver's binding, and
  `value` containing `name`, `driver_id`, `team_id` and `race_number` when available.
  Missing, duplicate, wrong-scope/tenure or fingerprint-mismatched descriptors
  do not publish a name. Empty/control-character names are explicitly unavailable
  without discarding otherwise usable timing. Older snapshots without this
  optional field remain usable for player/position-based timing questions.
- `lap` contains decoded source fields with their original units: millisecond
  timing/gaps, metre distances, lap/sector numbers, position, pit status, validity
  flags and penalties. Raw source zero values are not automatically usable
  answers: zero completed-lap/sector timing means no measured time. Position
  zero is unknown. Consumers must interpret source status and validity flags.
- `tyre` has explicit availability/reason and, when observed, actual/visual
  compound IDs and age. It comes from an unambiguous status packet in the same
  assembled frame; it is never silently carried forward from an old frame.
  Missing/zero compounds do not generate a fabricated tyre-change event.

The runtime's existing telemetry-staleness setting is used for data freshness,
not as a response-latency acceptance target. Non-receiving runtime states, old
lap observations or a future receiver timestamp return unavailable results with
empty driver/event lists. Acquisition gaps, malformed/conflicting lap data,
session/format/lifecycle boundaries and shutdown invalidate old current facts.

Participant metadata is retained only within the active verified roster tenure;
unlike tyre status, it does not require a new roster packet in every lap-data
frame. The current tenure and source participant identity hash are checked on
publication. Conflicting rosters fence disputed slots, replacement starts a new
tenure, and gaps/lifecycle boundaries discard open descriptors. No names are
reconstructed from official driver IDs, network IDs, race numbers or old session
history, and no new detailed-trace selection or persistence schema is introduced.

## Event contract

Events are observations, not completed coaching tasks. Types are
`lap_completed`, `sector_completed`, `pit_entered`, `pit_exited`,
`tyre_compound_changed` and `driver_discontinuity`. They carry driver binding,
scope, frame and source timestamps. First observations, identity/player changes
and new scopes establish baselines without inventing earlier events.

- A lap event requires an observed adjacent positive lap-number transition
  between active-driver observations. Missing last-lap time is `null`. Its
  qualification is `observed_lap_transition`, not full driving-trace coverage.
  The prior observed invalidity flag is not a certified final lap-validity result.
- Sector events cover observed adjacent transitions into sectors two/three,
  reporting the completed sector as one/two. Missing times are `null`.
- Pit entry/exit is a pit-state transition, not proof of a serviced pit stop.
  The player's pit-entry event includes the previous coherent observed field
  positions/identities and their receipt time. Each field entry also has
  `leader_gap_ms` (zero for the leader; unknown/zero follower gaps are `null`).
  Additive `pre_pit_stop_count` and `pre_pit_tyre` carry the previous observed
  stop count and same-frame tyre value, with unavailable tyre data `null`.
  Consumers must independently confirm a stop-count increase and observed
  compound change or tyre-age reset after pit exit before claiming service and
  tyre replacement. Unknown positions are `null`;
  later benchmarking must still validate position uniqueness and eligibility.
- Compound changes require two observed nonzero compounds. Missing status
  frames break that comparison; no unobserved change is inferred.
- Skipped/rewound lap numbers or rewound session time produce discontinuity,
  not manufactured completions or pit/tyre transitions across that boundary.

At most 128 events are retained. `event_cursor` is the latest event ID;
`event_floor` is the cursor immediately before retained history;
`events_dropped` counts capacity evictions. Invalidation clears retained events.
IDs are process-local, not durable playback acknowledgements. Consumers must
bind cursors to logical session/scope, detect cursor resets/history loss and
start at the current cursor on initial connection rather than announcing history.
They must check individual event age and relevance before queueing speech;
fresh current facts do not make every retained event fresh.

## Verification and remaining work

`uv run --extra app --extra dev pytest tests/test_engineer_facts.py -q`:
**35 passed**, including provenance, binding changes, stale data, event capacity,
pipeline/detail-profile integration, authenticated API behaviour, logical session
export and actual loopback UDP publication followed by silence invalidation.

Final full Python regression run: **1,764 passed, 16 skipped** in 284.73 seconds.
The existing Starlette/httpx deprecation warnings remain. Python compilation,
local documentation-link checks and diff-whitespace checks also pass. Browser
checks are not run for this backend-only increment.

This is not real-game or MOZA/audio acceptance. No production deployment or
runtime/model substitution is performed. Next work connects these facts/events
to the standalone radio path, then driver resolution, conversation and tasks.

## Demand-owned opponent observation prerequisite — October 14, 2026

The live driver row can now include an opponent `observation` when a current
explicit detail demand owns that verified participant. Automatic nearby-car
selection and the full-trace profile do not implicitly authorize this live
field. The player observation retains its existing shape and behavior.

The opponent field uses the same `status`/`reason`/`value`/`source`/`identity`
shape and adds sorted, unique `detail_task_ids` (at most four demand owners).
The value includes the selected opponent's own lap number, distance and lap
clock, together with its measured wire speed/brake/throttle/steering/gear; the
player's lap clock is never substituted. Source packet format, session, frame,
session clock, receipt provenance and lap/telemetry association must match.
Missing, conflicting or invalid telemetry returns unavailable values, not zeros.
Non-racing/unknown result status is also unavailable. The field remains diagnostic
and `coaching_eligible` is false.

Every owner is independently matched to the current scope/tenure/fingerprint;
a valid demand cannot make a stale demand for the same car eligible. Conflicting
owner IDs, malformed IDs, over-budget demands and conflicted frames fail closed.
When the owner releases/expires, the next processed frame omits the observation
rather than carrying forward the old sample. Snapshot copies remain isolated.
Consumers must still enforce demand activation, ownership, age and lap-window
eligibility; a current observation is not complete-lap evidence.

The pipeline supplies active demands only under `demand_v1`. This change does
not broaden detailed trace selection, create extra persisted rows or expose all
field telemetry by default. It prepares the data path for bounded opponent
region collection; standalone radio detail-demand ownership, independent
player/opponent capture windows, pairing and spoken comparison remain pending.

Validation: 43 new opponent-observation cases plus existing player observation,
pipeline, detail policy/demand/API and radio suites pass in a 1,350-test selection.
Two existing Starlette/httpx deprecation warnings remain. Compilation and
whitespace checks pass. This selection is not a new full-suite run or real-game
acceptance evidence.

## Owned opponent region capture prerequisite — October 14, 2026

The authenticated `/api/v2/engineer/region-captures/{lease_id}` endpoint now
accepts an opponent variant of the existing player capture body. Add an exact
verified `opponent` identity (car slot, tenure ordinal and participant fingerprint)
and `detail_task_id` for an already-active demand in this same logical session.
Both fields are required together; arbitrary extra fields remain rejected.
The binding still includes the current player/session/generation/scope, so player
replacement or session change fences the collection.

The requested `first_lap` must be the opponent's own next lap, not the player's
next lap. Each opponent lease owns an independent regional tracker. Completion
uses that opponent's lap clock, finish-line event and reported last-lap time;
the player's earlier/later crossing cannot complete it. Response views add the
fixed opponent and detail owner, while results retain individually labelled lap
numbers, source receipt times and diagnostic numeric evidence.

The API requires `demand_v1` and an active runtime owner, then checks its current
opaque binding against the requested car, packet format, lifecycle epoch,
tenure ordinal and fingerprint. Detail-target metadata now includes the latter
two identity fields so callers need not bind by car slot alone. Reads revalidate
runtime ownership even when the last frame still contains old owner metadata;
revoked owners lose their capture results. Observed owner loss, identity change,
player/opponent discontinuity and event-history loss also fence the lease.

All player/opponent leases share the existing three-active-capture budget,
twenty-lap horizon, bounded sample storage and thirty-second renewal requirement.
Renewal preserves samples. Release, expiry and invalidation clear owned samples;
incomplete/interrupted windows remain unavailable rather than becoming measured
comparisons. Existing player-only request/response behavior remains compatible.

This is backend collection, not completed radio tracking: the radio still needs
to activate/release detail demands, schedule both drivers independently, retain
the pair until both crossings, validate comparability and speak the result.
Real-game acceptance remains pending.

Validation: 44 new opponent-capture cases pass, including both crossing orders,
unusable/interrupted windows, lease cleanup, exact identity publication and
authenticated owner revalidation. The integrated radio/live-facts, capture,
detail policy/demand/API/review and pipeline selection passes 1,413 tests with
two existing deprecation warnings. Compilation and whitespace checks pass.
This is not a new complete-suite or real-game acceptance run.

## Radio demand ownership prerequisite — October 14, 2026

The radio HTTP client now supports target discovery and detail-task admission,
status and release through the existing loopback API. Requests retain bearer
authentication, no proxy/redirect behavior and bounded time/response limits.
Task/session/binding IDs and exact admission fields are checked before network
I/O. Target lists are limited to twenty-four rows; task responses are bounded
to sixty-four KiB. Error bodies are not exposed.

The new radio demand broker copies at most four desired task/version descriptors
and owns at most four records including pending cleanup. Each descriptor fixes
the logical session, lifecycle scope and opponent slot/tenure/fingerprint. Only
one exact nonplayer target may supply its opaque binding; replacement or duplicate
targets cannot silently rebind a request. Generated IDs are radio-instance-owned,
and cleanup never addresses an unrelated dashboard demand.

Admission acknowledgement is not activation. Only a matching authoritative GET
with an applied active owner and stable activation ordinal/frame exposes active
ownership. Periodic renewals retain the activation and wait for authoritative
application. Ambiguous admission/renewal/release responses do not trigger repeated
commands. Temporary target transport failures pause readiness rather than revoke
an otherwise valid binding; confirmed identity loss initiates release. Terminal
owners do not automatically revive. Admission and observed activation latency are
reported without a numerical acceptance threshold.

Cancellation, supersession and shutdown request cleanup. A queued DELETE is not
proof of release: owned records remain bounded and pending until a matching
terminal owner snapshot is observed. Shutdown reports pending IDs honestly;
backend TTL remains fallback cleanup, not synchronous release evidence. An
ambiguous admission with no verifiable owner can retain a bounded pending slot.

This broker is a collection prerequisite, not a completed spoken feature. It is
not yet connected to the radio polling worker. Active ownership alone does not
prove same-frame observation availability; paired scheduling must also wait for
the owner ID in the opponent observation, preserve both next-lap boundaries and
retain ownership until both capture results have been consumed.

Validation: sixty-three new transport/ownership cases pass against the real
bounded backend command queue and mocked HTTP transport. The integrated radio,
capture, live-facts, detail and pipeline selection passes 1,476 tests with two
existing deprecation warnings. Real-game acceptance remains pending.

## Radio capture cleanup hardening — October 14, 2026

Region lease ownership now survives a failed or malformed DELETE response.
Only a matching terminal lease response verifies cleanup; otherwise shutdown
reports pending lease IDs. Retries use a ten-second backoff, appropriate for this
synchronous idempotent endpoint (unlike queued detail-demand release commands).
Pending cleanup shares the three-record radio ownership budget with collection,
so cancellation/network failure cannot create unbounded abandoned owners. A
capture being released cannot be rearmed by readding the same request, and a
lost DELETE acknowledgement is not represented as verified cleanup. Backend TTL
remains a safety fallback, not evidence that the client observed release.

Polling and shutdown cleanup snapshots are serialized for both region leases
and detail demands. Nine new release/budget cases pass; the integrated radio,
capture, live-facts, detail and pipeline selection passes 1,485 tests with two
existing deprecation warnings. Paired scheduling and real-game acceptance
remain pending.

## Radio paired capture prerequisite — October 14, 2026

A new paired-capture coordinator composes the bounded radio detail-demand and
region-lease managers. It accepts one fixed player/opponent regional request at
a time, consuming two leases from the shared backend three-active-lease budget.
Each task/version freezes session/generation/scope, both exact identities,
numeric window, diagnostic track length and independent upcoming lap numbers.
An in-place descriptor change requires a new task version.

Neither leg is admitted while the detail owner is only queued, before matching
owner metadata appears in the opponent row, or from a frame predating the owner
activation frame. Admission requires a fresh processed frame, valid geometry and
both drivers still on the lap immediately before their originally requested
window. If either boundary is missed, the pair becomes unavailable; it never
silently moves to a later lap.

Player-first and opponent-first completion are both supported. The first leg
and detail owner remain retained while the second driver finishes. Complete
pair views retain explicitly different lap labels and require both fixed leases,
binding/window/track-length agreement and complete unique lap-result sets.
Complete collection is not proof of comparable or coaching-eligible measurements;
the future reporting layer must validate the numeric diagnostic evidence and
disclose conditions, timings and unsupported claims.

Definitive partial admission failure releases the other leg instead of exposing
a half-comparison. Scope/identity/geometry changes fence both leases. Temporary
owner transport failures pause pair readiness without discarding already-owned
collection. Cancellation and shutdown retain the managers' bounded, honest
pending-cleanup semantics. Backend reserve slots twenty-two and twenty-three
now use the same exact-identity admission policy as other supported slots; zero
tenure ordinals are rejected.

This coordinator is not yet wired into the standalone radio worker/controller.
No spoken opponent tracking, complete focus-corner coaching or real-game
acceptance is claimed by this prerequisite. Thirty-four new paired cases pass
using the real region trackers and bounded detail queue; two reserve-slot demand
cases also pass. They include both crossing orders, queued/late activation,
partial budget rejection, capture-response corruption and cancellation during
admission.

The integrated radio/capture/live-facts/detail/pipeline selection passes 1,521
tests with two existing deprecation warnings. This remains automated prerequisite
evidence, not a demonstrated in-game capability.

## Captured completion provenance — October 14, 2026

Every owned regional lap result now includes a bounded additive completion view:
original event ID/kind, lifecycle scope, exact driver identity, qualification,
lap number/time, observed invalidity and monotonic receipt. These fields are copied
from the actual completion event, not reconstructed from later live lap numbers
or fabricated for numeric comparison. Arbitrary event fields are not retained.
Player and opponent clocks/identities stay distinct even after the earlier
finisher's source event leaves the current history. Returned views and later
source mutations cannot modify the retained facts; lease release/expiry retains
the existing evidence-clearing behavior.

The paired coordinator now binds each result to this completion view and rejects
driver/scope/event/receipt/qualification conflicts, malformed lap clocks and
negative completion age. Invalid laps keep their actual invalidity and unavailable
measurement rather than becoming valid evidence. Older backends without these
fields cannot satisfy the new pairing provenance contract; the radio does not
invent the missing completion facts.

Pair publication also rechecks the processed frame after network I/O. A frame
that becomes stale during polling cannot publish a ready comparison; collection
stays owned while the coordinator waits for a fresh frame. This retains the
existing three-second telemetry safety boundary, not a new latency acceptance
gate. The earlier driver's retained lap may legitimately be older while the
second driver finishes; it remains labelled owned historical evidence, not a
claim of fresh simultaneous telemetry.

Eleven new provenance/invalidity/freshness cases pass. The integrated radio,
capture, live-facts, detail and pipeline selection passes 1,532 tests with two
existing deprecation warnings. The complete suite at the preceding paired-capture
increment passed 3,195 tests, with sixteen platform skips and two existing
warnings; that run does not cover these later provenance/freshness additions.
Spoken numeric comparison/controller integration and real-game acceptance remain
pending.

## Guarded opponent numeric reports — October 14, 2026

A deterministic report formatter now accepts an authoritative fixed pair request
and a matching fresh capture view. It validates the task/version, exact owner,
activation identity, both lease requests, geometry, completion provenance and
complete numeric measurement eligibility before producing a comparison. Snapshot,
polling and per-leg retrieval age are carried explicitly and rechecked at report
construction; queued/inactive owners and stale payloads cannot produce numeric
reports. A later controller must still validate its active registry/scope before
submission and cancel queued audio when that authority changes.

Reports explicitly label the player's lap and the opponent's independently
requested lap. They give each sampled minimum, measured region time and supported
brake-onset interval. Matching compounds permit labelled descriptive numeric
differences; overlapping/touching onset uncertainty does not imply an earlier or
later brake point. Missing/censored onset retains supported minimum-speed facts
and says onset is unavailable. Different compounds retain the absolute facts but
do not produce like-for-like delta fields.

Tyre age, fuel, conditions and traffic remain explicitly unverified. Every report
is diagnostic, not a named-corner recommendation or driver ranking. Retained
completion ages are preserved as historical ownership evidence, not interpreted
as simultaneous live telemetry. A complete paired lap may be formatted before a
longer requested horizon ends; an unfinished/missing corresponding lap is never
substituted. Copied output provenance cannot mutate the underlying capture.

This is a verified formatter, not yet a spoken opponent tracking task: controller
request parsing, registry lifecycle and background-worker integration remain.
Forty-nine new report cases pass using real paired captures. The integrated radio,
capture, live-facts, detail and pipeline selection passes 1,581 tests with two
existing deprecation warnings. Real-game acceptance remains pending.
