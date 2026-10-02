# EA telemetry protocol notes

The current EA F1 25 / 2026 Season Pack specification post provides separate UDP modes for the original F1 25 format and the 2026 Season Pack format. EA labels the documentation revision **Version 11.0**; that value is distinct from the per-packet `packetVersion` in the 29-byte header.

The post was checked on 2026-10-02. For the current F1 25 Session and Lap Data packet decoders, the implementation also references EA's [F1 25 UDP data output v3 PDF](https://forums.ea.com/t5/s/tghpe58374/attachments/tghpe58374/f1-games-game-info-hub-en/61/4/Data%20Output%20from%20F1%2025%20v3.pdf), retrieved on 2026-10-02. The full specification and 2026 attachments have not been copied into the repository or pinned with checksums; implementation support remains limited to the two documented packets below.

The envelope decoder recognizes `packetFormat` 2025 and 2026. The shared header fields are decoded little-endian. Packet 15 (`LapPositions`) is present in both formats; packet 16 (`CarTelemetry2`) is specific to the 2026 format. Unknown packet IDs and unsupported packet bodies are retained without interpretation. Typed body support currently consists of F1 25 (`packetFormat=2025`) Session packet (`packetId=1`, `packetVersion=1`) and Lap Data packet (`packetId=2`, `packetVersion=1`).

## F1 25 Session packet v1

EA documents this packet as 753 bytes including the 29-byte common header, so its fixed body is 724 bytes. The adapter validates that body length and the counts for the fixed marshal-zone, weather-sample, and weekend-session arrays. It decodes session type, game mode, ruleset, track, current weather and temperatures, lap count/length, network state, and selected assistance/performance settings. Raw numeric identifiers are preserved when an enum value is unfamiliar; the normalized value is then `null`.

The enum mapping follows the F1 25 v3 appendix. Session type `18`, game mode `5`, and ruleset `2` each mean Time Trial. The official document also states that Time Trial packet 14 is only sent in Time Trial mode, but the canonical mode comes from Session packet fields rather than packet presence.

## F1 25 Lap Data packet v1

EA documents this packet as 1,285 bytes including the 29-byte header. Its 1,256-byte body contains 22 packed 57-byte `LapData` records followed by the Time Trial personal-best and rival car indices. The decoder preserves every record, converts split minute/millisecond timing fields to milliseconds, and retains raw status identifiers. The player lap tracker follows the `playerCarIndex` from each packet header; the capture used for validation consistently identifies car index 0.


The recording at `recordings/session-aus-mclaren-1.f1ecap` contains 467 F1 25 Session v1 packets; all are 753 bytes and consistently report session type 18, game mode 5, ruleset 2, track 0 (Melbourne), and track length 5,276 m. It also contains 13,950 Lap Data v1 packets, all 1,285 bytes. Ordered replay identifies two completed laps (79.295 s and 81.437 s), both marked invalid by the game, followed by a partial third lap when capture ended. The resulting inventory has no eligible Time Trial PB reference. The recording completed with all 83,139 datagrams persisted and no reported queue drops or socket errors. The repository keeps one 753-byte Session datagram as `tests/fixtures/f1_25_session_packet_v1.bin` instead of checking in the 92 MB capture. Fixture SHA-256: `202f887495439430280b5b3090563be5f123b2040bcd457e5899e231a062e4e7`.

The 2026 Session and Lap Data packets are not decoded yet. Synthetic tests verify that Race session type is distinguished from its Career game mode and that Race laps do not pass the initial Time Trial reference policy. Real Race-mode behavior and 2026 mode mappings still need capture validation. All other packet bodies remain opaque.

The EA post currently links these primary references:

- [F1 25 and 2026 Season Pack UDP specification](https://forums.ea.com/blog/f1-games-game-info-hub-en/ea-sports%E2%84%A2-f1%C2%AE25-2026-season-pack-udp-specification/12187347)
- [EA F1 25 UDP specification discussion](https://forums.ea.com/discussions/f1-25-general-discussion-en/discussion-f1%C2%AE-25-udp-specification/12187351)

The remaining structures and any attached PDFs need to be pinned by retrieval date and checksum before claiming complete packet support. The current decoder validates the common header for every packet, F1 25 Session v1, and F1 25 Lap Data v1; it does not claim full packet parsing or race-mode lap eligibility.
