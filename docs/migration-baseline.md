# Ubuntu / Windows migration baseline

Updated: 2026-10-09. This is the single project baseline replacing historical
plans, audits and development-rule documents. Measurements are carried forward
from recorded investigations, not rerun during this cleanup. They describe
specific fixtures/hosts, not verified Ubuntu performance.

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
  speech/model provisioning remains unverified.
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

Model status is honestly `model_pin_missing`; Linux transcription/model
provisioning and a spoken-question round trip are still pending. Windows browser
speech discovers its local voice; no microphone recording or playback acceptance
was performed. Game UDP destination should be `192.168.1.115:20777`, not the
Windows dashboard/tunnel address. No live-game throughput acceptance is claimed.
Settings still contain local-machine wording and Windows-only model setup hints;
make those backend/companion-aware during runtime provisioning.

## Migration milestones

Progress is completed acceptance milestones, not estimated code volume:
1. **Complete:** Linux compatibility and green backend regression suite.
2. **Complete:** Authenticated private transport and server-side companion credentials.
3. **Complete:** Ubuntu service plus Windows dashboard verified across the actual SSH link.
4. **Pending:** Linux transcription/model provisioning and spoken-question round trip.
5. **Pending:** Representative live telemetry, eligible analysis and persistence acceptance.
6. **Pending:** Restart/disconnect/cancellation and long-run resource acceptance.

Milestone progress: **3/6 (50%)**. This is not live-game acceptance or an estimate
of remaining implementation time.

The first implementation slice fixes the filename/FIFO fixtures and applies
explicit completion-JSON shape bounds rather than relying on Python recursion
limits. It adds `api --require-auth`, `F1_ENGINEER_API_TOKEN_FILE`, an Ubuntu user
service template and a Windows companion launcher. Missing credentials fail
closed; HTTP docs and read routes are protected; existing local mode remains
compatible. Windows still owns audio devices. No language rewrite, model
installation, legacy-data move or reduced raw durability is included.
