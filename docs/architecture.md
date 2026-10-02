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

## Data flow

```text
UDPSource / ReplaySource
          ↓
      RawDatagram
       ↙       ↘
  Capture     Header decoder
                  ↓
          DecodedPacket envelope
           ↙                 ↘
SessionContextDecoder     FrameAssembler
           ↓             ↙           ↘
SessionContextTimeline  LapDataDecoder  CarTelemetryDecoder  MotionDecoder
                         ↘             ↓             ↙
                         Player samples
                              ↓
                       Parquet + SQLite
```

Capture precedes decoding so every datagram successfully persisted survives parser errors and future decoder changes. A single ordered writer thread keeps disk I/O off the UDP receive loop. The bounded receive queue reports overflow, and the capture footer records orderly completion and drop counts; `status=complete` means the file was finalized, not that the UDP socket dropped no packets. The frame assembler emits frames after a configurable three-frame reorder window and is bounded by frame keys, packet count, and tracked session watermarks. Emission stays in increasing overall-frame order within each session. Under frame-capacity pressure, it drops an incoming older frame if admitting it would require emitting a newer frame first. It flushes pending frames when a session ends or a session watermark is evicted, and can flush one session without retiring it at a packet-format boundary. It suppresses byte-identical envelopes, retains different updates with the same packet ID, and ignores packets beyond the watermark or in its bounded recently-closed history. Packet families that arrive at slower rates may appear in separate frames and are not treated as missing merely because they did not arrive with motion packets.

## Contracts

- Wire values and packet identifiers stay in `f1_engineer.udp`.
- Frozen dataclasses carry packet headers, capture records, and assembled frames.
- Both live input and replay produce `RawDatagram` values and use `TelemetryPipeline`.
- Known Session packet versions produce canonical `SessionContext`; unsupported variants remain raw and are reported as unavailable context. Attempts snapshot context changes with frame provenance.
- Known F1 25 Lap Data v1 packets produce immutable records for all 22 cars; the first lifecycle inventory follows only the header-designated player and preserves invalid, partial, and abandoned attempts.
- F1 25 Car Telemetry v1 and Participants v1 preserve all 22 wire records. Assembled-frame synchronization joins player Car Telemetry to the player's Lap Data by session UID, frame, and player car index; missing telemetry remains explicitly unavailable in the canonical sample.
- F1 25 Motion v1 decodes all 22 packed car records. The primary player's world position, velocity, forward/right directions, G-forces, and orientation join to Lap Data by assembled session/frame and the header-designated car index. Missing Motion stays null and is never carried forward. MotionEx remains opaque.
- Completed and partial attempts, context history, participant snapshots, and canonical samples are persisted to SQLite and checksummed Parquet traces. New traces use schema v2; readers preserve compatibility with schema v1 and expose its absent Motion fields as null. Imports stream bounded row groups and publish them atomically.
- An observed-trajectory export preserves source attempt/run/checksum/context, units, frame/distance/time anchors, quality, and discontinuity segments. It is a diagnostic driven path and is never identified as a track centreline.
- Replay timing is based only on the monotonic intervals stored in the capture; maximum-speed replay skips sleeps.
- The capture format has a magic value and schema version. Unknown packet IDs remain inspectable.

## Deferred decisions

- Remaining packet-body parsers are added from EA's official structure files, with their source and revision recorded. Support is explicit per `(packet_format, packet_id, packet_version)`. Current typed body support covers F1 25 Session, Lap Data, Participants, Car Telemetry, and Motion packet v1; Session packet adapters populate canonical gameplay context independently of the wire format.
- Add canonical traces for additional cars only when validated multi-car capture coverage justifies them. Slower packet families will use freshness windows rather than being required in every frame.
- Distance comparison, run-scoped Time Trial reference selection, and the read-only local historical explorer are implemented for diagnostics. Validated circuit geometry, race reference policy, and actionable coaching remain deferred until their supporting evidence and interfaces are ready.
- Voice, LLM, and frontend work remain above deterministic analysis; no LLM is needed to capture or inspect telemetry.
