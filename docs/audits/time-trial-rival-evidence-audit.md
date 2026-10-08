# Time Trial rival evidence audit

Baseline commit: `f32ad3e31ae5ccd962ccc848beee3182fb04fb5b` (`origin/main` at the branch point).

This is a capability audit. It does not add a rival comparison to the engineer. Desired Time Trial behaviour (a fixed personal-best reference, then a detailed comparison with the in-game rival when the wire supports it) is context for the questions below. It is not a description of the current pipeline.

## Conclusion

**F1 25 (`packetFormat` 2025): timing-only comparison is demonstrated. A detailed rival comparison is not established.**

**2026 Season Pack (`packetFormat` 2026): not established.** None of the inspected captures uses packet format 2026. The 2026 Time Trial layout is documented and covered by a synthetic size test only.

The one real Time Trial capture is F1 25 at Melbourne. After the rival index becomes car 4, packet 14 and Session History both report one rival lap time, 78,129 ms, and the same overall frames carry lap distance, speed, brake, throttle, gear, and world position for that slot. Those samples are a different slot from the player and from the personal-best ghost. They still fail the game's own rule for a car that is actively providing data: Lap Data result status is Invalid (0) on every selected frame, and the slot is outside Participants `m_numActiveCars`. Decoded telemetry speed reaches 486 km/h, including 4,283 samples at or above 400 km/h. The player's telemetry peaks at 324 km/h and the player's speed trap peaks at 321.0 km/h. The ghost speed trap stays 0. The audit therefore keeps the rival comparison at the demonstrated lap time. It does not treat the populated channels as a braking-point or minimum-speed trace.

The channel-coverage label emitted for that rival is `detailed_channels_with_lap_coverage`. That label means the numeric coverage thresholds passed. `blocks_detailed_comparison` is true for the same rival, and that flag is the verdict used here.

A second F1 25 capture, Suzuka Practice 3, keeps the rival index at 255 for every Lap Data packet and contains no packet 14. Rival evidence there is not established. Cars in that session have result status active and telemetry peaks at or below 338 km/h. Those cars are session participants, not a Time Trial ghost.

The recovered Shanghai capture is the same kind of control across six F1 25 career sessions (sprint shootout and practice). Every session keeps the rival index at 255, and the file contains no packet 14. Its footer is truncated, so it is not a complete recording. It does not supply a Time Trial ghost.

## What was separated

| Question | Documented protocol | Current code | Captures and tests |
| --- | --- | --- | --- |
| PB and rival car indices | Lap Data trailer `m_timeTrialPBCarIdx` and `m_timeTrialRivalCarIdx`. 255 means invalid. | Decoded and stored on `LapDataPacket`. No production consumer reads them. | Melbourne: PB stays 1; rival is 255, then 4. Suzuka: both stay 255. Synthetic tests decode 255 and, for 2026, PB 23. |
| Packet 14 contents | Three timing datasets at 1 Hz, Time Trial only. F1 25 packet is 101 bytes. 2026 packet is 104 bytes because `teamId` is `uint16`. No speed, brake, throttle, gear, or position samples. | Enum value exists. Body stays raw. | Melbourne: 234 packets, every one 101 bytes, rival time 78,129 ms on 228 valid datasets. Suzuka: zero packet 14 datagrams. |
| Lap distance and lap boundaries | Per-slot `m_lapDistance` in metres, which may be negative before the line. Lap number and current-lap invalid flag are separate fields. | All-car observations keep lap distance. Player attempts follow the header player only. | Rival slot spans about one track length on lap number 2, then wraps three times without incrementing the lap number. |
| Speed, brake, throttle, gear | Car Telemetry is an array for every car slot. `m_speed` is `uint16` km/h. | Decoded for every slot. The player sample and the canonical archive can both see a slot. The archive does not store a rival role. Speed up to 500 km/h passes the canonical range check. | Rival slot: brake above 0.05 on 1,119 frames, throttle above 0.05 on 10,661 frames, gears 0 through 8, speed 0 to 486 km/h. |
| Motion | Motion packet table text says the player's car. The struct is an array for every car. Motion Ex is a separate packet. | Motion is decoded for every slot and joined to all-car observations. Motion Ex stays opaque. | Rival world-axis span about 1,737 m, separated from the player on 13,275 frames. |
| Same-frame coverage | Menu-rate packets for one frame are sent together. 1 Hz packets can arrive on any frame. | Production frame assembly joins same-frame telemetry and motion. This audit uses its own 256-key receive buffer. | Melbourne rival: 13,673 of 13,674 selected frames have lap, telemetry, and motion together. One assembled frame had no telemetry. No index, telemetry, or motion conflicts. |
| Result status and active-car count | A slot is actively providing data only when result status is neither Invalid (0) nor Inactive (1). Also check `m_numActiveCars`. | Result status is retained on lap records. The rival index is not checked against it. | Rival and PB ghost: result status 0 on every frame, active-car count 1, player index 0. Suzuka practice cars: result status active, active-car count 20. |
| Session History timing | One car index per packet, not a distance trace. | Non-player history packets are counted and skipped. | Melbourne history for car 4 repeats 78,129 ms. The pipeline would skip those packets today. |
| Identity | Names and team ids are roster fields. The index is the link inside one packet. | Participant tenure fingerprint includes the name. An empty fingerprint is unavailable. Names are not a persistent person. | Ghost `driverId` is 255, the same value as the player. Several AI slots share one name hash. Team id matches packet 14 for the selected slot and still does not identify a person. |
| Selection changes and stale slots | Each Lap Data packet carries the current indices. | Not consumed, so a change cannot update a stored role. | Melbourne has one transition, 255 to 4, with no stale movement. It does not show a change from one in-range rival to another. A synthetic stream does. |
| 2026 wire layout | 24 cars, Lap Data packet 1,399 bytes, Time Trial packet 104 bytes, dataset 25 bytes. Header `packetVersion` is separate from the specification revision. | 2026 Lap Data, telemetry, motion, and participants adapters exist and are synthetic-fixture tested. Packet 14 is not decoded. Automatic reference eligibility stays on validated F1 25 Time Trial. | No real 2026 capture was in the inspected set. |

## Specification revision and wire version

The EA post labels the documentation revision **Version 11.0**. That number is not the header `packetVersion` and not `packetFormat`. The repository records that distinction in `docs/telemetry-protocol.md` lines 3–13.

| Source | What it identifies | SHA-256 verified for this audit |
| --- | --- | --- |
| [F1 25 and 2026 Season Pack UDP specification](https://forums.ea.com/blog/f1-games-game-info-hub-en/ea-sports%E2%84%A2-f1%C2%AE25-2026-season-pack-udp-specification/12187347) | Specification post, documentation revision 11.0 | Not a file hash. The post is the link cited by `docs/telemetry-protocol.md`. |
| [F1 25 UDP data output v3 PDF](https://forums.ea.com/t5/s/tghpe58374/attachments/tghpe58374/f1-games-game-info-hub-en/61/4/Data%20Output%20from%20F1%2025%20v3.pdf) | F1 25 structures. Local file `f1-25-v3.pdf`. | `850199d1ea817b887b150118095c5ca86527a397d578d53fd5c83427636b92d5`. This hash is not pinned in the repository. |
| [2026 structures `.txt`](https://forums.ea.com/t5/s/tghpe58374/attachments/tghpe58374/f1-games-game-info-hub-en/61/8/2026%20Season%20Pack%20Telemetry%20Output%20Structures%20(1).txt) | F1 26 / Season Pack C structures. Copyright 2026 EA. `cs_maxNumCarsInUDPData` is 24. | `4f97867924f5f13f11b7fde6eb84ccb3aefcaf69062424bc077e387090647c72`. Matches the pin in `docs/telemetry-protocol.md`. |
| [Season 8 v1.2 PDF](https://forums.ea.com/t5/s/tghpe58374/attachments/tghpe58374/f1-games-game-info-hub-en/61/10/Data%20Output%20from%20F1%2025%202026%20Season%20Pack%20(Season%208).pdf) | "Data Output from F1 25: 2026 Season Pack", document v1.2. | `3f38858c3ca65b2faa55a90a35277dd2767bb9cea2911e741b61b370a39365ae`. Matches the pin. |

Both inspected captures use header `packetFormat` 2025, game year 25, major 1, minor 26, and `packetVersion` 1 on every datagram. That is the F1 25 wire format. It is not a 2026 capture and it is not documentation revision 11.0 stored in the header.

Official Time Trial layout:

- F1 25 v3 PDF, Time Trial section spanning pages 15–16. Frequency 1 per second. Size 101 bytes. Version 1. Dataset `<uint8 car, uint8 team, 4×uint32 times, 6×uint8 flags>`. Three datasets: player session best, personal best, rival. Assist fields are 0 = off and 1 = on. `m_valid` is 0 or 1.
- 2026 structures file, lines 842–868, and Season 8 PDF pages 16–17. Size 104 bytes. The dataset uses `uint16 m_teamId`, so each dataset is 25 bytes and the body is 75 bytes. The same three dataset names and the same 0/1 assist comments apply.
- Lap Data indices: 2026 structures lines 285–286. The same two trailing `uint8` fields are in the F1 25 v3 Lap Data packet. 255 means invalid.
- Result status: 2026 structures line 269. `0` invalid, `1` inactive, `2` active, then finished and other terminal values. `m_currentLapInvalid` is a different field: `0` valid, `1` invalid (line 261). `m_driverStatus` `1` means flying lap (line 268).
- Active-car rule: F1 25 v3 PDF page 20, and Season 8 PDF page 22. The array can hold 22 or 24 cars and is not always filled. `m_numActiveCars` is the count. A vehicle index has valid data when its Lap Data result status is not Invalid or Inactive.
- Menu-rate packets on one frame are sent together. Packets with their own rate, including Time Trial and Session History, can arrive on any frame (same FAQ pages).
- Car Telemetry `m_speed` is `uint16` kilometres per hour (2026 structures line 527). The F1 25 packet is the same field at the documented F1 25 size, 1,352 bytes, which this capture matches.
- Motion: the packet-id table on F1 25 v3 page 3 says "Contains all motion data for player's car – only sent while player is in control". The struct on the next page is `CarMotionData m_carMotionData[22]` with the comment "Data for all cars on track". The 2026 struct says the same for 24 cars (structures file line 91). Motion Ex is the player-car extra packet and is not a rival trace.

## Behaviour implemented in this tree

Indices are decoded from the last two body bytes in `f1_engineer/udp/lap_data.py` lines 135–138 and exposed as `time_trial_pb_car_index` and `time_trial_rival_car_index` at lines 55–56. A repository search of `f1_engineer` finds those names only in that file. `tests/test_time_trial_rival_evidence_audit.py` locks that search.

`PacketId.TIME_TRIAL = 14` is declared in `f1_engineer/udp/models.py` line 27. `PacketDecoder` (`f1_engineer/udp/decoder.py` lines 22–44) parses the 29-byte header (`f1_engineer/udp/header.py` lines 11–12) and returns the body unchanged. No adapter parses packet 14. F1 25 packet 16 is forced to an unknown kind at decoder lines 30–34. The 2026 packet 16 body stays opaque, as `docs/telemetry-protocol.md` lines 91–93 already say.

The player lap path uses `packet.header.player_car_index` (`f1_engineer/pipeline.py` line 334). All-car `CarObservation` rows are built for every Lap Data slot at pipeline lines 519–548 through `make_car_observation` (`f1_engineer/telemetry/canonical.py` lines 446–462). Those rows include lap distance, speed, throttle, brake, gear, and motion. They have a car index and the header player index. They have no PB or rival role. Canonical speed accepts 0 through 500 km/h (`canonical.py` line 477), so a stored 486 km/h sample would pass that check.

Session History for a car other than the header player is counted in `session_history_non_player_packets` and skipped (`pipeline.py` lines 908–910). The decoder itself can read another car index. The skip is pipeline policy.

Participant identity for diagnostic slot tenures is a fingerprint of AI control, driver, network, team, race number, nationality, name, and platform (`f1_engineer/sessions/car_lap_inventory.py` lines 74–88). An all-zero identity is unavailable. `docs/architecture.md` lines 1308 and 1392 describe slot tenure and the Shanghai Practice comparison as diagnostic same-session evidence, not as a persistent rival. The sentence at architecture line 21 about identifying ghost references is product direction. This capture set does not make that sentence true.

`docs/telemetry-protocol.md` lines 29 and 89 already record that the Melbourne capture's two completed player laps, 79.295 s and 81.437 s, are game-invalid and that 4,334 non-player Session History packets were skipped. This audit recounts the raw capture and agrees with those lap times.

## Melbourne Time Trial capture

| Item | Value |
| --- | --- |
| File | `D:\F1-Engineer\recordings\session-aus-mclaren-1.f1ecap` |
| Bytes | 92,040,422 |
| SHA-256 | `cb82f6ddef9f7ff93ee7ad4b5efe5e99b041881c06c40cb475d97606f85e2316` |
| Created | `2026-10-02T04:07:04.827855+00:00` |
| Recorder | `f1-race-engineer` 0.1.0, schema 1, bind port 20777 |
| Footer | `status=complete`, received = recorded = 83,139, queue drops 0, socket errors 0, late packets 0 |
| Reader | Complete. `reader_error` is null. |
| Header | `packetFormat` 2025, game 25.1.26, `packetVersion` 1 on all 83,139 datagrams |
| Session | UID `14237356543050158953`, session type 18, game mode 5, ruleset 2, track 0 Melbourne, length 5,276 m |
| Session clock | 0.813 s through 233.369 s |

Packet counts that matter here, all format 2025 and version 1:

| Packet | ID | Count | Bytes |
| --- | ---: | ---: | ---: |
| Motion | 0 | 13,950 | 1,349 |
| Lap Data | 2 | 13,950 | 1,285 |
| Participants | 4 | 48 | 1,284 |
| Car Telemetry | 6 | 13,949 | 1,352 |
| Session History | 11 | 4,644 | 1,460 |
| Time Trial | 14 | 234 | 101 |
| Lap Positions | 15 | 234 | 1,131 |

There is no packet 16. Packet 14's 101-byte size matches the F1 25 document. The audit join reports 13,950 lap packets, 0 index conflicts, 0 telemetry conflicts, 0 motion conflicts, 0 late lap packets after a flush, 0 frames flushed without motion, and 1 frame flushed without telemetry.

### Selection

| Overall frame | Session time (s) | Player | PB index | Rival index |
| ---: | ---: | ---: | ---: | ---: |
| 47 | 0.835 | 0 | 1 | 255 |
| 323 | 5.443 | 0 | 1 | 4 |

`selection_transitions` is 1. The first row is the first observation, not a transition. Rival index 255 accounts for 276 lap packets. Index 4 accounts for 13,674. The PB index is 1 on all 13,950 lap packets. Neither ghost index equals the player index. `stale_rival_movement_frames` and `stale_pb_movement_frames` are 0. This capture shows a missing rival becoming car 4. It does not show car 4 being replaced by another in-range rival, and it does not show the old slot continuing to move after the index leaves it.

`m_numActiveCars` is 1. Every in-range PB and rival frame is outside that count.

### Packet 14 timing

All 234 bodies parsed with the 24-byte F1 25 dataset. No size mismatches.

| Dataset | Car index | Team id | Valid flag | Positive lap time | Lap time (ms) | Agreement with the Lap Data index |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Player session best | 0 on all 234 | 8 on all 234 | 143 valid, 91 invalid | 91, and none of those 91 are valid | 79,295 where positive | Player index is not compared. |
| Personal best | 1 on all 234 | 8 on all 234 | 234 valid | 234 valid and positive | 78,349 | Agrees on 229. Five packets arrive before a Lap Data index is known. |
| Rival | 4 on 228, 0 on 6 | 1 on 228, 0 on 6 | 228 valid, 6 invalid | 228 valid and positive | 78,129 | Agrees on 224. Disagrees on 5. Five packets have no Lap Data index yet. |

The player's session-best dataset never pairs `m_valid = 1` with a positive lap time. The 79,295 ms value appears only on the invalid flag. That is the same time as the player's first completed lap in Session History. The repository already records that lap as game-invalid.

The rival dataset's team id 1 matches the Participants team id on slot 4. The six early datasets use car 0 and team 0. The player's participant team id is 8, so those six datasets are not the player's roster row. They belong to the opening window, including the five packets before the Lap Data rival index exists. They are not folded into the car-4 trace by the valid-time count: the valid positive rival times are the 228 datasets at 78,129 ms.

### Session History

| Car index | Packets | Positive lap rows | Distinct positive times (ms) | Header car is the player |
| ---: | ---: | ---: | --- | ---: |
| 0 | 310 | 135 | 79,295 and 81,437 | 310 |
| 1 | 310 | 310 | 78,349 | 0 |
| 4 | 310 | 304 | 78,129 | 0 |
| Every other index | about 309 or 310 | 0 | none | 0 |

Car 4's history time matches packet 14 and the Lap Data `lastLapTime` on the selected rival frames (78,129 ms on 13,673 frames). Car 1 matches the personal-best dataset (78,349 ms). History for cars 1 and 4 is present in the capture and is discarded by the current pipeline because the car index is not the header player.

### Channels on the indexed slots

Only three slots meet the audit's moving-slot filter: slot 0 (player), slot 1 (PB index), and slot 4 (rival index). No other slot shows a moving speed, a lap-distance span above 1 m, or an active result status.

| | Player slot 0 | PB slot 1 | Rival slot 4 |
| --- | ---: | ---: | ---: |
| Selected lap frames | 13,950 | 13,950 | 13,674 |
| Result status | 2 on 13,950 | 0 on 13,950 | 0 on 13,674 |
| `m_currentLapInvalid` | 0 on 8,974, 1 on 4,976 | 0 on 13,950 | 0 on 13,674 |
| Driver status | 0 on 2,080, 1 on 11,870 | 0 on 2,081, 1 on 11,869 | 1 on 13,674 |
| Speed min / max (km/h) | 0 / 324 | 0 / 486 | 0 / 486 |
| Samples at or above 360 km/h | 0 | 4,607 of 13,949 | 4,617 of 13,673 |
| Samples at or above 400 km/h | 0 | 4,269 of 13,949 | 4,283 of 13,673 |
| Speed trap max (km/h) | 321.048 | 0 | 0 |
| Brake above 0.05 | 1,444 | 1,233 | 1,119 |
| Throttle above 0.05 | 9,892 | 12,907 | 10,661 |
| Gears observed | 0, 2–8 | 0–8 | 0–8 |
| Lap numbers | 1, 2, 3 | 2 | 1 (one sample), 2 |
| Lap-distance min / max (m) | -5,447.815 / 5,276.591 | 0.809 / 5,276.606 | 1.118 / 5,276.606 |
| Forward distance integral (m) | 17,616.324 over 10,295 frames | 11,154.054 over 9,011 frames | 11,165.504 over 8,977 frames |
| Backward jumps | 11,748.431 m over 5 frames, 2 of them larger than 1,000 m | 15,822.568 m over 3 frames, all 3 larger than 1,000 m | 15,825.198 m over 3 frames, all 3 larger than 1,000 m |
| Same-frame lap + telemetry + motion | 13,949 | 13,949 | 13,673 |
| World axis span (m) | 1,734.906 | 1,735.497 | 1,737.069 |
| Frames with world position more than 2 m from the player | 0, by definition | 13,377 | 13,275 |
| Equal speed while both move, above 10 km/h | — | 13 of 10,003 | 17 of 9,977 |
| Speed versus lap-distance residual | 384 mismatches in 13,943 frames, absolute error sum 2,300.914 m | 2,180 / 13,945, sum 7,470.793 m | 2,213 / 13,669, sum 7,473.152 m |

The rival's three backward jumps sum to 15,825.198 m, which is three steps of about 5,275 m. That is a lap-distance wrap near the 5,276 m track length. The lap number stays on lap 2 for 13,673 of the selected frames, so the wrap does not advance the lap counter. The forward integral, 11,165.504 m, is a little more than two track lengths. Min-to-max span on lap 2 is 5,275.488 m, which is why the coverage classifier accepts the lap. Span is not path length.

The speed check compares reported km/h with the lap-distance change over the session-time delta. Frames with a gap above 0.1 s, or a jump above 200 m, are excluded so the wraps are not counted as speed errors. The rival disagrees on 2,213 of 13,669 compared frames, which is 16.2 percent. The audit's disagreement gate is half of the compared frames, so that gate does not fire. The player's disagreement rate on the same definition is 384 of 13,943 frames, which is 2.8 percent. The ghost channels are mostly consistent with each other, and they are consistently fast: about 31 percent of rival telemetry samples are at or above 400 km/h. This audit does not convert that consistency into a claim that 486 km/h is a usable Melbourne braking reference. The player speed trap and the player telemetry peak are the scale this capture itself supplies for the driven car.

The PB ghost is a different index and a different constant lap time (78,349 ms versus 78,129 ms), with team id 8 rather than 1. Its distance integrals and 486 km/h peak are close to the rival's. This audit did not test whether the two sample sequences are copies of each other. A later comparison has to follow the index on each frame. Sharing a speed peak is not identity.

`m_currentLapInvalid` is 0 on every ghost frame, and the struct comment maps 0 to a valid current lap. Driver status is flying lap on every rival frame. Result status is still Invalid on every one of those frames. The FAQ's active-data rule uses result status. This audit does not let the lap-invalid flag or the driver-status flag replace that rule.

### Roster confusion

Participants report one active car. Slot 0 is the human player, team id 8, `driverId` 255. Slot 1 is AI-controlled, team id 8, `driverId` 255. Slot 4 is AI-controlled, team id 1, `driverId` 255. `driverId` 255 is also the value on every other listed participant in this capture, so it does not separate the player from either ghost.

The name hash on slot 4 is shared with other AI slots that do not move. Slot 14's name hash matches the player's, with a different network id, and slot 14 does not move. Name hashes are recorded in the local audit JSON and are not repeated here. A hash collision is evidence that the name string is not a unique identity. It is not evidence of who the ghost is.

The moving non-player slots in this file are the indexed PB and rival slots. An unselected slot is not the source of the ghost samples. The samples can still be attributed to the wrong role if a consumer ignores the index and treats every non-player moving slot as the rival, because slot 1 and slot 4 both move.

## Suzuka Practice capture

This file is a non-Time-Trial control. It is not a rival ghost.

| Item | Value |
| --- | --- |
| File | `D:\F1-Engineer\recordings\f1e-20261008T004412-5c1255a0.f1ecap` |
| Bytes | 159,837,027 |
| SHA-256 | `0c57129347fd253813ac4c801058b02a7656ba85839fa0265f57046ea4c530f4` |
| Created | `2026-10-08T00:44:12.887875+00:00` |
| Footer | Complete, 144,189 received and recorded, format 2025 |
| Header | Game 25.1.26, `packetVersion` 1, 0 header errors |
| Session | UID `8870022886893926208`, practice 3, game mode `driver_career_25` (28), ruleset practice/qualifying, Suzuka, track length 5,809 m |
| Player index | 7 on the only selection row, at session time 0 |
| PB index | 255 on all 24,236 lap packets |
| Rival index | 255 on all 24,236 lap packets |
| Packet 14 | Absent. Packet ids present are 0, 1, 2, 3, 4, 5, 6, 7, 10, 11, 12, 13, and 15. |
| Verdict | `not_established`. `blocks_detailed_comparison` is null because no rival slot was selected. |

`m_numActiveCars` is 20. The player slot's telemetry peak is 338 km/h, and every other moving slot in the summary peaks between 271 and 331 km/h. Result-active frames equal lap frames for those slots. Practice cars can carry a full telemetry array with active result status. That does not assign any of them to a Time Trial rival index. The rival index in this file is the documented missing value.

## Shanghai capture

`D:\F1-Engineer\data\d0058-browser-capture-root\shanghai-recovered.f1ecap` is the recovered practice file already described in `docs/telemetry-protocol.md`. It is a non-Time-Trial control. Architecture line 1392 calls paired player and opponent telemetry in that practice session a diagnostic comparison. That wording is not a Time Trial ghost.

| Item | Value |
| --- | --- |
| Bytes | 839,456,137 |
| SHA-256 | `825e8ac560c40a4d21f04331222f91b19d852a773b584e11dfe10fef310ad21e` |
| Created | `2026-10-03T03:32:07.772308+00:00` |
| Footer | Incomplete. `truncated_record_at_eof`, 46 trailing bytes discarded, 757,517 recovered datagrams. `reader_error` is null because the audit kept the datagrams already read. |
| Header | `packetFormat` 2025, game 25.1.26, `packetVersion` 1, 0 header errors, 0 decode errors |
| Sessions | Six. Sprint shootout 3, sprint shootout 3, sprint shootout 2, sprint shootout 2, sprint shootout 1, and practice 1. Track Shanghai. Game mode driver career where the Session packet supplied it. |
| Player index | 7 on the single selection row of each session |
| PB and rival indices | 255 for every Lap Data packet. Rival `index_in_range` is 0 in every session. `selection_transitions` is 0. |
| Packet 14 | Absent from the packet histogram. |
| Verdict | `not_established` in every session. `blocks_detailed_comparison` is null. |

Rival lap-packet counts, all with the missing index, are 19,965, 9,340, 11,468, 5,076, 26,805, and 55,122. No role in the report has a speed sample at or above 400 km/h. The file shows ordinary multi-car career sessions with the Time Trial index left at the documented missing value. It is not a rival trace, and the truncated footer keeps it out of the reference-eligibility path the protocol notes already describe.

## Failure cases

| Case | What this evidence shows |
| --- | --- |
| Missing rival | Index 255 on the first 276 Melbourne lap packets, and on all 24,236 Suzuka lap packets. Packet 14 can still be present in the Melbourne opening seconds. A missing index is not a car, and an early packet 14 dataset is not the later car-4 trace. |
| Invalid index | No out-of-range index other than 255 appeared. The 2026 synthetic fixture uses a valid PB index of 23 and rival 255 (`tests/test_season_pack_2026.py` lines 387–388). |
| Selection change | Melbourne changes 255 to 4 once, at 5.443 s, and the PB index stays 1. No stale movement is counted. A change between two in-range rivals is not in these captures. `tests/test_time_trial_rival_evidence_audit.py` builds that change in a synthetic stream and counts stale movement on the slot that was left. |
| Rival index equal to the player | Not observed. The audit refuses to sample a role when the selected index is the player index, so a collision cannot become a detailed trace. |
| PB ghost mistaken for the rival | Both slots move and both peak at 486 km/h. Their lap times differ: 78,349 ms on car 1 and 78,129 ms on car 4. `index_equals_other` is 0. |
| Stale slot | Not observed in the real captures. The synthetic test is the demonstration. |
| Inactive result status with populated channels | Observed on both Melbourne ghost slots for the whole selected window. |
| Implausible speed with an internally consistent distance rate | Observed. The half-frame disagreement gate does not fire. The 400 km/h count and the zero speed trap still block a detailed comparison. |
| Lap wrap without a new lap number | Three rival wraps of about one track length while the lap number stays 2. |
| No valid player session best | Packet 14 has zero valid positive player session-best times. The pipeline's existing Melbourne finding of no eligible PB reference still stands. |
| Names as identity | Shared name hashes and `driverId` 255. |
| Non-TT session | Suzuka and all six recovered Shanghai sessions keep the sentinel index and omit packet 14 while other cars produce ordinary telemetry. Shanghai's footer is truncated. |
| 2026 capture absent | No measured 2026 rival index, packet 14, or ghost channel. |

## Proposals

These are proposals for a later change. This audit does not implement them.

1. Persist `time_trial_pb_car_index` and `time_trial_rival_car_index` with session UID, overall frame, and session time. Store 255 as missing.
2. Decode packet 14 as three timing summaries. Use the 24-byte dataset for packet format 2025 and the 25-byte dataset for packet format 2026. Keep sector times, the valid flag, car index, and team id. Do not create samples from this packet.
3. Bind a detailed comparison to the indexed slot only on frames where the index is in range, different from the player, result status is 2 or higher, and the same overall frame has lap distance, telemetry, and motion. Require the speed magnitude to be credible against the driven car in that session. The Melbourne ghost fails this gate.
4. When that gate fails, publish the packet 14 and Session History times only. Do not invent brake points, minimum-speed points, or a path from the lap time or from the index.
5. Keep the player's saved valid reference lap fixed for the attempt, and keep it separate from the in-game PB ghost index. This capture has no eligible saved player lap. The in-game PB ghost is slot 1.
6. Do not use a name, a shared name hash, a team id, or `driverId` 255 as a persistent rival identity.
7. The all-car archive already stores the slot channels without a role. A later binder can label those rows. It has to apply the result-status gate before calling a row the rival's trace. A 486 km/h sample currently passes `make_car_observation`'s 500 km/h ceiling.
8. Repeat the measurement on a real `packetFormat` 2026 Time Trial before enabling the 2026 path. The wider team id and the 24-car arrays are specified. They are not demonstrated here.

## Capture experiment still required

The open questions are a ghost whose result status is active, a rival change between two in-range ghosts, a valid player session best beside that rival, and any real 2026 Time Trial.

Record two sessions into a finished capture. Leave the capture files outside git.

Settings, each session:

- UDP telemetry on, destination this PC, port 20777, send rate 60 Hz.
- Session A: F1 25 UDP format, so the header `packetFormat` is 2025.
- Session B: 2026 Season Pack UDP format, so the header `packetFormat` is 2026. A 2025 file does not answer this session.
- Mode: Time Trial. A saved rival ghost must exist before the session. Melbourne is the useful repeat of session A. Any 2026 track is enough for session B if the Session packet's track length is present.

Drive, in one continuous recording for each format:

1. Start with no rival selected if the game allows it. Complete one flying lap. The expected Lap Data rival index is 255.
2. Select a rival ghost that is not the personal-best ghost. Complete one full lap. Note the on-screen rival name and lap time. The note is a lookup aid for the later packet 14 time. It is not identity.
3. Change to a different rival ghost without leaving the session. Complete another full lap.
4. Set a valid session-best lap if the game accepts one, so packet 14's player session-best dataset can show a positive time with `m_valid = 1`.
5. Optional: flash back or restart once, to see whether the index and the slot contents survive the boundary.

Inspect, with `scripts/audits/time_trial_rival_evidence.py` or an equivalent read-only decode:

- Lap Data trailing indices, including 255 and each change.
- Packet 14 size (101 bytes for 2025, 104 for 2026), the three car indices, team ids, lap and sector times, and valid flags.
- For the player slot, the PB index, the rival index, and one unselected slot: result status, `m_numActiveCars`, lap distance, lap number, speed, brake, throttle, gear, speed trap, and world position, joined on session UID and overall frame.
- Session History whose car index equals the rival index, compared with the player history. Expect the current pipeline to skip the rival history.

Acceptance for a verified detailed comparison, all of which must hold on a real capture:

- The rival index stays in range for a full lap, differs from the player index, and differs from the PB index.
- Result status on those frames is not 0 or 1, and the index is inside `m_numActiveCars` or the report explains an official exception. None is documented today.
- Same-frame speed, brake, throttle, gear, lap distance, and position are present and are not copies of the player while both are moving.
- Speed stays on the scale of the driven car in that session. In the Melbourne capture that scale is a telemetry peak of 324 km/h and a speed trap of 321 km/h. A peak of 486 km/h with a speed trap of 0 does not pass.
- Lap distance advances around the track. A wrap either increments the lap number or is reported as a repeated loop of one stored lap. Min-to-max span alone is not enough.
- Packet 14's valid rival lap time matches Session History for that car index. The time is not used as the trace.
- After the index changes, later samples come from the new slot. The previous slot is not still labelled as the rival.
- Session B shows the same gates with 24-car packets and a 104-byte Time Trial packet.

If a selected rival never leaves result status 0 or 1, the supported comparison remains the packet 14 time.

## Reproduction

From the audit worktree, read-only:

```text
uv run --frozen --extra dev python scripts/audits/time_trial_rival_evidence.py --json-out %TEMP%\tt-rival-melbourne.json D:\F1-Engineer\recordings\session-aus-mclaren-1.f1ecap
uv run --frozen --extra dev python scripts/audits/time_trial_rival_evidence.py --json-out %TEMP%\tt-rival-20261008.json D:\F1-Engineer\recordings\f1e-20261008T004412-5c1255a0.f1ecap
uv run --frozen --extra dev python scripts/audits/time_trial_rival_evidence.py --json-out %TEMP%\tt-rival-shanghai.json D:\F1-Engineer\data\d0058-browser-capture-root\shanghai-recovered.f1ecap
```

This audit ran those captures with the worktree's existing environment:

`C:\Users\massi\AppData\Local\Temp\tt-rival-audit-venv\Scripts\python.exe`

The script imports only the standard library and `f1_engineer`. It does not modify the capture. JSON reports stayed in the temp directory and are not part of the commit. Official PDFs and the structures file stayed in the temp directory as well.

Classifier constants are in `scripts/audits/time_trial_rival_evidence.py` lines 45–51. `conclude_rival` is at line 105. The result-status and active-car gate is `_activity_gate` at line 182. A speed-versus-distance failure is `reported_speed_disagrees_with_lap_distance`, added only when more than half of at least 20 compared frames disagree (line 165). The channel label can still read `detailed_channels_with_lap_coverage` when that gate blocks the comparison.

Focused tests, from the same worktree:

```text
uv run --frozen --extra dev python -m pytest tests/test_time_trial_rival_evidence_audit.py -q --tb=short
```

The run recorded for this report used the temp environment above and passed 9 tests. Re-run the `uv` command before relying on a later checkout.

The audit script's join buffer holds 256 overall-frame keys in receive order. It is not the production frame assembler. Melbourne and Suzuka both reported zero late lap packets, so that buffer did not drop a lap packet in those files.
