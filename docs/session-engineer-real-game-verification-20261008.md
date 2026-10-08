# Real-game foundation verification — 2026-10-08

Status: **failed end-to-end acceptance at the current game/host settings**.
The user drove a real practice session while the foundation backend ran. This
replaces the prior statement that no in-game experiment was available; it does
not make the foundation milestone complete or certify the remaining scenarios.

## Setup and preserved artifacts

- Feature branch: feature/session-engineer-foundation-20261008-implementation.
- Tested source HEAD: dd1f5bb (F1–F5 implementation and synthetic validation).
- API launched with the existing virtual environment, automatic UDP on
  0.0.0.0:20777 and loopback API on 127.0.0.1:8765. No legacy recorder/importer,
  model or track authoring was used.
- A separate verification archive/sidecar was used under ignored data/. Existing
  saved sessions and the unrelated uv.lock/AGENTS.md workspace changes were not
  modified.
- Observed format 2025, Bahrain/Sakhir (track 3), 5,408 m, short practice,
  Grand Prix mode, UID 40044578078393254. Session identity was established
  automatically as afc9d283e8905fa2a8da480a45f7983f.
- Exact configured game telemetry frequency/privacy settings were not separately
  confirmed with the user; incoming rate was measured, not inferred from a menu.

Local reproducibility artifacts (not committed because they contain raw game
telemetry and a large database):

- data/session-engineer-verification-20261008-evidence.sqlite3 and its WAL.
- data/session-engineer-verification-observations.jsonl.
- data/session-engineer-real-game-baseline-20261008-163122.sqlite3, a consistent
  SQLite online backup made while ingestion continued.
- Matching .summary.json with snapshot ledger counts and a later runtime reading.

The baseline snapshot is 350,019,584 bytes. It may include one journal entry
beyond the committed processing checkpoint; that is a deliberate recovery seam,
not an assertion that every admitted datagram was already published. Raw journal
input is retained for deterministic reproduction. Do not commit the control
token or these raw artifacts.

## Verified observations

| Observation | Result |
| --- | --- |
| Automatic UDP receipt and logical session establishment | Passed |
| Context/format decoding | Known Bahrain practice context; zero top-level decode errors in the preserved snapshot |
| Observed driver association | 20 distinct bound car slots, 380 tenure bindings in the snapshot |
| Silence handling | One telemetry-silence interruption recorded; did not fabricate session end |
| Completed player/opponent measurement readiness | Failed: no published attempts |
| Comparison while acquisition continued | Not reached: no eligible pair |
| Post-end exact comparison persistence | Not tested: no comparison was created |
| Real-game replay parity | Not yet run; admitted journal preserved |

The first 1,000 admitted datagrams spanned 2.8194609 seconds of captured monotonic
time (approximately 355 datagrams/s). Packet IDs were 0, 1, 2, 3, 5, 6, 7, 10,
11, 12, 13 and 15; this is materially richer than the synthetic lap/telemetry mix.
Later observer windows measured roughly 350–360 received datagrams/s against
approximately 35–50 processed datagrams/s as overload accumulated. These are
sampled wall-clock counter deltas, not a certified performance benchmark.

At the runtime reading following the baseline backup:

- Received: 108,368; processed: 20,149; known queue drops: 87,194.
- Queue limit and observed maximum depth: 1,024.
- Socket errors: 0; runtime error: null; state: receiving.

The persisted baseline snapshot contains:

- 20,025 admitted datagrams, 21,969,187 raw payload bytes.
- 7,618 journal gap entries; 7,615 committed queue-pressure gap metadata entries,
  one listener-start entry and one silence entry.
- Committed sequence 27,642; gap epoch 7,617.
- 4,770 player attempts, all quarantined; no opponent attempts and no saved
  comparisons. Recent inspected player fragments were abandoned one-row
  attempts. This is not evidence of completed-lap readiness.

The initial API observer inspected at most five 100-row pages per session; that
bounded scan alone could not establish global absence of eligible laps. The
result above was therefore checked against aggregate queries over the entire
consistent ledger snapshot, including latest readiness dispositions.

## Interpretation and next gate

The implementation detects overload and keeps incomplete/unowned fragments out
of measurement readiness, but **cannot sustain this observed real-game packet
mix/rate on this host**. Automatic acquisition/session establishment alone is
not a successful foundation acceptance test. The synthetic resource/latency
results remain valid for their fixture and are not a substitute for this failure.

Repeated queue gaps and abandoned fragments coincide with escalating ledger
work and disk growth. Per-datagram FULL durability, repeated gap processing and
staging queries are investigation candidates, not yet profiled root-cause
conclusions. No thresholds, ownership checks, complete-run guards or coverage
rules were relaxed, and the game frequency was not silently reduced to obtain a
pass. Increasing queue capacity alone would only postpone sustained overload.

The user was asked to pause/return to the garage after the failure was confirmed.
The verification backend was stopped after preserving the baseline; its process
identity was checked before stopping it. The bounded observer also finished.
This stop is not scored as a graceful lifecycle or hard-kill recovery experiment.

Before asking for another long drive:

1. Reproduce/profile the actual admitted journal and rich packet mix without the
   game, accounting for explicit gap admission/publication and staging growth.
2. Fix the throughput/overload amplification at the persistence/processing seam
   while preserving journal ordering, committed publication and replay parity.
3. Add a realistic packet-rate/lap-duration regression, not just one-second laps.
4. Repeat uninterrupted live laps at the user's intended settings. Only then
   proceed to active comparison, flashback, reconnect, end/persistence, real-game
   replay parity and race-length memory/disk/startup tests.

No real-game completed-lap comparison or milestone completion is claimed.
