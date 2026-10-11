# Ubuntu / Windows migration baseline

Updated: October 10, 2026. Single handover for architecture, measurements,
unresolved issues and acceptance. Superseded plans remain in Git history,
not active development instructions. See README for setup commands.

## Deployment and workflow

Keep Python processing and TypeScript UI; persistence/model loading dominate
measured costs, so a language rewrite is not justified.

| Host | Ownership |
| --- | --- |
| Ubuntu | UDP decoding/acquisition, analysis, SQLite/Parquet evidence, recordings, CPU transcription and private model routing. |
| Windows | Dashboard, browser microphone, editable questions and local spoken playback. |
| Transport | Authenticated loopback API through SSH; credentials server-side. Game UDP directly to Ubuntu. |

Edit Windows `D:\F1-Engineer`, commit each topic, push main, pull the exact SHA
on Ubuntu and test. `uv.lock` is tracked; use frozen installation. Ubuntu repo:
`/home/massimo-nodin/GP...T`; SSH `massimo-nodin@192.168.1.115`, Windows key
`id_ed25519_ubuntu`. Companion launcher: `scripts/start-ubuntu-companion.ps1`.

Enabled/lingering user services: `f1-engineer`, `f1-engineer-ollama`.
Ubuntu API `127.0.0.1:8765`, model `127.0.0.1:11435`; Windows SSH tunnel
`127.0.0.1:18765`, dashboard `127.0.0.1:3001` (launcher DashboardPort 3001).
Game UDP: **192.168.1.115:20777**, UFW allowed from Windows **192.168.1.111**.
Synthetic benchmark uses separate UDP 49077. No LAN API/model listener.

Production data: `/mnt/nvme/f1-engineer/{data,recordings,models}`. Installed
`f1-engineer-nvme.conf` / `f1-engineer-ollama-nvme.conf` user storage drop-ins
fail closed if the mount/destination is missing, rather than falling back to
SATA. SQLite backup API copies passed quick_check; model blobs were SHA-256
verified, legitimate internal symlinks preserved. Original SATA files remain
for rollback. Control token stays in ignored checkout data: Ubuntu mode 0600,
Windows owner-only ACL. Never copy live SQLite or open it over a network share.

Pinned Ollama **0.40.2**, Qwen3:4b Q4_K_M:
`sha256:359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`.
CPU two threads, context 4096, keep-alive 10 s, HTTP/API deadlines 58/60 s.
Ubuntu whisper.cpp **1.9.3**, pinned tiny.en, CPU. Browser clips <= 12 s;
transcripts are unverified editable drafts. Windows discovers Microsoft George.
Runtime/model updates require explicit pin acceptance.

On this verified four-core host, optional CPU drop-ins restrict the backend to
CPUs 0-1 and Ollama to 2-3, with model nice level 5. Other OS processes may use
those CPUs; this is not exclusive reservation. Actual affinity was checked
after restart and reboot. These host-specific templates are not portable
defaults; remove only the CPU drop-ins to roll back. This is a contention
mitigation experiment, not proof that every previous loss had the same cause.

## Boot and storage repairs

Host: i5-4690K four cores, about 7.7 GiB RAM, 4 GiB swap, GTX 1060. Root SATA
has about 9.4 GiB free; recovered 1 TB NVMe about 860 GiB. NVMe UUID:
`eaf66b95-0d3f-48bd-a075-c127e2448684`.

NVIDIA 535 DKMS failure interrupted kernel 7.0 configuration, leaving initramfs
missing. Recommended **580.178.04** prebuilt modules replaced 535 DKMS; package
audit/dependency checks and actual reboot into **7.0.0-38-generic** pass. GRUB
explicitly defaults to that tested kernel with a visible 10 s menu; retained
**6.17.0-19-generic** is marked manual as fallback. Validate future kernels
before promoting the boot default.

Operator stopped nginx, unmounted and power-cycled the failed NVMe. It now
reports live/full capacity and passes offline read-only e2fsck, fsync/readback
and SQLite WAL/FULL checks; nginx restored. SMART critical warnings/media errors
zero, unsafe shutdowns 25. APST disabled with
`nvme_core.default_ps_max_latency_us=0`: reversible mitigation, not proven root
cause or long-term reliability. Override:
`/etc/default/grub.d/99-local-nvme-stability.cfg`. Preserve fallback/backups and
watch kernel NVMe/ext4 errors. No destructive repair or automatic deletion.

## Useful measurements

Sampled percentiles are observations, not exhaustive latency statistics.
Synthetic runs and historical imports are not current live-game acceptance.

| Experiment | Result / significance |
| --- | --- |
| Oct 8 clean replay | About 354.3 packets/s; processing CPU about 0.35 ms/packet. 600 commits 0.690 s vs processing 0.234 s: persistence dominated. |
| Oct 9 admitted replay | Batch 32 reduced 53.39 s to 45.09 s (15.5%), same evidence/eligibility fingerprints; cannot restore lost packets. |
| Actual F1 25 practice, `db094fdf` | 29,302 received, 20,968 journaled, 7,822 overflow drops plus 512 unadmitted at stop. Backlog p95/max 5.373/5.797 s; no measurement-ready comparison. Historical live acceptance failed. |
| SATA contention diagnostic | Publication/backlog p95 57.348/302.599 ms; CPU route 23.406 s. SQLite/I/O waits dominated. Background-checkpoint experiment exceeded 16 MiB WAL guard; not shipped. |
| Recovered NVMe 90 s, `4060827`, run l | 33,700 lossless, queue/kernel/socket losses zero; publication p95 13.905 ms, backlog p95/max 46.982/140.013 ms, queue peak 25. |
| NVMe + real model calls, five minutes, `e9c3425`, run m | 108,250 lossless; publication p95 15.105 ms, backlog p95/max 49.229/195.608 ms, queue peak 29, RSS peak 117,784,576 bytes, SQLite 699,748,352 bytes. CPU routes 19.906/20.272/20.087 s; comparison/continued ingestion/integrity pass. |
| Interrupted extended run n, concurrent imports | Sender stopped early; 318,137 received/processed, two observed kernel drops. Not accepted. Mixed import contention remains unresolved. |
| Extended repeat o, before CPU isolation | Two kernel drops; stopped rather than accepting a lossy run. Model calls and service restart also occurred; causality is not established. |
| CPU-isolated 90 s plus two model calls, `2297beb`, run p | 33,700 lossless; kernel/app/socket drops zero, publication p95 19.279 ms, backlog p95/max 59.272/170.454 ms, queue peak 41. CPU routes 21.329/19.610 s; comparison/integrity pass. |
| CPU-isolated 30 minutes, `2297beb`, run q | 640,750 sent/received/journaled/processed; kernel/app/socket drops zero. Publication p95 16.857 ms, backlog p95/max 48.417/849.490 ms, queue peak 376 then drained. RSS peak 161,349,632 bytes, SQLite 4,119,347,200 bytes. Retained comparison, continued ingestion and integrity pass. Windows proxy CPU routes 19.891/19.594/20.891 s; correlated JFK transcription 1.766 s. |
| Actual full-filesystem test, `bd125ef` | Isolated 128 MiB ext4 loop, reserve zero, zero free bytes. Real SQLITE_FULL, WAL/FULL atomic rejection and integrity/reopen pass. SessionCoordinator fences at sequence 32, reopens/publishes to 64 with no partial admissions. Production disk untouched. |
| Historical captures transferred/imported | Checksums match Windows originals; 144,189 + 83,139 packets, seven attempts; no malformed/decode/frame-overflow import errors. Invalid/partial laps and source gaps remain; no eligible reference. Originals preserved. |
| Windows browser Ask on imported evidence | Correct lap time 1:19.295 plus game-invalid qualification; pinned model CPU, 0 VRAM, 20.5 s. Not microphone/playback acceptance. |
| Oct 10 actual Austria short practice after migration | Ubuntu received/journaled/processed 71,214 packets, app drops/socket errors zero, kernel drops 25; queue drained, peak 261. Completed game-valid player lap times 1:09.728 and 1:08.881 retained, but both start-unobserved and practice mode policy unimplemented; quarantined, not comparison acceptance. Four kernel-overflow gap records observed. |
| Oct 10 actual Bahrain practice after restart into independent receiver and explicit practice policy, `33f9f5f` | 228,237 received/journaled/processed, app/kernel/socket losses zero, queue drained, peak 442. Six game-valid completed player laps retained; laps 7-10 fully start-observed without acquisition-gap qualifications. Lap 6 start-unobserved; lap 11 acquisition-gap qualified. 300 advancing-publication samples: publication p95/max 30.077/57.108 ms, backlog p95/max 78.503/306.634 ms; not exhaustive latency statistics. Comparisons 9 vs 8 and 10 vs 7 both reject HTTP 422 `analysis_read_budget_exceeded`: clean laps contain 20.55-21.61 MB of evidence, exceeding the 16 MiB reader budget. Useful live capture, not retained-comparison acceptance; do not request repeat driving to compensate for the reader limitation. |
| Operator-authorized 32 MiB per-lap reader experiment on saved Bahrain evidence | Isolated Ubuntu worker compares 9 vs 8 and 10 vs 7 successfully, retains and rereads identical reports. Times 0.570/0.539 s; worker peak RSS 139,186,176 bytes. Explicit practice diagnostic policy and qualifications remain; differences -654/+3130 ms. This measures sequential saved-evidence reads, not two concurrent requests or ingestion coexistence with these larger laps. |
| Restarted Ubuntu API with the one-line 32 MiB trial | Authenticated comparisons 9 vs 8 and 10 vs 7 both HTTP 200 in 0.899/0.741 s; retained report GETs HTTP 200 and payloads match. Independent receiver recovered and is ready, queue/socket/app/kernel counters zero after restart; no new driving during these comparison reads. |
| Fixture speech transport | JFK WAV correctly transcribed via Windows proxy/SSH on Ubuntu in 1.48 s; not microphone hardware acceptance. |

Earlier cold model call **58.063 s** approached the deadline; idle/warm/unloaded
observations 31.671/2.884/21.175 s. NVMe results do not establish a cold-start
percentile or guaranteed conversational SLA. Process-kill/reopen, restart,
cancellation and SSH disconnect/reconnect checks pass. Anonymous credentials
rejected; disconnected/missing-token transport fails closed, no Windows backend
fallback or credentials exposed to the browser.

Run q sampled resources every five seconds (364 samples): available RAM never
below 3,139,043,328 bytes; receiver swap remained zero, but system swap increased
298,881,024 bytes. Whole-host memory full-pressure total increased about 1.876 s;
I/O pressure rose during final integrity scanning. Harness RSS includes retained
samples, not proof of indefinitely flat memory. SMART warning/media counts stayed
zero, unsafe shutdowns 25 and error entries 3 unchanged. smartctl exit 4 reflected
an unsupported self-test log, not a successful exit or a proven new media fault.

Post-soak reboot retained kernel 7.0.0-38, mounted NVMe, both active services,
CPU affinity/nice settings, model/transcription readiness and authenticated API
(anonymous 403). Both production databases pass quick_check; all 15 registered
Parquet hashes still match. The Windows companion was restarted after its SSH
connection ended; isolated disconnect/reconnect verification passes again.
Idle acquisition reports stale with zero received, no error; live game is pending.
Boot logs retain ACPI firmware warnings; no observed NVMe/ext4 failure this boot.

Final Windows full regression: **1,069 passed, 8 skipped** at `0ba5577`;
focused storage tests **14 passed** after `f847ec1`. Final Ubuntu full regression:
**1,076 passed, 1 skipped** at `f847ec1`. The replacement safety test now allocates
its replacement before atomic rename, avoiding an ext4 inode-reuse assumption;
no production behavior changed. All dashboard test scripts, TypeScript,
changed-file formatting and an isolated production build pass. Frozen lock check
passes on Ubuntu. Non-failing Starlette/httpx deprecation remains.

Evidence stays ignored: Ubuntu `data/migration-*20261010*`, NVMe
`/mnt/nvme/f1-engineer-migration-checks/migration-nvme-*20261010-*`, Windows
`data/migration-windows-*20261010*`. Run m contains coexistence resources;
`migration-full-filesystem-acceptance-20261010-final.json` disk-full proof.
Postboot browser Ask returns 1:19.295 with the invalid-lap qualification through
the pinned CPU model in 21.3 s. Capture hashes/import results preserved. Run q contains
extended soak/resources, model and speech fixture evidence; postboot report:
`data/migration-nvme-postboot-20261010.json`.
Actual game investigation: `data/migration-live-investigation-20261010.json`.
Latest actual Bahrain window: Windows
`data/migration-live-ready-20261010-1912/` contains the baseline, bounded
two-second diagnostics log, six player attempt manifests, failed comparison
responses and `verification-summary.json`. Background sampling stopped after
the operator finished; no competing UDP receiver or bulk import was started.
Production evidence ledger read-only `quick_check` passes after this run.
The two terminal gap records (sequences 569040 and 569042) are telemetry silence,
not kernel/app/socket loss; lap 11 remains gap-qualified rather than being
promoted to clean comparison evidence.
`read-budget32-worker.json` preserves the isolated comparison measurements.
`read-budget32-api-verification.json` and the `comparison32-api-*` responses
preserve the successful restarted-API retries. New byte-budget tests accept a
20 MiB payload and reject a 33 MiB payload. The relevant regression run passed
47 of 48 tests initially; the timing-sensitive live UDP comparison test failed
with `attempt_not_measurement_ready` and passed its isolated rerun. Existing
Starlette/httpx deprecation warning remains; no unrelated test change was made.
The requested byte-limit change is 16 to 32 MiB per lap; the 20,000-row limit,
checksums/ownership checks, two-request comparison admission and receiver/WAL
settings are unchanged. Ubuntu received only the one-line reader change as an
uncommitted experiment, with rollback copy
`data/migration-evidence-reader16-20261010-before.py`; no unrelated Windows
dashboard changes were deployed. Normal deployment still requires an explicitly
authorized commit/push of this change.

## Safeguards and unresolved issues

- Keep WAL/FULL, batch cap 32, scheduling 20 ms, queue cap 1,024. Gates:
  publication p95 <= 50 ms, backlog p95 <= 250 ms, maximum <= 1 s. Never enlarge
  buffers, weaken persistence or relax thresholds to hide drops.
- Kernel drops are socket-specific sampled Linux evidence, separate from app
  drops. Unknown remains unknown. Increments journal/fence a gap, not reconstruct
  an exact missing frame.
- Automatic v2 acquisition owns UDP 20777; legacy controls correctly reject a
  second receiver. Imported/v1 Ask and v2 live evidence are separate paths;
  importing history does not validate live Ask.
- Dashboard/Live Telemetry now has a separate read-only automatic v2 acquisition
  panel: receiver state, admission/publication counters, separate app/kernel/socket
  losses, queue occupancy and last publication backlog. Unknown kernel losses stay
  unknown; disconnect/hidden tabs clear values. The authenticated Windows proxy
  does not start a competing receiver. Player gauges/charts still require a pinned
  v1 recording or replay; v2 player-monitor integration remains pending.
  Explicit practice/qualifying comparison policy is implemented. The latest actual
  Bahrain run has no reported app/kernel/socket losses. Its initial comparisons
  failed the 16 MiB budget; the operator-authorized 32 MiB experiment now retains
  diagnostic comparisons in an isolated worker and through the restarted API.
  Verify larger-lap read contention before treating the new resource budget as accepted;
  the cause of earlier kernel losses remains
  unproven and one clean run does not establish universal reliability.
  Stored completed laps alone are not clean comparison evidence.
- Current captures contain invalid/partial laps and missing damage samples.
  Completion/integrity is not reference/coaching eligibility. The latest run has
  four valid, fully start-observed, gap-unqualified live player laps and retained
  diagnostic comparisons at 32 MiB; calibrated geometry and coaching eligibility
  remain unproven.
- Cold-model headroom remains a risk; operator microphone/edit/submit/read-aloud
  and representative live-game telemetry remain acceptance gates. Do not bulk-import
  during live validation; simultaneous import contention is not resolved.
- **Manual resource policy:** 100 GiB app-evidence budget, 10 GiB NVMe free reserve.
  Not an automatic quota/retention guarantee. Synthetic run m grew about
  8 GiB/hour; actual packet mix/duplicate archives change growth. Check Settings/df,
  archive verified closed-session data with consistent DB references, stop before
  budget/reserve breach. Never prune live DB/WAL. SATA rollback is not remote backup.
- Repaired-device checks cannot rule out controller failure. Unrelated reviewer
  service boot DNS retry policy stays outside migration scope.

## Milestones and operator gates

Acceptance counts completed milestones, not code volume:
1. **Complete:** Linux compatibility and backend regression baseline.
2. **Complete:** Authenticated private transport and server-side credentials.
3. **Complete:** Ubuntu service / Windows companion across actual SSH.
4. **Operator gate:** transcription/model and historical Ask work; real
   microphone/edit/submit/audible playback pending.
5. **Live-game gate:** repaired NVMe 30-minute model coexistence passes;
   earlier actual Bahrain run retains valid fully observed player laps with zero
   reported receiver losses and retained diagnostic comparisons at the requested
   32 MiB budget through the restarted authenticated API. New-budget large-lap
   ingestion contention remains pending; the October 10 reapproval run reports
   receiver losses before stress workloads launch and does not pass the live gate.
6. **Complete:** crash/disconnect/capacity/full-filesystem checks, 30-minute
   resources, final regressions and actual NVMe/service reboot persistence pass.

**[####--] 4/6 accepted (67%)**. No synthetic claim of live-game acceptance.

Milestone 5 reapproval run armed October 10 after the 32 MiB API retries:
Windows `data/migration-m5-live-coexistence-20261010-2146/` records a fresh
baseline (61 old received/processed packets excluded), 0.25-second latest-value
samples and five-second Ubuntu process/cgroup/memory/pressure/storage samples.
After a new completed player lap, the harness schedules at most 24 batches of
two simultaneous saved Bahrain lap comparisons, 15 seconds apart, plus three
pinned CPU model calls at offsets 0/90/180 seconds. The model calls use verified
historical v1 evidence to exercise contention, not live v2 Ask. Total harness
duration is bounded to 45 minutes; no bulk imports or competing listener.
Observed receiver losses stop additional workloads without stopping acquisition.
Driving protocol: fresh dry Bahrain practice, one out-lap then five uninterrupted
timed laps; pause after the fifth timed completion and request result review.
Milestone 5 stays pending until overlap, loss, sampled latency, resource and fresh
lap/retained-comparison evidence are inspected. Sampling is not exhaustive
per-publication maximum-latency proof; do not silently relax existing thresholds.

Reapproval result (October 10, stopped 22:07:34 Sydney / 11:07:34 UTC):
**Milestone 5 remains pending; 4/6 milestones accepted.** The harness and its
resource sampler stopped; acquisition and Ollama services remain active. Since
baseline, 222,230 packets were received and 222,139 journaled/processed, with
91 application queue drops, 7 reported kernel drops and zero socket errors. The
queue drained afterward; peak occupancy was 992/1024. First sampled loss was
21:59:39 Sydney, before the 22:00:12 completed-lap workload trigger. The safety
guard therefore launched zero comparison batches and zero model calls during
driving. Preflight comparisons preceded arming and do not establish coexistence.

Across 2,363 advancing-publication samples, publication p95/max was
33.126/368.988 ms and backlog p95/max was 75.092/3,513.788 ms. The sampled
backlog maximum exceeds the existing 1-second gate; percentile passes do not
override packet losses or missing workload overlap. These are sampled
latest-value statistics, not exhaustive per-publication latency measurements.
The 185 resource samples retained at least 6.18 GB available memory and
903.20 GB NVMe free, with NVMe mounted throughout and no parent-service process
swap. Backend/model CPU affinities remained 0-1/2-3; no cause of the stalls is
established from this alone.

Fresh session `6a0c945288aa51dab29768cbbd31261c` retained two game-valid,
fully start-observed, gap-unqualified published player laps: lap 4 (95,174 ms,
20,554,007 bytes) and lap 5 (111,363 ms, 24,049,507 bytes). Their post-driving
32 MiB diagnostic comparison and authenticated retained GET succeeded, report
`e78642e128a2f610fb50a5951e91b786b10383ceb8749112762a5ec57ffe5118`.
This proves these new large laps can be read, not live model/comparison overlap
or coaching eligibility. Run artifacts, loss chronology and sampled resource
summary are retained in the ignored run directory above. Investigate acquisition
stalls before requesting another driving run; do not increase buffers or relax
acceptance thresholds to hide the losses.

### October 10 kernel-receive isolation investigation

Read-only inspection of the production UDP socket confirmed seven socket drops;
host UDP errors were receive-buffer errors, not checksum or UDP-memory errors.
The first loss precedes retained opponent-lap publication in the new run. Raw
journal sequences 641444/641445 have a 190.430 ms monotonic receive gap near the
first loss. These user-space timestamps cannot distinguish network/game bursts
from receiver scheduling delays; the exact historical trigger remains unproven.

A controlled, independent-process sender reproduced the receiver-thread failure
on Ubuntu with unchanged 212,992-byte socket buffering and 1,024-packet admission:
530 packets/s plus a deliberate 190 ms parent-interpreter stall lost 11 of 240
datagrams in the threaded receiver, versus zero with process-isolated reception.
A 355 packets/s, 500 ms diagnostic also reproduced losses (69/240 threaded,
zero isolated). Artificial stalls establish the failure mechanism, not attribution
of every historical loss to a particular processing function.

Linux automatic acquisition now selects an isolated socket-owner process with
a bounded shared mapped ring; Windows retains the independent-thread path.
The ring has the same packet-count limit, supports full UDP payloads, and uses
anonymous temporary backing with sparse allocation. No socket buffers or queue
limits are increased. Shutdown preserves queued packets for the existing discard
fence, releases resources on failures, and terminates reception on parent death.
Status identifies this path as `isolated_process`. Opponent
selection/persistence policy is unchanged.

Reproduce on Linux with:

```
python -m scripts.benchmark_receiver_isolation --output data/unique-isolation.json
```

An isolated-checkout 90-second loopback ingestion/comparison run admitted,
journaled and processed all 33,700 packets with zero kernel/application/socket
losses and retained its comparison after shutdown. Queue peak was 414/1024;
sampled publication p95 was 117.382 ms and backlog p95/max 579.211/912.642 ms.
Its database was on the host root filesystem, not production NVMe; do not compare
these timings directly with production or claim all live latency gates passed.
Evidence is in Ubuntu data/udp-isolation-20261010-fix/ and the corresponding
ignored Windows data directory. Production services/source were not replaced;
normal explicitly approved push/pull deployment and fresh live-game acceptance
remain necessary. Milestone 5 remains pending.

Follow-up on October 11: Windows-to-Ubuntu synthetic traffic exposed a second
remaining risk. The host receives over `wlp6s0` Wi-Fi with power saving enabled.
The original sender generated packets while sending and had catch-up delays of
630/452 ms; two runs lost 597/297 packets despite isolated reception. Preparing
the same 33,700 datagrams before sending reduced maximum sender lateness to
120 ms, but the power-saving-on run still recorded 14 kernel drops.

A temporary power-saving-off run with the same precomputed workload and a
comparable 118 ms maximum sender lateness admitted, journaled, and processed
all 33,700 packets without application/kernel/socket loss. All split-host checks
passed, including retained comparison, concurrent ingestion, and SQLite
integrity. Publication p95 was 17.791 ms, oldest-delay p95/max was
51.620/141.498 ms, and queue peak was 43/1024. The database was on NVMe.
This A/B result implicates the Wi-Fi power-saving path in the residual synthetic
losses; it does not establish the cause of every historical live-game drop.
Power saving was restored to on after the test; the production service remained
active with its original PID/source. Results are under
`/mnt/nvme/f1-engineer-migration-checks/udp-isolation-20261010-lan-*` and the
ignored Windows `data/udp-isolation-20261010-fix/` directory.

Before live acceptance, prefer wired reception or explicitly approve disabling
power saving for the telemetry Wi-Fi connection. On this host the connection
is `dusty`; record its existing value before making a persistent change:

```sh
nmcli -g 802-11-wireless.powersave connection show dusty
sudo nmcli connection modify dusty 802-11-wireless.powersave 2
sudo iw dev wlp6s0 set power_save off
iw dev wlp6s0 get power_save
```

These commands are an operator recommendation, not a persistent change already
applied. Do not restart the Wi-Fi connection during active ingestion. Restore
the recorded connection value and previous runtime setting to roll back. The
isolated receiver also still needs the normal approved code deployment.

Validation: full isolated Ubuntu suite passed (1,111 passed, one skipped).
The Windows full suite had 1,095 passed, 16 skipped, and one failure in the
200 ms stale-timeout live-comparison test. That test failed again during
concurrent synthetic sending, then passed standalone on both unchanged HEAD
and the patched checkout; do not report the complete Windows suite as green.
The focused receiver run had 20 passed and nine skipped apart from that test.
Compilation and diff-whitespace checks passed.

After automated work, operator:
1. Set F1 UDP `192.168.1.115:20777`; drive clean completed comparable laps;
   inspect receiver loss/backlog and retained comparison evidence.
2. On selected-attempt Engineer, record a short question, review/edit transcript,
   submit and confirm qualified evidence-based response.
3. Click Read aloud; confirm Windows speaker/headset output. No ambient recording
   or invented audible verification by the agent.
