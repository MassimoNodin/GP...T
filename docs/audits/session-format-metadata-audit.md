# Session-Format Metadata Audit for Live Race Engineer

**Branch**: `audit/session-format-metadata`  
**Baseline Commit SHA**: `f32ad3e31ae5ccd962ccc848beee3182fb04fb5b` (`origin/main`)  
**Status**: Ready for Review (PR targeting `main`)  
**Scope**: Bounded metadata audit and gap analysis across Practice, Qualifying variants, Race variants, and Time Trial.

---

## 1. Executive Summary & Audit Context

The live engineer assistant must adapt its focus, competitive targets, and tactical recommendations based on the current session format. Practice sessions demand an internal focus on player improvement, representative lap consistency, and setup evaluation, keeping opponent data suppressed unless explicitly queried. Qualifying demands stage-aware cutoff monitoring, best-valid-lap comparisons against immediate timing-sheet neighbours, remaining session time tracking, and strict adherence to format rules (e.g. forbidding repeat attempts in One-Shot qualifying). Race sessions demand classification-aware rival tracking, recent representative pace comparisons, traffic vs. clean air distinction, and race distance progress. Time Trial requires clean format discrimination and personal best/rival ghost reference selection without confusing session modes with career races.

This audit maps the end-to-end evidence path—from UDP wire packets and binary unpack layouts, through packet decoders, session context trackers, frame assembly, pipeline buffering, SQLite/Parquet persistence, and live observer snapshots—to assess the feasibility of session-aware engineer behaviour.

### Key Audit Findings

1. **Protocol Decoders vs Discarded Metadata**:
   - Packet 1 (Session) decodes `m_sessionTimeLeft` and `m_sessionDuration` from the wire prefix, but discards them as local variables (`_session_time_left`, `_session_duration`) in [`f1_engineer/udp/session_context.py:190-191`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/session_context.py#L190-L191). They are absent from `SessionContext` and live snapshots.
   - Packet 2 (Lap Data) decodes `m_carPosition` (classification/timing-sheet position) and `m_driverStatus` (in-garage, flying lap, in-lap, out-lap) for all cars in [`f1_engineer/udp/lap_data.py:166-178`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/lap_data.py#L166-L178), but [`AcquisitionObserver._publish_live_lap_timing`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/recording/service.py#L1914-L1941) discards them from live snapshots.
2. **Opponent History Dropped at Pipeline Boundary**:
   - Packet 11 (Session History) carries round-robin lap histories, sector times, and validity flags for every car on the grid. However, [`f1_engineer/pipeline.py:909`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/pipeline.py#L909) explicitly filters out non-player packets (`if packet.body[0] != packet.header.player_car_index: self.session_history_non_player_packets += 1; continue`). Opponent best valid times are therefore not retained in SQLite or buffered for live lookup.
3. **Qualifying Cutoff Thresholds Are External to UDP**:
   - The EA telemetry stream *never* transmits the cutoff position (e.g. P15 in Q1 or P10 in Q2) or the number of cars eliminated. Inferring cutoff thresholds solely from stage name (e.g. `qualifying_1`) is unsafe without knowing the active grid size (20 vs 22 vs 24 cars) and sporting regulations. External rules configuration is required.
4. **Session Lifecycle & Continuity Integrity**:
   - `SessionTracker` in [`f1_engineer/sessions/manager.py:47-100`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/sessions/manager.py#L47-L100) correctly anchors session identity to the 64-bit `session_uid`. Telemetry dropouts or brief packet silences do *not* trigger spurious session resets as long as `session_uid` remains constant. Session transitions, restarts, and flashbacks (`FLBK`) have distinct, well-isolated lifecycles.

---

## 2. Official Source References & Specification Standards

The UDP telemetry structures evaluated in this audit correspond to the official Codemasters / EA Sports specifications pinned in [`docs/telemetry-protocol.md`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/docs/telemetry-protocol.md):

* **EA UDP Forum Specification**: *EA SPORTS™ F1® 25 & 2026 Season Pack UDP Specification*, **Documentation Revision 11.0** (checked 2026-10-03).
* **F1 25 Documentation Reference**: *Data Output from F1 25 v3.pdf* (Document version 3.0).
* **2026 Season Pack Text Specification**: `2026 Season Pack Telemetry Output Structures (1).txt` (SHA-256: `4f97867924f5f13f11b7fde6eb84ccb3aefcaf69062424bc077e387090647c72`).
* **2026 Season Pack PDF Reference**: `Data Output from F1 25 2026 Season Pack (Season 8).pdf`, **Document Version 1.2** (SHA-256: `3f38858c3ca65b2faa55a90a35277dd2767bb9cea2911e741b61b370a39365ae`).

> [!IMPORTANT]
> **Documentation Revision vs. Packet Version**:  
> While the official EA documentation is published under external revisions **11.0**, **v3**, or **v1.2**, the internal packet header field `m_packetVersion` (`PacketHeader.packet_version`) remains **`1`** for both F1 25 (`packetFormat = 2025`) and 2026 Season Pack (`packetFormat = 2026`) across all supported packet IDs. Do not confuse the game documentation version with the wire `packet_version`.

### Supported Packet IDs & Sizes

| Packet ID | Packet Name | F1 25 Header + Body (Bytes) | 2026 Season Pack Header + Body (Bytes) | Header `packet_version` | Decoder Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **0** | Motion | 29 + 1320 = **1349** | 29 + 1440 = **1469** | 1 | Decoded ([`f1_engineer/udp/motion.py`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/motion.py)) |
| **1** | Session | 29 + 724 = **753** | 29 + 897 = **926** | 1 | Decoded ([`f1_engineer/udp/session_context.py`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/session_context.py)) |
| **2** | Lap Data | 29 + 1256 = **1285** (22 cars) | 29 + 1370 = **1399** (24 cars) | 1 | Decoded ([`f1_engineer/udp/lap_data.py`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/lap_data.py)) |
| **3** | Event | 29 + 16 = **45** | 29 + 16 = **45** | 1 | Decoded ([`f1_engineer/udp/events.py`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/events.py)) |
| **4** | Participants | 29 + 1255 = **1284** (22 cars) | 29 + 1441 = **1470** (24 cars) | 1 | Decoded ([`f1_engineer/udp/participants.py`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/participants.py)) |
| **5** | Car Setups | 29 + 1100 = **1129** (22 cars) | 29 + 1200 = **1229** (24 cars) | 1 | Decoded ([`f1_engineer/udp/car_setups.py`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/car_setups.py)) |
| **6** | Car Telemetry | 29 + 1348 = **1377** (22 cars) | 29 + 1470 = **1499** (24 cars) | 1 | Decoded ([`f1_engineer/udp/car_telemetry.py`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/car_telemetry.py)) |
| **7** | Car Status | 29 + 1210 = **1239** (22 cars) | 29 + 1320 = **1349** (24 cars) | 1 | Decoded ([`f1_engineer/udp/car_status.py`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/car_status.py)) |
| **8** | Final Classification | 29 + 1012 = **1041** | 29 + 1104 = **1133** | 1 | Recognized ID only; raw body |
| **10** | Car Damage | 29 + 990 = **1019** (22 cars) | 29 + 1080 = **1109** (24 cars) | 1 | Decoded ([`f1_engineer/udp/car_damage.py`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/car_damage.py)) |
| **11** | Session History | 29 + 1431 = **1460** | 29 + 1431 = **1460** | 1 | Decoded ([`f1_engineer/udp/session_history.py`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/session_history.py)) |
| **14** | Time Trial | 29 + 108 = **137** | 29 + 108 = **137** | 1 | Recognized ID only; raw body |
| **15** | Lap Positions | Present in both formats | Present in both formats | 1 | Recognized ID only; raw body |
| **16** | Car Telemetry 2 | N/A (F1 25 returns `None`) | 29 + 864 = **893** (24 cars) | 1 | Recognized ID only; raw body |

---

## 3. Per-Format Capability Matrix

The table below details every session format defined in [`f1_engineer/sessions/context.py`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/sessions/context.py), specifying the required engineer operational behaviour, automatic rival policy, metric selection, cutoff feasibility, and wire vs storage availability.

| Session Type ID & Enum Name | Category | Knockout Stage | One-Shot? | Automatic Rival Policy | Comparison Metric | Cutoff / Elimination Support | Repeat Attempt Feasibility | Wire Evidence Available? | Stored / Live Available? |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `0`: `unknown` | Unknown | None | No | None (suppressed) | None | No | No | Partial (header only) | No |
| `1`: `practice_1` | Practice | None | No | **None / Explicit Only** | Representative lap consistency | No (timing only) | Open | Yes | Context only (times discarded) |
| `2`: `practice_2` | Practice | None | No | **None / Explicit Only** | Representative lap consistency | No (timing only) | Open | Yes | Context only (times discarded) |
| `3`: `practice_3` | Practice | None | No | **None / Explicit Only** | Representative lap consistency | No (timing only) | Open | Yes | Context only (times discarded) |
| `4`: `short_practice` | Practice | None | No | **None / Explicit Only** | Representative lap consistency | No (timing only) | Open | Yes | Context only (times discarded) |
| `5`: `qualifying_1` | Qualifying | **Q1** | No | Timing-sheet $P \pm 1$ | Best valid lap in Q1 | **Yes** (Reqs external rules) | Computed from $T_{\text{left}}$ | Yes | Context only (gaps in rivals) |
| `6`: `qualifying_2` | Qualifying | **Q2** | No | Timing-sheet $P \pm 1$ | Best valid lap in Q2 | **Yes** (Reqs external rules) | Computed from $T_{\text{left}}$ | Yes | Context only (gaps in rivals) |
| `7`: `qualifying_3` | Qualifying | **Q3** | No | Timing-sheet $P \pm 1$ | Best valid lap in Q3 | No (pole shootout) | Computed from $T_{\text{left}}$ | Yes | Context only (gaps in rivals) |
| `8`: `short_qualifying` | Qualifying | None (Full) | No | Timing-sheet $P \pm 1$ | Best valid lap in session | No (single session) | Computed from $T_{\text{left}}$ | Yes | Context only (gaps in rivals) |
| `9`: `one_shot_qualifying` | Qualifying | One-Shot | **Yes** | Timing-sheet $P \pm 1$ | Best valid lap | No | **Strictly Forbidden** | Yes | Context only (gaps in rivals) |
| `10`: `sprint_shootout_1` | Qualifying | **SQ1** | No | Timing-sheet $P \pm 1$ | Best valid lap in SQ1 | **Yes** (Reqs external rules) | Computed from $T_{\text{left}}$ | Yes | Context only (gaps in rivals) |
| `11`: `sprint_shootout_2` | Qualifying | **SQ2** | No | Timing-sheet $P \pm 1$ | Best valid lap in SQ2 | **Yes** (Reqs external rules) | Computed from $T_{\text{left}}$ | Yes | Context only (gaps in rivals) |
| `12`: `sprint_shootout_3` | Qualifying | **SQ3** | No | Timing-sheet $P \pm 1$ | Best valid lap in SQ3 | No (pole shootout) | Computed from $T_{\text{left}}$ | Yes | Context only (gaps in rivals) |
| `13`: `short_sprint_shootout` | Qualifying | None (Full) | No | Timing-sheet $P \pm 1$ | Best valid lap in session | No (single session) | Computed from $T_{\text{left}}$ | Yes | Context only (gaps in rivals) |
| `14`: `one_shot_sprint_shootout`| Qualifying | One-Shot | **Yes** | Timing-sheet $P \pm 1$ | Best valid lap | No | **Strictly Forbidden** | Yes | Context only (gaps in rivals) |
| `15`: `race` | Race | None | No | Race classification $P \pm 1$ | Recent representative pace | No (finishing order) | N/A (continuous) | Yes | Context + Player live lap |
| `16`: `race_2` (Sprint) | Race | None | No | Race classification $P \pm 1$ | Recent representative pace | No (finishing order) | N/A (continuous) | Yes | Context + Player live lap |
| `17`: `race_3` (Feature) | Race | None | No | Race classification $P \pm 1$ | Recent representative pace | No (finishing order) | N/A (continuous) | Yes | Context + Player live lap |
| `18`: `time_trial` | Time Trial | None | No | Personal Best / Rival Ghost | Best valid lap | No | Open | Yes | Context only (PB/Rival in UDP) |

---

## 4. Field-to-Decoder-to-Storage/API Mapping

Every critical telemetry signal required for session-aware engineer reasoning has been traced across the codebase:

```
[Wire Packet] 
     │
     ▼
[UDP Parser / Decoder] ───(Decoded Struct)
     │
     ├──► [Pipeline Dispatcher] ───► [SQLite / Parquet Storage]
     │
     └──► [Live Acquisition Observer] ───► [Live Snapshots / API / CLI]
```

### Complete End-to-End Tracing Table

| Telemetry Field | Wire Packet & Offset | Decoder Class & Method | Intermediate Model Field | Pipeline & Storage Status | Live Observer Snapshot Field | Current Availability Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `session_uid` | Header: `[8:16]` (`<Q`) | [`parse_header`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/header.py#L19-L26) | `PacketHeader.session_uid` | Stored in `sessions.session_uid` ([`storage/database.py:55`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/storage/database.py#L55)) | `session_uid` across all live snapshots | **Retained historically & live** |
| `m_playerCarIndex` | Header: `[27:28]` (`<B`) | [`parse_header`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/header.py#L19-L26) | `PacketHeader.player_car_index` | Stored in `player_participant_observations` | `player_car_index` in live snapshots | **Retained historically & live** |
| `m_sessionType` | Packet 1: byte 6 | [`SessionContextDecoder`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/session_context.py#L187) | `SessionContext.session_type` | Stored in `session_contexts.context_json` | Stored in `observer.latest_context` | **Retained historically & live** |
| `m_gameMode` | Packet 1: byte 665 | [`SessionContextDecoder`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/session_context.py#L210) | `SessionContext.game_mode` | Stored in `session_contexts.context_json` | Stored in `observer.latest_context` | **Retained historically & live** |
| `m_ruleSet` | Packet 1: byte 666 | [`SessionContextDecoder`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/session_context.py#L211) | `SessionContext.rule_set` | Stored in `session_contexts.context_json` | Stored in `observer.latest_context` | **Retained historically & live** |
| `m_sessionTimeLeft` | Packet 1: bytes `[8:10]` (`<H`) | [`_decode_session_context`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/session_context.py#L190) | Unpacked as `_session_time_left` | **DISCARDED** (Not in `SessionContext`) | **DISCARDED** (Not in live snapshot) | **Decoded but discarded** |
| `m_sessionDuration` | Packet 1: bytes `[10:12]` (`<H`) | [`_decode_session_context`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/session_context.py#L191) | Unpacked as `_session_duration` | **DISCARDED** (Not in `SessionContext`) | **DISCARDED** (Not in live snapshot) | **Decoded but discarded** |
| `m_totalLaps` | Packet 1: byte 3 | [`_decode_session_context`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/session_context.py#L185) | `SessionContext.total_laps` | Stored in `session_contexts.context_json` | Stored in `observer.latest_context` | **Retained historically & live** |
| `m_safetyCarStatus` | Packet 1: byte 124 | Not unpacked in prefix | Skipped in body | None (relies on Event `SCAR`) | Not in live conditions snapshot | **Skipped on wire** |
| `m_carPosition` | Packet 2: byte 25 in car record | [`LapDataDecoder._decode_car`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/lap_data.py#L166) | `CarLapData.car_position` | **DISCARDED** from `lap_attempts` | **DISCARDED** from live lap snapshot | **Decoded but discarded from live API** |
| `m_driverStatus` | Packet 2: byte 37 in car record | [`LapDataDecoder._decode_car`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/lap_data.py#L178) | `CarLapData.driver_status_id` | **DISCARDED** from `lap_attempts` | **DISCARDED** from live lap snapshot | **Decoded but discarded from live API** |
| `m_pitStatus` | Packet 2: byte 27 in car record | [`LapDataDecoder._decode_car`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/lap_data.py#L168) | `CarLapData.pit_status_id` | Reflected as `pit_encountered` bool | **DISCARDED** from live lap snapshot | **Decoded but discarded from live API** |
| `m_currentLapNum` | Packet 2: byte 26 in car record | [`LapDataDecoder._decode_car`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/lap_data.py#L167) | `CarLapData.current_lap_number` | Stored in `lap_attempts.lap_number` | Published as `lap_number` (live) | **Retained historically & live** |
| `m_currentLapInvalid`| Packet 2: byte 30 in car record | [`LapDataDecoder._decode_car`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/lap_data.py#L171) | `CarLapData.current_lap_invalid_id` | Reflected in `game_valid` boolean | Synthesized as `validation_flags` | **Retained historically & live** |
| `m_lapDistance` | Packet 2: bytes `[16:20]` (`<f`) | [`LapDataDecoder._decode_car`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/lap_data.py#L163) | `CarLapData.lap_distance_m` | Persisted in Parquet telemetry | **DISCARDED** from live lap snapshot | **Retained in Parquet only** |
| `m_lastLapTimeInMS` | Packet 2: bytes `[0:4]` (`<I`) | [`LapDataDecoder._decode_car`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/lap_data.py#L157) | `CarLapData.last_lap_time_ms` | Stored on completed lap attempt | Published as `previous_lap_time_ms` | **Retained historically & live** |
| `m_currentLapTimeInMS`| Packet 2: bytes `[4:8]` (`<I`) | [`LapDataDecoder._decode_car`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/lap_data.py#L158) | `CarLapData.current_lap_time_ms` | Stored on active lap attempt | Published as `current_lap_time_ms` | **Retained historically & live** |
| `m_deltaToCarInFront`| Packet 2: bytes `[10:13]` | [`LapDataDecoder._decode_car`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/lap_data.py#L151) | `CarLapData.delta_to_car_in_front_ms` | **DISCARDED** from storage | **DISCARDED** from live lap snapshot | **Decoded but discarded** |
| `m_deltaToRaceLeader`| Packet 2: bytes `[13:16]` | [`LapDataDecoder._decode_car`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/lap_data.py#L152) | `CarLapData.delta_to_race_leader_ms` | **DISCARDED** from storage | **DISCARDED** from live lap snapshot | **Decoded but discarded** |
| `time_trial_pb_car_index`| Packet 2: trailer byte -2 | [`_decode_lap_data_v1`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/lap_data.py#L137) | `LapDataPacket.time_trial_pb_car_index` | **DISCARDED** from pipeline | **DISCARDED** from live observer | **Decoded but discarded** |
| `time_trial_rival_car_index`| Packet 2: trailer byte -1 | [`_decode_lap_data_v1`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/lap_data.py#L138) | `LapDataPacket.time_trial_rival_car_index` | **DISCARDED** from pipeline | **DISCARDED** from live observer | **Decoded but discarded** |
| `m_bestLapTimeLapNum` (Opponents) | Packet 11: byte 3 | [`SessionHistoryDecoder`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/session_history.py#L158) | `SessionHistoryPacket.best_lap_time_lap_number` | **FILTERED OUT** ([`pipeline.py:909`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/pipeline.py#L909)) | **IGNORED** (Player index only) | **Filtered out at pipeline boundary** |
| `m_lapValidBitFlags` (Opponents) | Packet 11: lap records | [`SessionHistoryDecoder`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/session_history.py#L28) | `SessionHistoryLap.validity_flags` | **FILTERED OUT** ([`pipeline.py:909`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/pipeline.py#L909)) | **IGNORED** (Player index only) | **Filtered out at pipeline boundary** |
| `m_name`, `m_raceNumber` (All Cars) | Packet 4: records 0..23 | [`ParticipantsDecoder`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/participants.py#L129-L157) | `ParticipantData.name`, `race_number` | Stored in `driver_snapshots` table | Not published in live snapshot | **Retained in SQLite snapshots** |
| `FLBK` (Flashback event) | Packet 3: 16 bytes | [`EventDecoder`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/events.py#L70-L87) | `EventData.target_frame_identifier` | Stored in `lifecycle_events` table | Processed in live frame filter | **Retained historically & live** |

---

## 5. Current Gaps and Practical Effect on Agreed Engineer Behaviour

### 5.1 Practice Sessions
* **Agreed Policy**: Focus strictly on the player car's improvement, representative lap consistency, tyre wear, and balance. Opponent comparisons must *only* be presented upon explicit user request. Do not automatically treat nearby cars on track or timing-sheet neighbours as threats.
* **Telemetry Capability**: 
  - `SessionType` is accurately identified (`PRACTICE_1`, `PRACTICE_2`, `PRACTICE_3`, `SHORT_PRACTICE`).
  - Player car lap attempts, consistency, and delta are fully tracked.
* **Gaps**:
  - `m_sessionTimeLeft` is discarded. The live engineer cannot notify the player how much session time remains to complete run plans (e.g. "12 minutes left in FP2; enough for one long run").
  - Opponent lap traces are diagnostic-only (`observed_car_lap_attempts` with `diagnostic_only = 1`). While explicit diagnostic slot comparison is supported via [`compare_player_slot_window`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/analysis/car_slot_comparison.py#L34-L110), fast query of opponent best lap times is hindered by the pipeline filtering of Packet 11.

### 5.2 Qualifying Sessions (Knockout Q1/Q2/Q3, Short Q, One-Shot Q, Sprint Shootout)
* **Agreed Policy**:
  - Cover every supported format variant: knockout stages (Q1/Q2/Q3), short qualifying, one-shot qualifying, and sprint shootouts (SQ1/SQ2/SQ3, short shootout, one-shot shootout).
  - Automatic rivals are strictly the drivers immediately above ($P-1$) and below ($P+1$) the player on the timing sheet.
  - Comparisons must use the **best valid lap time in the current stage**, never recent average pace or invalid laps.
  - Provide stage-aware cutoff updates (who is on the elimination bubble) and session time-remaining warnings.
  - **Never** suggest another attempt in One-Shot Qualifying.
  - Any recommendation on whether another attempt is possible must be strictly supported by timing and rules evidence, never guessed.
* **Gaps & Practical Failures**:
  1. **Timing-Sheet Adjacent Rivals Missing Live**: While Packet 2 Lap Data unpacks `m_carPosition` for all cars in memory, [`AcquisitionObserver._publish_live_lap_timing`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/recording/service.py#L1914) discards `m_carPosition`. As a result, the live engineer cannot determine who is $P-1$ or $P+1$ from the live snapshot without modifying or extending the observer.
  2. **Rival Best Valid Laps Discarded**: Packet 11 (Session History) provides `m_bestLapTimeLapNum` and sector-by-sector validity flags (`m_lapValidBitFlags`). However, [`f1_engineer/pipeline.py:909`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/pipeline.py#L909) explicitly discards all non-player Session History packets. The live engineer cannot look up the best valid lap time of the rival in P15 or the driver in P-1 unless non-player session histories are retained or tracked in a live standings cache.
  3. **Advancement Cutoff Positions Are Not In Telemetry**: The UDP stream does *not* transmit cutoff position numbers (e.g. cutoff at P15 in Q1, P10 in Q2). If the grid has 20 cars, bottom 5 drop out; if 22 cars (Andretti or custom), bottom 6 may drop out; if 24 cars (2026 Season Pack), regulations vary. The engineer *must not* infer the cutoff position solely from the stage name without an external rules configuration mapping `(session_type, active_car_count) -> cutoff_position`.
  4. **Next Attempt Feasibility Blindness**: To determine whether another flying lap attempt is possible before the chequered flag, the engineer must compute:
     $$T_{\text{remaining}} > T_{\text{pit\_exit}} + T_{\text{out\_lap}} + \Delta_{\text{margin}}$$
     Because `m_sessionTimeLeft` is decoded as `_session_time_left` and discarded ([`f1_engineer/udp/session_context.py:190`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/session_context.py#L190)), the live engineer has zero knowledge of $T_{\text{remaining}}$ and cannot verify whether a lap attempt is mathematically possible.
  5. **One-Shot Guardrail**: `SessionType.ONE_SHOT_QUALIFYING` and `SessionType.ONE_SHOT_SPRINT_SHOOTOUT` are unambiguously identified in `SessionContext.session_type`. However, the engineer policy engine must implement an explicit guardrail asserting `max_flying_laps = 1` and suppress all "box for fresh tyres and go again" prompts.

### 5.3 Race Sessions (Grand Prix & Sprint Variants)
* **Agreed Policy**:
  - Automatic rivals are immediately above and below in **race classification** ($P \pm 1$), *not* physical track proximity.
  - Comparisons must use **recent representative pace** (rolling 3–5 clean laps), excluding in-laps, out-laps, pit stops, and safety car periods.
  - Track total laps, current lap number, and distance progress. Treat backmarker traffic or leaders lapping the player as traffic, not classification threats.
* **Gaps & Practical Failures**:
  1. **Race Classification vs Physical Proximity**: In Packet 2 Lap Data, `m_carPosition` indicates race classification order, whereas `m_lapDistance` indicates track position. Because `m_carPosition` is not published in [`_live_lap_timing_snapshot`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/recording/service.py#L1914), the live engineer cannot distinguish between an opponent on the same lap fighting for position and a car physically alongside that is a lap down or pitting.
  2. **Interrupted / Pit Lap Qualification**: `m_driverStatus` (0 = garage, 1 = flying, 2 = in-lap, 3 = out-lap, 4 = on-track) and `m_pitStatus` (0 = none, 1 = pitting, 2 = pit-lane) are available in `CarLapData` but omitted from the live snapshot, impeding automatic filtering of unrepresentative laps.
  3. **Race Progress**: `m_totalLaps` is stored in `SessionContext`, and `m_currentLapNum` is in the live lap snapshot. However, lap count delta to leader (`m_deltaToRaceLeaderInMS`) is discarded, so identifying whether a car is on the lead lap requires deriving lap count differences from `CarLapData.current_lap_number`.

### 5.4 Time Trial
* **Agreed Policy**: Recognize Time Trial format; select personal best or rival ghost as reference trace. Detailed rival telemetry trace feasibility is delegated to the peer agent (`audit/time-trial-rival-evidence`).
* **Telemetry Capability**: 
  - `SessionType.TIME_TRIAL`, `GameMode.TIME_TRIAL`, and `RuleSet.TIME_TRIAL` provide three-way redundant classification.
  - Trailer indices `m_timeTrialPBCarIdx` and `m_timeTrialRivalCarIdx` are decoded in [`LapDataPacket`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/lap_data.py#L55-L56).

---

## 6. Conservative Fallback Behaviour Matrix

When telemetry fields are missing, discarded, corrupt, or ambiguous, the live engineer must fail closed to conservative, honest communication rather than hallucinating tactical advice:

| Telemetry Condition / Signal Gap | Format Context | Forbidden Engineer Behaviour | Required Conservative Fallback Behaviour |
| :--- | :--- | :--- | :--- |
| `m_sessionTimeLeft` is missing or discarded | Qualifying (all) | Guessing whether another run is possible; saying "You have time for one more lap" | "Session time remaining is unverified. If you intend to run again, leave the pit box immediately." |
| `m_carPosition` is missing or unverified | Qualifying / Race | Picking physical track neighbours as rivals; announcing incorrect positions | Suppress automatic rival delta calls. Fall back to: "Position data unconfirmed. Focusing on your delta and sector pace." |
| Opponent Session History is unavailable | Qualifying (all) | Using previous-session times or guessing rival cutoff time | "Rival best times unavailable on timing feed. Target your personal best delta." |
| Active session is `ONE_SHOT_QUALIFYING` or `ONE_SHOT_SPRINT_SHOOTOUT` | Qualifying | Suggesting a cool-down lap, aborting for another run, or pitting for tyres | "One-shot format active. Exactly one flying lap permitted. Make this attempt count." |
| Cutoff position regulations unknown for grid size | Knockout Q1 / Q2 / SQ1 / SQ2 | Assuming standard P15/P10 cutoff when grid has 22 or 24 cars | State: "Cutoff threshold unconfirmed for active grid size. Push for maximum position." |
| Player has set no valid lap time yet | Qualifying / Practice | Reporting delta to nonexistent lap; dividing by zero | "No benchmark lap set. Complete this lap cleanly to establish your baseline." |
| Tied lap times on timing sheet | Qualifying | Declaring player definitely ahead without tie-break evidence | "Pace tied with [Rival]. In-game tie-break rules apply based on order achieved." |
| Player is P1 (Pole / Leader) | Qualifying / Race | Calculating delta to $P-1$ (index -1 out of range) | Shift automatic rival to $P+1$ (car behind). Announce: "You are currently P1. Gap to P2 is $\Delta$." |
| Player is in last position ($P = N$) | Qualifying / Race | Calculating delta to $P+1$ (out of range) | Shift automatic rival to $P-1$ (car ahead). Announce: "You are in P[N]. Target is P[N-1] at $\Delta$." |
| Temporary packet silence ($< 10$ seconds) | Any | Resetting conversation context; announcing "New session started" | Maintain active session state. If query received: "Telemetry link momentarily paused; standing by." |
| `session_uid` changes | Any | Retaining old session context, old rivals, or old lap counts | Terminate previous session context. Clear stale roster. Start completely fresh engineer session. |
| Flashback event (`FLBK`) received | Any | Retaining lap attempts completed in the rewound timeframe | Mark rewound attempts as `superseded = 1`. Invalidate invalidated split times immediately. |
| In-lap (`m_driverStatus = 2`) or Pitting (`m_pitStatus != 0`) | Race / Practice | Blending slow pit/in-lap into rolling representative pace | Exclude lap from rolling average pace calculation. Flag lap disposition as `interrupted / pit`. |

---

## 7. Proposed Minimal Metadata Additions (Architectural Proposals Only)

> [!NOTE]
> The items below are **proposals for the primary development agent**, documented here to fulfill audit requirements. In accordance with the audit boundary, **no production models, schemas, or decoders have been modified in this branch.**

### Proposal A: Preserve Session Duration and Time Remaining in `SessionContext`
* **File Target**: [`f1_engineer/udp/session_context.py`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/udp/session_context.py) & [`f1_engineer/sessions/context.py`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/sessions/context.py)
* **Change**: Stop discarding `_session_time_left` and `_session_duration` in `_decode_session_context`. Add:
  ```python
  session_time_left_s: int  # Unpacked from bytes 8-10 (<H)
  session_duration_s: int   # Unpacked from bytes 10-12 (<H)
  ```
* **Benefit**: Enables mathematically verified next-attempt feasibility calculations in Qualifying and time-budgeted run planning in Practice.

### Proposal B: Add Classification Position and Status to Live Lap Snapshot
* **File Target**: [`f1_engineer/recording/service.py:1914`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/recording/service.py#L1914) (`_publish_live_lap_timing`)
* **Change**: Populate the already-unpacked fields from `CarLapData`:
  ```python
  "car_position": car.car_position,
  "driver_status_id": car.driver_status_id,
  "pit_status_id": car.pit_status_id,
  "lap_distance_m": car.lap_distance_m,
  "penalties_s": car.penalties_s,
  ```
* **Benefit**: Allows the live engineer assistant to query current classification and pit/track status directly without re-parsing raw datagrams.

### Proposal C: In-Memory Live Leaderboard Cache in `AcquisitionObserver`
* **File Target**: [`f1_engineer/recording/service.py`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer/t3code-fb1c05fa/f1_engineer/recording/service.py)
* **Change**: Maintain a lightweight 24-slot array updated on each Lap Data packet:
  ```python
  @dataclass(slots=True)
  class LiveLeaderboardEntry:
      car_index: int
      car_position: int
      current_lap: int
      driver_status: int
      best_lap_time_ms: int | None
      last_lap_time_ms: int
  ```
* **Benefit**: Allows instantaneous $O(1)$ lookup of timing-sheet neighbours ($P-1, P+1$) and cutoff bubble cars ($P15, P10$) without changing durable Parquet/SQLite storage schemas.

### Proposal D: External Sporting Regulations Cutoff Configuration
* **File Target**: `f1_engineer/analysis/qualifying_rules.py` (New module proposal)
* **Contract**:
  ```python
  def get_advancement_cutoff(session_type: SessionType, active_car_count: int) -> int | None:
      """Return cutoff position (e.g. 15 for Q1 in 20-car grid) or None if no elimination."""
      if session_type in (SessionType.QUALIFYING_1, SessionType.SPRINT_SHOOTOUT_1):
          eliminated = 4 if active_car_count <= 18 else 5 if active_car_count <= 21 else 6
          return active_car_count - eliminated
      if session_type in (SessionType.QUALIFYING_2, SessionType.SPRINT_SHOOTOUT_2):
          return 10  # Standard top 10 advance to Q3/SQ3
      return None
  ```
* **Benefit**: Eliminates guessing; cleanly handles varying grid sizes (F1 20 cars vs 22 cars vs 24 cars).

---

## 8. Prioritized Integration Checklist for Primary Agent

A prioritized sequence of tasks for the implementation agent:

- [ ] **Phase 1: Session Time Remaining Exposure (High Priority, Low Effort)**
  - In `f1_engineer/udp/session_context.py`, map `_session_time_left` and `_session_duration` to `SessionContext.session_time_left_s` and `session_duration_s`.
  - Expose `session_time_left_s` in `AcquisitionObserver._live_session_conditions_snapshot`.
  - Add unit test verifying that non-zero session duration and remaining time decode accurately from raw fixtures.
- [ ] **Phase 2: Live Leaderboard & Position Publication (High Priority, Medium Effort)**
  - In `AcquisitionObserver._publish_live_lap_timing`, add `car_position`, `driver_status_id`, and `pit_status_id`.
  - Maintain a live `_leaderboard: dict[int, CarLapData]` in `AcquisitionObserver` storing the latest `LapDataPacket.cars` record for all active cars.
  - Implement helper `observer.get_rivals(player_car_index)` returning $P-1$ and $P+1$ `CarLapData` records.
- [ ] **Phase 3: Qualifying Stage & Rules Engine (High Priority, Medium Effort)**
  - Implement `classify_session_type(session_type)` in `f1_engineer/sessions/context.py` distinguishing knockout stages, short, and one-shot variants.
  - Implement strict guardrail forbidding repeat attempt prompts in `one_shot_qualifying` and `one_shot_sprint_shootout`.
  - Implement out-lap feasibility calculator:
    $$\text{feasible} = T_{\text{time\_left}} > (\text{out\_lap\_estimate} + \text{flying\_lap\_estimate} + \text{margin})$$
- [ ] **Phase 4: Practice & Race Comparison Policy Dispatch (Medium Priority, Medium Effort)**
  - Wire session-aware policy selection into `f1_engineer/analysis/engineer_query.py`:
    - If `practice_*`: default rival focus to `None` (focus on player consistency); only query slot rivals when user prompt contains explicit driver identifier.
    - If `race*`: automatically select $P-1$ and $P+1$ in classification; calculate rolling representative pace over clean laps.
- [ ] **Phase 5: Opponent Session History Buffer (Medium Priority, High Effort)**
  - Review `f1_engineer/pipeline.py:909`. Remove the hard drop of non-player Session History packets in live mode, routing them to a bounded memory cache `_live_session_history_by_car: dict[int, SessionHistoryPacket]` to allow true best-valid-lap comparisons against rivals.

---

## 9. Reproducible Validation & Capture Plan

To validate session-aware behaviour across formats not currently covered by repository test fixtures (e.g. `f1_25_session_packet_v1.bin` is Time Trial), the following test scenarios and datagram generation plans must be executed:

### 9.1 Synthetic Fixture Matrix

The primary agent can use `tests/helpers.py:make_datagram` to generate minimal valid datagrams for all formats:

```python
def make_session_datagram(
    session_type: int,
    session_time_left: int = 600,
    session_duration: int = 1080,
    total_laps: int = 0,
    packet_format: int = 2025,
) -> RawDatagram:
    # Construct 724-byte F1 25 body or 897-byte 2026 body with prefix
    ...
```

1. **Test Scenario: One-Shot Qualifying Guardrail**:
   - Construct Packet 1 with `m_sessionType = 9` (`ONE_SHOT_QUALIFYING`).
   - Feed completed flying lap into pipeline / observer.
   - Assert that `attempt_tracker.next_attempt_permitted` is `False`.
   - Assert that engineer query prompt generator omits "box for new softs" or "prepare for second run".
2. **Test Scenario: Knockout Cutoff Time Delta**:
   - Construct Packet 1 with `m_sessionType = 5` (`QUALIFYING_1`), `m_sessionTimeLeft = 180`.
   - Feed Packet 2 with Player in P16, Car B in P15 (cutoff bubble).
   - Feed Packet 11 with Car B best lap: `1:29.450` (valid) and Player best lap: `1:29.600`.
   - Verify that engineer query identifies Car B as the cutoff rival with delta $+0.150\,\text{s}$ required to advance.
3. **Test Scenario: Race Classification vs Traffic Discrimination**:
   - Construct Packet 1 with `m_sessionType = 15` (`RACE`), `m_totalLaps = 58`.
   - Construct Packet 2:
     - Player: Lap 25, `m_lapDistance = 1500.0`, `m_carPosition = 6`.
     - Car X (backmarker): Lap 24, `m_lapDistance = 1520.0` (20m ahead on track), `m_carPosition = 18`.
     - Car Y (rival ahead): Lap 25, `m_lapDistance = 2100.0` (600m ahead on track), `m_carPosition = 5`.
   - Verify that the automatic rival selected is **Car Y** ($P=5$), and Car X is identified as backmarker traffic.
4. **Test Scenario: Flashback Rewind Invalidates Attempt**:
   - Inject completed lap attempt with `start_time = 100.0`, `end_time = 190.0`.
   - Inject Packet 3 `FLBK` event with `target_session_time_s = 150.0`.
   - Verify `reconcile_attempt_lifecycle` marks the lap as `superseded = True` and invalidates any live lap comparison based on it.

---

## 10. Audit Verification Scripts & Test Suite

In conjunction with this audit, two non-intrusive audit verification tools were created:

1. **Inspection Tool**: [`scripts/audits/session_format_metadata.py`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer\t3code-fb1c05fa/scripts/audits/session_format_metadata.py)
   - Standalone CLI tool enumerating all 19 `SessionType`s, evaluating packet layouts, checking field retention status, and outputting JSON format capabilities via `--json`.
2. **Focused Test Suite**: [`tests/test_session_format_metadata_audit.py`](file:///C:/Users/massi/.t3/worktrees/F1-Engineer\t3code-fb1c05fa/tests/test_session_format_metadata_audit.py)
   - Verifies classification of practice, qualifying (knockout vs one-shot), race, and time trial formats.
   - Tests detection of discarded fields (`_session_time_left`, `_session_duration`).
   - Confirms pipeline boundary drop rule for non-player session histories (`pipeline.py:909`).
   - Verified passing in `0.26s` under pytest.
