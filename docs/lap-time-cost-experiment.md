# Lap-time tracking cost experiment

On 2026-10-27, a full Jeddah race recording was used to compare a minimal
lap-time tracking loop for the player and all 22 car slots. This is a
feasibility experiment, not an accepted telemetry feature.

## Data and prototype

The loop reads F1 25 Lap Data v1 packets: the header's player car index and,
for each selected car, the current lap number and current and last lap times
in milliseconds. It processes every lap-data packet, updates in-memory current
and last values, and records a completed time when the lap number increases by
one. It does not handle flashbacks, session resets, identity changes, missing
packets, or historical corrections. No feature latency budget was declared.

A release-mode offline benchmark on the Ubuntu deployment host loaded all
74,684 lap-data packets from the recording (444,689 datagrams total), then
ran nine alternating rounds. Its measured wall time excludes reading the file,
packet dispatch, queueing, and the ingest pipeline. The medians are:

| Scope | Time per lap-data packet | Time averaged over all datagrams |
| --- | ---: | ---: |
| Player | 62.6 ns | 10.5 ns |
| All 22 car slots | 83.4 ns | 14.0 ns |

These measurements apply to this small prototype and host; they are not a
measurement of a finished consumer or a CPU usage percentage.

## Synthetic-load replay

The Ubuntu ingest was tested at commit `096bf67`, using an isolated instance
with the same default pipeline configuration and a 1 microsecond synthetic
consumer for every recognized packet. The minimum whole-microsecond setting
models both scopes with the same load, so the replay does not compare the two
implementations directly. The production ingest remained running separately.

The Windows sender replayed all of `corpus/jeddah-race.f1r` without a window
at 1x and 2x. Its expected payload digest was
`444689/f8a2be782616972b`. Both runs passed the stress tool's default
pipeline budgets, achieved their requested speeds, and produced:

| Speed | Digest match | App / kernel drops | Missed consumer batches | Written p99 | Durable p99 | Consumer p99 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1x | yes | 0 / 0 | 0 | 10.224 ms | 1006.633 ms | 10.486 ms |
| 2x | yes | 0 / 0 | 0 | 10.224 ms | 1006.633 ms | 10.486 ms |

Both runs also had zero write errors. The highest speed tested and passed was
2x. The consumer p99 is reported for context; the stress tool did not enforce
an independently declared feature latency budget.

The sender report is `stress-results/20261026T223947Z-jeddah-race.json`, with
copies of the individual verdicts in the same directory as
`20261026T223947Z-jeddah-race-1x-verdict.json` and
`20261026T223947Z-jeddah-race-2x-verdict.json`. The benchmark rounds are in
`stress-results/20261026T223947Z-lap-cost-benchmark.txt`. These files are local
and ignored by Git. The original verdicts and archives remain on the Ubuntu
host under `/mnt/nvme/f1-engineer/lap-cost-experiment/tests/`.

This run covers one corpus recording and a synthetic consumer; it does not
establish the correctness or acceptance of an actual lap-time tracking
consumer. The feature must declare its latency budget and pass the applicable
full-corpus checks under the feasibility rules.
