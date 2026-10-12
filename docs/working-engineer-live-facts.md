# Live engineer facts and events

Implemented October 12, 2026 as the first backend increment on
`feat/working-engineer-v1`. This does not implement standalone speech,
conversation, coaching tasks or the post-pit benchmark itself.

## API and ownership

`GET /api/v2/engineer/live` requires the configured control token in
`Authorization: Bearer <token>`, even when other read routes are unauthenticated.
It returns `{"data": {...}}` with a versioned current snapshot and bounded event
history. Disabled acquisition returns an explicit unavailable result.

The existing acquisition pipeline is the sole producer. Facts cover verified
field drivers independently of detailed-trace selection; no second UDP receiver,
model call or API-time database scan is introduced. The single persistence owner
publishes a cached snapshot after evidence publication. API readers receive
isolated copies and recheck freshness at read time.

## Snapshot contract

- `schema_version`, `status` and `reason` identify availability.
- `generation` and `session_id` identify the logical evidence session.
  `scope` contains the wire session UID, packet format and association epoch.
- `frame_ordinal`, `frame_identifier` and `session_time_s` identify the
  assembled lap-data frame. `captured_at_ns` is receiver wall-clock time;
  `received_monotonic_ns` is the original receiver's monotonic performance-clock
  timestamp, not publication time. `age_s` is computed against the same clock.
- Each driver has a car slot plus verified tenure ordinal and participant
  identity fingerprint. Names/natural-language driver resolution are deferred.
- `lap` contains decoded source fields with their original units: millisecond
  timing/gaps, metre distances, lap/sector numbers, position, pit status, validity
  flags and penalties. Raw source zero values are not automatically usable
  answers: zero completed-lap/sector timing means no measured time. Position
  zero is unknown. Consumers must interpret source status and validity flags.
- `tyre` has explicit availability/reason and, when observed, actual/visual
  compound IDs and age. It comes from an unambiguous status packet in the same
  assembled frame; it is never silently carried forward from an old frame.
  Missing/zero compounds do not generate a fabricated tyre-change event.

The runtime's existing telemetry-staleness setting is used for data freshness,
not as a response-latency acceptance target. Non-receiving runtime states, old
lap observations or a future receiver timestamp return unavailable results with
empty driver/event lists. Acquisition gaps, malformed/conflicting lap data,
session/format/lifecycle boundaries and shutdown invalidate old current facts.

## Event contract

Events are observations, not completed coaching tasks. Types are
`lap_completed`, `sector_completed`, `pit_entered`, `pit_exited`,
`tyre_compound_changed` and `driver_discontinuity`. They carry driver binding,
scope, frame and source timestamps. First observations, identity/player changes
and new scopes establish baselines without inventing earlier events.

- A lap event requires an observed adjacent positive lap-number transition
  between active-driver observations. Missing last-lap time is `null`. Its
  qualification is `observed_lap_transition`, not full driving-trace coverage.
  The prior observed invalidity flag is not a certified final lap-validity result.
- Sector events cover observed adjacent transitions into sectors two/three,
  reporting the completed sector as one/two. Missing times are `null`.
- Pit entry/exit is a pit-state transition, not proof of a serviced pit stop.
  The player's pit-entry event includes the previous coherent observed field
  positions/identities and their receipt time. Unknown positions are `null`;
  later benchmarking must still validate position uniqueness and eligibility.
- Compound changes require two observed nonzero compounds. Missing status
  frames break that comparison; no unobserved change is inferred.
- Skipped/rewound lap numbers or rewound session time produce discontinuity,
  not manufactured completions or pit/tyre transitions across that boundary.

At most 128 events are retained. `event_cursor` is the latest event ID;
`event_floor` is the cursor immediately before retained history;
`events_dropped` counts capacity evictions. Invalidation clears retained events.
IDs are process-local, not durable playback acknowledgements. Consumers must
bind cursors to logical session/scope, detect cursor resets/history loss and
start at the current cursor on initial connection rather than announcing history.
They must check individual event age and relevance before queueing speech;
fresh current facts do not make every retained event fresh.

## Verification and remaining work

`uv run --extra app --extra dev pytest tests/test_engineer_facts.py -q`:
**35 passed**, including provenance, binding changes, stale data, event capacity,
pipeline/detail-profile integration, authenticated API behaviour, logical session
export and actual loopback UDP publication followed by silence invalidation.

Final full Python regression run: **1,764 passed, 16 skipped** in 284.73 seconds.
The existing Starlette/httpx deprecation warnings remain. Python compilation,
local documentation-link checks and diff-whitespace checks also pass. Browser
checks are not run for this backend-only increment.

This is not real-game or MOZA/audio acceptance. No production deployment or
runtime/model substitution is performed. Next work connects these facts/events
to the standalone radio path, then driver resolution, conversation and tasks.
