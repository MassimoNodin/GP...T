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
SessionContextTimeline  LapDataDecoder  CarTelemetryDecoder
                         ↘             ↙
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
- Completed and partial attempts, context history, participant snapshots, and canonical player samples are persisted to SQLite and checksummed Parquet traces. Imports stream bounded row groups and publish them atomically.
- Replay timing is based only on the monotonic intervals stored in the capture; maximum-speed replay skips sleeps.
- The capture format has a magic value and schema version. Unknown packet IDs remain inspectable.

## Deferred decisions

- Remaining packet-body parsers are added from EA's official structure files, with their source and revision recorded. Support is explicit per `(packet_format, packet_id, packet_version)`. Current typed body support covers F1 25 Session, Lap Data, Participants, and Car Telemetry packet v1; Session packet adapters populate canonical gameplay context independently of the wire format.
- Add canonical traces for additional cars only when validated multi-car capture coverage justifies them. Slower packet families will use freshness windows rather than being required in every frame.
- Distance resampling, comparison metrics, and the API/frontend follow this durable player-trace slice.
- Voice, LLM, and frontend work remain above deterministic analysis; no LLM is needed to capture or inspect telemetry.
