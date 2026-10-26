# Live standalone radio settings

Dashboard radio settings are authoritative after the first save. Before that
(radio revision zero), CLI defaults remain in effect. Assistance preferences
apply independently, including at radio revision zero. No tracking, capture,
interpreter, or playback task is restarted by a settings update.

## HTTP contracts

`GET /api/v2/engineer/radio/settings` returns this envelope, with `ETag: "0"`
for the initial document:

```json
{"api_version":"v2","status":"ok","reason":null,"data":{"schema_version":1,"revision":0,"proactive_mode":"rules","answer_mode":"intent","intent_mode":"luna","tts_engine":"edge","voice":null,"rate":4,"volume":75,"microphone":null,"cue_output":null,"cue_volume":0.08,"capture_seconds":12}}
```

`PUT` at the same path takes the complete `data` object **without**
`schema_version` and `revision`. It requires the control bearer token. Optional
`If-Match: "N"` provides optimistic concurrency; a conflict returns HTTP 409,
the current ETag, and `{"status":"unavailable","reason":"radio_revision_conflict","data":{"revision":N}}`.
Identical saves preserve the revision. Invalid values return 422, oversized
bodies 413, unauthorized writes 403, and unreadable storage 503. An explicit
save without If-Match can repair corrupt storage, matching Assistance semantics.
Storage is `.f1-radio-settings.json` beside the configured database, atomically
replaced; revision-zero defaults are not written automatically.

Allowed values: proactive `off|rules|model`, answer `intent|grounded`, intent
`luna|parser`, TTS `edge|windows`, voice null or 1-256 characters, integer rate
-10 to 10, integer volume 0-100, nullable nonnegative integer device indices,
cue volume greater than zero and at most 0.3, capture seconds 1-12. Grounded
answers require Luna intent mode. The UI switches intent mode to Luna when
grounded is selected.

The existing authenticated `GET /api/v2/engineer/live` response's `data` gains:

```json
{"settings":{"revision":"3:7","radio_revision":7,"assistance":{"topics":{"gap_information":true},"verbosity":"balanced","interruption":"queue","depth":"standard"},"radio":{"proactive_mode":"rules"},"radio_connected":true}}
```

The example abbreviates topics and radio: all eight Assistance topics and all
radio fields are sent. The combined revision is `assistance_revision:radio_revision`.
Files are stat-checked but parsed only when mtime/size changes. Unreadable
documents yield `settings: null`; the running radio keeps its last good state.

`PUT /api/v2/engineer/radio/presence` requires the control bearer token:

```json
{"instance_id":"random-per-process","version":"0.1.0","applied_revision":"3:7","tts_engine":"edge","voices":["en-AU-WilliamNeural"],"devices":[{"index":0,"name":"Microphone","input":true,"output":false}],"wheel_status":"ready","speaking":false,"listening":false}
```

`GET` returns `{"data":{"connected":true,"age_s":0.2,...presenceFields}}`.
Before the first heartbeat it returns `{"data":{"connected":false,"age_s":null}}`.
Presence expires at six seconds. Up to sixteen recent process leases prevent
one stale radio masking another fresh one; the most recent lease is displayed.
Voices/devices are bounded to 64 entries, names to 256 characters, metadata to
128 characters, and requests to 32 KiB. Device discovery runs once off-loop,
and again on demand. Authenticated `PUT /api/v2/engineer/radio/presence/refresh`
returns `{"data":{"devices_revision":N}}`; this counter travels in live settings
and signals discovery on the heartbeat thread. The dashboard exposes a Refresh
radio devices button; results arrive in a subsequent presence heartbeat.
Edge offers two curated Australian voices; Windows reports installed voices.
Switching engine and saving updates its offered voices on the next heartbeat.
GET endpoints are dashboard-readable; the web only proxies GET presence, not
radio heartbeat writes. Proxies preserve If-Match, ETag and conflict status.

## Application boundaries

- Voice configuration is staged and playback rebuilt only before an idle
  playback starts its next utterance. Settings never call playback cancellation.
- Capture settings and the interpreter closure are staged until the next
  capture request; the active worker retains its device, duration and interpreter.
- Proactive mode changes wait for speech/capture idle, then call the existing
  controller setter before the next proactive poll (the setter cancels automatic
  gap playback, so invoking it during active speech would be unsafe).
- Grounded answer length budgets are captured for the next request, not changed
  underneath in-flight requests. Concise/quick uses 420 characters, balanced/
  standard 900, detailed/in-depth 1000; the smaller budget wins. Truncation retains
  the final qualifying sentence when short enough. Backend validation is unchanged.
- `allow` retains existing behavior; `queue` accepts proactive messages during
  capture and waits for playback availability; `never` drops collisions with
  active playback/capture at submission. Existing expiry/dispatch guards remain.
- Every accepted combined revision emits `radio_settings_applied` with changed
  fields, monotonic application time, delivery elapsed and application elapsed.
  Applied means staged for the next natural boundary, not an utterance cancelled
  or an active capture switched. Invalid snapshots emit `radio_settings_invalid`.
- The separate two-second heartbeat does no work on the main loop. Offline
  radios accept the latest snapshot after reconnecting, without an extra GET.
- Drive keeps its read-aloud storage preference and text history. Presence
  temporarily suppresses browser speech and clears pending browser calls; new
  calls speak again automatically after disconnect. Presence polls every two
  seconds with bounded timeouts and six-second failure tolerance.

## Proactive owner mapping

| Owner | Assistance topic |
| --- | --- |
| automatic_gaps, gap_changes, opponent_regions | gap_information |
| lap_time, pace_sectors, lap_reports | driving_technique |
| pit_benchmark | race_strategy |
| tyres | tyre_management |
| track_limits | rule_reminders |
| weather | track_conditions |
| setup | setup_suggestions |
| Other/unowned proactive messages | general_information |

Driver-requested answers bypass topic filtering. Already accepted messages are
not cancelled when a topic changes; the next proactive submission is filtered.

## Reproducible smoke

Run `uv run --extra app --extra radio python scripts/smoke_radio_settings.py`.
It starts an isolated real API and `f1-engineer radio --dry-run --log-file`, saves
three configurations using If-Match, and measures each HTTP PUT return against
the corresponding JSONL event's monotonic timestamp. Temporary database, radio
logs and process logs are retained at the printed artifact path; both processes
are stopped. This tests transport and application, not physical audio hardware.

An October 16, 2026 Windows smoke run measured PUT-return-to-applied latencies
of 712.04 ms, 43.28 ms, and 403.10 ms while concurrent validation saturated the
six-core host. These are observed timings, not a latency guarantee. Delivery
uses the existing 250 ms live poll and 50 ms main-loop tick; server processing,
network time, and host scheduling add to that nominal polling budget. The smoke
launches both CLI processes with the invoking Python environment to avoid
concurrent dependency synchronization, and allows five minutes for cold startup.

## Validation record

- `uv run --extra app --extra dev --extra radio python -m pytest -q tests/test_radio_live_settings.py`: 20 passed.
- `.venv/Scripts/python.exe -m pytest -q tests/test_standalone_radio.py tests/test_radio_questions.py tests/test_radio_intents.py tests/test_engineer_preferences.py --tb=short`: 283 passed.
- `uv run --extra app --extra dev --extra radio python -m pytest -q --tb=short`: 4,108 passed, 17 skipped, one failure. The existing `test_runtime_uses_sqlite_owner_and_preserves_batching[demand_v1]` observed a batch of five rather than eight under host load; both parameterizations passed on immediate isolated rerun. No processing-runtime code was changed.
- In `web/`, `npx next typegen` and `npx tsc --noEmit -p .` succeeded.
- The new `radio-settings.test.mjs` and `npm run test:settings`, `npm run test:settings-tabs`, `npm run test:drive-radio`, and `npm run test:ui-proxy-routes` passed: 113 tests combined.
- `git diff --cached --check` passed. Physical microphone, wheel, Windows voice output, and Edge audio playback were not exercised by the dry-run smoke.
