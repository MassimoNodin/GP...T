# Session engineer foundation: validation and operator runbook

Date: 2026-10-08

Status: F1–F5 implementation submitted for review; milestone completion is not
claimed. An initial real-game practice run exposed sustained acquisition
overload and no comparison-ready laps; see the
[real-game report](session-engineer-real-game-verification-20261008.md).
End-to-end real-game acceptance and process crash/power-loss experiments remain
pending. The dashboard/radio/task redesign is not part of this change.

## Reproducible validation

Run from the repository root with the existing dev/app extras:

~~~powershell
uv sync --frozen --extra dev --extra app
.venv/Scripts/python.exe -m pytest -q -rs
.venv/Scripts/python.exe -m pytest tests/test_session_evidence_load.py -q -s
~~~

The final full suite passed **946 tests, 10 skipped**, in 122.14 s. Skips concern
Windows symlink privileges, unavailable POSIX descriptor/FIFO operations and
platform-specific handle-pinning behaviour. The isolated measured run passed
both sustained load tests in 44.76 s; the final suite also includes the added
diagnostic-history bound regression. One existing Starlette TestClient/httpx
deprecation warning remains. No unrelated dependency/lockfile update is included.

Focused foundation coverage includes:

- Completed owned player/opponent laps committed and compared while session
  lifecycle remains active; identical saved report after termination.
- Real localhost UDP sockets receiving synthetic datagrams, including default
  API lifespan acquisition, queue-size-1 pressure and explicit gap fencing.
- All 24 observed slots with correctly bounded participant-tenure ownership;
  monotonic binding extents through telemetry-only outputs.
- Interrupted acquisition, reconnect requiring fresh roster, retired inputs,
  UID zero, and SEND/SSTA reuse of the same UID as a new occurrence.
- Restart reconstructing a committed prefix without duplicate publications;
  five injected failures after journal admission, chunk sealing, manifest
  boundaries and immediately before publication commit.
- A simultaneous reader during an uncommitted writer transaction observes only
  the preceding committed snapshot. Concurrent analyses also run throughout
  sustained ingestion.
- Flashback readiness supersession without rewriting historical reports;
  missing channels, checksum corruption, row/read budgets and geometry conflict.
- Immutable table deletion guards, source ownership lock, additive prototype
  schema migration and authenticated comparison/read APIs.
- Finalized CaptureReader input and live input use the same coordinator and
  produce equal normalized evidence, eligibility and measurements, before and
  after terminal flush. Operational session/revision/report identities are
  excluded. Legacy Parquet float32 versus JSON float64 values are compared with
  explicit 1e-5 tolerance rather than falsely requiring byte parity.
- Existing pipeline ordering, tenure, flashback, reset and wraparound tests are
  preserved. Legacy complete-run/capture and integrity-checked trace paths remain
  in use. Manual recording tests explicitly opt out of automatic UDP ownership.

These are synthetic admission/replay and failure-injection proofs. They are not
proof of actual F1 opponent channel availability, a disk-full filesystem, sudden
process termination, power loss, or operating-system packet loss visibility.

## Measured synthetic load (isolated run)

Windows, this workspace's Python virtual environment, SQLite WAL/FULL durability.
The fixture uses 24 cars, 1,000 frames and 2,001 datagrams (participants, lap and
car telemetry). Its deliberately short 20-frame laps exercise 1,176 sealed
attempts and 19 concurrent comparisons against pinned earlier laps.

| Measurement | Result |
| --- | ---: |
| Serial admitted processing throughput | 161.78 datagrams/s |
| Ingest/publication p95 | 14.91 ms |
| Maximum ingest/publication call | 53.64 ms |
| Sidecar database size | 38,150,144 bytes |
| Remaining staging rows | 408 |
| Maximum sealed chunk | <=256 rows |
| Python traced peak, separate 600-frame run | 1,359,592 bytes |
| Python retained at 200 frames | 95,172 bytes |
| Python retained at 600 frames | 118,888 bytes |

Measurements are wall-clock call latency and Python tracemalloc, not end-to-end
radio latency or RSS. Native SQLite/Arrow/socket allocations, UDP queues, large
real lap traces and filesystem caches are excluded from traced-memory figures.
The load assertions enforce bounded histories/frame buffering, <2 MiB retained
Python growth, <32 MiB traced peak and <5 s individual ingest calls. They are
regression guards, not a certified full-game throughput SLA. The full game packet
mix and configured frequency may exceed this measured durability throughput;
queue loss must remain explicit and cannot be marketed as lossless acquisition.

Default raw queue payload bound is approximately 64 MiB; the 512-packet frame
buffer contributes another approximately 32 MiB worst-case raw payload, before
object/copy/native overhead. The API admits two simultaneous reads/analyses;
each analysis may read two 16 MiB/20,000-row attempts, plus decoded object and
resampling overhead. These limits are materially above the small fixture peak.
Historical journal/chunks/reports consume disk indefinitely. Restart cost grows
with the admitted generation journal. Retention, disk capacity alerts, batching
optimizations and long-duration startup performance require follow-up, not silent
deletion of pinned comparison evidence.

## Automatic live usage (API-only foundation)

Start the API normally; configure game UDP to this machine on port 20777. Do not
start a second recorder on that port. No recording/import action is necessary.
The default sidecar is data/f1-engineer-evidence.sqlite3 when the archive path is
data/f1-engineer.sqlite3. The existing dashboard remains the legacy interface.

~~~powershell
uv run --extra app f1-engineer api --database data/f1-engineer.sqlite3 --recordings-root recordings --control-token-file data/.f1-engineer-control-token
~~~

Use the new bounded endpoints to inspect readiness and compare explicit laps:

~~~powershell
$base = "http://127.0.0.1:8765/api/v2/session-evidence"
Invoke-RestMethod "$base/status"
$sessions = (Invoke-RestMethod "$base/sessions?limit=100").data
$session = $sessions | Where-Object lifecycle -eq "active" | Select-Object -First 1
$attempts = (Invoke-RestMethod "$base/sessions/$($session.id)/attempts?limit=100").data
$target = $attempts | Where-Object { $_.role -eq "player" -and $_.readiness.state -eq "published" } | Select-Object -First 1
$reference = $attempts | Where-Object { $_.role -eq "opponent" -and $_.readiness.state -eq "published" -and $_.payload.car_index -ne $target.payload.car_index } | Select-Object -First 1
$token = (Get-Content data/.f1-engineer-control-token -Raw).Trim()
$report = (Invoke-RestMethod -Method Post -Headers @{ Authorization = "Bearer $token" } -Uri "$base/sessions/$($session.id)/compare?target=$($target.id)&reference=$($reference.id)").data
Invoke-RestMethod "$base/comparisons/$($report.id)"
~~~

Only issue comparison after both explicit attempts exist and are published.
IDs are opaque, not names. Pages use after=<last-returned-id>; a page limit of
100 must not be interpreted as the entire session. Select the intended UID and
session occurrence rather than assuming the first active session is correct.
GET sessions/<id>/attempts/<revision> returns bounded committed raw samples and
the manifest. GET legacy-sessions lists complete archived runs; existing archive
APIs remain their analysis path. Inspect current readiness before using a saved
comparison as a current reference. A historical report is intentionally immutable.

No manual track model, Ollama process or named-corner authoring is required for
numeric distance/braking measurements. Unknown channels, fuel/tyres, geometry,
validity and censored onset intervals are exposed as missing/qualified evidence.
The report is a measurement, not a claim that later braking is faster or causal.

To use the old Recordings start/stop/import diagnostics, start the API with
--manual-acquisition instead. The standalone record command must not compete
with an automatically listening API. Existing .f1ecap and archive files remain
usable without rewriting. Back up the sidecar consistently with SQLite WAL;
do not copy only its main database while an active writer is running.

## Acceptance matrix and remaining experiments

| Acceptance gate | Automated evidence | Remaining real-game work |
| --- | --- | --- |
| Automatic session establishment | API lifespan + localhost UDP | Real UID/session transitions |
| All observed drivers retained with ownership | 24-slot fixtures/tenure tests | Availability/privacy settings and actual field mix |
| Completed laps before closure | Active lifecycle/read snapshot tests | Actual lap completion cadence |
| Player/opponent comparison while ingesting | UDP + concurrent load reads | Full-rate sustained run and supported opponent channels |
| Same evidence after end | Saved report equality | Actual SEND/disconnect distinction |
| Replay parity | Same admitted finalized capture before/after flush | Replay a captured real-game admitted journal |
| Failure/recovery and bounds | Five failpoints, restart, queue pressure, memory guards | Hard kill, disk-full, long-session RSS/disk/startup |
| Legacy compatibility | Full Python suite/integrity APIs | Existing personal archive smoke check |

Before declaring the milestone complete:

1. Run F1 25 and the supported 2026 packet format with telemetry enabled for the
   available opponents. Record game settings, packet mix and delivery frequency.
   Do not infer detailed Time Trial rival channels from a rival index.
2. Complete player/opponent laps without stopping the listener. Issue a v2
   comparison while packets continue; record source ranges, missing channels,
   coverage and brake onset intervals. Repeat with unavailable/restricted data.
3. Exercise flashback, reconnect/silence, actual session end and another session;
   verify epochs/tenures/readiness and that old reports remain byte-equivalent.
4. Replay the exact admitted journal, including explicit gaps, in an isolated
   sidecar and compare normalized evidence/eligibility/measurements. Preserve the
   raw capture/journal as the reproducibility artifact.
5. Sustain the full packet mix for a race-length interval while reading evidence.
   Measure queue drops/depth, p95/max publication and comparison latency, RSS,
   WAL/database growth and restart time. Require explicit gaps under overload.
6. Terminate the process during acquisition/publication, restart, and repeat with
   storage-full errors on a disposable volume. Verify only committed manifests
   were visible, source ownership is reclaimed, and historical reports survive.

An initial in-game experiment subsequently exposed throughput failure at the
observed packet rate. Its admitted journal and a consistent baseline snapshot
are preserved locally; no completed-lap comparison pass is claimed. The assigned
Time Trial and session-format audit reports were not required, edited, or treated
as established capabilities. Follow-up must not remove complete-run, checksum,
ownership or coverage guards to make a demonstration appear successful.
