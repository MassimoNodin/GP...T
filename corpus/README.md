# Telemetry corpus

The corpus holds real F1 25 sessions used to stress-test the pipeline and to
accept or reject features (see [feasibility rules](../docs/feasibility-rules.md)).
The recordings are large binary files, so they are not committed; only this
catalog is. Keep the files on the machine that sends stress tests (the Windows
PC) under `corpus/`, and never modify them once they are cataloged.

A recording is identified by its **payload digest**: the datagram count plus
the hex sum printed by `f1-rec info`. The digest depends only on the datagram
bytes. The file's SHA-256 identifies one exact copy; it changes if the file is
re-converted, because the footer records its close time.

| File | Content | Datagrams | Span | Rate | Payload digest | Sessions |
| --- | --- | ---: | ---: | ---: | --- | ---: |
| `jeddah-race.f1r` | Jeddah race, 2026-10-15 | 444,689 | 23m49s | 311/s | `444689/f8a2be782616972b` | 1 |
| `shanghai-recovered.f1r` | Shanghai, recovered capture, 2026-10-03 | 757,517 | 43m03s | 293/s | `757517/f732c3eed4030738` | 7 |
| `session-20261008.f1r` | Session, 2026-10-08 | 144,189 | 6m52s | 350/s | `144189/1fe108d7bec1c4be` | 1 |
| `melbourne-practice.f1r` | Melbourne practice, McLaren | 83,139 | 4m25s | 314/s | `83139/0f53c13cad2a1ef1` | 1 |

All four were converted with `f1-rec convert` from the legacy `.f1ecap`
captures in the old checkout:

- `recordings/jeddah-race-20261015.f1ecap`
- `data/d0058-browser-capture-root/shanghai-recovered.f1ecap`
- `recordings/f1e-20261008T004412-5c1255a0.f1ecap`
- `recordings/session-aus-mclaren-1.f1ecap`

Datagram and per-packet-type counts match the legacy tool's own totals, and
`f1-rec verify corpus` passes.

## Current worst cases

- **Longest single session:** `jeddah-race.f1r` (a full race with a full grid).
  This is the default acceptance recording.
- **Highest sustained rate:** `session-20261008.f1r` (350 datagrams/s).
- **Many session transitions:** `shanghai-recovered.f1r` (7 session UIDs).

Packet rates in these sessions are set by the game's UDP send-rate setting,
not by on-track action. A recording made at a higher send rate (e.g. 120 Hz)
would become the new worst case, and accepted features would have to be
re-tested against it.

## Adding a recording

1. On the PC running the game, record the session, forwarding to the ingest so
   the live archive gets it too:

   ```powershell
   f1-rec record --out corpus\<name>.f1r --forward 192.168.1.115:20777 --label "<what, where, conditions>"
   ```

2. Check it with `f1-rec verify corpus\<name>.f1r` and `f1-rec info corpus\<name>.f1r`.
   Losses must be zero; otherwise re-record.
3. Add a row to the table above with the payload digest from `info`.
