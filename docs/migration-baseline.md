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
| Actual full-filesystem test, `bd125ef` | Isolated 128 MiB ext4 loop, reserve zero, zero free bytes. Real SQLITE_FULL, WAL/FULL atomic rejection and integrity/reopen pass. SessionCoordinator fences at sequence 32, reopens/publishes to 64 with no partial admissions. Production disk untouched. |
| Historical captures transferred/imported | Checksums match Windows originals; 144,189 + 83,139 packets, seven attempts; no malformed/decode/frame-overflow import errors. Invalid/partial laps and source gaps remain; no eligible reference. Originals preserved. |
| Windows browser Ask on imported evidence | Correct lap time 1:19.295 plus game-invalid qualification; pinned model CPU, 0 VRAM, 20.5 s. Not microphone/playback acceptance. |
| Fixture speech transport | JFK WAV correctly transcribed via Windows proxy/SSH on Ubuntu in 1.48 s; not microphone hardware acceptance. |

Earlier cold model call **58.063 s** approached the deadline; idle/warm/unloaded
observations 31.671/2.884/21.175 s. NVMe results do not establish a cold-start
percentile or guaranteed conversational SLA. Process-kill/reopen, restart,
cancellation and SSH disconnect/reconnect checks pass. Anonymous credentials
rejected; disconnected/missing-token transport fails closed, no Windows backend
fallback or credentials exposed to the browser.

Windows full regression before final storage refinements: **1,066 passed,
8 skipped**. Latest focused storage tests **14 passed**; all dashboard test
scripts pass, including speech ownership text. Last full Ubuntu result:
**1,061 passed, 1 skipped**, `c5dfd61`; refresh on final SHA. Non-failing
Starlette/httpx deprecation remains.

Evidence stays ignored: Ubuntu `data/migration-*20261010*`, NVMe
`/mnt/nvme/f1-engineer-migration-checks/migration-nvme-*20261010-*`, Windows
`data/migration-windows-*20261010*`. Run m contains coexistence resources;
`migration-full-filesystem-acceptance-20261010-final.json` disk-full proof.
Capture hashes/import results/browser screenshot preserved. Restarted 30-minute
run o is still being evaluated; file/process existence is not acceptance.

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
- Current captures contain invalid/partial laps and missing damage samples.
  Completion/integrity is not reference/coaching eligibility. Valid live laps,
  retained comparison and calibrated geometry still need evidence.
- Cold-model headroom, operator microphone/edit/submit/read-aloud and sustained
  representative live-game telemetry remain acceptance gates. Do not bulk-import
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
5. **Live-game gate:** repaired NVMe five-minute model coexistence passes;
   representative valid live laps and retained comparison pending.
6. **In progress:** crash/disconnect/capacity/full-filesystem checks pass;
   extended resources, final regressions and NVMe persistence pending.

**[###---] 3/6 accepted (50%)**. No synthetic claim of live-game acceptance.

After automated work, operator:
1. Set F1 UDP `192.168.1.115:20777`; drive clean completed comparable laps;
   inspect receiver loss/backlog and retained comparison evidence.
2. On selected-attempt Engineer, record a short question, review/edit transcript,
   submit and confirm qualified evidence-based response.
3. Click Read aloud; confirm Windows speaker/headset output. No ambient recording
   or invented audible verification by the agent.
