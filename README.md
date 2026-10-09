# GP...T

A telemetry-backed race engineer for F1 25 and the 2026 Season Pack, with a
Python processing/API service and a Next.js dashboard.

The backend can run on Ubuntu while the dashboard, microphone and playback
remain on Windows. An authenticated SSH tunnel connects them without exposing
the API on the LAN. Linux model/speech provisioning and live-game acceptance
remain pending. The [migration baseline](docs/migration-baseline.md) is the single
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
`F1_ENGINEER_API_URL` accepts only loopback HTTP addresses. For Ubuntu, the
companion configures a local SSH-forwarded address and a server-only
`F1_ENGINEER_API_TOKEN_FILE`; all upstream requests then carry the Ubuntu token.

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

## Ubuntu test host

The checkout is `/home/massimo-nodin/GP...T` on `192.168.1.115`, with user-local
uv at `/home/massimo-nodin/.local/bin/uv`. Connect from Windows:

```powershell
ssh -i "$env:USERPROFILE\.ssh\id_ed25519_ubuntu" -o IdentitiesOnly=yes massimo-nodin@192.168.1.115
```

On Ubuntu:

```bash
cd ~/GP...T
git pull --ff-only origin main
~/.local/bin/uv sync --frozen --extra app --extra dev
~/.local/bin/uv run --frozen --extra app --extra dev pytest -q
~/.local/bin/uv run --frozen --extra app f1-engineer api --database data/f1-engineer.sqlite3 --recordings-root recordings --control-token-file data/.f1-engineer-control-token
```

Windows remains the editing/commit workspace. Push there, pull on Ubuntu and
verify `git rev-parse HEAD` matches the intended commit before testing. Do not
overwrite a dirty Ubuntu checkout. Linux speech/model setup is still pending.

## Run the split deployment

On Ubuntu, after syncing dependencies, install the user service. Its template
assumes the checkout is `~/GP...T`; adjust the paths if using another directory.

```bash
mkdir -p ~/.config/systemd/user
cp scripts/ubuntu/f1-engineer.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now f1-engineer
systemctl --user status f1-engineer
```

The service authenticates every HTTP request on `127.0.0.1:8765` and listens for
game telemetry on UDP `20777`. Set the game's UDP destination to `192.168.1.115`
for this host. Ubuntu firewall/network access and real-game throughput must
still be verified. The existing host has user lingering enabled; another host
may need its administrator to enable lingering for service operation at logout.
Inspect logs with `journalctl --user -u f1-engineer`; stop it with
`systemctl --user stop f1-engineer`. Pulling new code does not restart the service;
after syncing dependencies, run `systemctl --user restart f1-engineer`.

On Windows, run from the repository root:

```powershell
.\scripts\start-ubuntu-companion.ps1
```

The launcher fetches the service credential over SSH into an ignored,
owner-restricted local file, opens `127.0.0.1:18765` as an SSH tunnel, verifies
authenticated backend readiness, and starts the Windows dashboard. It does not
start a Windows Python backend. Keep this terminal open; Ctrl+C closes the
dashboard/tunnel, not the Ubuntu service. Use `-DashboardPort 3001` if needed.
An occupied tunnel port or failed credentials abort startup; there is no
fallback to a Windows backend. Microphone capture and playback remain local;
transcription will be unavailable until the Ubuntu speech runtime is installed.
