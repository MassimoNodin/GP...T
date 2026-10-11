# Recorded telemetry and subscription AI coexistence

## Scope

Verified on October 11, 2026 against commit
`3939067f2a4d46d8a93a9b828f992072c57d7a1f`. This is a Windows-to-Ubuntu
recorded-datagram replay, not live-game acceptance or verification of the game's
sender. Three saved Career race-lap AI requests ran in the same API process
that acquired, journaled and published the replay telemetry.

The validation API used ports 8766/20778, the production demand_v1 profile,
an isolated Linux UDP receiver and CPU affinity 0-1. SQLite backup snapshots
of the existing archive/evidence databases supplied historical AI evidence;
all replay writes stayed in the validation directory. The production service,
ports 8765/20777, authentication profile and real-game evidence were not replaced.
No credentials were copied into the validation database or artifacts.

## Results

- Capture: data/d0058-browser-capture-root/melbourne.f1ecap, one pass at speed 1.
- Sender: 83,139 datagrams / 88,215,536 payload bytes in 264.543 seconds.
- Ubuntu: 83,139 received, journaled and processed; zero app/kernel/socket drops.
- Final receive queue and pending publication: zero. Peak queue depth: 118/1,024.
- 267 status samples, including 241 samples with advancing publication counts.
- Publication duration p95: 14.224 ms; publication-delay p95: 40.351 ms.
- Three successful gpt-6-luna subscription answers: 3.317, 3.744 and 3.193 seconds.
- Received during those requests: 1,179 / 1,353 / 1,141 datagrams.
- Processed during those requests: 1,175 / 1,356 / 1,136 datagrams.
- All answers retained the selected 93,853 ms Bahrain lap measurement.
- No alternate model, Ollama or API-key fallback; no monitor errors.
- Post-run Windows UDP replay, race diagnostics and integration tests: 49 passed.

Publication timings are sampled last-batch values, not per-packet latency.
Sender acceptance alone is not delivery proof; the equal receiver, journal and
publication counts provide delivery confirmation for this isolated run.
The Melbourne capture's observed game-invalid lap was not substituted for the
admitted historical race lap used by AI. Live-game AI, future-lap detail demands,
full race strategy and sustained heavy comparison load remain unverified.

The browser's existing server-side runtime proxy returned HTTP 200 and Codex
ready for exactly gpt-6-luna with fallback disabled. Settings still contains
legacy Ollama/pinned-model wording; that copy was not changed by this backend
validation, and no browser question-composer or microphone acceptance is claimed.

## Evidence

Ubuntu artifacts are retained under
/mnt/nvme/f1-engineer/data/telemetry-ai-replay-20261011-3939067-02/:
provenance.json, monitor.py, samples.jsonl and summary.json. Windows copies plus
sender.json and sender-error.txt are under the matching data/ directory.

The first run (-01) stopped before the sender completed. Its partial evidence
and interrupted.json are retained separately and are not counted as a pass.
The successful rerun used a hidden standalone Windows sender and a transient
systemd monitor so tool-session interruption would not terminate validation.
