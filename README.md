# GP...T

A telemetry-backed race engineer for F1 25 and the 2026 Season Pack, with a
Python processing/API service and a Next.js dashboard.

**Current deployment is single-machine and loopback-only.** Ubuntu processing
with a Windows microphone/playback companion is the next step, not implemented
functionality. The [migration baseline](docs/migration-baseline.md) is the single
project document for direction, measurements, unresolved issues and next steps.
Historical plans and development mandates have been retired; source and tests
remain intact.

## Run the current application

Use Python 3.11+, uv, and Node.js supported by the installed Next.js. From the
repository root:

```powershell
uv sync --extra app --extra dev
uv run --extra app f1-engineer api --database data/f1-engineer.sqlite3 --recordings-root recordings --control-token-file data/.f1-engineer-control-token
```

In a second terminal:

```powershell
cd web
npm ci
npm run dev
```

Open `http://127.0.0.1:3000`. Set the game's UDP destination to this computer,
port `20777`. The API listens on `127.0.0.1:8765` and automatically acquires
telemetry on `0.0.0.0:20777`; do not start another receiver on that port.
Add `--manual-acquisition` for diagnostic recording controls.

The Next.js server reads the control token, not the browser. When overriding
`F1_ENGINEER_CONTROL_TOKEN_FILE`, use an absolute path. The current
`F1_ENGINEER_API_URL` accepts only loopback HTTP addresses, not an Ubuntu host.

## Optional model and voice on Windows

Install Ollama, then run from the repository root:

```powershell
.\scripts\start-private-ollama.ps1 -InstallModel
.\scripts\setup_local_speech_runtime.ps1
```

Later model starts omit `-InstallModel`; stop it with
`.\scripts\stop-private-ollama.ps1`. The private model endpoint is
`127.0.0.1:11435`. Microphone input produces an editable transcript; questions
are submitted explicitly. Playback uses browser speech synthesis. Typed input
and telemetry workflows do not require the optional speech/model runtimes.

## Validation and diagnostics

```powershell
uv run --extra dev --extra app pytest
uv run --extra app f1-engineer --help
uv run --extra dev --extra app python -m scripts.benchmark_session_publication --duration-s 90 --batch-size 32
cd web
npm run test:api-transport
npm run test:local-speech
npm run build
```

Additional dashboard checks are in `web/package.json`. Benchmarks are not
real-game acceptance. Keep captures, databases, model files, credentials and
measurement artifacts out of Git; `data/` and `recordings/` are ignored. Stop
writers and take consistent backups before moving persistent data.
Framework-generated web agent files are ignored, not maintained project docs.
