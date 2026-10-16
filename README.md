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
`--manual-acquisition` turns automatic acquisition off for legacy manual capture
diagnostics; do not use it for normal sessions.

The dashboard has three areas: **Drive** shows engineer and telemetry status with
the current or most recent session; **Review** lists acquired and imported
sessions, with lap times, validity, sector data and telemetry traces when the
backend has them; **Settings** shows runtime and storage status and saves display
preferences in this browser. Data the backend does not provide is shown as
unavailable on real sessions. When no local session data is available, the
dashboard may show illustrative reference content; it is not recorded telemetry.
Optional circuit photos go in `web/public/tracks/<track>.jpg` (for example `monaco.jpg`).

The Next.js server reads the control token, not the browser. When overriding
`F1_ENGINEER_CONTROL_TOKEN_FILE`, use an absolute path. The current
`F1_ENGINEER_API_URL` accepts only loopback HTTP addresses. For Ubuntu, the
companion configures a local SSH-forwarded address and a server-only
`F1_ENGINEER_API_TOKEN_FILE`; all upstream requests then carry the Ubuntu token.

## Simulate live telemetry from Windows

`replay-udp` sends the original datagram payloads from a complete `.f1ecap`
recording to the normal UDP receiver. Unlike `replay`, it exercises Windows sending, the network, Ubuntu's socket,
the acquisition queue, journal and live evidence pipeline. No F1 game is needed.

Existing local Melbourne evidence can be checked without sending anything:

```powershell
.\.venv\Scripts\f1-engineer.exe replay-udp data/d0058-browser-capture-root/melbourne.f1ecap --host 192.168.1.115 --dry-run
```

Keep F1 off and ensure no other replay is sending. For clean comparisons, use
a separate validation evidence directory on Ubuntu and restart the API against
it before each run; do not overwrite or delete previous evidence. Keep exactly
one receiver on port 20777. Then send one real-time pass:

```powershell
.\.venv\Scripts\f1-engineer.exe replay-udp data/d0058-browser-capture-root/melbourne.f1ecap --host 192.168.1.115 --confirm-idle
```

`--speed 1` preserves recorded timing; `--speed 2` halves the duration for an
explicit stress test. Ctrl+C stops sending. The full capture is validated before
the first send; keep the source file unchanged during playback. There is no
automatic loop. Payloads, session UIDs, frame numbers, invalidity and recorded
gaps are not rewritten, so repeated runs must not be treated as new real sessions.
The source IP/port and receiver arrival timestamps are new. OS scheduling and
network jitter can alter actual arrival spacing, and late scheduling can bunch
packets. A sender count means datagrams accepted by the local socket, not
confirmed delivery: compare it with receiver counts and application/kernel drops.
Replay cannot reproduce packets missing from the original recording or validate
the game's sender. Do not mix replay evidence with real-game acceptance evidence.

## Optional model and voice on Windows

Install Ollama, then run from the repository root:

```powershell
.\scripts\start-private-ollama.ps1 -InstallModel
.\scripts\setup_local_speech_runtime.ps1
```

Later model starts omit `-InstallModel`; stop it with
`.\scripts\stop-private-ollama.ps1`. The private model endpoint is
`127.0.0.1:11435`. Microphone input produces an editable transcript; questions
are submitted explicitly. Dashboard playback uses browser speech synthesis. Typed input
and telemetry workflows do not require the optional speech/model runtimes.

## Standalone lap radio on Windows

An initial browser-independent radio client speaks fresh player lap completions
and answers the fixed console command `lap` using the authenticated live backend.
In verified race sessions it also notices new adjacent-car gap and valid-lap
pace trends, with freshness checks and bounded chatter. Rules mode needs no
model; optional model mode can select an evidenced notice or silence, never
invent speech or tactical orders. See the
[proactive radio validation guide](docs/proactive-radio-validation.md) for modes,
offline scenarios and the deferred joint test sequence.
Optional wheel-triggered microphone capture and listening cues now produce an
unverified transcript draft; the full conversational engineer remains pending.

```powershell
uv run --extra app f1-engineer radio --list-voices
uv run --extra app f1-engineer radio --dry-run
uv run --extra app --extra radio f1-engineer radio
```

See the [standalone radio guide](docs/working-engineer-standalone-radio.md) for
split-host token/tunnel settings, controls, timing diagnostics and pending
hardware acceptance. The backend must already be running; no browser is needed.
Optional wheel-button learning and live reassignment use `radio-input` with
`--extra radio`; see the guide. `radio --capture-test` tests listening and cues
without a backend, uploaded audio or saved recordings. Capture starts only on
an explicit button press or console `listen` command.

### Using the dashboard with the radio

The dashboard and the radio share one backend and can run together.

- **Settings reach the running radio live.** Assistance topics, interruption,
  verbosity and depth, and the Radio card in **Settings › Voice & Controls**
  (proactive and answer mode, voice, rate, volume, microphone, cue output and
  capture length) arrive on the radio's existing live poll, normally within a
  few hundred milliseconds. Nothing restarts: the current sentence, capture or
  request finishes, and each change applies at its next natural point. Until
  the Radio card is saved, the radio keeps its command-line values for voice,
  rate, volume, modes and capture. Assistance preferences apply on their own,
  including before that first save. After a Radio card save, those dashboard
  values win. See [radio live settings](docs/radio-live-settings.md).
- **Radio History includes the radio.** Questions asked on the radio and what
  it actually spoke appear on **Drive** with the **Radio** filter. See
  [radio activity history](docs/radio-activity-history.md).
- **One voice at a time.** While a radio is connected, Drive's "Read calls
  aloud" stays switched on but does not speak. A call already queued or
  playing is cancelled. Speech starts again after the radio disconnects.
- **Speech recognition is selectable** in **Settings › Voice & Controls**: local
  whisper.cpp (`tiny.en` or `large-v3`, CPU or CUDA, for each variant installed
  with the setup script below) or OpenAI transcription. Switching needs no
  restart; a clip already being transcribed finishes on its original runtime.
  OpenAI needs an API key, added under **Settings › Connections › API keys**.
  The key file sits beside the database. On Ubuntu it is owner-only; on
  Windows, access relies on the data folder's permissions. The key itself is
  never returned, and Settings shows only its last four characters.
  The `--speech-provider` flag only sets the starting choice.

## Validation and diagnostics

The first working-engineer backend increment exposes authenticated live facts
at `GET /api/v2/engineer/live`. It includes freshness-qualified field timing,
verified driver bindings and bounded lap/sector/pit events. See the
[live facts contract](docs/working-engineer-live-facts.md) and
[v1 acceptance criteria](docs/working-engineer-v1-acceptance.md).
The standalone lap radio consumes these facts, but the full spoken engineer's
acceptance criteria remain pending.

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
For a CUDA host, install the matching CUDA toolkit and build tools, then pass
`--model large-v3 --device cuda --cuda-architectures 61` with the GPU's compute
capability. `--cuda-host-compiler` can select a supported host compiler. The
full large-v3 model requires several gigabytes of storage and GPU memory; build
and inference fit depends on the GPU and other active GPU workloads.
Each model and device is pinned separately
(`.f1-engineer-whisper-pin.<model>.<device>.json`), so several variants can be
installed and chosen in the dashboard; an existing
`.f1-engineer-whisper-pin.json` is still used for the variant it declares.
The API rereads these pins, so installing speech does not require a service
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
