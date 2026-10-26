# Feasibility rules

Every feature must prove that it fits on the deployment host before it is
built in. "Fits" means the telemetry pipeline still archives every datagram,
and the feature gets the data it needs in time, while running alongside every
feature already accepted. The proof is a stress test against recorded real
sessions. Opinions and estimates are not proof.

These rules apply to every contributor and coding agent. A proposal that cannot
be tested this way is out of scope until it can be.

## 1. The ingest path is fixed

- The receiver and writer threads only receive, timestamp, archive and index
  headers. Nothing else is added to them.
- All other computation (packet decoding, derived values, features) runs as a
  `Consumer` (`crates/f1-pipeline/src/consumer.rs`). Each consumer gets its own
  thread and bounded queue and is fed only after data is written. A consumer
  can fall behind; it cannot slow down or lose archive data.

## 2. Declare data needs before building

Each feature proposal states, in its pull request or design note:

1. **Packets and fields:** which packet types and fields it reads. Example: car
   telemetry speed and throttle for all 22 cars.
2. **Rate:** how often it needs them. Example: every packet, or once per lap.
3. **Latency budget:** how soon after arrival its result must exist. Example:
   p99 within 100 ms.
4. **Estimated cost:** CPU time per packet it consumes.

If the game does not send the data, the feature is out of scope. Data is not
invented or approximated without saying so.

## 3. Model it before building it

Run the stress test with a synthetic consumer set to the estimated per-packet
cost, with all accepted consumers enabled:

```text
f1-ingest ... --test-control --synthetic-consumer-us <estimated cost>
f1-rec stress <worst-case recording> --target <host:port> --speeds 1,2,4,8
```

If the model fails, the idea is reshaped (cheaper, lower rate, fewer cars) or
rejected. It is not built in the hope that it will turn out cheaper.

## 4. Acceptance gate

A feature is accepted only when a stress run meets all of the following.

**Where and with what:**

- On the deployment host, with the production `f1-ingest` configuration.
- Against the full worst-case corpus recordings, not a shortened window.

**Pass criteria at 1x:**

- **Archive lossless:** sent digest equals archived digest; zero app drops,
  zero kernel drops and zero write errors.
- **Consumers kept up:** zero missed batches for every consumer.
- **Feature latency:** each consumer's p99 latency is within its declared budget.
- **Pipeline latency:** receive-to-written p99 is at most 50 ms, and
  receive-to-durable p99 is at most 2 s (the `f1-rec stress` defaults).

**Headroom:** the same criteria must also pass at **2x**. The highest passing
speed is reported in every acceptance; when it drops toward 2x, the next
feature has to buy its headroom back.

**Cumulative:** the test runs with every previously accepted consumer enabled.
A feature that only passes alone is not accepted.

## 5. Never move the goalposts

- Do not raise latency budgets, enlarge buffers or queues, skip corpus
  recordings or shorten runs to make a feature pass.
- A change to pipeline configuration is its own change. It is validated against
  the whole corpus and existing features before anything depends on it.
- "Unknown" is never "zero". Windows does not report per-socket kernel drops,
  so acceptance runs on a host that does (Linux). The digest comparison still
  catches any loss.

## 6. Evidence

- Keep the `stress-results/*.json` report and the ingest's `verdict.json` for
  every acceptance run. Cite them in the feature's pull request, with the
  commit tested.
- Runs over loopback or on a development PC are development checks only. They
  are useful for catching regressions, but they do not accept a feature.

## 7. Corpus

- Corpus recordings are immutable. Add new worst cases with `f1-rec record`
  (full-grid race, wet race, safety car, online lobby), and catalog them in
  [`corpus/README.md`](../corpus/README.md) with their digest.
- When a new recording is more demanding than the current worst case, every
  accepted feature is re-tested against it.
