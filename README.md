# AI F1 Race Engineer

A local-first race engineer for F1 25 and its 2026 Season Pack. The system is being built in layers: raw telemetry capture, deterministic analysis, coaching decisions, and finally natural-language and voice features.

## Current slice

The foundation captures raw UDP datagrams, inspects captures, and replays them through the same packet-header and session pipeline. Captures retain unknown and malformed datagrams that reach the recorder; the bounded UDP receive queue reports any packets it had to drop.

F1 25 Session v1, Lap Data v1, Participants v1, Car Telemetry v1, and Motion v1 are decoded. Importing a capture synchronizes player input and Motion samples to Lap Data frames, retains missing packet families as null values, stores session/lap metadata in SQLite, and writes one checksummed Parquet trace for every completed, invalid, partial, or abandoned attempt. New traces use schema v2; schema-v1 traces remain readable and report Motion fields as unavailable. Race and Time Trial captures share this pipeline; automatic reference eligibility remains conservative and currently permits only known, valid Time Trial laps.

The analysis CLI compares two explicitly selected, completed Time Trial attempts on a shared distance grid. It reports the delta curve, per-channel coverage and gaps, and the official lap-time difference separately. Invalid laps can be compared for diagnosis, with their exclusion reasons included in the result. `reference` selects the fastest eligible prior Time Trial attempt from the same imported run/session/player and includes that comparison; it abstains when no reference qualifies. A versioned, read-only FastAPI service exposes session browsing, explicit comparison, and reference evidence to the Next.js historical lap explorer.

## Requirements

- Python 3.11 or newer
- Windows for the initial F1 25 UDP target; the capture and replay code is platform independent
- Node.js 20.9 or newer for the dashboard

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
python -m f1_engineer reference TARGET_ATTEMPT_KEY
python -m f1_engineer trajectory ATTEMPT_KEY --output data/trajectory.json
```

Run the local API and dashboard in separate terminals after importing a capture:

```powershell
uv sync --extra app
uv run --extra app f1-engineer api --database data/f1-engineer.sqlite3
cd web
npm ci
npm run dev
```

The API binds to `127.0.0.1:8765`; Next.js uses it from the server and serves the dashboard at `http://localhost:3000`. Set `F1_ENGINEER_API_URL` only if the local API uses a different loopback URL. The API's database path is selected at server startup and is never accepted from a request.

`record` listens on `0.0.0.0:20777` by default. Set the game's UDP telemetry destination to the computer's local address and port 20777. Use `Ctrl+C` to stop recording. `--speed 0` replays as fast as possible; positive values replay relative packet timing at that multiplier.

Capture paths are created exclusively by default. Pass `--overwrite` only when you intend to replace an existing capture.
`import` defaults to `data/f1-engineer.sqlite3` and stores Parquet traces beneath `data/f1-engineer.sqlite3.traces/`. Pass `--database path.sqlite3` to choose another database; its traces are stored in a database-specific sibling directory. Importing the same capture with the same pipeline version and configuration is idempotent. If a run was interrupted, rerunning `import` resumes it by rebuilding that run's traces from the raw capture. Simultaneous imports of the same run into one database are rejected while the active OS lock is held.
`compare` uses `lap_distance_m` and the lap clock from the stored traces, defaults to a 1 m grid, and does not extrapolate over unsupported gaps. Both attempts must be completed laps with known, compatible Time Trial context. The command takes an explicit target and reference; it does not choose a personal best automatically.
`reference` currently implements `session_best`: it selects the fastest eligible prior lap from the same processing run, session, and player, then runs the distance comparison. It requires matching known Time Trial settings and weather/temperature, plus a finalized capture with no recorded receive losses or replay frame-assembly losses. It lists why each candidate was excluded and returns `no_eligible_reference` when none pass. Comparison may be reported as unavailable if the selected laps do not have a supported overlapping distance range. Race reference policy and all-time personal-best selection remain unsupported.

`trajectory` exports a versioned JSON record of observed world-space samples, with frame/distance/time anchors, trace checksum and session-context provenance. Missing Motion, frame gaps, distance/time regressions, session-time gaps over 100 ms, lap-clock rewinds over 20 ms, and position jumps over 25 m split the output into separate segments. The artifact includes those continuity limits, keeps G-forces in lateral/longitudinal/vertical vehicle-relative axes, and is explicitly diagnostic rather than a track centreline; use `--overwrite` to replace an existing output.

Pass `--track-model f1_engineer/tracks/data/melbourne_f1_25_tt_draft_v1.json` to include the current diagnostic region analysis. Those six regions are draft windows around braking observations in two game-invalid Melbourne laps; they are not verified corner numbers or apex locations. The analysis reports censored braking/throttle onset, minimum-speed support, steering-only turn-in proxies, exit-speed coverage, and delta changes. It does not produce coaching from draft definitions.

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

See [docs/architecture.md](docs/architecture.md) for the accepted architecture decisions and [docs/telemetry-protocol.md](docs/telemetry-protocol.md) for protocol compatibility notes.
