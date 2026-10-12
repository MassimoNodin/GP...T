# Backend support for current and planned UI

October 12, 2026. Audited the current UI and all five areas in
`ui-new-areas-design-plan.md`. Existing services already cover laps/sectors/tyres,
stints, conditions/events/dates, traces/comparisons, regions, explicit telemetry
materialization, v1/v2 questions, imports/progress/cancel/retry, captures/downloads,
live/runtime/speech status, preferences and storage. A route-dependency regression
test covers these areas; existing service tests exercise their behavior.

| New backend support | Related UI | Contract |
| --- | --- | --- |
| Recorded player setup, first/latest observations and change count | Overview: Setup & Conditions | Existing v2 session `/summary` adds `player_car_setup` with `status`, `reason`, `first_observed`/`last_observed` (sequence, time, car index, setup) and `observed_change_count`. |
| Recorded steering/braking/gearbox assist IDs and rule facts | Overview: Assists, Session facts | `/summary` adds `assists` with first/latest observations; `session` also exposes format, track length and rule set. Damage-setting telemetry remains unavailable. |
| Backward activity pagination | Drive: Radio History; Lap Analysis: question history | `GET /api/v2/engineer/events?before=<oldest-id>&limit=200` returns older events oldest-first and `next_before`. Stop on an empty page. Do not combine `before` and `after`; keep the latest-page `next_after` for live polling. Retention remains 5,000 entries. |
| Exact imported-session/lap association and isolated histories | Lap Analysis: saved questions; Drive: session filters | v1 asks resolve the selected attempt in the archive. Session filters include global system/live notices, not unbound questions or other sessions' answers. |
| Reliable driver labels after telemetry/setup packets | Overview: Events | Session summaries retain the Participants packet instead of replacing it with unrelated packets. |

All summary additions derive from the existing bounded committed-journal scan,
respect logical-session boundaries, reuse existing decoders and report partial,
invalid or missing evidence explicitly. First observed does **not** mean session
start. No evidence migration, model invocation or receiver change is introduced.

Frontend follow-up: consume the additive summary fields and admit `before` in
the web events proxy/parser (currently only forward polling is wired). Existing
UI work is untouched. Browser devices/audio, derived plots/exports and local
feedback remain frontend responsibilities. Session editing/deletion, retention
enforcement, reviewed corner authoring and damage/build guesses remain excluded
as specified by the design plan. v1 questions retain their existing 1,024-byte
limit; the plan's 2,000-character composer limit must be endpoint-aware.
