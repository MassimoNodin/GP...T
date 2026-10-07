# GP...T

A local race engineer for F1 25.

GP is the race-engineer call on Max Verstappen's radio. The extra T makes GPT, a nod to the AI the engineer is built around. The app runs on your own machine.

GP...T listens to the game's UDP telemetry and keeps the raw capture. From there you can import laps, compare them, and ask about one you have already selected. Answers stay with the recorded evidence. Coaching comes later.

## Purpose

The game gives you a lap time and then moves on. GP...T keeps the capture on the computer that drove the lap, so you can come back and see where two laps differ.

## What you can do

- Record a session from F1 25 or the 2026 Season Pack.
- Import the capture and browse sessions, laps, and capture quality.
- Compare two laps along the track.
- Replay a capture without the game running.
- Ask a question about a lap you selected. A model on this machine answers from the stored evidence.
- Hear a stored report read aloud in the browser.

## Installation and usage

You need Python 3.11 or newer, and Node.js 20.9 or newer for the dashboard. The game target is F1 25 on Windows. Capture and replay run on other platforms too.

From the repository root, install the app and start the API:

```powershell
uv sync --extra app
uv run --extra app f1-engineer api --database data/f1-engineer.sqlite3 --recordings-root recordings --control-token-file data/.f1-engineer-control-token
```

The API listens on `127.0.0.1:8765`. The recorder listens on `0.0.0.0:20777`, which is the address the game can send to.

In a second terminal, start the dashboard:

```powershell
cd web
npm ci
npm run dev
```

Open [http://127.0.0.1:3000](http://127.0.0.1:3000).

In F1 25, set the UDP telemetry destination to this computer and port 20777. On the Recordings screen, start recording before you drive and stop it when the session is over. Stopping writes a `.f1ecap` file into the inbox. Import that file from the same screen.

To record, inspect, and import from the command line:

```powershell
uv run --extra app f1-engineer record --output recordings/session.f1ecap
uv run --extra app f1-engineer inspect recordings/session.f1ecap
uv run --extra app f1-engineer import recordings/session.f1ecap
uv run --extra app f1-engineer sessions
uv run --extra app f1-engineer laps
```

`Ctrl+C` stops a command-line recording. Import writes `data/f1-engineer.sqlite3` and stores one trace per lap beside it.

For every command:

```powershell
uv run --extra app f1-engineer --help
```

Flags, comparison rules, and capture limits are in [docs/usage.md](docs/usage.md).

To serve the dashboard on another loopback port, set the origin to that exact address, then start Next.js there:

```powershell
$env:F1_ENGINEER_WEB_ORIGIN = "http://127.0.0.1:3001"
npm run dev -- --port 3001
```

The API writes a control token at the path you passed. The Next.js server reads it for recording, import, replay, and upload. If you choose a different token file, point Next.js at the same path:

```powershell
$env:F1_ENGINEER_CONTROL_TOKEN_FILE = "data/.f1-engineer-control-token"
```

## Optional local model and voice

Ask GP...T uses a private model on this machine. Install Ollama 0.9.0 or newer, then start the app-managed runtime and download the pinned model once:

```powershell
.\scripts\start-private-ollama.ps1 -InstallModel
```

Later starts use the same script without `-InstallModel`. Stop the runtime with:

```powershell
.\scripts\stop-private-ollama.ps1
```

It binds to `127.0.0.1:11435` and keeps the model under `%LOCALAPPDATA%\GP...T\ollama`. Questions go only to that model. The rest of the app runs without it.

To dictate a question, install the local speech runtime once from the repository root:

```powershell
.\scripts\setup_local_speech_runtime.ps1
```

The script builds a pinned whisper.cpp and downloads the English `tiny.en` model. In Engineer, choose **Record question**. **Use transcript** copies the draft into the question box, and you send the question yourself. Typed questions keep working when the microphone is unavailable.

## How a session moves

```text
UDP source ──────┐
                 ├─ raw capture ─ version-aware envelope decoder
Replay source ───┘                              │
                                                ├─ session events
                                                ├─ SessionContext decoder
                                                └─ bounded frame assembly
```

Raw datagrams are written before they are decoded, so a truncated or unknown packet stays in the capture. Recording and replay share that pipeline.

The accepted decisions are in [docs/architecture.md](docs/architecture.md). Packet notes are in [docs/telemetry-protocol.md](docs/telemetry-protocol.md). What is built, and what is still planned, is in [docs/implementation-plan.md](docs/implementation-plan.md).

## Developing

Install the test tools and run the Python tests from the repository root:

```powershell
uv sync --extra dev --extra app
uv run --extra dev --extra app pytest
```

Dashboard setup is in [web/README.md](web/README.md).
