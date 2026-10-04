# Foundation architecture

## Decision 0001: start with the telemetry foundation

**Status:** accepted
**Date:** 2026-10-02

Start with the Phase A foundation in the development plan, before a dashboard, voice features, or AI integration. Use one Python process and explicit package boundaries for UDP input, recording/replay, packet decoding, telemetry assembly, and session state. Add the API and web app when stored laps can be compared.

The first executable slice records raw datagrams, identifies the EA wire format from the packet header, replays captures through the same pipeline, tracks session UID changes, and groups packets by session and overall frame identifier. It decodes F1 25 Session packet v1 into canonical session context and Lap Data packet v1 into player lap attempts. It does not claim full Phase A completion: per-car telemetry samples and most packet bodies are not yet decoded, and Race-mode capture validation is still needed.

## Decision 0002: represent gameplay mode as session context

**Status:** accepted
**Date:** 2026-10-02

The plan should explicitly model gameplay mode, including Time Trial, practice, qualifying, and race. This is a small extension to the session and analysis contracts, not a reason to split the telemetry pipeline. Capture and replay remain shared; upcoming synchronization, canonical car samples, distance resampling, and delta calculations will also remain shared.

The `sessions` domain owns a canonical `SessionContext` containing normalized session type and game mode, plus track/layout, rules or performance settings, and conditions when the protocol provides them. Versioned packet adapters decode the wire structures and feed context updates. The application keeps EA's wire-format choice (F1 25 versus 2026 Season Pack) separate from gameplay mode. Preserve raw identifiers; represent missing or unfamiliar values as unknown. Use Session packet fields as the canonical mode source, with packet 14 only as corroborating evidence. Session context history accepts reordered updates within the shared frame reorder window without rolling back the current context, retaining newer unchanged packets as restoration boundaries. A newer packet-format change within the same UID invalidates the prior decoded context; delayed packets from an older format neither invalidate context nor enter the current session's stateful telemetry pipeline.

Completed laps retain every context transition within the attempt, including transitions that occur between Lap Data packets or immediately before the completion observation, so later updates do not rewrite the comparison context of historical laps. Mode policies control lap eligibility, reference selection, and coaching. Time Trial can compare valid attempts and identify ghost references; race analysis can account for active participants, pit and out/in laps, interruptions, and available fuel, tyre, traffic, damage, and race-phase context. Keep the scoring and telemetry comparison math shared, and keep advanced race strategy in its existing later phase.

Sequence the work as follows:

1. The F1 25 Session packet v1 adapter now exposes detected or unknown mode in inspection and replay. Add a 2026 Session adapter only after its packet structure is sourced and pinned. No rewrite of the current recorder or header parser is needed.
2. Before Phase A is complete, validate context transitions and deterministic replay with Time Trial and Race captures, including missing Session packets and unfamiliar mode identifiers. The available capture validates F1 25 Time Trial only. Unknown context remains recordable and inspectable but limits automatic reference selection.
3. In Phase B, add shared driver and lap tracking with mode-aware eligibility, ghost roles, and discontinuity segments, then persist the associated session context.
4. In Phase C and later coaching, apply eligibility filters before selecting references. Validate Time Trial comparison first, then ordinary green-flag race laps. Leave advanced race strategy in its existing phase.

## Decision 0003: build a conservative lap-attempt inventory

**Status:** accepted
**Date:** 2026-10-02

Decode F1 25 Lap Data v1 into immutable per-car records and keep all 22 car entries plus the Time Trial PB/rival indices. Initially create lap attempts only for the header-designated player car. Join lap updates through assembled frames and process them in overall-frame order; packet arrival order alone is not a safe order for stateful lap tracking.

Give each attempt a session UID, car index, and per-car attempt ordinal. Lap number alone is not a stable identity because a Time Trial restart or discontinuity can reuse it. Track garage/pre-start state, attempt starts, invalidity, pit/in-lap/out-lap exposure, completed laps, abandoned segments, and capture-ended partial laps. Latch game-reported invalidity through the outgoing lap and finalize that lap before applying the next lap's reset flags. A distance crossing or regression is evidence for segmentation, not proof of lap completion; require a lap-number transition and a positive `lastLapTime` to confirm a completed attempt.

Store incomplete and invalid attempts with explicit exclusion reasons. Personal-best reference eligibility is conservative and mode-aware: this first slice permits only complete, game-valid, non-pit Time Trial laps with a known Time Trial session context. Unknown modes and race laps are retained but not offered as references until their policy and capture validation are implemented. Snapshot session-context changes with their effective frame on each attempt. Evaluate every context segment on a completed attempt: each segment must have a recognized Time Trial session type, game mode, ruleset, and track, and the mode/track signature must remain stable for the whole attempt. Weather and temperature changes are retained but do not by themselves exclude a reference. If the wire format changes, first flush and process the old-format frames in order, then close the active lap segment so queued packets cannot start a duplicate partial attempt.

The available Melbourne Time Trial recording is local lifecycle validation data: it contains two game-invalid completed laps and a capture-ended partial third lap, so it must produce no eligible PB reference. Keep the full recording local; use synthetic valid attempts for positive eligibility tests. The SQLite/Parquet slice now persists this inventory, with Car Telemetry v1 and Lap Data joined into distance/time/input traces for comparison.

## Decision 0004: persist canonical player traces with SQLite and Parquet

**Status:** accepted
**Date:** 2026-10-02

Proceed as a capture-to-storage vertical slice: decode F1 25 Participants v1 and Car Telemetry v1, synchronize player Car Telemetry with Lap Data on the same session and overall frame, create canonical samples, and persist lap metadata/context in SQLite with one Parquet trace per attempt. Preserve missing packet families as null fields and explicit availability flags. Store invalid, partial, and abandoned attempts for inspection, while preserving conservative reference eligibility.

Keep responsibilities explicit: `udp` owns EA packet adapters; `telemetry` owns canonical units, validation, and synchronization; `sessions` owns participant/lap identity and context timelines; `storage` persists canonical records without interpreting wire packets. Use a deterministic processing-run key from capture SHA-256, pipeline version, and configuration so re-imports are idempotent. Scope session and lap IDs to the run, and store EA's unsigned 64-bit session UID as decimal text because SQLite INTEGER is signed. Publish a complete, checksummed Parquet file before marking its row ready in SQLite; leave unfinished runs identifiable for recovery. Keep the full raw capture local and use the initial Melbourne recording as integration evidence, not a checked-in 92 MB test fixture.

Initially write only player-car traces, because the available capture has one active driver. Decode and retain all 22 Participants and Car Telemetry records so later multi-car capture support does not change the wire model. Use PyArrow for explicit nullable Parquet schemas and bounded row groups. Defer DuckDB, Motion/Car Status enrichment, interpolation, corner analysis, and API/frontend until the player trace can be durably imported and queried.

## Decision 0005: stream imports and isolate their trace outputs

**Status:** accepted
**Date:** 2026-10-02

Replay canonical samples into bounded Parquet row groups as frames complete; do not retain a full lap or capture worth of samples in memory. Keep at most one active trace writer per attempt and publish each finished trace through a temporary file, fsync, atomic replacement, and checksum before completing the processing-run transaction. Hold a non-blocking operating-system lock per database and processing run across cleanup, replay, and publication so two processes cannot delete or replace each other's outputs. Namespace trace roots by database filename as well as run ID so separate databases in the same directory remain independent.

Expose accepted SessionContext history as changes emitted by the shared pipeline. Persist every accepted checkpoint, including unchanged restoration packets, reorder-window history, unknown baselines, and format invalidations, without reserializing the growing history for every raw datagram. If a delayed first context removes a provisional baseline, emit an explicit removal change so stored history matches the canonical timeline. Keep current session context separate from historical context so delayed packets cannot roll live state backward.

An import is complete only when its SQLite inventory and every ready Parquet output agree. Recheck the source capture's size and SHA-256 after replay so an active recorder cannot append bytes under an identity computed from an earlier prefix. If the source changed, fail and retry after recording stops. If a trace is missing or its checksum fails, replay the source capture and rebuild the run's outputs. Version changes to the import semantics receive a new deterministic run identity. The full source capture stays local; database-specific trace directories and lock files are generated data and are not committed.

## Decision 0006: compare stored laps by distance

**Status:** accepted
**Date:** 2026-10-02

Start Phase C with explicit comparison of two stored, completed Time Trial attempts. A caller may manually compare game-invalid laps for diagnosis; the result must show their game and capture-quality exclusions. This comparison does not select a personal best. Automatic reference selection remains a separate service and only uses eligible laps.

Keep storage responsible for locating complete runs, verifying trace checksums, and loading trace columns plus attempt/context metadata. Put validation and distance resampling in `analysis.resampling`, delta and quality summaries in `analysis.comparison`, and orchestration in a comparison service. The CLI is the first caller; an API and chart can later use that same service without owning analysis rules.

Use `lap_distance_m` and `current_lap_time_ms` from each sample. Resample onto a configurable 1 m grid anchored at the lap start, limited to the shared observed distance range and known track length. Do not extrapolate or subtract the first captured timestamp. Interpolate time, speed, and continuous controls linearly; hold gear and DRS from the preceding observation. Preserve frame chronology, collapse exact duplicate observations deterministically, split on distance or lap-clock regressions, and leave unsupported spans null. Initially require each interpolation bracket to span no more than 100 ms or 25 m; enforce availability per channel and report coverage, gaps, and exclusions.

Require every context segment to have a known, stable track and compatible Time Trial context. Compare the same wire format, track ID and length, formula, car-performance setting, and driving assists. Weather differences remain visible context but do not block comparison. Race comparison, cross-mode references, 2026 adapters, and population selection remain later work.

Return the sampled traces, per-channel masks and coverage, excluded spans, full delta curve, official lap-time difference, observed-range delta change, source trace checksums, and analysis/configuration versions. Define positive delta as the target being slower than its reference. Synthetic traces validate math and exclusions; the available Melbourne capture validates import/replay and diagnostic comparison of two invalid completed attempts but cannot prove automatic PB selection. A clean Time Trial capture is still needed for positive reference validation.

## Decision 0007: begin corner analysis with distance metadata and observed events

**Status:** accepted
**Date:** 2026-10-02

Start Phase D with versioned, metadata-backed distance regions and deterministic events derived from the stored distance, speed, brake, throttle, and steering channels. Motion decoding is not required for braking, observed minimum speed, throttle pickup, exit speed, or boundary delta analysis. Use the existing Melbourne capture for diagnostic regions only: its two completed laps are game-invalid, and the plan's example distances are not verified game corner coordinates. Label these regions as draft and do not use them for automatic coaching.

Put immutable, validated track and region models in `tracks.model`, JSON loading and provenance in `tracks.loader`, and pure sustained-event and region-feature calculations in `analysis.events` and `analysis.corners`. The comparison service loads verified traces and requires explicit track-model selection before returning region analysis. Preserve completed Time Trial compatibility checks and expose invalid-lap exclusions with every diagnostic result.

Track models identify packet format, track ID, layout, expected lap length, distance origin, revision, provenance, and validation status. Regions have half-open analysis/search windows, optional direction, a nominal apex anchor, and complex membership. Reject unordered or out-of-track bounds; permit overlapping windows only when the regions share an explicit complex. Report event distance brackets rather than implying sub-sample precision.

Detect sustained threshold events in chronological raw observations using session time for duration; do not use the lap clock because it may repeat. Break event continuity on missing values, excessive time or distance gaps, and time/distance regressions. If a threshold is active at a search-window entrance, label the event left-censored and do not invent its onset. Retain multiple brake applications and throttle lifts. Reuse Phase C resampling masks for boundary values and never bridge unsupported spans. Mark observed minima as partial when the full region is unsupported.

Steering-only turn-in is a proxy. A configured track apex is a metadata anchor and carries the model's validation status. Driver-apex and trajectory-based turn-in remain unavailable until Motion position/direction is stored with validated track-relative geometry. A player's driven line is evidence for a reference path, not automatically the circuit centreline.

## Decision 0008: persist Motion before calibrating track geometry

**Status:** accepted
**Date:** 2026-10-02

Decode F1 25 Motion packet v1, join it to canonical samples by session UID, overall frame, and car index, and persist the primary player's position, velocity, normalized forward/right directions, G-forces, and orientation. Missing Motion stays null; never reuse a neighbouring frame. Decode all 22 wire records but initially persist Motion for the Lap Data header-designated player. Add an availability flag and validation flags for unusable vectors; preserve valid field groups when another group is invalid. Keep MotionEx opaque for this slice.

The supplied Melbourne capture contains 13,950 Motion packets with the expected 1,349-byte v1 layout; their frame keys match all 13,950 Lap Data packets. The player's positions are finite and direction-vector norms are near one. This is enough evidence to add and inspect a trajectory, but the two completed laps are game-invalid and cannot validate a circuit centreline, track boundaries, or geometric apexes. EA's F1 25 specification defines Motion v1's packed 22-car layout and normalized direction encoding: https://forums.ea.com/t5/s/tghpe58374/attachments/tghpe58374/f1-games-game-info-hub-en/61/4/Data%20Output%20from%20F1%2025%20v3.pdf.

Version the canonical trace schema and importer pipeline. Write schema v2 traces while retaining read support for v1 traces; distance-only analysis must continue to work when geometry fields are unavailable. Add a versioned observed-trajectory JSON export with attempt/run identity, trace checksum, session context, units, frame/distance/time anchors, quality/validity, and explicit discontinuity segments. Label it as an observed driven trajectory, never a centreline. Preserve invalid laps as diagnostic artifacts.

Keep the Motion G-force axes named lateral, longitudinal, and vertical in trajectory JSON. Require nonnegative session-time and integer-millisecond lap-time anchors for supported trajectory points. Split continuity at session-time gaps above 100 ms, 3D position steps above 25 m, and lap-clock rewinds above 20 ms; preserve exact one-frame adjacency (including uint32 wrap) and the existing session-time and lap-distance regression checks. Apply one float32 ULP of tolerance at the endpoint magnitude to session-time gap/regression checks, and sqrt(3) ULPs at the largest position component to 3D step checks. Compare lap-clock changes in their integer-millisecond source units. Include the active continuity limits in each artifact. These intentionally conservative thresholds exceed the supplied capture's adjacent-sample maxima (31.815 ms and 3.006 m) and observed 12 ms lap-clock jitter. Do not reject on velocity/position consistency: this capture has apparent position/time speeds up to 182.4 m/s, so that rule needs better evidence.

Defer track projection, centreline calibration, racing-line comparisons, and driver-apex detection until clean captures establish validated geometry and manually reviewed corner boundaries. A driven path alone does not describe the circuit centreline; requiring MotionEx would add no necessary geometry evidence at this stage.

Acceptance requires strict packet size/version checks; synthetic tests for layout, signed direction conversion, malformed/non-finite values; frame-join tests for ordering, missing Motion, player-index changes, and lap boundaries; a replay of the supplied capture with 13,950 successful Motion decodes and same-frame canonical enrichment; unchanged lap inventory/reference eligibility/distance comparison; idempotent v2 imports plus v1 trace reads; and trajectory export that records provenance, validity, coverage, and unsupported discontinuities.

## Decision 0009: select recorded Time Trial references before generating coaching

**Status:** accepted
**Date:** 2026-10-02

Begin Phase E with a deterministic `SESSION_BEST` selector for a chosen Time Trial attempt. Select the fastest eligible earlier attempt within the same processing run, session UID, and player car index. This is the best eligible lap recorded before the target in that import, not necessarily the game's full-session personal best. Exclude the target and later attempts; break equal-time ties by earlier attempt ordinal.

Keep reference policy separate from storage and comparison mathematics. Storage returns completed-run inventories and verified trace snapshots. A Time Trial policy checks completed, game-valid, start-observed, non-pit attempts, positive official timing, the persisted reference-eligibility decision, stable known Time Trial context, matching track/formula/performance/assists, and known matching weather/temperature. The result includes the selected identity, lap time, trace checksum, policy version, scope, and every candidate's exclusion reasons. Use only finalized recordings with zero reported recording losses and zero replay frame-assembly losses. Persist the replay assembler's late-packet and overflow counters with the import's capture-quality metrics; an older run without those metrics is insufficient evidence and must abstain until reimported. If no candidate qualifies, abstain instead of choosing an invalid or incompatible lap.

An invalid target may use a selected valid reference for manual diagnostic comparison, but never becomes an automatic reference itself. Keep reference eligibility separate from metric support: older schema-v1 traces can be selected for distance analysis even without Motion, while per-channel gaps stay visible in comparison masks. `SESSION_BEST` is a reference kind, not a gameplay mode. Unknown context and race modes return explicit unavailable/unsupported-policy results. Keep canonical data and comparison math shared; race reference policy waits for validated pit/stint, tyre, fuel, traffic, damage, and interruption context.

Defer all-time personal bests until persistent player identity and cross-session comparability are established. Defer theoretical-best laps, corner opportunity rankings, diagnoses, confidence scoring, and actionable coaching until track regions and reference evidence are validated. The supplied Melbourne capture exercises abstention because both completed laps are game-invalid; synthetic clean laps validate deterministic positive selection. Positive integration evidence remains pending until a clean Time Trial capture is available.

## Decision 0010: expose historical analysis through a local application

**Status:** accepted
**Date:** 2026-10-02

Bring the planned historical API and dashboard forward now that stored laps can be compared. Add a read-only FastAPI adapter and a minimal Next.js session/lap explorer with explicit distance comparison and recorded-session reference selection. Keep analysis, compatibility, and eligibility policy in the existing Python services; the frontend only renders their evidence.

Open the API on loopback by default and configure its database at server startup, never from a request. Use read-only SQLite connections for browsing and analysis. Version every response and preserve source identities, checksums, analysis versions, validity, coverage, masks, and exclusion reasons. Keep session UIDs as strings. Browse all recorded modes through shared models; comparison and reference policy return explicit unsupported/unavailable states outside supported Time Trial cases. Charts preserve nulls and masks, and distinguish official lap-time difference from observed-range delta change.

The available Melbourne recording validates the session explorer, diagnostic comparison, and reference abstention. It does not validate positive reference selection, circuit geometry, or coaching. Keep HTTP capture import, live telemetry, trajectory overlays, corner charts, and generated coaching out of this slice; revisit them when their evidence and interfaces are ready.

The dashboard collapses repeated imports of the same capture/session to the most recently completed processing run while preserving separate recordings with different capture checksums. Its page is server-rendered and calls the loopback API with caching disabled; browser requests carry opaque session and attempt keys only.

## Decision 0011: expose diagnostic distance-region analysis

**Status:** accepted
**Date:** 2026-10-02

Bring the existing deterministic distance-region analysis into the historical explorer. This advances the planned corner inspection interface using implemented, capture-backed measurements while verified geometry and actionable coaching remain deferred.

Register immutable packaged track models by model ID and revision. Expose their metadata, provenance, and validation status through the read-only local API. Require explicit model selection and retain comparison-service compatibility checks; requests must never supply model filesystem paths. Session telemetry does not independently establish circuit layout.

Reuse the existing Python comparison and region-analysis services. Display regions in distance order with local trace plots, regional delta change, observed minimum speed, braking/throttle event brackets, exit speeds, full requested-window channel coverage, and unsupported statuses. Compute coverage in Python from the requested region bounds and excluded spans; do not derive it from only the shared chart grid. Preserve censoring, multiple events, and missing data. Draft models always produce diagnostic presentation. Steering-derived turn-in remains a proxy; driver apex and calibrated track-relative geometry remain unavailable even though world-position samples are stored.

The Melbourne recording validates six draft analysis windows and diagnostic comparisons between game-invalid laps. It does not validate official corner numbering, geometry, reference eligibility, or coaching. Do not rank these regions as actionable opportunities or generate diagnoses, confidence scores, theoretical-best laps, or advice. Preserve the existing Time Trial policy boundaries and explicit unsupported results for other modes.

## Decision 0012: ingest existing local recordings through durable import jobs

**Status:** accepted

**Date:** 2026-10-03

Add a local recording inbox and background import workflow so existing captures can enter the historical library through the application. Keep this increment limited to existing-capture ingestion.

Persist opaque capture identities and import-job lifecycle in SQLite. Map each capture ID to a configured-root namespace and relative capture path; re-resolve and enforce root containment before execution. Requests supply opaque IDs, never filesystem paths. Imported content identity remains the existing SHA-256.

Use one bounded application import worker. Persist coarse phases, terminal state, creation/update timestamps, result identity and failure reason; keep worker handles and high-frequency counters transient. Order inbox job links by their persisted update timestamp so retries remain discoverable. Coordinate the controller with a database-scoped OS lock. After acquiring that lock on restart, mark abandoned jobs interrupted and require explicit retry. Revalidate source identity and root containment when the worker executes. Reuse the existing importer’s locking, checksum verification, idempotency and recovery.

Protect local mutation endpoints with server-held authorization and same-origin frontend handling. Preserve read-only browsing and analysis. Keep current captures selectable, hide stale entries without jobs, and retain missing-file entries that have jobs as unavailable so their durable status remains visible. Report success only after durable import completion; interrupted or failed jobs must never appear successful.

The Melbourne recording validates ingestion, retry and unchanged diagnostic analysis. This workflow does not establish positive reference eligibility, race policy, opponent coverage, geometry or coaching.

## Decision 0013: record UDP telemetry through the local application

**Status:** accepted

**Date:** 2026-10-03

Add explicit Start, Stop and status controls for UDP recording, completing the application's capture → inbox → import → review workflow. Recording is mode-independent: Time Trial, Race, unknown modes and unsupported packets remain capturable. This increment adds acquisition controls and diagnostic status; it does not add live comparison, reference selection or coaching.

Extract a reusable recording service from `cli._record` and preserve the CLI's current arguments, output, overwrite policy, and capture semantics. Configure the API's UDP bind address, port, queue limit, and recordings root at server startup. Browser requests carry opaque recording IDs; the server generates output names and paths. Protect mutations with the existing server-held token and same-origin proxy.

Persist the recording lifecycle in SQLite and acquire controller ownership before recovery. Serialize managed imports and recordings through the app controller. Make repeated Start and Stop requests idempotent. Persist every accepted raw datagram before decoding; malformed and unsupported packets must not stop capture. Expose bounded status snapshots with elapsed time, packet/write/drop counts, receive state, and latest known session context. Keep session UIDs as strings. Managed recording uses a bounded acquisition observer that tracks packet/frame loss and current session context without retaining lap attempts, decode-error strings, or context history. The CLI keeps its full inventory collector; historical lap analysis and detailed metrics remain the importer's responsibility, and managed capture summaries omit metrics that have not been computed.

Use `starting → recording → stopping → complete`, with terminal `failed` and `interrupted` states. Record to an exclusively created staging file outside the inbox's `.f1ecap` discovery pattern. Stop closes reception, drains accepted queued datagrams, finishes pending writes, writes the capture footer, flushes and fsyncs, closes the file, then publishes it atomically without replacement. Only a published finalized capture enters the inbox; importing remains explicit. Reconcile a completed file only after the live recorder no longer owns its job, so a destination collision cannot stop an active capture prematurely.

The Start endpoint's `202 Accepted` acknowledges durable job creation, not listener readiness. Clients wait for persisted `recording` status before assuming the UDP socket is bound and the capture writer is initialized; `starting` means acquisition setup is still in progress.

A complete recording means orderly finalization, not zero receive losses, valid laps, or reference eligibility. Preserve the existing loss metrics and eligibility checks, and never substitute fabricated zero counters for unavailable evidence. On startup, mark abandoned recordings interrupted after controller ownership is acquired. Preserve staging artifacts for inspection, require an explicit new recording, and never resume or append automatically. API shutdown follows the same graceful Stop path. Disk, bind, and publication failures retain accurate failure state and must not advertise successful publication.

Validate CLI compatibility, start/stop idempotency, controller ownership, import/record serialization, stop while waiting and during queued writes, malformed packets, queue overflow, socket and disk errors, publication collisions and staging cleanup failures, restart recovery, path containment, authorization, same-origin proxying, and mode-independent acquisition with synthetic Time Trial, Race, and unknown-context streams. Replay the Melbourne capture through the extracted service and preserve payload ordering, lap inventory, diagnostic comparison, and reference abstention. A clean valid Time Trial capture is still required to validate positive reference selection; race and opponent policies and geometry keep their separate evidence gates.

## Decision 0014: inspect telemetry quality per attempt

**Status:** accepted
**Date:** 2026-10-03

Add a versioned standalone quality report for every persisted attempt with a verified trace, including completed, invalid, partial, and abandoned attempts. Build it in a shared Python analysis service and expose it through the CLI, read-only API, and selected-attempt dashboard panel. The report combines trace provenance, capture/footer, recording-observer and replay-assembly evidence, game validity and disposition, channel availability, chronological discontinuities, and distance-supported coverage. Keep recording losses, recording-observer losses, replay losses, game validity, lap disposition, and metric support as separate evidence domains. Validate footer statuses and loss counters before exposing them; malformed and missing evidence remain distinguishable.

Use the existing checksum-verified trace loader and shared distance-resampling rules. Share the existing float32-tolerant session-time continuity rule. Show channel support across the observed distance range separately from support across the track length reported in attempt context. An unobserved capture tail is unknown coverage, not evidence of dropped packets. Schema-v1 traces report Motion as unavailable in that schema; missing counters remain unavailable rather than defaulting to zero. This report supports any recorded gameplay mode without changing reference-selection eligibility.

Defer aggregate quality scores, confidence values, score-based reference thresholds, geometry inference, ranking, diagnosis, and coaching until representative captures support those claims. No storage migration is needed.

## Decision 0015: monitor player telemetry during recording

**Status:** accepted

**Date:** 2026-10-03

Extend the bounded app-managed acquisition observer to expose one current player telemetry snapshot while recording. This helps the driver verify UDP acquisition and collect the clean Time Trial evidence still needed by the historical reference workflow.

Build the snapshot only from assembled frames. Use the player car index in the Lap Data header and join Car Telemetry from the same session and overall frame. Reuse canonical channel validation; absent, malformed, mismatched, or out-of-range values remain unavailable rather than becoming zero. Preserve source session UID, frame identifier, packet format, player index, and monotonic receive-time provenance. Keep a single latest snapshot with explicit `waiting`, `fresh`, `stale`, `unsupported`, or `unavailable` state. Start with a 500 ms player-snapshot freshness limit, evaluated server-side from the monotonic receive timestamp. Reset snapshot and player-selection state at session, wire-format, or player changes; reject delayed updates that no longer match active provenance. Session context continues to use the session tracker's own lifecycle, separately from player-snapshot freshness.

Advance player identity only after the frame assembler admits an envelope. Record the frame where a player change takes effect and reject older buffered snapshots with wrap-safe frame ordering. Bind receive timestamps to accepted wire fingerprints so a duplicate or rejected envelope cannot refresh the sample. Calculate age only from the selected valid Lap Data and Car Telemetry packets; malformed packets cannot refresh values decoded from another envelope. Require receive-time provenance for every selected packet and age from the oldest selected receive time. If required provenance was evicted before frame assembly completes, report the frame as unavailable instead of fresh. Reject frames containing packets from an inactive wire format, including frames flushed during a format transition. A selected-player Lap Data decode failure publishes an unavailable snapshot with that frame's provenance and clears values from the prior frame. These rules keep status, identity, and freshness aligned with the same admitted source evidence.

Expose the monitor through the existing local recording-status response and same-origin dashboard proxy, using faster bounded polling while capture is active. Raw datagram persistence remains first and authoritative. Keep observer memory bounded and retain no lap inventory or telemetry history; add no database migration. The monitor is available across supported F1 25 Time Trial and Race contexts, and unknown contexts remain recordable. It does not add live comparison, lap completion, reference selection, opponent state, trajectory projection, diagnosis, or coaching; those policies remain unchanged.

Synthetic streams validate mode-independent state display, same-frame joins, missing channels, ordering, player/session/format resets, unsupported variants, and freshness expiry. The Melbourne capture validates that live values can be decoded from the existing source; its invalid laps remain explicitly invalid and it does not establish positive reference eligibility. Preserve capture payload ordering, loss accounting, shutdown, and publication behavior.

## Decision 0016: add source-pinned 2026 player-trace adapters

**Status:** accepted

**Date:** 2026-10-03

Add a bounded 2026 Season Pack compatibility slice for Session v1, Lap Data v1, and core Car Telemetry v1. Use EA's Version 11.0 structures attachment and current Season 8 PDF v1.2, pinned with URLs, retrieval date, and checksums in `docs/telemetry-protocol.md`. Select parsers by `(packet_format, packet_id, packet_version)` and validate exact documented body sizes, array counts, and car bounds. Keep format-specific record layouts, widths, and car counts in `f1_engineer.udp`.

Normalize only fields with equivalent documented meanings into the existing `SessionContext`, lap lifecycle, canonical samples, and schema-v2 Parquet trace. Keep 24-car layouts for 2026, account for the `uint8` engine-temperature width in Car Telemetry, and preserve the packet-6 DRS field as DRS. Do not map packet-16 Active Aero or Overtake values onto legacy DRS. Preserve unknown identifiers and nullable missing channels. Participants, Motion, Car Telemetry 2, and other 2026 packet bodies remain opaque in this increment.

Use the existing capture, frame assembly, import, and query paths. Bump the importer pipeline identity so prior opaque captures reimport deterministically; retain trace schema v2 because no canonical trace columns changed. The recording monitor reports unsupported status only for unsupported required core adapters, so an opaque packet 16 cannot invalidate a supported same-frame Session/Lap/Car Telemetry snapshot.

Keep automatic reference eligibility restricted to validated F1 25 Time Trial. A 2026 Time Trial context, Race context, or unknown context may produce diagnostic attempts but must abstain from automatic references. Synthetic TT, Race, unknown-context, reordered, malformed, unsupported-version, highest-player-index, and format-transition streams validate protocol and lifecycle behavior. Label support as specification and synthetic validated; real 2026 capture validation remains a separate evidence gate.

## Decision 0017: complete 2026 Motion and Participants adapters

**Status:** accepted

**Date:** 2026-10-03

Extend 2026 Season Pack support with Motion v1 and Participants v1 using the pinned EA structures and Season 8 v1.2 PDF. Select adapters by `(packet_format, packet_id, packet_version)`, validate the documented packet body sizes, decode all 24 records, and preserve participant driver/network/team identifiers as unsigned 16-bit values including sentinel `65535`.

Normalize Motion into the existing `CarMotionData` units and `CarSample` fields. Divide 2026 signed quantized lateral, longitudinal, and vertical G-force values by `1000.0`; retain the documented XYZ directions, world vectors, and radian orientation. Preserve independent valid field groups when another group is malformed. Join Motion to player Lap Data only for the same admitted assembled session/frame and header-designated player index. Missing Motion remains null. Participants remain session-scoped snapshots in the existing SQLite table; names and IDs do not establish persistent player identity or automatic cross-session references.

Bump the importer identity so captures imported before these packet families were decoded can be reprocessed. Keep trace schema v2 because the canonical trace already has Motion columns and participant snapshots already have durable storage. Reject delayed participant snapshots from an older wire format after a same-UID format transition. Reuse the existing observed-trajectory export, including its units, provenance, and discontinuity segmentation; the output remains an observed driven path, not a centreline.

Synthetic packet fixtures and capture imports establish documented-layout decoding, player-23 joins, identifier preservation, storage, and export behavior. Real 2026 capture validation remains pending. Automatic references remain limited to validated F1 25 Time Trial, and geometry, coaching, and opponent-reference policies stay deferred.

## Decision 0018: persist exact-frame Car Status evidence

**Status:** accepted

**Date:** 2026-10-03

Decode Car Status packet 7 v1 for F1 25 and the 2026 Season Pack using the official F1 25 v3 PDF and pinned 2026 Season 8 v1.2 PDF/structures. Dispatch by `(packet_format, packet_id, packet_version)` and validate the complete documented bodies: 22 records of 55 bytes (1,210-byte body) for F1 25, and 24 records of 59 bytes (1,416-byte body) for 2026. Keep format-specific layouts, including the 2026 ERS harvest-limit insertion, inside `f1_engineer.udp`.

Join Car Status to a player Lap Data sample only within the same admitted session, wire format, assembled overall frame, and primary player index. Never carry a status value across frames. Missing, malformed or unsupported, format-mismatched, player-mismatched, and conflicting packets produce an unavailable sample with a reason. A decoded packet can remain available while individual invalid fields are null and identified in validation flags. Preserve raw compound IDs and signed FIA flags, including `-1`; keep F1 25 fuel-in-tank/capacity values as reported quantities with no unit metadata because the source does not specify their unit. Permit finite negative `fuel_remaining_laps` values.

Add nullable Car Status fields to trace schema v3 and keep explicit readers for schemas v1 and v2. Older traces synthesize null Status fields and report the channel unavailable in their schema. Bump the importer identity; SQLite needs no migration. The quality report adds matched-sample and per-field valid/missing/invalid counts, first/last frame provenance, bounded changes between adjacent matched samples, compound labels only when the stored formula context defines them, and unavailable-reason counts. Report Status in Time Trial, Race, and unknown modes as diagnostic evidence. Do not use it for reference eligibility, interpolation, strategy, fuel-consumption estimates, inferred tyre sets/stints, or live-monitor requirements.

The supplied F1 25 capture is the real-data acceptance source: replay must decode its 13,949 Status packets and show exact-frame joins against the 13,950 Lap Data frame keys, with one Lap frame missing Status and no Status-only keys. Preserve its two invalid completed laps and partial third, with no eligible references. Synthetic F1 25/2026 fixtures cover layouts, raw sentinels, bad fields, unsupported versions, packet order, player mismatch, conflicts, and mode-independent output. Real 2026 capture validation remains pending.

## Decision 0019: expose bounded observed-path previews

**Status:** accepted

**Date:** 2026-10-03

Expose the existing observed-trajectory analysis through a shared Python service, a read-only local API endpoint, and the selected-attempt dashboard. Reuse the trace checksum verification and accepted trajectory continuity policy. Preserve source attempt/run/session/player identity, checksum, trace schema, disposition, game validity, reference eligibility/exclusion reasons, full-source coverage, and original frame/time/distance/position anchors across Time Trial, Race, unknown, completed, invalid, and partial attempts.

The API returns a versioned preview distinct from the CLI's full-fidelity JSON export. Before Parquet materialization, cap the source at 100,000 rows and 64 MiB using the same checksummed file snapshot; check both the stored row count and Parquet metadata before decoding. Bound session context at 1,024 segments and 4 MiB of serialized values before fetching it. Cap rendered output at 2,000 points, 256 segments, and 20 examples for each break category. Thin deterministically and proportionally within each existing segment while retaining every segment's endpoints. Report source coverage and preview reduction independently. If the source exceeds its limits, no position evidence exists, or preserving segment endpoints exceeds the preview limits, return an explicit unavailable reason; never drop a segment, bridge a discontinuity, or invent an interpolated position. Keep all caps server controlled; requests cannot provide trace or output paths.

Display the path as an explicitly observed world-X/world-Z projection in metres with equal spatial scale. Label source validity and provenance, preserve discontinuity segments, and expose original frame, time, distance, and XYZ values for the selected point. This projection has no asserted compass orientation. It does not establish a centreline, calibrated circuit geometry, ideal racing line, lateral error, apex, or coaching recommendation. Schema-v1 traces without Motion report the channel unavailable. No database/trace migration, importer identity change, reference-policy change, or live-monitor dependency is introduced.

Acceptance uses the supplied Melbourne capture: all three attempts remain viewable as captured (two invalid completed attempts and one partial), with their full-source segment and coverage counts unchanged and no reference becoming eligible. Synthetic checks cover deterministic point caps, endpoint retention, discontinuities, singleton/all-missing data, oversized and fragmented abstentions, and legacy Motion unavailability. UI checks cover equal axes, labels, provenance, and accessible point inspection. Clean valid Time Trial, Race, and real 2026 capture validation remain future evidence gates.

## Decision 0020: expose standalone diagnostic region observations

**Status:** accepted

**Date:** 2026-10-03

Expose the existing per-attempt distance-region measurements independently of lap comparison and reference selection. This allows invalid, partial, and abandoned attempts to provide useful Phase D evidence when no reference is available, using the capture already on hand.

Reuse the chronological control-channel threshold detectors, censoring behavior, resampling masks, and requested-window coverage calculations. Apply the shared source float32-ULP tolerance to session-time gaps/regressions and endpoint-ULP tolerance to minimum sustained-event duration so exact 100 ms observations remain stable at different session-time magnitudes. Preserve source attempt, trace checksum/schema, session-context and model revision/provenance, selected observed events, full event counts, and unsupported/partial reasons. Bound source reads using D0019's row/byte/context limits, cap the resampling grid at 100,000 positions, and return at most 20 event examples per channel and region without changing the selected event or total count. Support trace schemas v1/v2/v3 where their control channels exist; Motion is not required.

Require an explicitly selected packaged track-model revision in CLI, API, and dashboard flows. API requests identify a registry entry by model ID/revision and cannot supply filesystem paths. Keep the current stable Time Trial context and track-model compatibility gate, but do not require completion, game validity, or reference eligibility. Race, unknown, changing, or incompatible contexts return explicit unavailable policy reasons; their quality and observed-path evidence remains accessible.

Return a versioned single-attempt report through a shared Python service, a read-only local API, the CLI, and the selected-attempt dashboard, independently of comparison success. Keep every result diagnostic-only. These distance windows are not validated corners; steering onset is only a proxy, and driver apex/track-relative geometry remain unavailable. Do not calculate comparative loss, rankings, diagnoses, theoretical-best laps, or advice. No storage migration, trace schema change, importer identity change, or packet decoder change is needed.

Acceptance uses the Melbourne capture: both invalid completed attempts produce the six draft-window reports, and the partial third reports only observations supported by its recorded range. Under identical configuration, standalone metrics match each attempt's target/reference metrics from existing comparison reports. Synthetic checks cover censoring, missing channels, gaps/regressions, partial windows, repeated events and truncation. Policy tests cover invalid/partial Time Trial, Race/unknown/changing context, model incompatibility, schema-v1/v2/v3 traces, checksums, source limits, and grid limits. Dashboard inspection remains available when automatic reference selection abstains.

## Decision 0021: link distance regions to observed spatial evidence

**Status:** accepted

**Date:** 2026-10-03

Let a user select one diagnostic distance window and inspect the matching recorded positions on the existing observed-path preview. This makes the relationship between draft distance boundaries and source motion evidence inspectable with the current Melbourne capture, including invalid and partial attempts.

Reuse the immutable packaged model revision and checksum-verified, bounded trace reads. Before linking reports, require an exact match on attempt key, run, session UID, player index, trace checksum and schema version. Region membership is half-open `[start, end)`. Preserve original frame, lap-distance, time and XYZ anchors for the full source support; return no more than 20 continuity fragments per region. Keep each fragment separate across source discontinuities. The dashboard may draw only retained preview samples and must report their count separately from full-source position counts. Preview thinning can remove a point from the highlight and must never be reported as missing source evidence. Schema-v1 traces explicitly report Motion as unavailable.

Keep the existing stable compatible Time Trial model gate for regions. Race and unknown contexts continue to expose independent quality and observed-path evidence but do not receive region overlays. Add no storage migration, importer change or coordinate projection layer.

Acceptance uses the available Melbourne capture: both invalid completed attempts can link their draft windows to same-trace positions, and the partial attempt exposes only the positions it recorded. Synthetic checks cover half-open bounds, repeated distances, source discontinuities, singleton segments, preview thinning, provenance matching, schema-v1 Motion unavailability and fragment caps. This feature is diagnostic only and does not validate corner boundaries, establish a centreline, infer a driver apex, or enable reference selection. A clean complete capture remains required for positive reference validation. Reliable geometry requires independently justified track boundaries and coordinate/distance alignment evidence; averaging clean driven laps alone yields an average driven path, not a physical circuit centreline.

## Decision 0022: expose standalone player control traces

**Status:** accepted

**Date:** 2026-10-03

Expose a bounded single-attempt trace inspector for speed, throttle, brake and steering without requiring a comparison, eligible reference, or calibrated track model. This advances the basic telemetry-chart milestone and makes practice, qualifying, race and unknown-mode attempts useful for inspecting raw observed driving behavior.

Use stored session time as the chart coordinate and preserve original frame, time and distance anchors. Keep sample order; split output runs at frame/time discontinuities and missing channel values; never join gaps while thinning. Deterministically bound points and runs, preserve their endpoints, and report source counts and preview reduction separately. Reuse checksummed snapshot loading and existing source row, byte and context limits. Support trace schemas v1/v2/v3 and completed, invalid, partial and abandoned attempts.

Keep outputs diagnostic-only and mode labels sourced from persisted context. Charts make no claim that attempts are comparable and calculate no loss, diagnosis, geometry or coaching. Add no trace migration, importer identity change or reference-policy expansion. Validate with existing captures and synthetic chronology, missing-channel, frame-wrap and cap cases; finalize the active Shanghai capture before using it as immutable acceptance evidence.

## Decision 0023: summarize persisted capture evidence by processing run

**Status:** accepted

**Date:** 2026-10-03

Expose a bounded, read-only run summary through the local dashboard and API so long captures with multiple sessions can be understood before individual attempts are opened. Read only persisted SQLite metadata and the existing import summary; do not reread the source capture or materialize Parquet traces.

Keep capture hash/size, processing identity/status, and capture-footer completion separate from acquisition losses, replay assembly losses, decoder/join counts, session/attempt totals, disposition, game validity, and stored reference eligibility. Aggregate eligibility and exclusion reasons; never describe eligibility as a selected reference. Missing or legacy metrics remain unknown. Context labels must preserve unknown intervals and clearly name the snapshot they represent rather than implying that one context value covers the whole session.

Bound run, session, and attempt result pages. Provide links from the recording inbox and session browser to run evidence and from session/attempt rows to existing history views. Do not add an overall "clean", "ready for coaching", or equivalent certification. Add no storage migration, importer identity change, source capture reread, reference-policy expansion, or trace loading.

Validate summary counts against stored importer results and synthetic runs for multiple contexts, unknown contexts, failed/incomplete processing, absent metrics, pagination, and unsigned session UIDs. The recovered Shanghai career capture has an explicit incomplete footer because its final 46-byte record was truncated; it can validate the retained large multi-session ingestion, practice/qualifying transitions, lap lifecycle, exact-frame joins, and mode-independent inspection, but it cannot be described as an uninterrupted capture or establish positive Time Trial reference eligibility. A clean, game-valid Time Trial recording remains required for that gate. Geometry and coaching retain their independent evidence gates.

## Decision 0024: explicitly compare same-session practice and qualifying attempts

**Status:** accepted

**Date:** 2026-10-03

Add an explicitly selected manual diagnostic comparison policy for known practice and qualifying session types, including sprint shootout, with the `practice_qualifying` ruleset. Require both attempts to belong to the same completed processing run, session UID and primary player car index. Each attempt must be completed with a positive reported lap time, an observed start, no recorded pit encounter, and known stable context throughout. Require matching wire format, track identity/length, gameplay mode, exact session type, ruleset, formula, performance setting and the assists already checked by the Time Trial comparison contract. Unknown, changing, mismatched or unsupported context returns an explicit unavailable reason. Race comparison is deferred to a separately versioned diagnostic policy with real race lap-lifecycle, restart and neutralization acceptance. This is an evidence boundary, not a limitation of distance-delta mathematics; Race remains inspectable through standalone traces, quality reports and trajectories and never falls back into this policy.

Reuse the existing distance-grid, resampling masks, channel differences and delta-time calculations. Identify the comparison policy and both source attempts/checksums in the result; preserve game validity, exclusions and persisted recording/replay evidence. Keep official lap-time difference separate from the supported observed-range delta. Bound API source reads and grid allocation before materialization using the established analysis limits. Invalid laps and an incomplete capture may provide manual diagnostic evidence; neither becomes an eligible automatic reference through this policy.

Retain the current Time Trial comparison behavior and TT-only automatic reference selection. Practice/qualifying requests must deliberately select this policy; do not infer it from a permissive fallback in the Time Trial guard. Keep track-model region analysis and spatial overlays under their existing Time Trial gates. No storage migration, trace schema change or importer identity change is needed. Report that fuel load, tyres, traffic, cooldown intent and other driving conditions are not controlled; calculate no attributable loss, ranking, diagnosis, theoretical best or coaching recommendation.

The recovered Shanghai capture supplies completed same-session pairs for acceptance: practice attempts 2/3 and sprint-shootout-1 attempts 1/3 have observed starts, no recorded pit encounter and no null context segments. Confirm full context compatibility before comparing them. Preserve and expose its incomplete-footer and replay-loss evidence in comparison results, and separately validate reported lap-time differences, supported delta masks and channel coverage. Practice attempt 1 must abstain for missing context/unobserved start, and attempt 6 for unobserved start. Synthetic checks cover unsupported Race/unknown modes, context changes, session/player/run mismatches, invalid and pit attempts, legacy trace schemas, checksums, source/grid limits, and unchanged Time Trial/reference/region policies. A clean eligible TT capture remains required for positive automatic-reference validation; verified geometry and coaching remain separate evidence gates.

## Decision 0025: expose observed conditions beside lap comparisons

**Status:** accepted

**Date:** 2026-10-03

Expose observed fuel quantity, tyre compounds and age, and weather/temperature context for both source attempts beside explicit lap comparisons. Reuse the schema-v3 exact-frame Car Status data and the existing quality-summary semantics, preserving valid/missing/invalid counts and first/last frame, session-time and distance anchors. Describe those anchors as first/last observed within the trace; they do not guarantee lap-start or lap-end conditions. Resolve compound labels using the formula context where known; retain raw IDs and unknown labels otherwise. Keep quantities in source-reported units without inferring fuel consumption.

These observations help explain diagnostic differences but do not establish matched conditions, normalize lap time, attribute causation, infer a stint or tyre degradation, or alter Time Trial / Practice-Qualifying comparison and automatic-reference policies. Legacy trace schemas report Car Status conditions as unavailable; unknown fields and context remain unknown. Add no migration, packet adapter, importer identity change or reference-policy expansion. Bound returned discrete/context examples and retain exact anchors in the response. Acceptance uses the Shanghai Practice 2/3 pair, the existing Melbourne invalid Time Trial captures, and synthetic legacy/missing/invalid/unknown/formula-change/frame-wrap cases. Clean Time Trial references and independently validated geometry remain separate gates.

## Decision 0026: inspect one selected comparison distance interval

**Status:** accepted

**Date:** 2026-10-03

Allow one explicitly selected numeric distance window within an already authorized completed-lap comparison. Require finite bounds satisfying `0 ≤ start < end ≤ reported track length`; represent its samples as the half-open interval `[start, end)`. Do not infer or persist a corner definition, track model, or geometry from the selected numbers. Keep the existing Time Trial and manual same-session Practice/Qualifying policy gates; Race, unknown context, and partial attempts continue to abstain.

Within the selected window, report channel coverage including unsupported tails and recorded excluded spans, source-anchored observed minimum speed and peak brake, and bounded sustained brake/steering/throttle episodes with full counts, censoring, short-event rejection, and unsupported-break evidence. Report supported target-minus-reference lap-clock deltas at each boundary. Calculate interval delta change only when both traces and their shared delta have connected supported time evidence across the complete interval, including float32-tolerant continuity of raw source session time; endpoint values alone do not establish interior support. Keep official lap-time difference separate and preserve all existing source identities, checksums, capture evidence, and observed conditions.

Use the established 100,000-row, 64 MiB trace, 1,024-context-segment, 4 MiB context, and 100,000-grid limits for interval requests under either policy. Read each verified trace once. Cap each threshold's event examples at 20 per attempt and excluded-span examples at 20 while retaining full counts and truncation flags. No window is selected automatically. Add no migration, trace/parser/importer change, reference expansion, ranking, diagnosis, causal claim, line/apex inference, or coaching.

Acceptance uses numeric windows over the recovered Shanghai Practice 3/2 and sprint-shootout 3/1 comparisons, preserving their policy, source identities, condition summaries, and incomplete-capture evidence. Check a predetermined Shanghai window against the shared delta curve and raw event detector; verify unsupported spans remain unavailable. Existing Melbourne invalid-lap comparisons remain diagnostic, and matching a draft region reproduces its shared coverage, minimum-speed, and threshold observations. Partial/unobserved-start attempts cannot bypass comparison eligibility. Synthetic cases cover legacy schemas, missing channels, interior gaps, unsupported tails, censoring, multiple bounded episodes, sub-grid windows, frame wrap, chronology discontinuities, invalid/out-of-track bounds, and source/grid limits. Verified geometry, clean eligible Time Trial references, Race lifecycle, opponent comparison, and coaching remain separate gates.

## Decision 0027: persist Event and rewind lifecycle evidence

**Status:** accepted

**Date:** 2026-10-03

Add format- and packet-version-specific Event v1 decoding for F1 25 and the 2026 Season Pack. Preserve exact four-byte event codes and the documented 12-byte detail union; interpret only the documented `SSTA`, `SEND`, and `FLBK` lifecycle signals. For malformed bodies, retain at most the first 12 detail bytes plus original byte length and a truncation flag; full datagram bytes remain in the capture. Keep other documented event payloads uninterpreted and report bounded per-code counts. Unknown codes, unsupported versions, and malformed event bodies create an uncertain boundary, quarantine that frame's Lap Data, and block automatic references to the prior scope. Persist selected lifecycle events and synthesized session-time-regression boundaries with processing run, session, wire format, current header frame IDs, source session time, and a monotonic internal ordinal per admitted assembled frame. `FLBK.flashbackFrameIdentifier` and `flashbackSessionTime` describe the target; neither replaces the current header frame or drives packet/frame ordering.

Treat an explicit flashback or independently observed float32-tolerant session-time regression as a conservative attempt boundary. Use only successfully decoded player Lap Data to establish timer baselines. A flashback target later than the last valid pre-boundary Lap Data time is ambiguous; reset the timer baseline at explicit or uncertain boundaries so Event-only frames cannot consume rewind recovery. Close the active attempt from its last pre-boundary observation, clear previous observations, and quarantine Lap Data in a frame containing `FLBK` because packet ordering within that frame cannot establish its branch. The first post-boundary attempt has an unobserved start until a new ordinary line crossing or confirmed lap advance proves one. Do not retire the session UID or infer a flashback from a regression alone. The bounded managed live observer applies the same Event and selected-Lap-Data regression boundary guard, clears the old snapshot, and suppresses values from the boundary frame.

Preserve prior attempt dispositions, official times, validity, checksummed Parquet bytes, and diagnostic inspectability. Reconcile superseded completed attempts separately with a per-session sweep using pre-boundary frame ordinals and a max-heap of observed completion times against the target time with the shared float32 tolerance; never compare wrapped uint32 values as ordinary integers. The sweep emits at most one state-change relationship per attempt. Cap reconciliation at one million activation/change operations; if a session exceeds the cap, mark its unsuperseded attempts unassessed and report the truncated session. Identical flashback signals in one frame create one boundary; conflicting, future, or incomplete targets record uncertainty and block automatic references from the affected historical scope. Superseded flags are nullable so legacy/unassessed imports remain unknown; newly analyzed attempts are explicitly assessed. Superseded or unassessed state also clears stored reference eligibility and adds an exclusion reason. Rewinds never clear an existing superseded result.

Add additive SQLite migrations and an importer identity bump. Keep trace schema v3 unchanged. Stream bounded lifecycle events into persistence; include event-code counts, lifecycle-analysis version, and reconciliation work/cap status in run metrics. Add bounded ordinal-paginated lifecycle timeline and attempt lifecycle status to CLI/API/dashboard, including the `/laps` response model. Explicit completed-lap comparisons may still inspect superseded attempts with target and reference lifecycle exclusions visible; automatic reference selection rejects superseded, ambiguous, and unassessed evidence. `SSTA` and `SEND` are annotations and do not end a session or discard trailing packets. Race reference/lifecycle policy remains deferred.

Acceptance replays the recovered Shanghai capture without changing the pre-D0027 lap inventory or D0026 comparisons. Its raw scan contains 4,000 Event v1 packets (five `SSTA`, five `SEND`, and no `FLBK`); the frame-admitted import persists five `SSTA` annotations and no `SEND`, because all five `SEND` packets carry late overall-frame zero identifiers. The other unadmitted Event packets are 85 byte-identical `BUTN` duplicates and 140 late frame-zero `BUTN` packets; no Event overflow or `FLBK` packet was lost. One session-time regression has no verified rewind target, so its five preceding Shanghai Practice attempts remain lifecycle-unassessed. The existing Melbourne capture remains unchanged. Synthetic cases cover future and missing target evidence, bounded malformed details, capped reconciliation, boundary frames with and without Lap Data, an Event-only frame before resumed Lap Data, duplicate/conflicting/delayed/malformed/unsupported events, independent session-time regressions, post-rewind start recovery, player/session/format transitions, API/UI diagnostic visibility, schema migration, and UID zero. Missing or inconsistent target evidence remains explicitly uncertain; no real flashback or Race behavior is claimed without a real capture.

## Decision 0028: persist reported lap and sector timing evidence

**Status:** accepted

**Date:** 2026-10-03

Add Session History v1 adapters for F1 25 and the pinned 2026 Season Pack specification. Dispatch by `(packet_format, packet_id, packet_version)`. Validate the fixed 1,460-byte packet, populated lap and tyre-stint counts, and format-specific car-index bounds. Preserve reported lap and sector times, validity bits, best-lap markers, tyre-stint records, and source provenance without normalizing reported values. Unknown best-marker values remain raw and unavailable rather than invalidating otherwise usable evidence. The fixed lap array may include a current partial lap, but the last populated row is not assumed to be partial; its count alone never establishes that a lap is complete. Match only through an already completed attempt, its exact reported lap time, and history admitted strictly after the attempt's completion observation.

Associate evidence only with existing completed attempts for the header-designated primary player. Require matching processing run, session UID, wire format, player index, association epoch, actual lap number, and exact reported lap time. The epoch advances once for each admitted frame containing a lifecycle boundary and for admitted primary-player or wire-format changes. Conflicting player/format headers make the scope unassessable. Attempts and history observations carry the epoch; attempts that start and complete across epochs cannot be associated. This closes scopes across player7→player8→player7 and 2025→2026→2025 transitions without relying on Session context packets. The evidence observation's internal frame ordinal must be strictly greater than a separate attempt completion-observation ordinal; D0027's last active `end_frame_ordinal` keeps its existing meaning. Session History is periodic and asynchronous, not exact-frame telemetry; do not join it to a same-frame Lap Data sample. Duplicate or repeated attempt identities, conflicting snapshots, missing completion evidence, and uncertain scope produce explicit ambiguous, conflicting, unavailable, or truncated results. Session History never creates attempts, changes attempt disposition or game validity, changes lifecycle reconciliation, or restores reference eligibility.

Use the existing ordered frame admission and internal ordinals. Wrapped identifiers are not sorted as ordinary integers. Rewinds and uncertain lifecycle boundaries close the evidence-association scope; later history snapshots cannot revise attempts from an earlier epoch. Late final bulk packets do not bypass the frame assembler. Persist one timing report per attempt with an additive SQLite migration and importer identity bump. Include source frame identifiers, internal frame ordinal, capture sequence, packet format/version, source lap number, association epoch, completion ordinal, validity flags, and bounded reason/status fields. The report's provenance wrapper includes the attempt key, processing run ID, capture SHA-256, and nullable completion-observation ordinal, so the stand-alone timing endpoint can establish both capture identity and strict post-completion chronology. Legacy reports preserve unknown completion provenance as null. Keep Parquet trace schema v3 and its bytes unchanged. Legacy imports report timing evidence as unavailable. Bound total association work, candidates, per-identity signatures, and conflict examples; report truncation, propagate frame/lifecycle evidence loss, and abstain when a cap prevents a unique result.

Standalone timing inspection is mode-independent and does not require a valid Session context if the attempt's wire format is known from its admitted packet metadata. Sector comparisons inherit the existing completed-lap Time Trial and Practice/Qualifying policies; Race and unknown modes gain evidence inspection only. When both comparison sides have unique timing evidence, report target-minus-reference sector deltas separately from the resampled distance-window delta. Keep each game-reported sector unchanged and report sector-sum residual separately from the reported lap time. Preserve raw zero and out-of-range sector components, expose them as unavailable, and calculate sector deltas only when both reported sector times are positive and supported. Sector validity remains independent from whole-lap validity. Automatic references, theoretical best, sector rankings, opponent histories, geometry, and coaching policies remain unchanged.

Capture acceptance uses the recovered Melbourne and Shanghai F1 25 v1 evidence without requiring another recording. Required covered cases include invalid Melbourne attempts whose individually valid sectors disagree with whole-lap validity, and Shanghai Practice attempts 2/3 that match actual laps 4/5 by lap number and reported lap time. Capture footer incompleteness remains visible, no recovered sector evidence makes a lap reference-eligible, and late frame-zero snapshots cannot bypass frame ordering. Synthetic coverage includes F1 25 and 2026 layouts, 2026 player index 23, count/body/version errors, unknown validity bits and markers, opponent cycling, UID zero, player/format changes, uint32 wrap, partial records, conflicting and repeated identities, flashback rewriting, uncertain boundaries, missing/invalid sectors, residuals, migration, bounded work, idempotent import, and unchanged trace schemas.

## Decision 0029: explain existing diagnostic comparisons deterministically

**Status:** accepted

**Date:** 2026-10-04

Add a bounded, versioned comparison brief generated only from the structured comparison result already in memory. Use deterministic templates to report the official target-minus-reference lap-time difference, supported reported sector differences with independent validity labels, and an explicitly requested distance-window delta only when the interval has connected supported time evidence. Include material limitations for game validity, lifecycle state, capture completion/losses, comparison coverage, and uncontrolled Practice/Qualifying conditions.

Return the same structured facts and rendered text through the comparison CLI, API, and dashboard. Each fact names its source fields, units, target/reference attempt identities, run IDs, and trace checksums. Cap the brief at six facts and eight grouped limitations while retaining complete detail in existing comparison reports. Unsupported, missing, invalid, conflicting, or truncated inputs cannot produce supported facts. Race, unknown, partial, or incompatible pairs remain unavailable under the existing comparison-policy gates; the brief never selects a different reference.

The brief reports what the stored evidence shows. It does not infer causes, recommend changes, rank opportunities, identify corners, claim verified geometry, or change reference eligibility. It advances the post-lap explanation milestone while deterministic coaching waits for validated corner and reference evidence. No persistence, importer, trace, or schema changes are needed.

Acceptance covers positive, negative, and tied values with stable rounding; invalid sectors; missing, conflicting, and truncated timing evidence; unsupported windows; diagnostic Melbourne invalid laps and their reference abstention; and Shanghai Practice 3/2's -1.232 s official difference, source identities, incomplete capture, and lifecycle warnings. Synthetic tests verify the CLI, API, and dashboard receive the same bounded facts/text and that unavailable comparisons produce no successful brief. No extra trace reads or raw arrays enter the formatter.

## Decision 0030: isolate validated geometry from distance-region models

**Status:** accepted

**Date:** 2026-10-04

Add a bounded, versioned geometry artifact and pure projection kernel, separate from the existing distance-region `TrackModel`. The artifact identifies packet format, track/layout, lap length, game-distance origin, world coordinate frame and units, lateral-sign convention, ordered XYZ anchors, explicit supported segments, source provenance, model role, and independent geometry and distance-calibration validation evidence. Roles distinguish an observed reference path from an independently reviewed centreline.

Use normalized game lap distance as the lookup coordinate, with the model's explicit additive distance-origin transform to an unwrapped one-lap axis; never replace it with Euclidean path length or modulo-alias the lap endpoint to the start. A cyclic lap seam is usable only when the artifact explicitly declares a closed seam and its supported endpoints are position- and tangent-continuous; otherwise exact lap endpoints are unavailable. At supported distances, interpolate XYZ by game distance, derive a local horizontal tangent/normal, and report signed lateral, longitudinal, and vertical residuals with model identity and support state. Reject mismatched format/track/layout, nonfinite or ambiguous anchors, gaps, unsupported seams, degenerate tangents, extrapolation, and work beyond fixed model/query bounds. Return explicit unavailable reasons. Projection support and validation status are separate: observed paths stay diagnostic, and a centreline is reviewed only when both its geometry and distance calibration have separately documented review evidence; lap/reference eligibility remains unevaluated by the kernel.

Keep the kernel independent of database, importer, trace schema, API, dashboard, corner definitions, lap validity, and reference eligibility. Add a local read-only CLI command that checks artifact structure and reports validation evidence without granting it. Do not package or activate real geometry in this decision. Melbourne/Shanghai observations, including averaged driven paths, cannot claim a physical centreline or coaching support. Enabling a real track later requires independently justified geometry and reviewed game-distance alignment. No extra recording or persistence migration is required.

Acceptance uses synthetic straight, curved, and closed-path fixtures with known offsets and numerical tolerances; coordinate/sign convention and distance-origin cases; segment gaps/seams, duplicate/out-of-order distances, degenerate tangents, nonfinite data, identity mismatch, and model/query resource bounds. Existing traces with missing Motion and legacy schemas remain distance-only and unavailable for geometry. Existing comparisons, reference selection, and deterministic briefs remain unchanged. No observed capture is elevated to validated geometry.

## Decision 0031: require connected evidence for corner interval deltas

**Status:** accepted

**Date:** 2026-10-04

Use one pure interval-delta evaluator for numeric distance windows and corner regions. Supported entry and exit values remain independently visible, but their difference is available only when both boundaries, all interior resampled time points, the shared delta, and raw session-time chronology are connected for both attempts. Preserve float32 clock tolerance, uint32 frame-wrap handling in resampling, and the existing half-open window convention. Return per-attempt and shared coverage, each support/continuity result, and bounded reason codes. Numeric windows retain their existing `supported`, `unsupported_boundary`, and `unsupported_interior` statuses; a disconnected raw chronology maps to `unsupported_interior` there. Corner deltas retain diagnostic/supported labels only after measurement support passes, and otherwise report unsupported boundary, source chronology, or interior status with a null change value.

Bound model and comparison work before evaluation: accept at most 64 distance regions and 1 MiB of serialized model JSON; reuse the existing 100,000-row, 64 MiB trace, context and 100,000-point distance-grid limits for requests with windows or models; reserve at most 4,000,000 conservative interval-work units across all selected regions and an optional numeric window. The reservation is `interval_count × (target rows + reference rows + target grid + reference grid + shared delta grid + target excluded spans + reference excluded spans)`. Reject over-limit requests before evaluating any interval. These are execution limits; the persisted `TrackModel` JSON schema is unchanged.

Version corner analysis as `distance-regions-v2`, keep source attempt/run/trace-checksum and model-revision provenance, and expose the added coverage and support reasons in the existing API, CLI result, and dashboard. Do not change single-attempt braking/speed/throttle observations, reference selection, mode gates, geometry validation, capture/import/storage schemas, or coaching permission. Measurement support, reviewed region definitions, reference eligibility, and coaching remain separate gates.

Acceptance covers evaluator parity between numeric windows and regions; supported clean intervals; unsupported boundaries, interior gaps, clock gaps/rewinds, unsupported tails, and sub-grid missing brackets; float32 timing tolerance and half-open bounds; resource-limit rejection before partial region output; and draft Melbourne output remaining diagnostic. A read-only replay of recovered Shanghai Practice 3/2 over `[500 m, 1,200 m)` retained `unsupported_interior`, a null delta change, and target/reference/shared coverage of 0.957143/0.941629/0.906007, exactly matching the pre-refactor calculation. Race and unknown mode policy remains unchanged.

## Decision 0032: rank only gated recorded corner-time differences

**Status:** accepted

**Date:** 2026-10-04

Add a pure, versioned `corner_loss_candidates` measurement builder over D0031 corner interval evidence. Return at most three positive target-minus-reference interval differences that remain nonzero at the existing one-millisecond display precision, with half-open region bounds, target/reference attempt/run/checksum identity, registered model revision and approval provenance, the session-best policy version and selected identity, and the connected-support coverage/continuity evidence. Sort by unrounded difference descending, then start distance and stable region identifier. Expose bounded region counts, exclusion reasons, and omitted counts. Use “recorded time differences” (or “largest supported recorded time differences” when coverage is incomplete); never present these as recoverable time, diagnoses, or advice. Keep `coaching_eligible` false.

Enable ranking only for completed, game-valid, positive-time, observed-start, non-pit, lifecycle-assessed, nonsuperseded Time Trial targets with supported stable conditions; clean finalized capture and replay evidence; an exact match between the explicit comparison reference and the full server-generated session-best assessment, including scope and trace checksum; and a registered, validated region-model revision with a separate explicit approval bound to its exact content fingerprint. `TrackModel.validation_status` alone is not approval. Require connected D0031 interval evidence and reject complex-bearing or overlapping models for this version. Unsupported regions are excluded, never treated as zero. Practice, Qualifying, Race, unknown modes, draft/unregistered/unapproved models, invalid attempts, incomplete/unknown capture, reference mismatch, unsupported intervals, diagnosis, and coaching abstain with bounded reasons. Before scanning candidate traces, bound the inventory to the target and at most 256 prior attempts, context to 1,024 segments and 4 MiB in aggregate, and eligible prior traces to the existing 64 MiB/100,000-row source limits in aggregate; exceeding any cap abstains.

Keep the existing comparisons and deterministic comparison brief unchanged. Expose the same bounded object through the comparison service, CLI, API, and dashboard; do not add persistence, automatic processing, or raw trace reads beyond the bounded session-best selector. A candidate ranking reports measured interval differences and does not establish cause, recoverability, or permission to coach. Synthetic approved-model evidence validates positive behavior; current draft/invalid or incomplete captures validate abstention. No real region model is promoted by this decision.

Acceptance covers a synthetic clean approved Time Trial ranking; stable ties; nonpositive and sub-millisecond values; partial/unsupported intervals; overlapping and complex regions; mismatched selected reference, source identity, checksum, scope, or model approval; draft/unregistered models; invalid, incomplete, unknown, superseded, unassessed, or non-Time-Trial attempts; selector attempt-count bounds; stable omitted/excluded counts; and unchanged comparison briefs. Melbourne's draft model and the recovered incomplete Shanghai Practice capture must not produce candidates.

## Decision 0033: summarize ranked recorded corner evidence

**Status:** accepted

**Date:** 2026-10-04

Add a bounded deterministic corner-comparison brief over D0032 ranked measurements and their matching region observations. Preserve D0032 eligibility and provenance gates, including the exact approved model fingerprint, selected session-best identity, and connected interval support. For each of at most three regions, include the recorded interval-time difference and supported brake-threshold onset, observed minimum-speed, 50% throttle-onset, and configured exit-anchor speed facts, for at most five facts total. Require unique threshold events whose onset is not left-censored, full channel coverage, and no rejected short events or unsupported breaks; a right-censored continuation still supports an observed onset. Require complete-window minimum-speed coverage and matching, supported exit anchors. Retain onset brackets, source paths, attempt/run/checksum and model/approval provenance, and bounded reasons and omitted counts. Missing or ambiguous control measurements omit only those facts; a failed D0032 ranking abstains the entire corner brief.

Use descriptive recorded-measurement language only. Do not infer causes, recoverable time, confidence probabilities, or driving advice; keep `coaching_eligible` false. Reuse comparison and region results already in memory without additional trace reads, persistence, migrations, or changes to the existing deterministic comparison brief. Expose one shared result through comparison service, CLI, API, and dashboard. Synthetic approved-model evidence validates positive behavior; current draft Melbourne and incomplete Shanghai Practice evidence must abstain. A clean eligible Time Trial capture and independently approved region definitions remain prerequisites for real positive acceptance and any future coaching.

Acceptance covers clean synthetic approved evidence with all five facts, bounded sign/rounding behavior for sampled threshold brackets, missing, ambiguous, censored, partial, or unsupported control observations, mismatched model/reference/source provenance, and explicit gate/omission counts. It confirms that the existing `comparison_brief` output remains unchanged and that the current packaged model/captures produce no corner summary.

## Decision 0034: compare bounded observed trajectories diagnostically

**Status:** accepted

**Date:** 2026-10-04

Add a versioned paired world-X/Z preview for explicitly selected comparisons already supported by the Time Trial or Practice/Qualifying policies. For this first overlay, require both attempts to share the same processing run, session UID, and player car. Validate the shared mode-specific comparison policy from SQLite-only attempt/context metadata first, bounded to 1,024 context segments and 4 MiB per attempt. Do not run full distance resampling just to authorize an overlay. Then load each checksummed Parquet trace only through the existing 64 MiB/100,000-row source-limited trajectory preview and verify attempt/run/session/player/checksum/schema provenance. Enforce at most 2,000 rendered points and 256 preserved continuity segments per attempt.

Plot both recorded paths in a single shared equal-scale world coordinate frame. Preserve each path's source breaks; do not independently align, rotate, normalize, interpolate across gaps, or claim compass orientation. Retain each attempt's game validity (including unknown), lifecycle exclusions, capture completion (including unknown), source coverage, and thinning counts. If either Motion preview or provenance is unsupported, make the overlay unavailable while leaving the ordinary comparison usable. Race and unknown comparison policies remain unsupported.

This is observed-path inspection only. It does not identify an ideal line, track-relative lateral offset, turn-in/apex error, causes, or driving advice; it does not change geometry validation, reference eligibility, ranking, or coaching permission. Expose the same bounded result through a CLI JSON export, local API route, and dashboard overlay without storage migrations or changes to existing comparison outputs.

Acceptance covers the recovered invalid Melbourne pair and Shanghai Practice 3/2 with diagnostic labels and incomplete-capture/lifecycle warnings; explicit true/false/unknown validity and capture states; a shared coordinate scale; distinct paths and preserved segment breaks; source thinning and coverage; missing Motion/legacy schema; mismatched scope, checksum, schema or comparison policy; oversized context and source rejection before full comparison/resampling; and point/segment/source-read bounds. Ordinary lap comparisons, rankings, and briefs remain unchanged.

## Decision 0035: inspect distance-aligned observed positions

**Status:** accepted

**Date:** 2026-10-04

Add one optional game-lap-distance probe to the D0034 paired observed-path overlay. Preserve its Time Trial / Practice–Qualifying policy and same-run/session/player gates. Accept one finite requested distance within the known track range `[0, track_length]`; keep the start and finish endpoints distinct. Evaluate each side from the same bounded, checksum-verified full-source read used for its path preview, before preview thinning, and return the exact source observation or interpolation within one continuous source segment.

Require strictly increasing source-distance brackets, at most 25 m of distance span, the existing 100 ms session-time continuity bound, and frame adjacency with uint32 wrap allowed. Reject extrapolation, gaps, repeated or overlapping support, ambiguous matches, and unsupported/missing coordinates. A shared exact sample must not be counted again as two adjacent brackets. Preserve source frame/time/distance/XYZ anchors, segment identity, interpolation fraction and attempt/run/checksum/schema provenance. Report target-minus-reference world X/Z difference and horizontal separation only when both probes are supported; these are world-coordinate differences, not track-relative offset or performance loss.

An unsupported probe retains the paired path overlay and returns per-side reasons with null paired differences. Include supported probe positions in shared plot bounds and show them as diagnostic markers. Keep one probe per request; do not allocate a distance grid, add persistence, promote track geometry, or enable racing-line claims, diagnosis, or coaching. Expose the optional distance through the CLI, local API and dashboard.

Acceptance covers exact samples, known interpolation, endpoint distinction, shared bracket endpoints, duplicate/repeated distances, overlapping segments, discontinuities, frame wrap, missing Motion, provenance mismatch and source limits. Melbourne invalid attempts and Shanghai Practice 3/2 can validate observed probes and explicit support gaps; preview thinning must not change the result. No new capture is required. Clean eligible Time Trial evidence and independently approved region geometry remain prerequisites for positive coaching acceptance.

## Decision 0036: project stored trajectories against explicit geometry diagnostically

**Status:** accepted

**Date:** 2026-10-04

Connect one checksummed stored attempt to one explicitly selected versioned geometry artifact through a read-only service and CLI export. Keep the artifact separate from distance-region models. Project original source positions before rendering reduction, retain frame/time/game-distance/XYZ anchors on rendered observations, preserve source discontinuities and unsupported geometry spans as separate runs, and report full-source support and reason counts. Bound trace reads to 64 MiB, 100,000 rows, 1,024 context segments and 4 MiB of context; bound geometry to its existing file, segment and anchor limits; reserve no more than 4 million estimated segment/anchor lookups; and emit at most 2,000 points in 256 continuous runs. Reject excess fragmentation instead of joining paths.

The canonical session context does not carry a layout ID. Require the caller to declare it and verify that declaration against the geometry artifact, labelling the source `caller_declared`. Require known, stable packet format, track ID and lap length; accept the established 1 m length tolerance and do not rescale distances. Missing or changing identity, unsupported source spans and geometry gaps make affected evidence unavailable. Preflight every bounded geometry anchor bracket; if any has a degenerate or non-finite horizontal tangent, make the artifact unavailable before emitting a path, even if source samples skip that interval. Include attempt/run/trace checksum/schema and geometry checksum/revision/role/provenance plus independent geometry and calibration review records.

This export is diagnostic for Time Trial, Practice, Qualifying, Race, invalid, partial and abandoned attempts. Describe signed values as residuals against the selected artifact; an observed reference path does not become a reviewed centreline. Set `coaching_eligible` and physical verification to false for every result. Defer paired projected comparison, apex detection, diagnosis, reference-policy changes, API/dashboard geometry selection, and geometry activation until their independent evidence gates are met. No storage migration is needed.

Acceptance uses synthetic straight and curved artifacts through persisted checksummed traces, including known signed residuals, nonzero origin, endpoints, unsupported seams/gaps, skipped unusable anchor brackets, frame wrap, missing Motion and legacy schemas, unstable/mismatched context, layout assertion mismatch, source/work/output limits, continuity-preserving thinning and atomic CLI publication. Existing Melbourne and Shanghai data validate only their recorded diagnostic or unavailable cases; no real geometry is promoted. Physical projection validation still requires independently reviewed geometry and game-distance calibration, while clean eligible Time Trial evidence remains a separate coaching gate.

## Decision 0037: summarize supported control observations within explicit distance windows

**Status:** accepted

**Date:** 2026-10-04

Build a separate, versioned `distance_window_brief` from the existing in-memory comparison result. Do not add persistence, trace reads, geometry generation, or an LLM dependency. Keep the existing comparison and corner briefs unchanged, and expose the same bounded result in CLI JSON, the local compare API, and the dashboard.

The brief may contain at most five facts: connected interval-time change; observed minimum-speed difference; peak recorded brake-input difference in percentage points; sustained 10% brake-onset bracket difference; and sustained 50% throttle-onset bracket difference. Minimum speed and peak brake require complete requested-window channel coverage on both attempts plus valid source frame, time, and distance anchors. Threshold-onset differences require complete channel coverage and exactly one supported sustained event per side, with no truncation, left censoring, unsupported breaks, or rejected short episodes. Right-censored continuation can support an onset only when that censoring is disclosed. Compute onset-difference intervals from the two sampled distance brackets; never turn a bracket into an exact pedal event location.

Retain game-validity, lifecycle, capture-completion, acquisition-loss, and frame-admission warnings. Unsupported facts are omitted with bounded reason codes. Preserve the existing Time Trial and same-run/session/player Practice/Qualifying gates; Race and unknown comparison modes remain unavailable. The brief stays descriptive and diagnostic, sets `coaching_eligible` to false, and makes no corner, causal, recoverable-time, or driving-recommendation claim. No new capture is required for this diagnostic increment.

Acceptance covers known signed differences, overlapping onset brackets, censoring, multiple episodes, gaps/rewinds, threshold boundaries, incomplete coverage, invalid source anchors, incomplete captures, attempt validity and lifecycle warnings, mode/pair rejection, output caps, provenance, and CLI/API/dashboard parity. The recovered Melbourne and Shanghai attempts should emit only supported facts, retain their diagnostic warnings, and omit unsupported interval deltas.

## Decision 0038: inspect linked distance grids in comparison charts

**Status:** accepted

**Date:** 2026-10-04

Make the existing speed, lap-delta, and driver-input charts share one cursor over the existing resampled comparison distance grid. Pointer selection maps to the nearest existing point, with exact ties selecting the lower-distance point; keyboard arrows, Home, and End move across the same grid. The readout reports the selected distance, requested-versus-selected distance when they differ, source channel availability, and target-minus-reference values with explicit units. It reads original resampled values and masks without additional interpolation or an added analysis pass.

Keep cursor selection and zoom in a bounded client component. Local zoom only changes the plotted view; changing the comparison-analysis window remains an explicit submission through the existing window controls. Render at most 2,000 sampled points and 256 separate supported runs per series, preserving run breaks before thinning and reporting omitted runs or grid points. Cursor lookup remains against the original grid. If the grid is over 100,000 points, is not finite and strictly increasing, or does not align with each series and mask, show the linked inspector as unavailable.

The existing comparison policy, attempt and trace provenance, validity, lifecycle and capture warnings remain authoritative and unchanged. Race and unknown comparison modes remain unsupported under the existing comparison gates. This is measurement inspection only; it does not change API or CLI output, resampling, the comparison window, eligibility, or coaching claims.

Acceptance covers nearest-point selection and lower-distance ties, grid endpoints, pointer and keyboard use, drag and button zoom/reset including expansion from a two-point view, missing channel values and masks, units, delta signs and millisecond display precision, continuity across rendering limits, original-grid cursor values after thinning, current Melbourne and Shanghai diagnostic warnings, and unchanged API/CLI output. No new capture is required. Clean eligible Time Trial evidence and approved geometry remain prerequisites for any future real coaching.

## Decision 0039: inspect bounded observation sets across selected laps

**Status:** accepted

**Date:** 2026-10-04

Add a bounded, versioned observation set for 2–8 explicitly selected attempts in one numeric distance window. Reuse the single-attempt evaluator from `comparison-distance-window-v1`; do not run repeated pairwise comparisons or select a personal-best reference. Every selected attempt must share one completed processing run, game session and player, and pass the existing stable-context rules for Time Trial or same-run Practice/Qualifying. Race, unknown and incompatible contexts remain unavailable.

Return a diagnostic row for each selected attempt with source/checksum provenance, observed minimum speed, peak brake input, channel coverage, brake and throttle onset brackets, game validity, lifecycle status and capture/replay warnings. Invalid game laps may contribute supported scalar observations with explicit warnings. Superseded, lifecycle-unassessed and non-completed attempts stay visible but are excluded from aggregate ranges; Practice/Qualifying contributors also need a positive official time, observed start and no pit encounter. For each scalar metric, aggregate only fully covered, source-anchored measurements from eligible attempts, report metric-specific contributor counts and exclusion reasons, and require at least two contributors before reporting minimum, maximum and range. Preserve pedal onsets as per-attempt distance brackets; do not average bracket midpoints or claim exact event locations.

Enforce request-wide caps before reading the selected traces: 64 MiB trace bytes, 100,000 source rows, 100,000 aggregate resampling-grid points, 1,024 context segments, 4 MiB context bytes and eight output rows. Load/checksum traces sequentially and retain only bounded summaries. Expose one shared report through the CLI, read-only local API and dashboard selection controls. Add no persistence, migration, corner interpretation, consistency score, confidence estimate, coaching, or clean-capture gate. A finalized but incomplete capture remains visible with a warning; unsupported measurements remain excluded on their own support evidence.

Acceptance covers deterministic attempt ordering, known scalar ranges and metric-specific contributor counts; duplicate/missing keys, incompatible run/session/player/context, unsupported modes and distance bounds; partial, superseded and lifecycle-unassessed rows; invalid-lap warnings; missing channels, incomplete coverage, invalid anchors, multiple or censored events, gaps and rewinds; legacy trace schemas, checksum failures and cumulative resource limits; and CLI/API/dashboard parity. Melbourne's invalid completed laps can retain diagnostic measurements while the partial third attempt cannot contribute. Shanghai's Practice observations preserve incomplete-capture and uncontrolled-condition warnings, while unsupported window metrics remain unavailable. No new capture is required.

## Decision 0040: inspect standalone regions with configured local draft models

**Status:** accepted

**Date:** 2026-10-04

Load at most 16 flat local JSON models from a server-configured directory into an immutable startup catalog. Count at most 128 directory entries and read no model file larger than 1 MiB. Resolve requests only through `(model_id, revision)`; never accept paths from API requests. Local models must remain `draft`, cannot shadow any packaged model ID, and cannot duplicate an existing model identity. Record origin, filename, exact-source-byte SHA-256, revision, and the model's caller-declared layout identity. The canonical session packet has no layout field, so label the layout `caller_declared` rather than verified.

Use local drafts only for a standalone attempt's diagnostic distance-region report. Preserve the packaged-only resolver for paired comparison and candidate ranking; local model metadata cannot approve ranking or coaching. Keep the existing paired comparison policy unchanged. A compatible, stable known Time Trial or Practice/Qualifying context may be inspected; Race, unknown, changing, partial-context, and track-incompatible modes remain unavailable. Invalid, partial, abandoned, superseded, or lifecycle-unassessed attempts remain inspectable when their context is supported, with source and capture/replay warnings. Practice/Qualifying reports disclose uncontrolled fuel, tyre, traffic, and cooldown conditions. This does not validate the windows as real corners and adds no geometry or coaching claim.

Apply explicit weighted-work limits before expensive operations. For resampling, with `C` channels, `N` source rows, `G` grid points, `H` conservatively counted adjacent valid hard-block pairs, `B = min(G, ceil(max_bracket_distance_m / grid_step_m) + 1)`, and `S = (C + 4) * N + C * G`, estimate `C * (G * (H + 1) + N * (B + 2)) + S * ceil(log2(S + 1))`; reject above 16,000,000 units before resampling allocation. Preflight source row/byte/context, grid, and region counts from stored metadata where possible; decode only within existing trace caps before calculating `H`. After resampling, with `R` regions and actual `E` excluded spans, estimate `R * (40 * N + 16 * (G + E) * ceil(log2(max(G, E) + 1)))`; reject above 32,000,000 units before evaluating events or positions. Report policy versions, estimates, and limits. These conservative work estimates bound predictable operation counts; they are not wall-time guarantees.

Expose the same bounded standalone report through API, CLI, and dashboard. Include checksummed source trace, model provenance, mode, layout-declaration status, lifecycle, capture completion, acquisition loss, replay exclusions, and resource-policy evidence. Missing Motion remains explicit in position evidence. Do not make a fresh capture a prerequisite: current stored Time Trial and Practice data can validate this diagnostic scope. No database migration, model editor, automatic corner discovery, centerline inference, personal-best policy, paired local-model comparison, Race analysis, or coaching is added.

Acceptance covers local catalog root traversal, oversize/count caps, packaged shadowing and duplicate identities, draft-only enforcement and exact content provenance; packaged-only ranking and comparison resolution; stable Time Trial and Practice/Qualifying plus Race/unknown/context-change/incompatible identity cases; invalid, partial, lifecycle-unassessed and superseded attempts; missing Motion, capture warnings and replay counters; half-open regions, gaps, censoring and overlap rules; exact-limit acceptance and one-unit-over rejection before the appropriate evaluator; fragmented traces with high `E`; pre-resampling rejection for rewinds, duplicate distances and fine grids; six-region Melbourne and Shanghai observations; and API/CLI/dashboard parity. No fresh capture is required.

## Decision 0041: compare configured distance regions diagnostically

**Status:** accepted

**Date:** 2026-10-04

Add a separate, read-only paired report for two explicitly selected completed attempts and one explicitly selected packaged or configured local draft distance-region model. Require one completed processing run, session UID and player car index; stable compatible Time Trial or Practice/Qualifying context; and a model compatible with packet format, track ID/name and lap length within the established 1 m tolerance. Practice/Qualifying retains the positive reported lap time, observed start and no-pit requirements. Race, unknown or changing context, partial/abandoned attempts and incompatible scope abstain. Invalid, superseded, lifecycle-unassessed and incomplete-capture attempts remain diagnostic with warnings. Layout identity remains `caller_declared`; distances are not rescaled.

Decode and resample each trace once onto a shared origin-anchored full-track grid, then calculate one shared masked delta. Reuse `analyze_corner_regions`, existing event detectors, coverage helpers and the connected interval evaluator. Apply the model distance origin to region and search-window bounds; sample `[start, end)` while evaluating both time-delta boundaries. Preserve independently supported entry/exit deltas and event censoring/counts. Minimum-speed differences require complete speed coverage and valid source anchors on both attempts. Braking and 50% throttle onset comparisons use source-bracket difference intervals only when both channels have full coverage, exactly one sustained event per side, no event truncation, no left censoring, rejected short episodes or unsupported breaks; disclose right-censored continuation. Exit-speed differences require a configured exit anchor and supported channel-aware samples at the same distance on both sides. Missing search windows remain `unconfigured`. Never expose the older scalar sampled-onset subtraction as an exact onset difference.

The report is versioned as `paired_distance_region_observations` schema 1. It carries pair and model provenance, exact model-byte digest and caller-declared layout status, source trace checksums/schemas, validity/lifecycle/capture/replay evidence, per-region observations, coverage, independent boundaries, supported differences, warnings, and resource estimates. Every result and region is `diagnostic_only=true`, `coaching_eligible=false`, and `ranking_eligible=false`; call them distance regions, do not sort by largest loss, and do not invoke reference selection or ranking.

Keep the existing `/compare/laps` packaged-only resolver, D0032 ranking approval and D0033 corner brief authority unchanged. This decision supersedes D0040's prohibition on local paired inspection only for this new endpoint/report. It does not authorize local models for ordinary lap comparison, reference selection, candidate ranking or coaching.

Expose the same report through `GET /api/v1/compare/regions`, CLI `compare-regions`, and explicit dashboard paired-region cards. Resolve model identity by startup-catalog ID/revision; never accept request paths. If the report is unavailable, ordinary lap comparison and standalone inspection remain available. Add no persistence, migrations, model editing, geometry, official corner labels, automatic reference selection, personal/theoretical best, rankings, causality, advice or Race comparison.

Enforce request-wide aggregate limits of 64 MiB trace bytes, 100,000 trace rows, 100,000 grid points across both resampled traces, 1,024 context segments, 4 MiB context, 64 regions and the existing 20 event examples per threshold/attempt/region. Preflight SQLite estimates before trace reads; load and validate the target against its row, byte and context reservation before reading the reference, then cap that read by its reservation and remaining aggregate limits. Verify loaded row count, attempt identity, checksum, schema and context against the preflight. Reserve resampling using the D0040 estimate per side with the combined total capped at 16,000,000 units. After bounded decode, recompute both estimates with observed hard blocks and reject before resampling if over the cap. Before region evaluators, reserve D0031's 4,000,000 interval-work limit and the paired-region work cap of 32,000,000 units, using:

```text
L = ceil(log2(max(Gt, Gr, Gdelta, Et + Er) + 1))

region_work =
    R * [40 * (Nt + Nr)
         + 16 * (Gt + Gr + Gdelta + Et + Er) * L]
```

Reject over-limit requests without partial output. Any additional source scans introduced by implementation must be included in these reservations.

Acceptance covers synthetic Shanghai Practice 3/2 numeric-window parity and incomplete-capture/lifecycle warnings; the recovered Shanghai Practice 3/2 pair's bounded abstention before resampling when observed hard blocks exceed the combined 16,000,000-unit budget; Melbourne parity with existing standalone region calculations; preserved model search-window semantics; positive and unsupported intervals, sub-grid boundaries, gaps/rewinds/unsupported tails, ambiguous and censored events, exit support, missing channels and legacy schemas; pair/model/context/checksum drift; partial, Race and unknown-mode abstention; aggregate and exact-limit resource checks before evaluators; and CLI/API/dashboard evidence parity. Local models remain absent from ranking authority, and existing comparisons, references and briefs remain unchanged. No fresh capture is required. Clean eligible Time Trial evidence and independently reviewed region definitions remain prerequisites for real ranked coaching.

D0042 supersedes the capture-specific resampling-work abstention above; validate that Shanghai pair under the indexed estimator and the unchanged downstream analysis-work cap instead.

## Decision 0042: index hard-block masks during resampling

**Status:** accepted

**Date:** 2026-10-04

Replace the shared resampler's repeated scan of every hard-block span for every channel/grid point with indexed grid-range masks. For each span, map the existing inclusive interval `[start - epsilon, end + epsilon]` onto the sorted grid with `bisect_left` and `bisect_right`, apply range updates to a difference array, then use a prefix sum to build global and channel-specific masks. Keep the current hard-block reasons and channel matching rules.

Preserve resampled values, masks, coverage, excluded-span reasons/order, ambiguity handling, interpolation, counters and downstream measurement semantics exactly. The additional work and memory stay bounded by the existing source/grid caps and `O(C × G + H)` mask storage, where `C` is channel count, `G` grid points, and `H` hard blocks. Do not change source, context, grid, event or region limits.

Move the shared estimator beside the resampler and version it as `indexed-hard-block-resampling-preflight-v2`. With source rows `N`, grid points `G`, hard blocks `H`, `B = min(G, ceil(max_bracket_distance_m / grid_step_m) + 1)`, `S = (C + 4) × N + C × G`, and `L = ceil(log2(max(S, G, H, 1) + 1))`, reserve:

```text
W = C × [N × (B + 2) + 4 × G + H × (2 × L + 4)] + S × L
```

Standalone region analysis still caps one estimate at 16,000,000 units; paired region analysis still caps the sum of both estimates at 16,000,000 units. Retain the metadata-only preflight with `H=0`, then recompute with observed hard blocks after bounded decode and before resampling. This decision supersedes only D0040 and D0041's resampling implementation and work-estimator formula; their diagnostics, eligibility gates and downstream work limits remain unchanged.

Acceptance compares full resampler outputs against pre-change Melbourne and Shanghai capture baselines and checks differential membership against the original interval rule, including overlapping/nested and zero-width blocks, channel-specific blocks, epsilon endpoints, rewinds, duplicate distances, missing channels and ambiguous segments. Verify exact-limit acceptance and over-limit rejection before resampling, plus bounded work on highly fragmented traces. Run Shanghai Practice attempts 3 and 2 through the paired report with an explicitly selected region model that fits the separate 32,000,000-unit region-analysis limit, and retain incomplete-capture and lifecycle warnings. No database migration or new capture is required. This optimization does not enable Race comparison, ranking, coaching, geometry activation or claims of validated corners.

## Decision 0043: add a diagnostic region debrief

**Status:** accepted

**Date:** 2026-10-04

Add a deterministic, read-only debrief to each D0041 paired distance-region result, built only from that region's existing `supported_differences` and configured exit distance. Do not read traces again, interpolate, reanalyze, select references, or add a separate API route or persistence. Preserve D0041's same-run/session/player and stable Time Trial or Practice/Qualifying gates, plus all source/model provenance and capture, validity, replay, and lifecycle warnings. Race and unknown contexts remain unavailable.

For each region, consider at most five measurements in fixed order: connected-interval time change, observed minimum-speed difference, 10% brake-onset bracket, 50% throttle-onset bracket, and speed at the configured exit. Emit a fact only when the corresponding difference is already supported; otherwise return an explicit omission reason. Preserve onset bracket bounds, zero-straddling differences, and right-censored continuation. Bound reports to 64 regions, five facts and five omissions per region, and 240 characters per generated text. Do not rank regions, assign confidence, infer a cause or physical corner, or recommend a driving change.

Version the nested object as `diagnostic-region-debrief-v1` with schema version 1. Keep the parent report at schema version 1 and `paired-distance-region-observations-v1` because its existing measurements and meanings do not change. The field is additive: older consumers may ignore it, and updated consumers must tolerate its absence. API and CLI keep returning the same paired report; the dashboard renders the available facts and omissions while retaining detailed evidence.

The debrief remains diagnostic, coaching-ineligible, and ranking-ineligible, including for validated models. It does not inherit D0033's ranked-region brief gates or authority and does not change existing ranking or reference selection. Acceptance covers connected and disconnected timing, complete and incomplete minimum-speed coverage, ambiguous/left-censored and right-censored events, onset intervals that straddle zero, missing/unconfigured exit evidence, fixed fact order, output bounds, unchanged source-read counts, API/CLI response parity, and dashboard handling of absent or present debriefs. Validate Shanghai Practice attempts 3/2 with D0042's indexed resampling estimator; preserve its incomplete-capture and lifecycle warnings. Clean eligible Time Trial evidence and independently reviewed region definitions remain prerequisites for any future actionable coaching.

## Decision 0044: monitor live Car Status independently

**Status:** accepted

**Date:** 2026-10-04

Extend D0015's recording-time monitor with a separately qualified player Car Status group, reusing the existing F1 25 and 2026 Car Status decoders and D0018 canonical field validation. Expose reported fuel quantity, game-reported remaining laps, actual and visual tyre compound codes, tyre age, front brake bias, and pit-limiter state through the existing recording-status API and dashboard. Keep the fuel quantity unit unspecified and make no consumption forecast.

Join Car Status only to valid player Lap Data from the same admitted session, packet format, overall frame, and header-designated player index. Do not carry values between frames. Keep missing, malformed, unsupported, mismatched, conflicting, and individually invalid fields explicit; invalid fields remain null. The group has its own `waiting`, `fresh`, `stale`, `unsupported`, and `unavailable` state and 500 ms freshness limit. Measure freshness from the oldest selected Lap Data or Car Status receive timestamp and require provenance for all selected packets. Reset it across session, format, player, and rewind boundaries. Keep only one bounded snapshot and never let absent Status weaken the existing speed/control monitor. API/UI consumers tolerate the optional group's absence.

Use the existing capture, frame assembly, observer, status response, and same-origin dashboard proxy. Raw datagram persistence remains first and authoritative. Add no endpoint, storage migration, trace/importer change, or data history. The monitor is available across Time Trial, Practice/Qualifying, Race, and unknown contexts without changing reference or coaching policy.

Acceptance covers F1 25 and 2026 exact-frame player joins against capture/import evidence; Time Trial, Practice/Qualifying, Race, and unknown modes; missing, malformed, unsupported, mismatched, conflicting, delayed, duplicate, and individually invalid Status packets; receive-time eviction; frame wrap; and session, format, player, and rewind resets. Verify that speed/control remains available without Status, Status remains available without Car Telemetry, each group expires independently, memory stays bounded, optional API/UI fields are tolerated, raw payload order is unchanged, and stopping clears live state. No new capture is required. This is live diagnostic visibility only; fuel/tyre strategy, consumption estimates, comparisons, and coaching remain out of scope.

## Decision 0045: expose live reported lap timing independently

**Status:** accepted

**Date:** 2026-10-04

Extend the bounded app-managed acquisition monitor with a separate player Lap Data timing group. Reuse the existing F1 25 and 2026 Season Pack Lap Data v1 decoders and accepted assembled frames. Show current lap number and current-lap clock, current sector, game-reported previous-lap time, and reported sector-one and sector-two times. Convert the documented sector code `0/1/2` to display sector `1/2/3`; the EA F1 25 v3 and Season Pack Season 8 v1.2 specifications use this mapping. Zero timing values and lap number zero remain unavailable. Label the previous-lap value as game-reported; do not infer its validity from current-lap validity.

Update only from valid selected-player Lap Data and require receive-time provenance for that packet. Keep one snapshot with session, frame, wire-format, and player provenance and its own `waiting`, `fresh`, `stale`, `unsupported`, or `unavailable` state and 500 ms freshness limit. It remains usable without Car Telemetry or Car Status and expires independently. Clear prior values on malformed/unsupported selected-player Lap Data and across player, session, format, or rewind boundaries. Keep modes independent and do not infer completion, sector three, live delta, a personal best, or advice.

Expose the optional group through the existing recording-status API and dashboard. Add no route, persistence, migration, history, or capture changes. Imported attempts and Session History remain authoritative for historical timing association, validity, and eligibility.

Acceptance covers F1 25 and 2026 synthetic layouts; Time Trial, Practice/Qualifying, Race, and unknown contexts; lap and sector transitions; zero/missing values; malformed and unsupported packets; duplicates and late frames; frame wrap; timestamp eviction; pause freshness; player/session/format/rewind resets; independence from telemetry and Status; optional API/UI compatibility; bounded memory; and hiding live data after Stop. Replay Melbourne and Shanghai against selected admitted Lap Data records. No new capture is required; real 2026 validation remains pending.

## Decision 0046: replay catalog captures into the live diagnostic monitors

**Status:** accepted
**Date:** 2026-10-04

Expose app-managed diagnostic replay for one capture selected by its opaque catalog capture ID. Reuse `ReplaySource` and the bounded acquisition observer to drive the existing player telemetry, Car Status, and Lap Data timing monitors in the dashboard. The local API accepts only catalog IDs, resolves them beneath the startup-configured recordings root, and revalidates file identity before and after reading. Staging files and arbitrary paths are never replayable. Replay does not emit UDP packets, create a capture, import data, or modify telemetry, analysis, or database records.

Keep playback control ephemeral and non-resumable: `starting → playing → stopping → stopped`, or `completed` / `failed`. Support only speeds `0.5`, `1`, `2`, and `4`, fixed for the life of a playback. An active playback can be stopped promptly, including while waiting for the next paced datagram. A repeated Start for the same capture and speed returns the active playback; a different request is busy. On API restart no playback resumes. Capture footer completion remains separate evidence: exhausting a readable capture can complete playback while exposing `capture_complete=false` and its original footer status.

Keep three clocks separate. Preserve each source datagram's capture timestamp, monotonic source timestamp, sequence, and payload unchanged. `ReplaySource` schedules delivery using capture-relative monotonic intervals divided by the fixed playback speed. The observer receives a separate runtime delivery-monotonic timestamp for its 500 ms freshness calculations; its joined fields use the oldest selected packet's delivery timestamp. Game frame IDs and session-time evidence continue to control packet ordering and rewind resets. Replay freshness describes playback delivery recency and says nothing about original capture recency or quality.

Serialize replay with import and UDP recording through the existing local operation reservation. Reserve before dispatch and release only after the replay reader and worker have closed. Expose authenticated Start/Stop and read-only current status through the local API and same-origin dashboard proxy. Identify every snapshot with `source_kind=replay`, playback ID, capture ID, and immutable speed. Do not add a database migration or claim checksum verification for the replayed source.

Acceptance covers Melbourne and the recovered Shanghai capture through the dashboard, preserving invalid/incomplete source evidence; synthetic F1 25/2026 and Time Trial/Practice-Qualifying/Race/unknown contexts; malformed/unsupported, duplicate, late, rewind, and session transitions; every permitted speed and the separate source/delivery clocks; stale expiration from delivered packets; source identity changes and truncated input; prompt Stop during pacing; EOF/error cleanup; repeated commands, concurrency reservations, restart behavior, and bounded monitor state. Replay remains diagnostic and does not add comparison, reference selection, live analysis, corner findings, confidence, or coaching.

## Data flow

```text
UDPSource / ReplaySource
          ↓
      RawDatagram ───── ReplaySource deliveries carry a separate delivery clock
       ↙       ↘
  Capture     Header decoder
                  ↓
          DecodedPacket envelope
           ↙                 ↘
SessionContextDecoder     FrameAssembler
           ↓                    ↓
SessionContextTimeline     ┌─────┴───────────┐
                           ↓                 ↓
                    LapDataDecoder   SessionHistoryDecoder
                           ↓                 ↓
                       LapTracker ───────┐   │
                                         ↓   ↓
                              bounded timing association → SQLite
                  CarTelemetry / Motion → Player samples → Parquet
                       Lap attempts and context ─────────→ SQLite
```

Capture precedes decoding so every datagram successfully persisted survives parser errors and future decoder changes. A single ordered writer thread keeps disk I/O off the UDP receive loop. The bounded receive queue reports overflow, and the capture footer records orderly completion and drop counts; `status=complete` means the file was finalized, not that the UDP socket dropped no packets. The frame assembler emits frames after a configurable three-frame reorder window and is bounded by frame keys, packet count, and tracked session watermarks. Emission stays in increasing overall-frame order within each session. Under frame-capacity pressure, it drops an incoming older frame if admitting it would require emitting a newer frame first. It flushes pending frames when a session ends or a session watermark is evicted, and can flush one session without retiring it at a packet-format boundary. It suppresses byte-identical envelopes, retains different updates with the same packet ID, and ignores packets beyond the watermark or in its bounded recently-closed history. Packet families that arrive at slower rates may appear in separate frames and are not treated as missing merely because they did not arrive with motion packets.

## Contracts

- Wire values and packet identifiers stay in `f1_engineer.udp`.
- Frozen dataclasses carry packet headers, capture records, and assembled frames.
- Both live input and replay produce `RawDatagram` values and use `TelemetryPipeline`.
- Known Session packet versions produce canonical `SessionContext`; unsupported variants remain raw and are reported as unavailable context. Attempts snapshot context changes with frame provenance.
- Event v1 and Session History v1 are dispatched by wire format, packet ID, and packet version. Event lifecycle evidence follows admitted-frame ordering; reported lap/sector timing is persisted as a separate non-authoritative evidence record and never changes attempt validity or reference eligibility.
- F1 25 Lap Data v1 packets produce immutable records for all 22 cars; 2026 Season Pack Lap Data v1 produces all 24 records. The lifecycle inventory follows only the header-designated player and preserves invalid, partial, and abandoned attempts.
- F1 25 and 2026 Season Pack Car Telemetry v1 preserve their documented wire records (22 and 24 cars respectively); Participants v1 decodes 22 F1 25 or 24 2026 records. The 2026 adapter preserves widened driver, network, and team IDs. Assembled-frame synchronization joins supported player Car Telemetry to the player's Lap Data by session UID, frame, and player car index; missing telemetry remains explicitly unavailable in the canonical sample.
- Motion v1 decodes all 22 F1 25 or 24 2026 packed car records. The 2026 adapter converts quantized signed G-force values to g units. The primary player's world position, velocity, forward/right directions, G-forces, and orientation join to Lap Data by assembled session/frame and the header-designated car index. Missing Motion stays null and is never carried forward. MotionEx remains opaque.
- Completed and partial attempts, context history, participant snapshots, and canonical samples are persisted to SQLite and checksummed Parquet traces. New traces use schema v3; readers preserve compatibility with schemas v1 and v2 and expose unavailable Motion or Car Status fields as null. Imports stream bounded row groups and publish them atomically.
- An observed-trajectory export preserves source attempt/run/checksum/context, units, frame/distance/time anchors, quality, and discontinuity segments. It is a diagnostic driven path and is never identified as a track centreline. The dashboard API reuses this analysis and returns a source-limited, point-bounded preview without changing source coverage or continuity evidence.
- Selected draft distance regions can be linked to a source-bounded position summary only when region and trajectory reports match on attempt, run, session, player, trace checksum and schema. Full-source position counts are separate from retained preview points; anchors are bounded and continuity fragments remain separate.
- Geometry artifacts and projection use a separate schema from distance-region `TrackModel`. Projection retains normalized game lap distance, explicit supported segments and independent geometry/calibration evidence; observed paths remain diagnostic and no real centreline is currently activated.
- `project-trajectory` projects one checksummed attempt against a caller-selected geometry artifact for bounded diagnostics. It preserves source discontinuities and unsupported spans, and all modes remain ineligible for coaching through this command.
- `distance_window_brief` derives at most five measured facts from the already computed selected-window comparison. Complete coverage, source anchors and event uncertainty remain explicit; all results are diagnostic and coaching-ineligible.
- `diagnostic-region-debrief-v1` derives at most five deterministic facts from existing paired-region supported differences; omissions remain explicit, and the additive nested report is diagnostic, coaching-ineligible, and ranking-ineligible.
- `observation-set` reuses the single-attempt selected-window evaluator across at most eight explicitly selected same-run/session/player attempts. Scalar ranges require two fully supported, lifecycle-assessed completed attempts; onset evidence remains bracketed per lap, and every result is diagnostic and coaching-ineligible.
- Comparison speed, lap-delta, and driver-input charts share a bounded pointer and keyboard cursor over the original resampled comparison distance grid. Local chart zoom is presentation-only and preserves sampled continuity gaps; analysis, mode policy and comparison outputs are unchanged.
- Standalone attempt trace previews read the checksummed speed/control channels independently of comparison and reference eligibility. Session time remains the plot coordinate; frame/time gaps and missing channel values split runs, and deterministic point/run caps preserve discontinuities and report omitted data.
- Replay timing is based only on the monotonic intervals stored in the capture; maximum-speed replay skips sleeps.
- The capture format has a magic value and schema version. Unknown packet IDs remain inspectable.
- App-managed recording uses a server-generated staging file and the shared raw-first recorder. Stop finalizes and fsyncs the footer before no-replacement publication into the configured recordings root; import and recording operations are serialized by the controller.
- The app-managed acquisition observer retains separate bounded player telemetry, Car Status, and Lap Data timing snapshots. Recording status calculates each group's freshness from its selected monotonic receive-time provenance; history remains the importer's responsibility.

## Deferred decisions

- Remaining packet-body parsers are added from EA's official structure files, with their source and revision recorded. Support is explicit per `(packet_format, packet_id, packet_version)`. Current typed body support covers F1 25 and 2026 Season Pack Session, Lap Data, Event, Participants, Car Telemetry, Motion, Car Status, and Session History v1. Session packet adapters populate canonical gameplay context independently of the wire format.
- Add canonical traces for additional cars only when validated multi-car capture coverage justifies them. Slower packet families will use freshness windows rather than being required in every frame.
- Distance comparison, selected-window observation sets, run-scoped Time Trial reference selection, and the read-only local historical explorer are implemented for diagnostics. Standalone region observations support stable Time Trial and Practice/Qualifying contexts with packaged or configured local draft models, including invalid, partial, and abandoned attempts; Race/unknown policy, validated circuit geometry, and actionable coaching remain deferred until their supporting evidence and interfaces are ready.
- The local dashboard starts and stops mode-independent UDP capture and imports finalized `.f1ecap` files through durable jobs. It exposes bounded standalone player traces and an observed world-coordinate path, then links selected Time Trial distance regions to matching source positions for diagnostic inspection. Track calibration, line comparison, trajectory coaching, race reference policy, live analysis/coaching, and opponent coverage remain deferred.
- Voice, LLM, and frontend work remain above deterministic analysis; no LLM is needed to capture or inspect telemetry.
