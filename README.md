# AI F1 Race Engineer

A local-first race engineer for F1 25 and its 2026 Season Pack. The system is being built in layers: raw telemetry capture, deterministic analysis, coaching decisions, and finally natural-language and voice features.

## Current slice

The initial foundation can capture raw UDP datagrams, inspect a capture, and replay it through the same packet-header and session pipeline. Captures retain unknown and malformed datagrams that reach the recorder; the bounded UDP receive queue reports any packets it had to drop.

The envelope decoder recognizes the two EA UDP format identifiers and normalizes their common packet header. F1 25 Session v1 is decoded into session type, game mode, track, weather, and selected settings. F1 25 Lap Data v1 is decoded for all 22 cars, then used to build a player lap-attempt inventory with validity, completion state, context segments, and Time Trial reference eligibility. Most other packet bodies remain opaque. `inspect` reports the decoded session context and lap attempts; the full capture is not required for inspection or replay.

## Requirements

- Python 3.11 or newer
- Windows for the initial F1 25 UDP target; the capture and replay code is platform independent

The application currently uses only the Python standard library.

## Commands

Run from the repository root:

```powershell
python -m f1_engineer record --output recordings/session.f1ecap
python -m f1_engineer inspect recordings/session.f1ecap
python -m f1_engineer replay recordings/session.f1ecap --speed 0
```

`record` listens on `0.0.0.0:20777` by default. Set the game's UDP telemetry destination to the computer's local address and port 20777. Use `Ctrl+C` to stop recording. `--speed 0` replays as fast as possible; positive values replay relative packet timing at that multiplier.

Capture paths are created exclusively by default. Pass `--overwrite` only when you intend to replace an existing capture.

Install the package to use the `f1-engineer` command:

```powershell
python -m pip install -e .
f1-engineer --help
```

Install the development extra and run the foundation tests with:

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

## Architecture

```text
UDP source ──────┐
                 ├─ raw capture ─ version-aware envelope decoder
Replay source ───┘                              │
                                                ├─ session events
                                                ├─ SessionContext decoder
                                                └─ bounded frame assembly
```

Raw datagrams are written before decoding. That preserves the exact input even when a packet is truncated, unknown, or unsupported by the current decoder. Recording and replay feed a single processing pipeline.

See [docs/architecture.md](docs/architecture.md) for the first implementation decisions and [docs/telemetry-protocol.md](docs/telemetry-protocol.md) for protocol compatibility notes.
