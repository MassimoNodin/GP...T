# AI F1 Race Engineer

A local-first race engineer for F1 25 and its 2026 Season Pack. The system is being built in layers: raw telemetry capture, deterministic analysis, coaching decisions, and finally natural-language and voice features.

## Current slice

The foundation captures raw UDP datagrams, inspects captures, and replays them through the same packet-header and session pipeline. During app-managed recording, the dashboard also shows the latest synchronized player lap state, validity, pit/driver status, speed, gear, RPM, throttle, and brake with an explicit freshness state. Captures retain unknown and malformed datagrams that reach the recorder; the bounded UDP receive queue reports any packets it had to drop.

F1 25 Session v1, Lap Data v1, Participants v1, Car Telemetry v1, Motion v1, and Car Status v1 are decoded. The 2026 Season Pack also has typed Session, Lap Data, Participants, Car Telemetry, Motion, and Car Status v1 adapters. Importing a capture synchronizes player input, Motion, and exact-frame Car Status evidence to Lap Data frames, retains missing packet families as nullable values with explicit availability, stores session/lap metadata and participant snapshots in SQLite, and writes one checksummed Parquet trace for every completed, invalid, partial, or abandoned attempt. New traces use schema v3; schema-v1/v2 traces remain readable and report Car Status as unavailable. The quality report includes per-field status validity and frame provenance; fuel quantities retain the source's reported values without a claimed unit. The 2026 adapters are validated against the pinned EA specification and synthetic captures; real 2026 capture validation remains pending. Race and Time Trial captures share this pipeline; automatic reference eligibility remains conservative and currently permits only known, valid F1 25 Time Trial laps.

The analysis CLI compares two explicitly selected, completed Time Trial attempts on a shared distance grid. It reports the delta curve, per-channel coverage and gaps, and the official lap-time difference separately. Invalid laps can be compared for diagnosis, with their exclusion reasons included in the result. `quality` reports standalone evidence for any stored attempt, including invalid and partial laps, without requiring a comparison reference. `regions` reports per-attempt distance-window observations for explicitly selected packaged models, including invalid and partial Time Trial attempts when no comparison reference is available. These windows remain draft diagnostic evidence and do not produce losses, rankings, or coaching. `reference` selects the fastest eligible prior Time Trial attempt from the same imported run/session/player and includes that comparison; it abstains when no reference qualifies. `trajectory` exports a full-fidelity observed world-space path. The local FastAPI service exposes session browsing, explicit comparison, reference evidence, per-attempt quality, bounded trajectory previews and region observations, the packaged track-model catalog, a recording inbox, and app-managed UDP recording controls to the Next.js historical lap explorer. The inbox imports existing `.f1ecap` files through durable jobs. Trajectory plots are labelled world X/Z observations with retained discontinuities and source provenance; they do not claim a centreline or coaching diagnosis. Region requests select a registered model ID and revision; they cannot provide model paths.

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

Run the local API and dashboard in separate terminals. The dashboard can start and stop UDP recording directly; finalized captures appear in the inbox for explicit import:

```powershell
uv sync --extra app
uv run --extra app f1-engineer api --database data/f1-engineer.sqlite3 --recordings-root recordings --control-token-file data/.f1-engineer-control-token
cd web
npm ci
npm run dev
```

The API binds to `127.0.0.1:8765`; its managed UDP recorder listens on `0.0.0.0:20777` by default so the game can send telemetry to the PC. Set `--udp-host`, `--udp-port`, or `--udp-queue-size` on the API command to change those recording settings. Next.js serves the dashboard at `http://127.0.0.1:3000`. Set `F1_ENGINEER_API_URL` only if the local API uses a different loopback URL. The API's database path and recording root are selected at server startup and are never accepted from a request. The API creates a random control token at the configured token-file path; the Next server reads it for same-origin recording and import actions, and it is never sent to browser code. If you choose a different token-file path, set `F1_ENGINEER_CONTROL_TOKEN_FILE` for the Next server to that same path.

Start recording from the dashboard before driving and stop it after the session. Stop drains queued datagrams, writes and syncs the capture footer, then publishes the `.f1ecap` file into the inbox. A finalized capture can still have packet drops or invalid laps; inspect its capture-quality and lap evidence before treating it as a reference. Starting a capture while an import is running, or importing while recording, is rejected until the current operation finishes. If the API exits unexpectedly, its `.part` file is retained and marked interrupted; it is not resumed or imported automatically.

`record` listens on `0.0.0.0:20777` by default. Set the game's UDP telemetry destination to the computer's local address and port 20777. Use `Ctrl+C` to stop recording. `--speed 0` replays as fast as possible; positive values replay relative packet timing at that multiplier.

Capture paths are created exclusively by default. Pass `--overwrite` only when you intend to replace an existing capture.
`import` defaults to `data/f1-engineer.sqlite3` and stores Parquet traces beneath `data/f1-engineer.sqlite3.traces/`. Pass `--database path.sqlite3` to choose another database; its traces are stored in a database-specific sibling directory. Importing the same capture with the same pipeline version and configuration is idempotent. If a run was interrupted, rerunning `import` resumes it by rebuilding that run's traces from the raw capture. Simultaneous imports of the same run into one database are rejected while the active OS lock is held.
`compare` uses `lap_distance_m` and the lap clock from stored traces, defaults to a 1 m grid, and does not extrapolate over unsupported gaps. By default both attempts must be completed laps with known, compatible Time Trial context. To compare an explicitly selected practice or qualifying pair, pass `--comparison-policy practice_qualifying`; both laps must come from the same completed run, game session and player, have a positive official time, an observed start and no pit encounter. These comparisons remain diagnostic: fuel load, tyres, traffic and cooldown intent are not controlled, and they do not produce coaching or eligible references. Comparison output carries bounded Car Status and session-context observations for each lap: matched/missing samples, valid/missing/invalid field counts, first and last observed fuel and tyre values with frame/time/distance anchors, compound IDs with formula-aware labels, and weather/temperature context. These are observations within the stored trace, not guaranteed lap-boundary values; they do not certify equal conditions or normalize lap times. Legacy traces without Car Status report that evidence as unavailable. Comparison output also carries the source capture footer and persisted replay counters, including explicit incomplete status and unknown legacy counters. Race comparison is deferred to a separate policy with race lifecycle/restart/neutralization acceptance; standalone Race traces, quality and trajectories remain available. The command never chooses a personal best automatically.
`reference` currently implements `session_best`: it selects the fastest eligible prior lap from the same processing run, session, and player, then runs the distance comparison. It requires matching known Time Trial settings and weather/temperature, plus a finalized capture with no recorded receive losses or replay frame-assembly losses. It lists why each candidate was excluded and returns `no_eligible_reference` when none pass. Comparison may be reported as unavailable if the selected laps do not have a supported overlapping distance range. Race reference policy and all-time personal-best selection remain unsupported.

The local API's `GET /api/v1/track-models` lists packaged model metadata. Add `track_model_id` and `track_model_revision` to `GET /api/v1/compare/laps` to request region evidence; both are required together and must identify a registered model. The browser never chooses a model by filesystem path. Region evidence remains explicitly diagnostic, and is currently supported only for compatible Time Trial comparisons.

`trajectory` exports a versioned JSON record of observed world-space samples, with frame/distance/time anchors, trace checksum and session-context provenance. Missing Motion, frame gaps, distance/time regressions, session-time gaps over 100 ms, lap-clock rewinds over 20 ms, and position jumps over 25 m split the output into separate segments. The artifact includes those continuity limits, keeps G-forces in lateral/longitudinal/vertical vehicle-relative axes, and is explicitly diagnostic rather than a track centreline; use `--overwrite` to replace an existing output.

Pass `--track-model f1_engineer/tracks/data/melbourne_f1_25_tt_draft_v1.json` to include the current diagnostic region analysis. Those six regions are draft windows around braking observations in two game-invalid Melbourne laps; they are not verified corner numbers or apex locations. The analysis reports censored braking/throttle onset, minimum-speed support, steering-only turn-in proxies, exit-speed coverage, and delta changes. It does not produce coaching from draft definitions.

Use `regions ATTEMPT_KEY --track-model-id melbourne-f1-25-time-trial-draft-v1 --track-model-revision 1` to inspect one Time Trial attempt without selecting a reference. Invalid, partial, and abandoned attempts remain diagnostic; Race, unknown, changing, or model-incompatible contexts return an explicit unavailable reason. Source traces and event examples are bounded by server-controlled limits.

On the dashboard, a selected draft distance region can be highlighted on the same attempt's observed world-space path. Linking checks the attempt, run, session, player, trace checksum, and schema. Full-source position counts and frame/distance/time/XYZ anchors remain distinct from the thinner plotted preview, and no path is joined across recorded discontinuities. This inspection remains diagnostic; it does not validate track geometry or a centreline.

Every selected attempt also exposes standalone speed, throttle, brake, and steering traces on recorded session time, regardless of game mode or comparison eligibility. Missing channel values and frame/time discontinuities split the plotted runs. Export the same bounded versioned preview with `f1-engineer traces ATTEMPT_KEY --output traces.json`; the output preserves source anchors and reports any point or run reduction.

The dashboard's run summaries review persisted capture size/hash, recorder losses, replay assembly counters, decoder/import counts, session and attempt status, and stored reference-eligibility flags. The summary pages link to bounded session and attempt pages and do not reread capture files or telemetry traces. Capture completion, game validity, and reference eligibility remain separate evidence; the summary does not certify a capture as ready for coaching.

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
