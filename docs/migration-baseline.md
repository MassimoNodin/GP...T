# Ubuntu / Windows migration baseline

Updated: 2026-10-10. This is the single project baseline replacing historical
plans, audits and development-rule documents. Historical measurements are carried
forward; migration validations below identify their actual host and commit.
Synthetic results are not verified live-game performance.

## Direction and boundaries

Keep Python processing and TypeScript UI; profile before considering a language
rewrite. Start with one repository and two independently runnable components.

| Component | Intended ownership |
| --- | --- |
| Ubuntu service | UDP decoding, session/lap state, analysis, SQLite/Parquet evidence, recordings, transcription, Ollama and authenticated API |
| Windows companion | Dashboard, microphone capture, spoken playback, device preferences and connection status |
| Network | Questions/audio to Ubuntu; results/status to Windows. Prefer direct game UDP to Ubuntu; add a Windows relay only if required. |

Current API startup in `f1_engineer/cli.py` binds to loopback. The web server's
`web/src/lib/local-api-transport.ts` rejects remote URLs and
`web/src/lib/local-proxy.ts` reads a local control-token file. These are existing
constraints retained by the SSH-tunnel deployment rather than relaxed for LAN
HTTP. Browser microphone/playback are reused; a native Windows rewrite is not
required initially. Keep model
endpoints private to Ubuntu and credentials server-side in the companion.

`f1_engineer/processing/runtime.py` and `coordinator.py` run the live pipeline.
One serialized writer journals raw admissions with SQLite WAL/FULL durability
and publishes derived evidence in batches capped at 32 journal rows, scheduled
at 20 ms or the row cap. Publication still blocks that writer; this interval
does not guarantee completion in 20 ms. Imported and live evidence use separate
SQLite stores; traces also use Parquet. Ubuntu owns persistent files locally;
Windows must not open SQLite over a network share.

The intended product remains Live Engineer plus Session Review using the same
session evidence. Radio-first UI and format-aware automatic updates are not
completed features. Practice focuses on improvement; race/qualifying distinguish
classification/timing neighbours from physical traffic. Explicit requests keep
their selected driver. Time Trial starts with a valid comparable player
reference; detailed ghost comparisons require verified evidence.

## Useful measurements

| Recorded experiment | Result | Meaning |
| --- | --- | --- |
| Oct 8 real-journal clean-window replay | Offered mix about 354.3 datagrams/s; pipeline CPU about 0.35 ms/packet. A 300-admission profile spent 0.690 s in 600 SQLite commits versus 0.234 s in pipeline processing. | Persistence dominated this profile; translating decoding alone is not the first fix. |
| Oct 9 admitted-journal replay | Serial 53.39 s (340.89/s); batch 32 45.09 s (403.61/s), with matching normalized evidence/eligibility fingerprints. | 15.5% less elapsed time; replay cannot recover lost inputs. |
| Oct 9 synthetic steady, 90 s at 355/s | Batch 1: 118 drops, max queue 1,024. Batch 32: zero drops, max queue 176; sampled oldest-delay p95/max 290.38/542.09 ms. | Batching helps, but misses the proposed 250 ms p95 backlog goal; not live acceptance. |
| Oct 9 synthetic burst, 150-frame laps | 10 s at 530/s within 90 s: batch 32 dropped 393 packets; max queue 1,024; sampled oldest-delay max 2.630 s. | Burst losslessness and the proposed one-second maximum backlog goal failed. |
| Oct 9 real F1 25 practice, commit db094fdfc6d0715f40a54a434ef47678bd5900a7 | 29,302 received; 20,968 journaled/projected; 7,822 overflow drops (26.69%); 512 more unadmitted entries discarded at stop. Sampled receive/processing medians 356/242.75 packets/s; oldest-delay p95/max 5.373/5.797 s. | Latest recorded live throughput and eligible completed-lap comparison acceptance failed. |

The last live run saturated the 1,024-entry queue by the first 15 s sample and
produced no measurement-ready comparison. Socket/persisted decode errors were
zero; SQLite quick_check, admitted payload checksums and sealed-chunk integrity
checks passed. Preserving admitted data does not make acquisition lossless.
The approximately 92.53 s run's snapshot was 240,209,920 bytes. An early working
set sample was about 51 MB, not a whole-run peak or race-length memory result.
Delay metrics are sampled, not exhaustive per-packet percentiles.

Repeated staging scans were already addressed by pruning persisted sealed
ranges. That fix and derived batching do not eliminate shared-writer stalls.
Previously recorded Python regression result: 968 passed, 10 skipped; this is
not a fresh result for this cleanup or proof of live throughput.

Local evidence, if still present, remains in ignored `data/` locations:
`session-engineer-real-game-baseline-20261008-163122.sqlite3`,
`publication-benchmark-real-replay-20261009/`,
`publication-benchmark-batch-steady-90s-20261009/`,
`publication-benchmark-batch-150-90s-20261009/`,
`publication-benchmark-serial-steady-90s-final-20261009/`, and
`session-engineer-live-batching-20261009-173036/`. Do not delete or commit these
as part of the docs reset. Historical tracked documents remain in Git history;
their old directives are not inherited by this baseline.

## Unresolved issues and safeguards

- **Throughput:** measure raw commit latency, lap-sealing/publication stalls,
  queue growth and CPU/disk contention on Ubuntu before selecting a fix. Do not
  silently weaken raw durability or treat a larger queue as a solution.
- **Remote security:** configure bind address, authenticate reads and writes,
  provision credentials, choose encrypted/private transport and restrict peers.
  The first slice uses SSH encryption and loopback forwarding, plus token
  authentication for all requests. Request IDs, version negotiation and richer
  reconnect behavior remain follow-up work; never expose the legacy API unchanged.
- **Linux runtime:** replace Windows-only model provisioning; review Whisper's
  DLL-oriented pin validation, locks, permissions, explicit data paths and
  subprocess cleanup. Backend setup is now verified on the host below; Linux
  speech/model provisioning is now verified, with hardware voice acceptance pending.
- **Evidence correctness:** retain checksums, explicit loss gaps, committed-reader
  visibility, association guards and replay equivalence. Never promote gapped
  fragments into eligible laps or fabricate missing channels. Listener shutdown
  is not evidence that the game session ended.
- **Formats/rivals:** decoders cover 2025/2026 layouts, but synthetic layout tests
  are not live certification. The audited F1 25 Time Trial rival demonstrated
  timing only; ghost channels lacked valid active-car evidence. Detailed ghost
  telemetry and real 2026 rival validation remain outstanding. Missing progress
  or cutoff evidence must not become invented strategy advice; re-check old
  metadata audit gaps against current code rather than treating them as current.
- **Voice/UI:** preserve typed fallback and capture/transcription/playback
  cancellation. Distinguish device failures from backend/network failures;
  reconnect must not replay stale speech. Fixture checks do not establish
  radio-first or complete real-user workflow acceptance.
  Live v2 session evidence and legacy v1 Engineer question selection remain
  separate paths; retained v2 comparisons do not establish v1 Ask availability.
- **Resilience:** verify process-kill recovery, disk-full behavior, extended-session
  disk/RSS growth and bounded queues. Short runs and allocation measurements
  do not certify these cases or power-loss recovery.

## Next implementation steps

1. Run Python/web checks and establish a reproducible baseline without
   overwriting existing evidence or credentials.
2. Define the authenticated two-machine API/configuration with Ubuntu owning
   analysis/persistence and Windows owning hardware interaction.
3. Make Ubuntu independently runnable with explicit paths, private model setup
   and reliable service startup/shutdown.
4. Connect the Windows-hosted dashboard via server-side transport; retain local
   microphone/playback. Start with direct game UDP.
5. Move evidence only with writers stopped and consistent backups. Add a bounded,
   timestamped/sequenced relay only if direct UDP is unsuitable.
6. Verify game -> Ubuntu analysis and Windows microphone -> Ubuntu
   transcription/question -> Windows playback, including unauthorized access,
   network loss, restart and concurrent analysis during acquisition.

Acceptance requires lossless representative steady/burst acquisition, eligible
live comparisons retained after restart, measured queue/backlog/publication
latency, evidence parity and bounded long-run storage/memory. Start regressions
at 355/s steady plus a 10 s 530/s burst, then confirm actual target game traffic.
Re-evaluate the proposed 250 ms p95 backlog, one-second burst maximum and 50 ms
publication p95 goals explicitly; the harness's passed flag does not cover all
gates. Only consider native acceleration after a demonstrated CPU bottleneck.

## Development and test host

This Windows T3 Code session is the editing/commit workspace. Changes go to
GitHub, then the Ubuntu checkout pulls the intended commit for testing; no
parallel source edits or shared databases are required.

SSH target: `massimo-nodin@192.168.1.115` (host `massi-nodin-dev`). Checkout:
`/home/massimo-nodin/GP...T`. Ubuntu 24.04.1 LTS, Python 3.12.3 and user-local
uv 0.12.5. Initial checkout: `e09521933b422c29c8a3cd75ea5d167167c0c285`.
Lockfile validation, frozen app/dev installation and 54 focused CLI/API tests
passed on Ubuntu. Model runtimes were not installed during that initial setup.
The system Node.js 18 installation was left unchanged
because this host currently runs backend tests, not the Next.js dashboard.

Full Ubuntu baseline on that commit: **987 passed, 3 failed, 1 skipped** in
185.38 s. A real temporary API process returned HTTP 200 for evidence status
and shut down gracefully; it used disposable data and did not start UDP capture.
This establishes backend startup, not a clean full-suite or live-game acceptance.
Compatibility failures found in the initial baseline (now resolved):

- `tests/test_import_jobs.py`: the Unicode filename fixture exceeds this
  filesystem's filename byte limit (`ENAMETOOLONG`).
- `tests/test_recording_download.py`: the POSIX FIFO test shadows its `_source`
  helper with a local assignment and fails before exercising the download.
- `tests/test_storage.py`: deeply nested completion JSON is accepted where the
  test expects `invalid_json`; resolved with explicit JSON shape bounds.

Migration implementation `d59e1c5` passes the full Ubuntu suite: **1007 passed,
1 skipped** in 189.70 s. Windows full regression: **998 passed, 10 skipped** in
237.11 s. Dashboard checks: **82 passed**, production build and TypeScript passed.
The Starlette TestClient/httpx deprecation warning remains; it is not a failure.

The Ubuntu user service is installed, enabled and active with user lingering.
The API remains on loopback: anonymous requests return 403 and authenticated
requests return 200. The Ubuntu token has mode 0600. The Windows launcher stores
its token with an owner-only, non-inherited ACL before writing the secret, and
rejects redirected credential paths. Its SSH tunnel binds only Windows loopback.
The Windows dashboard at `http://127.0.0.1:3001` successfully reads Ubuntu
telemetry, model status and filesystem capacity through that tunnel. Browser
telemetry/model requests return 200 without an authorization response header;
credentials remain in the server-side transport.

Ubuntu private Ollama 0.40.2 and Qwen3 4B Q4_K_M are installed in user-local
storage with an enabled private user service. The release archive is checksum
pinned; model digest is
`sha256:359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`.
An actual selected-attempt question routed to `attempt_summary/lap_time`, with
Ollama confirming **CPU (0 VRAM)**. Cold inference took **58.063 s**, near the
60-second application deadline; latency under telemetry load is not accepted.
Three later isolated repetitions of the same actual CPU routing request took
**31.671 s** with an idle model, **2.884 s** immediately afterward, and
**21.175 s** after the 15-second unload window. All returned
`attempt_summary/lap_time` with the same pinned digest and CPU placement.
These observations show model-loading/cache effects, not a latency percentile
or a microphone-to-answer acceptance. No deadline, model pin or keep-alive
policy was relaxed to obtain them.
Ubuntu CPU transcription is now
installed from the exact whisper.cpp 1.9.3 source commit and checksum-verified
tiny.en model. CMake 3.31.6 is user-local; no sudo or system package changes were
needed. The provisioning/speech focused suites pass on both hosts: **21 passed**.
Upstream CMake regenerates its tracked JavaScript package version during setup;
the installer restores only that exact known change and preserves unexpected
changes for review, keeping subsequent setup runs repeatable.
A canonical public JFK WAV fixture sent from Windows through the dashboard and
SSH tunnel was transcribed on Ubuntu correctly in **1.48 s** (one observation,
not a latency percentile or simultaneous-telemetry acceptance). The browser's
speech-status endpoint reports ready. This is an audio-transport test, not a
real microphone or spoken-playback test. Runtime pins require explicit acceptance
before changing; installer failures do not publish an unverified replacement.
Windows browser speech discovers its local voice. Game UDP destination should be `192.168.1.115:20777`, not the
Windows dashboard/tunnel address. No live-game throughput acceptance is claimed.
Settings distinguish backend-host model/UDP services from Windows audio and
include Ubuntu and Windows standalone provisioning commands. Actual microphone
capture, editable-question submission and audible playback still require an
operator-driven round trip; fixture transport is not hardware acceptance.

Ubuntu 90-second synthetic 355/s runs with a 10-second 530/s burst:

| Implementation | Received / processed / dropped | Sampled backlog p95 / max | Result |
| --- | --- | --- | --- |
| Before raw batching | 33,700 / 17,162 / 16,538 | 10.176 / 11.203 s | 49.1% loss; no eligible comparison |
| FULL-durability raw batching | 33,700 / 32,905 / 795 | 2.262 / 3.200 s | 2.4% loss; eligible comparison retained |
| Binding cache and single chunk decode | 33,700 / 33,700 / 0 | 1.410 / 2.461 s | Lossless, but latency fails |

The last run retained an eligible 150-row lap comparison while ingestion
continued, with queue peak 966/1,024 and drain time 0.253 s. Sampled publication
p95 was 172.758 ms: the 50 ms publication p95, 250 ms backlog p95 and one-second
maximum backlog targets still fail. These same-process synthetic senders compete
with ingestion; the separate Windows sender harness isolates that interference.
No live-game acceptance is inferred. Raw batches remain capped at 32 and use
SQLite WAL/FULL; queued but unadmitted datagrams are not durable.

Full regression at `5360599`: Windows **1,017 passed, 10 skipped** (186.40 s),
Ubuntu **1,026 passed, 1 skipped** (188.07 s). The subsequent failed-run artifact
test at `d1ce1e8` passes separately on both hosts. Existing Starlette/httpx warning
remains. Actual subprocess termination/recovery and speech cancellation tests
pass on Ubuntu (**17 passed**). The independent Windows companion verification
passes authentication, ready services, credential non-disclosure, SSH loss (503),
and reconnect (200), without stopping the primary companion. The actual Ubuntu
API was safely restarted with zero received game packets and returned to
authenticated listening on UDP 20777.
An isolated SQLite page-cap test produces real `SQLITE_FULL`, verifies the
entire new journal batch rolls back without partial admission, then reopens and
continues the prior generation after capacity is restored (**3 recovery tests
passed on both hosts**). This validates database-capacity handling, not a full
host filesystem or sudden power-loss test.

The first Windows-to-Ubuntu synthetic LAN run sent 33,700 datagrams but received
none: Ubuntu kernel logs explicitly show UFW blocking UDP 49077 from Windows
`192.168.1.111`. This is a failed network precondition, not a processing result.
After the operator added the source-restricted UDP rule, the separate-host
90-second repeat received/journaled/processed **33,700**, with **zero drops**,
queue peak 298/1,024, retained comparison, continued ingestion and SQLite
quick_check passing. Sampled publication p95 **52.299 ms** and backlog p95
**316.328 ms** still fail their targets; backlog maximum **680.645 ms** passes.
Sampled receiver RSS peaked at **114,143,232 bytes**; final database size was
**218,435,584 bytes**. Evidence is `data/migration-two-host-20261010-b/` on Ubuntu.
The receiver exited unsuccessfully because its explicit latency checks failed;
losslessness does not mean full acceptance.
At `f7859e3`, shallow JSON dataclass projection preserves exact serialized
metadata/binding bytes without recursive deep copies. The next separate-host
90-second run remained lossless with queue peak **206**, publication p95
**51.976 ms**, backlog p95/max **258.807/699.070 ms** and RSS peak
**114,098,176 bytes**. The two p95 targets still fail, despite reduced backlog.
Evidence is `data/migration-two-host-20261010-c/`. At `e94d0d7`, chunk sealing
reuses canonical staging JSON bytes instead of re-encoding unchanged records;
canonical bytes, hashes and sizes are verified by a focused test. Raw durability,
row caps and acceptance thresholds remain unchanged.
The first requested 300-second soak at `e94d0d7` is **invalid as a sustained
soak**: its sender stopped at 35,680 packets when a nested fixture's 20-frame
lap counter overflowed the protocol's byte. The receiver retained all those
packets but spent most of the window idle; its latency percentiles must not be
used for acceptance. `45d1567` forwards the declared lap length through the
fixture and rejects configurations exceeding the protocol range. `99669d4`
fails early on premature sender silence while preserving diagnostics.
The fixture now generates a single braking window per declared lap rather than
the previous nested 20-frame pattern; historical runs are not identical workloads.

One Windows full-suite run alongside the synthetic sender failed the short-silence
UDP comparison test (`attempt_not_measurement_ready`); its isolated rerun passed.
The recorded failing run is not reported as green. Final regression and the
corrected sustained soak are rerun without competing host test workloads.
Final isolated regression at `45d1567`: Windows **1,029 passed, 10 skipped**
(167.97 s), Ubuntu **1,038 passed, 1 skipped** (186.11 s). The subsequent
premature-silence change at `99669d4` has **4 focused tests passed on both hosts**.
At `d466233`, a bounded two-view immutable active-tenure cache avoids repeated
snapshot construction between frame observations. Roster/lifecycle changes
invalidate it; raw durability and all acceptance thresholds remain unchanged.
Windows focused inventory/batching tests: **35 passed, 2 deselected**; Ubuntu
inventory/batching tests: **37 passed**. Full isolated regression at `d466233`:
Windows **1,035 passed, 10 skipped** (155.74 s), Ubuntu **1,044 passed, 1 skipped**
(188.10 s). Neither suite runs alongside a throughput sender/receiver.
The actual private Ollama service was stopped and restarted: API status changed
from unavailable back to ready and the model pin's file hash stayed unchanged.
The restored Windows companion renders Ubuntu model, telemetry and storage
status; browser model/telemetry requests return 200 without authorization headers.
The corrected five-minute LAN soak at `99669d4` offered **108,250** packets:
**103,249 admitted/projected**, **5,001 queue-overflow drops (4.62%)**. Sampled
publication p95 was **160.185 ms**, backlog p95/max **5.192/11.161 s**, with
queue peak 1,024. The first 180 seconds had no drops; publication stalls reached
3.689 s in the 180?210 s window and loss accumulated through 240 s. The final
queue drained, and the retained comparison and SQLite quick_check passed.
All **103,249 admitted raw checksums** and **1,630 sealed chunk hashes/sizes**
were independently verified afterward. Receiver RSS first/final/peak was
**100,552,704 / 118,919,168 / 120,418,304 bytes**; database size was
**692,662,272 bytes**. Evidence: `data/migration-two-host-soak-20261010-e/`.
This is a failed sustained-throughput gate, not acceptance. Short lossless runs
and preserved admitted data do not outweigh it. Profile actual publication
stalls before changing checkpoint policy; do not increase the queue or weaken
FULL durability. Longer sessions also need a declared disk budget/retention plan.
Synthetic harness failures now retain `failure.json`, `samples.json` and their
isolated evidence database rather than silently losing diagnostic artifacts.
The next five-minute LAN soak at `d466233` sent **108,250** packets but received,
journaled and processed **108,243**. There were **zero application queue drops**,
queue peak **180**, publication p95 **48.144 ms**, backlog p95/max
**239.103/601.416 ms**, RSS peak **119,001,088 bytes** and database size
**699,645,952 bytes**. All latency, retained-comparison, continued-ingestion and
SQLite integrity checks pass; **lossless acceptance still fails** because seven
sent packets were not received. Their loss location is not established by the
application counters. Evidence: `data/migration-two-host-soak-20261010-f/`.
This improvement is a measured repeat, not proof that the cache alone eliminated
the prior transient stalls or certification of real-game acquisition.
The independent five-minute repeat `g` at the same commit also passes every
latency/analysis/integrity gate: publication p95 **48.577 ms**, backlog p95/max
**222.764/547.887 ms**, queue peak **168**, zero application queue drops and RSS
peak **117,915,648 bytes**. It receives **108,229/108,250** sent packets.
Ubuntu's system-wide UDP `RcvbufErrors` and `InErrors` both increase by **21**
(704 to 725), matching the missing count. This points to kernel receive-buffer
overflow, not application queue overflow; the system-wide counter does not
attribute losses to one socket. No kernel/socket buffer, queue, durability or
acceptance limit was enlarged or relaxed. Both repeated soaks remain failed
lossless acceptance. Evidence: `data/migration-two-host-soak-20261010-g/`.
An actual pinned CPU routing call during a separate 90-second LAN run returns
`attempt_summary/lap_time` in **28.766 s**. However, simultaneous telemetry at
`d466233` receives **33,700**, processes **32,843** and loses **857** to the
application queue (peak **992**). Publication p95 **54.016 ms** and backlog
p95/max **695.249/3,603.042 ms** all fail. The comparison remains retained and
SQLite integrity passes. This verifies routing under load, **not acceptable
model/telemetry coexistence**, an evidence-backed API answer or a voice round
trip. Evidence: `data/migration-two-host-model-load-20261010-h/`, including
`model-routing.json`. Investigate CPU/disk/model-loading contention before
selecting resource isolation or provisioning changes; do not hide losses with
larger queues, weaker durability or looser thresholds.
Independent post-run verification passes every admitted payload checksum and
sealed-chunk hash/byte-size/row-count check: `f` **108,243 payloads / 2,288 chunks**,
`g` **108,229 / 2,288**, `h` **32,843 / 308**. Each run retains the checks in
`integrity-resource.json`; integrity of admitted data does not recover losses.

## Latest contention and storage investigation

At `7e4f2be`, an instrumented 90-second LAN/inference diagnostic (`i`) is
lossless but still misses publication/backlog p95 targets (**57.348/302.599 ms**).
Actual CPU routing takes **23.406 s**. During the first 20 seconds, before
inference starts, timed SQLite transactions spend **13.390 s wall time** and
**5.666 s thread CPU**, including **8.145 s in commit exit**. System I/O pressure
rises while CPU pressure remains low; these timings point to storage waits, not
proof that translating Python would solve throughput. The process sampler
does not capture every model child, so its CPU counters are not a complete model
profile. Evidence: `data/migration-contention-profile-20261010-i/`.

A diagnostic background-checkpoint experiment (`j`) keeps raw `synchronous=FULL`
but exceeds its **16 MiB WAL guard**, stopping the experimental worker. Its
publication p95 still fails (**58.804 ms**); it is **not safe bounded-checkpoint
acceptance**. No checkpoint-policy change is shipped. Evidence remains in
`data/migration-checkpoint-experiment-20261010-j/`.

Read-only host inspection finds `/mnt/nvme` unusable: the Kingston NVMe reports
**0 sectors**, controller state **dead**, ext4 mount option **shutdown**, and
directory I/O errors. Kernel logs record controller/reset failure and aborted
ext4 journal on October 9 (host timestamps), before these diagnostics. `df`'s
cached free-space figure is not proof that this mount works. No writes, remount,
filesystem repair, device reset or reboot is attempted. The repository, model
and evidence remain on the 120 GB SATA SSD, with about **11 GiB free** at this
inspection. Operator storage recovery is required before testing an NVMe-backed
deployment. Evidence: `data/migration-nvme-health-20261010.json`.

At `c5dfd61`, live status adds nullable **`kernel_dropped`**, sampled from the
listening socket's inode in Linux procfs, separately from application **`dropped`**.
Missing/unsupported observations stay unknown, not zero. Reads are bounded and
cached for 250 ms. Each observed increase journals `kernel_receive_overflow`
and interrupts the current evidence scope; unknown/repeated counts do not
manufacture new gaps. This is conservative sampled fencing, not reconstruction
of the exact lost packet/frame boundary. No buffer or queue is enlarged.
Focused validation: Windows **45 passed, 1 Linux-only test skipped**; Ubuntu
**46 passed**, including actual loopback receive-buffer overflow attribution.
Full isolated regression at `c5dfd61`: Windows **1,054 passed, 8 skipped**
(163.44 s), Ubuntu **1,061 passed, 1 skipped** (196.14 s).
The actual 90-second LAN repeat (`k`) receives/journals/processes **33,700**
with **zero application and observed per-socket kernel drops**, queue peak
**300**, retained comparison and SQLite integrity passing. Publication/backlog
p95 **51.386/270.041 ms** still fail; backlog maximum **685.139 ms** passes.
This verifies live socket-specific reporting without claiming latency acceptance.
Evidence: `data/migration-kernel-observer-20261010-k/`.

## Migration milestones

Progress is completed acceptance milestones, not estimated code volume:
1. **Complete:** Linux compatibility and green backend regression suite.
2. **Complete:** Authenticated private transport and server-side companion credentials.
3. **Complete:** Ubuntu service plus Windows dashboard verified across the actual SSH link.
4. **In progress:** Linux transcription and Ollama installed; fixture audio
   transport and real CPU inference verified. Operator microphone/question/
   playback acceptance and cold-inference latency headroom remain pending.
5. **In progress:** Separate-host five-minute latency/analysis gates pass without
   inference, but kernel receive losses and model-load queue drops prevent
   lossless acceptance. Representative live-game acquisition remains pending.
6. **In progress:** Actual crash recovery, service restart, SSH disconnect/reconnect
   and cancellation checks pass; real SQLite capacity exhaustion/recovery passes.
   Representative long-run resources and full-filesystem exhaustion remain pending.

Milestone progress: **3/6 (50%)**. This is not live-game acceptance or an estimate
of remaining implementation time.

The first implementation slice fixes the filename/FIFO fixtures and applies
explicit completion-JSON shape bounds rather than relying on Python recursion
limits. It adds `api --require-auth`, `F1_ENGINEER_API_TOKEN_FILE`, an Ubuntu user
service template and a Windows companion launcher. Missing credentials fail
closed; HTTP docs and read routes are protected; existing local mode remains
compatible. Windows still owns audio devices. The next slice adds user-local
Ubuntu transcription provisioning. No language rewrite, legacy-data move or
reduced raw durability is included.
