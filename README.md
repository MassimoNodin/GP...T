# GP...T

A telemetry-backed race engineer for F1 25 and the 2026 Season Pack, with a
Python processing/API service and a Next.js dashboard.

The backend can run on Ubuntu while the dashboard, microphone and playback
remain on Windows. An authenticated SSH tunnel connects them without exposing
the API on the LAN. Ubuntu speech and private model provisioning are available;
live-game acceptance remains pending. The [migration baseline](docs/migration-baseline.md) is the single
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
overwrite a dirty Ubuntu checkout.

### Split-host acceptance checks

The synthetic UDP harness uses a separate evidence database and UDP `49077`,
never the production listener or game database. On Ubuntu, permit only the
Windows sender (this setup uses `192.168.1.111`) when UFW is enabled:

```bash
sudo ufw allow from 192.168.1.111 to any port 49077 proto udp
~/.local/bin/uv run --frozen --extra app python -m scripts.benchmark_split_udp receive --host 0.0.0.0 --output data/split-acceptance-unique
```

Wait for `ready: true`, then run on Windows:

```powershell
uv run --frozen --extra app python -m scripts.benchmark_split_udp send --host 192.168.1.115
```

The default run lasts 90 seconds: 355 datagrams/s plus a 10-second 530/s burst.
It requires 33,700 lossless admissions, retained analysis, continued ingestion,
SQLite integrity and explicit latency targets. A nonzero receiver exit means
acceptance failed; inspect `summary.json` or `failure.json` and `samples.json`.
On Linux, `kernel_dropped` reports sampled losses for the receiving socket;
`dropped` counts application queue overflow. A null kernel count means unknown,
not zero. Observed kernel overflow also interrupts the current evidence scope.
Always use a new output directory. For a 300-second soak, pass `--duration-s 300`
on both sides and `--expected-datagrams 108250` on the receiver. These fixtures
do not certify real game traffic, microphone capture or audible playback.
Remove the optional test rule afterward with
`sudo ufw delete allow from 192.168.1.111 to any port 49077 proto udp`.

### Optional NVMe storage

The opt-in drop-ins `scripts/ubuntu/f1-engineer-nvme.conf` and
`scripts/ubuntu/f1-engineer-ollama-nvme.conf` use `/mnt/nvme/f1-engineer/`
for databases, recordings and model blobs. They refuse startup when the NVMe
mount or prepared directories are missing; the API token stays in the private
checkout data directory. Do not install these on an unverified drive.
Stop acquisition and both user services before copying data. Use SQLite's backup
API for each database, copy the verified runtime pins beside the destination
database, and checksum-verify model blobs and recordings before switching.
Preserve original data and units for rollback. Install the matching drop-in as
`~/.config/systemd/user/<service>.service.d/storage.conf`, reload user systemd,
then verify authenticated API readiness and the unchanged model digest.
The current host's migration and measured acceptance are in
`docs/migration-baseline.md`; never copy a live SQLite file or rely on a missing
mount silently falling back to the root disk.

### Ubuntu private model

On the four-core migration host, optional
`scripts/ubuntu/f1-engineer-four-core-cpu.conf` and
`scripts/ubuntu/f1-engineer-ollama-four-core-cpu.conf` restrict the backend to
CPUs 0-1 and model work to 2-3, with lower model scheduling priority. Other OS
processes can still use those CPUs; this is not exclusive reservation. Verify
`lscpu -e` first; these are not portable defaults for fewer cores or different
CPU topology. Install each as its matching user service's
`cpu-isolation.conf` drop-in, reload systemd and restart while acquisition is
idle. Remove only these drop-ins to roll back. Benchmark receivers must use
the same backend CPU set. This does not enlarge queues or weaken durability.

The x86_64 setup installs checksum-pinned Ollama 0.40.2 under
`~/.local/share/GP...T/ollama`, creates a private user service, downloads Qwen3
4B and pins its full digest beside the database. It requires `zstd`, user systemd
and at least 12 GiB free for initial provisioning; it does not use sudo or alter
an existing system Ollama installation.

```bash
cd ~/GP...T
~/.local/bin/uv run --frozen --extra app python -m scripts.setup_ubuntu_ollama
systemctl --user status f1-engineer-ollama
```

The endpoint is Ubuntu loopback `127.0.0.1:11435`. The app requests CPU inference,
the service permits one model/request at a time, and `OLLAMA_NO_CLOUD=1` is set.
Cloud routing remains labelled unverified rather than claiming daemon-wide
attestation. Repeat setup preserves installed models/pins; pass `--database`
for another data directory. Only use `--accept-model-update` after reviewing a
model change. A conflicting user unit or unrelated listener aborts setup.
Use `systemctl --user start f1-engineer-ollama` for later starts and
`systemctl --user stop f1-engineer-ollama` to stop only this private service.

### Ubuntu transcription

The setup builds the same pinned whisper.cpp commit and checksum-verified
tiny.en model used on Windows. It installs only in your home directory and
does not require sudo. Install Git and a C/C++ compiler first. If CMake is not
already installed, use the user-local tool below:

```bash
cd ~/GP...T
~/.local/bin/uv tool install cmake==3.31.6
~/.local/bin/uv run --frozen --extra app python -m scripts.setup_ubuntu_speech_runtime --cmake ~/.local/bin/cmake
```

The default build uses two jobs, CPU-only static whisper/ggml libraries and
the system C/C++ runtime. Source, build output and model live under
`~/.local/share/GP...T/speech-runtime/v1.9.3`; the verified pin is beside the
configured database. Pass `--database` when using a different data directory.
The API rereads this pin, so installing speech does not require a service
restart. A changed pin is refused unless you explicitly review and accept it
with `--accept-runtime-update`. Microphone clips remain bounded to 12 seconds;
transcripts are editable drafts, not verified telemetry evidence.

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
transcription runs on Ubuntu after installing the speech runtime above.
