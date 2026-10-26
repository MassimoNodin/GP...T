# Proactive radio: joint validation

Implementation includes lap-based and short-window gap detection, valid-lap
pace/control policy, bounded notice retention, optional model selection, and
offline scenarios. Commands below are the reproducible validation checklist;
they are not evidence that a particular revision passed hardware acceptance.

## Periodic evaluation and delivery

The existing radio loop checks for a due evaluation every 15 seconds. It reads
bounded, in-memory gap summaries; it does not open another telemetry stream or
start a model request on each tick. Evaluation diagnostics record duration_ms
and candidate counts so overhead can be measured during a live session.

Short-window gaps require roughly 30 seconds of continuous, fresh evidence for
the same adjacent driver, with at least 20 sampled observations. First, middle,
and last samples must agree on direction: each half changes by at least 0.2
seconds and the total by at least 0.6 seconds. Identity, position, pit/racing
status, physical order, lapping, session, and freshness safeguards still apply.
These are initial conservative thresholds, not a calibrated race strategy.
Spoken text states the measured time window rather than calling it two laps.
Pace notices still require three fully observed, valid, comparable laps.

At most three developments are retained: ahead gap, behind gap, and own pace.
Busy playback and driver workload defer delivery for up to 20 seconds from
detection. Newer developments replace older ones on the same topic.
Dispatch expiry is separate from the bounded playback timeout: a notice that
starts just before its deadline is allowed to finish rather than being cut off.
Before dispatch, the original evidence is revalidated against fresh telemetry; the
three-second telemetry limit and 0.5-second gap-drift limit are not relaxed.
Expiry, context changes, quiet/listening mode, and disabled proactive mode clear
ineligible notices. Detection does not consume the announcement cooldown;
playback start does. Quiet/listening still evaluates without retaining speech,
so resume does not replay developments detected while muted.

Driver answers can interrupt automatic speech. Automatic developments never
interrupt an active lap time or answer; they retry through the existing radio
loop after playback finishes, not after another 15-second evaluation. They
replace unspoken default lap announcements, but requested task reports retain
precedence. A pace notice detected at a lap boundary can combine its trend and
that lap time into one call. Existing global/topic cooldowns and chatter budgets
remain in force.

Model mode submits only new eligible candidates, using the existing single-flight
selector, timeout, foreground cancellation, and 15-second admission limit.
Silence is terminal for that selection; it does not trigger another model call
on every radio-loop iteration. Rolling candidates carry explicit sample_times_ms
so the selector cannot confuse time windows with completed-lap comparisons.

Inspect radio_proactive_evaluation, radio_proactive_history_reset,
radio_proactive_detected, radio_proactive_deferred, radio_proactive_discarded,
radio_proactive_candidate, and radio_proactive_started for the full lifecycle.

## 1. Automated checks when ready

From the repository root, start with the new scenarios and protocol boundaries:

```powershell
uv run --extra app --extra dev pytest tests/test_radio_proactive.py tests/test_radio_proactive_clock.py tests/test_radio_proactive_selection.py tests/test_radio_scenarios.py -q
```

Then run the adjacent radio/provider regressions and finally the full suite:

```powershell
uv run --extra app --extra dev pytest tests -k "radio or standalone_radio or codex_runtime" -q
uv run --extra app --extra dev pytest -q
```

These tests use synthetic evidence, mock speech/HTTP/model boundaries and fake
Codex servers. They must not open a microphone, wheel or real model session.
Stop on a failure and investigate before progressing to hardware acceptance.

## 2. Offline end-to-end radio scenarios

```powershell
uv run --extra app f1-engineer radio-scenario --scenario all --log-file data/proactive-scenarios.jsonl
```

This runs generated race snapshots through the real RadioController and
RadioScheduler with a simulated monotonic clock. It never contacts the backend,
opens wheel/microphone devices, produces audio or calls a model. It does not
validate UDP parsing, game telemetry or hardware latency.

Each radio_scenario_complete record should report success true. Closing, pace,
mid-lap and busy-radio expect one automatic start each. Quiet and session-change
expect zero. The command exits 2 if an expected automatic-start count differs.
Use --scenario mid-lap for periodic detection without a completed lap, or
--scenario busy-radio for an answer blocking a notice until playback finishes.
Quiet continues collecting evidence but must not replay the suppressed trend
after resume. Session-change invalidates a queued call before dispatch.

## 3. Prepare the actual backend and radio

Pushing the branch does not deploy it or restart Ubuntu. Deploy the same revision
to the backend using the existing [split deployment workflow](../README.md#run-the-split-deployment),
sync its dependencies and restart the service deliberately. Do not overwrite
unrelated server changes or switch a live capture underneath a running race.
Rules mode needs the existing live endpoint; model mode also needs the new
proactive-selection endpoint and the existing isolated Codex subscription login.
An older or unsupported backend cannot provide model selection.

The existing logged launcher is browser-independent:

```powershell
./scripts/start-standalone-radio.ps1 -CheckOnly
./scripts/start-standalone-radio.ps1 -ProactiveMode rules -DryRun
./scripts/start-standalone-radio.ps1 -ProactiveMode rules
```

CheckOnly does not open wheel or microphone devices. DryRun disables audio and
microphone/model work but still uses the saved wheel monitor in the ordinary
radio loop. Use radio-scenario instead if no hardware access is wanted.
The launcher reuses/opens the authenticated SSH tunnel, reads the existing
credential and writes private evidence beneath data/radio-tests. It does not
deploy or start the Ubuntu service. Keep logs and credentials out of commits.

For an already running local backend instead of the split-host launcher:

```powershell
uv run --extra app f1-engineer radio --proactive-mode rules --dry-run --log-file data/proactive-dry-run.jsonl
uv run --extra app --extra radio f1-engineer radio --proactive-mode rules --log-file data/proactive-live.jsonl
```

Use the existing --api-url and --token-file options for a nondefault tunnel.
No browser, new voice provider, voice cloning or model API key is required.

## 4. Rules-mode driving acceptance

Use an offline AI race, not qualifying/time trial, and initially install no
explicit tracking tasks: due requested reports deliberately take precedence.
Keep the radio connected throughout the observation window.

- Gap: observe at least three comparable player lap boundaries with the same
  adjacent driver, known active racing status and no pit/lapping discontinuity.
  A consistent gain/loss of at least 0.1 seconds on each interval and 0.3 seconds
  overall qualifies. Short-window detection can also qualify after roughly 30
  seconds without a lap boundary, using the stricter thresholds above. Expect
  a new development, not repeated commentary on each unchanged sample.
- Pace: allow the startup partial lap to complete, then at least three complete
  valid laps on one known compound. Both lap-time changes must be at least
  0.2 seconds in the same direction and total at least 0.6 seconds. Expect a
  measured quicker/slower notice with a conditions caveat, not a cause diagnosis.
- Speech: expect at most one automatic notice at a boundary; it replaces the
  ordinary lap-time call. Requested answers can interrupt. Automatic starts
  obey a 30-second global cooldown, 90 seconds per topic and two starts per
  rolling 120 seconds. Existing requested reports and default lap calls are
  not charged to this additional-coaching budget.
- Busy radio: ask for a comparison while a development qualifies. The answer
  must finish without interruption; the development should follow if still
  valid within its 20-second retention period. Repeat with a routine lap time
  already playing. A pending, unspoken default lap should be replaced instead.
- Controls: try status, proactive off, proactive rules, quiet, resume, cancel
  and repeat. Off disables only automatic gap/pace coaching; quiet suppresses
  routine speech including default lap calls. Explicit answers remain allowed.
  Resume must not replay an old suppressed development.
- Invalidation: pit, replace the adjacent driver, restart the session or
  disconnect telemetry. A queued obsolete call must not start; fresh comparable
  evidence must be collected again. Never deliberately compromise wheel access
  to simulate a telemetry outage.

Fresh aligned control telemetry defers automatic dispatch above 20 percent
brake or 15 percent steering. Missing control telemetry does not identify a safe
straight; dispatch can still occur on fresh racing evidence. Circuit-aware
delivery timing is not implemented.
If the saved wheel reader affects game controls, stop the radio immediately;
do not start discovery or automatic restarts while diagnosing it.

## 5. Optional model-mode acceptance

Only after rules-mode acceptance, switch with proactive model or launch:

```powershell
./scripts/start-standalone-radio.ps1 -ProactiveMode model
```

Check status for proactive_mode model. A new nonurgent development can create a
radio_strategy_started event. There is one background request at a time, at most
once per 15 seconds, with a six-second inference deadline and no retry backlog.
The model may choose silence, so a selected_id null is a valid result. Near
closing developments do not wait for the model. Spoken wording remains the
deterministic measured template; the selector cannot manufacture instructions.

While a selection is in flight, ask a question or start listening. Background
work should cancel, not delay the request until inference completes. Subprocess
cleanup may take up to 2.5 seconds before the shared slot is available; a busy
response is safer than concurrent inference if cleanup has not finished.
Check radio_strategy_selected/suppressed/unavailable, radio_proactive_candidate,
radio_proactive_started and radio_dropped alongside playback queue timings.
Returned candidates still need fresh current observations and an unchanged
subject/development. Their original ten-second lifetime cannot be extended.

Test unavailable login/provider or an old backend only in a controlled session:
ordinary background selections should remain silent, without freezing telemetry
or replaying after recovery. There is no implicit rules/provider fallback for
nonurgent selections; proactive rules explicitly restores deterministic mode.

## Acceptance limits

Do not infer tyre life, fuel sufficiency, pit strategy, safety-car awareness,
overtake prediction or a validated safe circuit location from these notices.
Gap movement and unmatched-condition lap-time trends are the supported evidence.
Application dispatch/completion logs do not prove audible onset or end-to-end
acoustic latency. Record the actual gameplay/audio findings before marking
this conversion accepted for regular driving.
