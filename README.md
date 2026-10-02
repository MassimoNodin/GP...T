# AI F1 Race Engineer

A local-first race engineer for F1 25 and its 2026 Season Pack. The system is being built in layers: raw telemetry capture, deterministic analysis, coaching decisions, and finally natural-language and voice features.

## Current slice

The foundation captures raw UDP datagrams, inspects captures, and replays them through the same packet-header and session pipeline. Captures retain unknown and malformed datagrams that reach the recorder; the bounded UDP receive queue reports any packets it had to drop.

F1 25 Session v1, Lap Data v1, Participants v1, and Car Telemetry v1 are decoded. Importing a capture synchronizes player input samples to Lap Data frames, retains missing packet families as null values, stores session/lap metadata in SQLite, and writes one checksummed Parquet trace for every completed, invalid, partial, or abandoned attempt. Race and Time Trial captures share this pipeline; automatic reference eligibility remains conservative and currently permits only known, valid Time Trial laps.

The analysis CLI compares two explicitly selected, completed Time Trial attempts on a shared distance grid. It reports the delta curve, per-channel coverage and gaps, and the official lap-time difference separately. Invalid laps can be compared for diagnosis, with their exclusion reasons included in the result.

## Requirements

- Python 3.11 or newer
- Windows for the initial F1 25 UDP target; the capture and replay code is platform independent

PyArrow is used for Parquet traces. SQLite and other capture tools use the Python standard library.

## Commands

Run from the repository root:

```powershell
python -m f1_engineer record --output recordings/session.f1ecap
python -m f1_engineer inspect recordings/session.f1ecap
python -m f1_engineer replay recordings/session.f1ecap --speed 0
python -m f1_engineer import recordings/session.f1ecap
python -m f1_engineer sessions
python -m f1_engineer laps
python -m f1_engineer lap RUN_ID:SESSION_UID:CAR_INDEX:ATTEMPT_NUMBER
python -m f1_engineer compare TARGET_ATTEMPT_KEY REFERENCE_ATTEMPT_KEY
```

`record` listens on `0.0.0.0:20777` by default. Set the game's UDP telemetry destination to the computer's local address and port 20777. Use `Ctrl+C` to stop recording. `--speed 0` replays as fast as possible; positive values replay relative packet timing at that multiplier.

Capture paths are created exclusively by default. Pass `--overwrite` only when you intend to replace an existing capture.
`import` defaults to `data/f1-engineer.sqlite3` and stores Parquet traces beneath `data/f1-engineer.sqlite3.traces/`. Pass `--database path.sqlite3` to choose another database; its traces are stored in a database-specific sibling directory. Importing the same capture with the same pipeline version and configuration is idempotent. If a run was interrupted, rerunning `import` resumes it by rebuilding that run's traces from the raw capture. Simultaneous imports of the same run into one database are rejected while the active OS lock is held.
`compare` uses `lap_distance_m` and the lap clock from the stored traces, defaults to a 1 m grid, and does not extrapolate over unsupported gaps. Both attempts must be completed laps with known, compatible Time Trial context. The command takes an explicit target and reference; it does not choose a personal best automatically.

Install the package to use the `f1-engineer` command:

```powershell
python -m pip install -e .
f1-engineer --help
```

Install the development extra and run the tests with:

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
