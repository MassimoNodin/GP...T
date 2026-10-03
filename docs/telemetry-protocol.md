# EA telemetry protocol notes

The current EA F1 25 / 2026 Season Pack specification post provides separate UDP modes for the original F1 25 format and the 2026 Season Pack format. EA labels the documentation revision **Version 11.0**; that value is distinct from the per-packet `packetVersion` in the 29-byte header.

The EA post was checked on 2026-10-03 and labels its documentation revision **Version 11.0**. For F1 25, the implementation also references EA's [F1 25 UDP data output v3 PDF](https://forums.ea.com/t5/s/tghpe58374/attachments/tghpe58374/f1-games-game-info-hub-en/61/4/Data%20Output%20from%20F1%2025%20v3.pdf), retrieved on 2026-10-02. The official 2026 attachments were retrieved on 2026-10-03 and pinned by SHA-256:

| Official attachment | SHA-256 |
| --- | --- |
| [2026 Season Pack Telemetry Output Structures (1).txt](https://forums.ea.com/t5/s/tghpe58374/attachments/tghpe58374/f1-games-game-info-hub-en/61/8/2026%20Season%20Pack%20Telemetry%20Output%20Structures%20(1).txt) | `4f97867924f5f13f11b7fde6eb84ccb3aefcaf69062424bc077e387090647c72` |
| [Data Output from F1 25 2026 Season Pack (Season 8).pdf, document v1.2](https://forums.ea.com/t5/s/tghpe58374/attachments/tghpe58374/f1-games-game-info-hub-en/61/10/Data%20Output%20from%20F1%2025%202026%20Season%20Pack%20(Season%208).pdf) | `3f38858c3ca65b2faa55a90a35277dd2767bb9cea2911e741b61b370a39365ae` |

The selected PDF is EA's current Season 8 document v1.2; it adds F2 driver IDs. The Session, Lap Data, and Car Telemetry sizes and consumed field widths described below match the structures attachment and the earlier v1.1 PDF. The complete attachments are not vendored. Keep exact supported adapters versioned by `(packet_format, packet_id, packet_version)` and derive layouts only from these EA sources.

The envelope decoder recognizes `packetFormat` 2025 and 2026. The shared header fields are decoded little-endian. Packet 15 (`LapPositions`) is present in both formats; packet 16 (`CarTelemetry2`) is specific to 2026. Unknown packet IDs and unsupported packet bodies remain raw and uninterpreted. Typed body support is F1 25 (`packetFormat=2025`) Session (1), Lap Data (2), Event (3), Participants (4), Car Telemetry (6), Car Status (7), Session History (11), and Motion (0), plus 2026 Season Pack (`packetFormat=2026`) Session (1), Lap Data (2), Event (3), Participants (4), Car Telemetry (6), Car Status (7), Session History (11), and Motion (0); each supported packet version is v1.

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

## 2026 Season Pack Motion packet v1

EA documents a 1,325-byte packet including the common header. Its 1,296-byte body contains 24 packed 54-byte `CarMotionData` records. The record has six float32 values for world position and velocity, six signed int16 values for forward/right directions, three signed int16 G-force components, and three float32 angles. Direction components are divided by `32767.0`; quantized lateral, longitudinal, and vertical G-force values are divided by `1000.0` and retained in g units. Angles remain radians.

The decoder validates the full body size and independently retains valid vector and orientation groups when another group is non-finite or invalid. It decodes all 24 records and joins only the header-designated player record to Lap Data in the same assembled session/frame. Missing Motion is null and is never carried between frames. The existing schema-v2 trace and observed-trajectory export already carry these fields and units.

## 2026 Season Pack Participants packet v1

EA documents a 1,470-byte packet including the header. Its 1,441-byte body contains a one-byte active-car count and 24 packed 60-byte participant records. Driver, network, and team IDs are unsigned 16-bit values; the adapter preserves values above 255 and sentinel `65535` without truncation. Names are decoded as UTF-8, and participant metadata remains indexed by vehicle index and stored as session-scoped snapshots.

## Car Status packet v1

EA documents F1 25 Car Status as a 1,239-byte packet including the 29-byte header. Its 1,210-byte body contains 22 packed 55-byte vehicle records. The 2026 Season Pack packet is 1,445 bytes total; its 1,416-byte body contains 24 packed 59-byte records and inserts `m_ersHarvestLimitPerLap` before deployed-this-lap. Both adapters dispatch by format, packet ID 7, and packet version 1, then validate the exact body length.

The canonical sample persists selected setup, fuel, tyre, DRS, FIA-flag, and network-pause fields. Fuel-in-tank and fuel-capacity values retain the reported float values without unit metadata; the EA field descriptions do not specify a unit. `fuel_remaining_laps` is in laps and finite negative values are retained. FIA flag `-1` remains the unknown/invalid sentinel; raw compound IDs remain intact if their formula-specific label is unknown. Individual malformed fields become null with validation flags while other fields in a matched Status packet remain available.

Status joins only the header-designated player record from the same admitted session, wire format, assembled overall frame, and matching primary-player index as Lap Data. Missing, malformed/unsupported, format-mismatched, player-mismatched, or conflicting evidence has an explicit unavailable reason. Values are never carried forward or interpolated. New traces use schema v3; readers support schemas v1/v2 and mark their absent Status fields unavailable. The quality report shows exact matched counts, per-field valid/missing/invalid counts, first/last frame provenance, sampled discrete changes, and formula-context-dependent compound labels. It does not infer fuel consumption, tyre sets/stints, or reference eligibility from Status.

The supplied Melbourne capture contains 13,949 Car Status v1 datagrams; all are 1,239 bytes and decode successfully. Their 13,949 unique session/format/frame/player keys match 13,949 of the 13,950 Lap Data keys; there are no Status-only keys, and Lap frame 2,793 has no Status key. Import persisted 10,247 player-attempt samples, all with exact-frame Status matches. The attempt inventory remains two game-invalid completed laps and one partial lap, with no eligible references. The 92 MB capture remains local and is not checked in.

## Event packet v1

EA documents Event packet ID 3 as version 1 and 45 bytes total in both supported formats. The 16-byte body contains a four-byte event code and a 12-byte event-detail union. Dispatch by `(packet_format, packet_id, packet_version)`; preserve the exact code and all 12 detail bytes for documented packets, and interpret only the union variant selected by a recognized code. Malformed bodies retain a maximum 12-byte prefix plus original detail length and a truncation flag; complete raw datagrams remain in the capture. For `FLBK`, the first eight detail bytes are a little-endian `uint32` target game-frame identifier and float32 target session time; remaining bytes stay raw. `SSTA` and `SEND` carry no lifecycle meaning beyond observed annotations. The decoder recognizes the documented F1 25 codes plus the 2026-only `PMEN`, `PMDI`, `OVEN`, and `OVDI` codes. Unknown codes, unsupported versions, and malformed bodies conservatively create an uncertain boundary. The 2026 attachment is the pinned Season 8 v1.2 source listed above; the F1 25 source is EA's F1 25 v3 PDF linked above (retrieved 2026-10-02).

The recovered Shanghai capture contains 4,000 raw Event v1 datagrams, all 45 bytes: 5 `SSTA`, 5 `SEND`, and no `FLBK`. Frame admission accepts 3,770: 85 byte-identical `BUTN` duplicates and 140 late frame-zero `BUTN` packets are ignored, along with five late frame-zero `SEND` annotations. The admitted inventory contains 5 `SSTA`, no `SEND`, and no `FLBK`; none of the excluded datagrams are flashbacks, and no Event packet was lost to overflow. The dashboard labels this as admitted Event packets. The supplied Melbourne capture contains 320 raw Event v1 datagrams, all 45 bytes, with no session-boundary or flashback events. These captures validate decoding and session annotations only; synthetic streams validate rewind lifecycle behavior until a real flashback capture is available.

## Session History packet v1

EA documents Session History as packet ID 11 with a 1,460-byte total size in both supported formats. The 1,431-byte body begins with seven one-byte fields: car index, populated lap count, tyre-stint count, and best-lap/best-sector lap numbers. It is followed by a fixed array of 100 14-byte lap records and eight 3-byte tyre-stint records. Each lap record preserves the reported lap time, three sector millisecond/minute parts, and validity bitfield. Each stint retains its end-lap marker and actual/visual compound IDs. The adapter dispatches by format, packet ID, and v1, validates counts and format-specific car-index bounds (22 for F1 25, 24 for 2026), and preserves unknown markers and validity bits.

Session History is a periodic full snapshot for one car, not frame-synchronized telemetry. The populated row count may include a current partial lap, but the last row is not discarded solely because it is last. Matching requires an existing completed attempt, exact player/session/format/association-epoch/lap-number identity and exact reported lap time, with the source frame admitted strictly after the attempt's completion-observation ordinal. Same-frame history is rejected. Source frame identifiers, internal ordinal, capture sequence, best markers, tyre stints, raw sector parts, validity flags, and sector-sum residual are retained. A raw zero or out-of-range millisecond part remains visible in the source row while the normalized sector value is reported unavailable.

Reported timing can be inspected for any session mode. Sector deltas appear only under the existing Time Trial and Practice/Qualifying completed-lap comparison policies. They are target-minus-reference values separate from distance-based delta; game validity and per-sector validity remain distinct. Race/unknown modes gain evidence inspection only. This packet family never creates or repairs attempts and never changes reference eligibility.

Capture replay acceptance: the Melbourne capture admitted 4,644 Session History packets; 310 primary-player snapshots decoded, 4,334 opponent snapshots were skipped, and the two completed invalid Lap Data laps matched. Lap 1 reports sectors 27,446/17,987/33,861 ms with validity flags `0x02`; lap 2 reports 29,776/17,801/33,860 ms with flags `0x06`. Both retain their original game-invalid status and remain ineligible as references. The recovered Shanghai capture admitted 42,626 History packets; 2,719 primary-player snapshots decoded, 39,907 opponent snapshots were skipped, and there were no decode errors or pipeline drops. Across six Shanghai sessions, eight attempts matched, none remained conflicting, twelve were unavailable, and none were truncated. Shanghai Practice attempts 2/3 match source laps 4/5 at 100,440/99,208 ms with sectors 26,598/30,525/43,316 and 25,988/29,705/43,513 ms. Sector-sum residuals are -1 and -2 ms. The final frame-zero bulk snapshots were not admitted around the frame assembler; no late packet was promoted for matching. The recovered Shanghai capture still has an incomplete footer (`truncated_record_at_eof`, 46 trailing bytes discarded), so that capture remains reference-ineligible.

## 2026 compatibility and validation status

The 2026 Session, Lap Data, Car Telemetry, Motion, Participants, and Car Status adapters are specification-derived and synthetic-fixture validated. Synthetic capture import, query, trace, and trajectory export cover Motion and participant evidence; Car Status has typed-layout and trace coverage. No real 2026 capture has validated their end-to-end behavior. Session contexts and attempts are retained for Time Trial, Race, and unknown modes, but automatic reference eligibility remains restricted to validated F1 25 Time Trial. Car Telemetry 2 and all other 2026 packet bodies remain opaque; missing channels remain unavailable.

## F1 25 Motion packet v1

EA documents a 1,349-byte packet including the 29-byte header. The 1,320-byte body contains 22 packed 60-byte car records. Each record carries world position and velocity as XYZ float32 vectors, forward and right directions as signed int16 XYZ vectors, three G-force values, and yaw/pitch/roll in radians. Direction components are divided by 32767.0 and accepted only when the resulting vector norm is within the implementation's 0.9–1.1 validity tolerance.

The pipeline decodes all 22 records but initially persists Motion for the header-designated player car. It joins Motion and Lap Data only inside the same assembled session/frame. Missing Motion is kept null; it is never copied from a neighbouring frame. The supplied capture has 13,950 Motion v1 packets, matching 13,950 Lap Data frames. Its two completed laps are game-invalid, so their observed paths are diagnostic evidence rather than validated track geometry. MotionEx remains opaque; no centreline or driver-apex claims are made from this data alone.

The EA post currently links these primary references:

- [F1 25 and 2026 Season Pack UDP specification](https://forums.ea.com/blog/f1-games-game-info-hub-en/ea-sports%E2%84%A2-f1%C2%AE25-2026-season-pack-udp-specification/12187347)
- [EA F1 25 UDP specification discussion](https://forums.ea.com/discussions/f1-25-general-discussion-en/discussion-f1%C2%AE-25-udp-specification/12187351)

The official 2026 sources are pinned above, but most packet bodies remain unparsed. The current decoder validates the common header for every packet and supports the packet families listed above; it does not claim full packet parsing, real 2026 capture validation, or race-mode reference eligibility.
