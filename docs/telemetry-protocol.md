# EA telemetry protocol notes

The current EA F1 25 / 2026 Season Pack specification post provides separate UDP modes for the original F1 25 format and the 2026 Season Pack format. EA labels the documentation revision **Version 11.0**; that value is distinct from the per-packet `packetVersion` in the 29-byte header.

The EA post was checked on 2026-10-03 and labels its documentation revision **Version 11.0**. For F1 25, the implementation also references EA's [F1 25 UDP data output v3 PDF](https://forums.ea.com/t5/s/tghpe58374/attachments/tghpe58374/f1-games-game-info-hub-en/61/4/Data%20Output%20from%20F1%2025%20v3.pdf), retrieved on 2026-10-02. The official 2026 attachments were retrieved on 2026-10-03 and pinned by SHA-256:

| Official attachment | SHA-256 |
| --- | --- |
| [2026 Season Pack Telemetry Output Structures (1).txt](https://forums.ea.com/t5/s/tghpe58374/attachments/tghpe58374/f1-games-game-info-hub-en/61/8/2026%20Season%20Pack%20Telemetry%20Output%20Structures%20(1).txt) | `4f97867924f5f13f11b7fde6eb84ccb3aefcaf69062424bc077e387090647c72` |
| [Data Output from F1 25 2026 Season Pack (Season 8).pdf, document v1.2](https://forums.ea.com/t5/s/tghpe58374/attachments/tghpe58374/f1-games-game-info-hub-en/61/10/Data%20Output%20from%20F1%2025%202026%20Season%20Pack%20(Season%208).pdf) | `3f38858c3ca65b2faa55a90a35277dd2767bb9cea2911e741b61b370a39365ae` |

The selected PDF is EA's current Season 8 document v1.2; it adds F2 driver IDs. The Session, Lap Data, and Car Telemetry sizes and consumed field widths described below match the structures attachment and the earlier v1.1 PDF. The complete attachments are not vendored. Keep exact supported adapters versioned by `(packet_format, packet_id, packet_version)` and derive layouts only from these EA sources.

The envelope decoder recognizes `packetFormat` 2025 and 2026. The shared header fields are decoded little-endian. Packet 15 (`LapPositions`) is present in both formats; packet 16 (`CarTelemetry2`) is specific to 2026. Unknown packet IDs and unsupported packet bodies remain raw and uninterpreted. Typed body support is F1 25 (`packetFormat=2025`) Session (1), Lap Data (2), Participants (4), Car Telemetry (6), and Motion (0), plus 2026 Season Pack (`packetFormat=2026`) Session (1), Lap Data (2), and Car Telemetry (6); each supported packet version is v1.

## F1 25 Session packet v1

EA documents this packet as 753 bytes including the 29-byte common header, so its fixed body is 724 bytes. The adapter validates that body length and the counts for the fixed marshal-zone, weather-sample, and weekend-session arrays. It decodes session type, game mode, ruleset, track, current weather and temperatures, lap count/length, network state, and selected assistance/performance settings. Raw numeric identifiers are preserved when an enum value is unfamiliar; the normalized value is then `null`.

The enum mapping follows the F1 25 v3 appendix. Session type `18`, game mode `5`, and ruleset `2` each mean Time Trial. The official document also states that Time Trial packet 14 is only sent in Time Trial mode, but the canonical mode comes from Session packet fields rather than packet presence.

## F1 25 Lap Data packet v1

EA documents this packet as 1,285 bytes including the 29-byte header. Its 1,256-byte body contains 22 packed 57-byte `LapData` records followed by the Time Trial personal-best and rival car indices. The decoder preserves every record, converts split minute/millisecond timing fields to milliseconds, and retains raw status identifiers. The player lap tracker follows the `playerCarIndex` from each packet header; the capture used for validation consistently identifies car index 0.


The recording at `recordings/session-aus-mclaren-1.f1ecap` contains 467 F1 25 Session v1 packets; all are 753 bytes and consistently report session type 18, game mode 5, ruleset 2, track 0 (Melbourne), and track length 5,276 m. It also contains 13,950 Lap Data v1 packets, all 1,285 bytes. Ordered replay identifies two completed laps (79.295 s and 81.437 s), both marked invalid by the game, followed by a partial third lap when capture ended. The resulting inventory has no eligible Time Trial PB reference. The recording completed with all 83,139 datagrams persisted and no reported queue drops or socket errors. The repository keeps one 753-byte Session datagram as `tests/fixtures/f1_25_session_packet_v1.bin` instead of checking in the 92 MB capture. Fixture SHA-256: `202f887495439430280b5b3090563be5f123b2040bcd457e5899e231a062e4e7`.

## 2026 Season Pack Session packet v1

EA documents a 926-byte packet including the 29-byte header, so its fixed body is 897 bytes. The first 724 body bytes retain the common Session fields used by F1 25. The adapter validates the marshal-zone, weather-sample, weekend-session, full/partial Active Aero, and DRS-zone counts. It decodes the documented common context fields and recognizes Madrid track ID 42; other listed track IDs share their documented names with F1 25. Formula ID 13 is preserved as the F1 26 formula. Active Aero zones remain separate from DRS and are not normalized into legacy DRS fields.

Session type, game-mode, and ruleset IDs retain the documented mappings used by F1 25, including session type 18, game mode 5, and ruleset 2 for Time Trial. Unknown raw IDs remain visible with a null normalized value.

## 2026 Season Pack Lap Data packet v1

EA documents a 1,399-byte packet including the common header. Its 1,370-byte body contains 24 packed 57-byte `LapData` records followed by personal-best and rival car indices. The record fields and units match the shared canonical Lap Data model; both indices are retained, including sentinel 255. Player indexes 0–23 are supported, while 24 and 255 are rejected by the pipeline as out of range.

## 2026 Season Pack Car Telemetry packet v1

EA documents a 1,448-byte packet including the header. Its 1,419-byte body contains 24 packed 59-byte car records and a 3-byte trailer. Compared with F1 25, engine temperature is a `uint8` rather than `uint16`; the adapter uses its own layout so later pressure and surface fields remain correctly aligned. Packet 6's DRS field retains its DRS meaning. Active Aero, Overtake Mode, and wrong-way state are in separate packet 16, which remains opaque and is not mapped onto DRS.

These 2026 adapters are specification-derived and synthetic-fixture validated. No real 2026 capture has validated their end-to-end behavior. Session contexts and attempts are retained for Time Trial, Race, and unknown modes, but automatic reference eligibility remains restricted to validated F1 25 Time Trial. Participants, Motion, Car Telemetry 2, and all other 2026 bodies remain opaque; missing channels remain unavailable.

## F1 25 Motion packet v1

EA documents a 1,349-byte packet including the 29-byte header. The 1,320-byte body contains 22 packed 60-byte car records. Each record carries world position and velocity as XYZ float32 vectors, forward and right directions as signed int16 XYZ vectors, three G-force values, and yaw/pitch/roll in radians. Direction components are divided by 32767.0 and accepted only when the resulting vector norm is within the implementation's 0.9–1.1 validity tolerance.

The pipeline decodes all 22 records but initially persists Motion for the header-designated player car. It joins Motion and Lap Data only inside the same assembled session/frame. Missing Motion is kept null; it is never copied from a neighbouring frame. The supplied capture has 13,950 Motion v1 packets, matching 13,950 Lap Data frames. Its two completed laps are game-invalid, so their observed paths are diagnostic evidence rather than validated track geometry. MotionEx remains opaque; no centreline or driver-apex claims are made from this data alone.

The EA post currently links these primary references:

- [F1 25 and 2026 Season Pack UDP specification](https://forums.ea.com/blog/f1-games-game-info-hub-en/ea-sports%E2%84%A2-f1%C2%AE25-2026-season-pack-udp-specification/12187347)
- [EA F1 25 UDP specification discussion](https://forums.ea.com/discussions/f1-25-general-discussion-en/discussion-f1%C2%AE-25-udp-specification/12187351)

The official 2026 sources are pinned above, but most packet bodies remain unparsed. The current decoder validates the common header for every packet and supports the packet families listed above; it does not claim full packet parsing, real 2026 capture validation, or race-mode reference eligibility.
