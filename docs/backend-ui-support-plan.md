# Backend support for the reference UI

October 12, 2026. Follows the reference UI rebuild and polish on `main`
(commit 0273069). The UI shows "not available" or reference content wherever the
backend cannot supply a section. This plan adds the backend data those sections
need. Web wiring follows once each endpoint lands.

## Audit: UI sections without backend data

| UI section | Current backend | Gap |
| --- | --- | --- |
| Lap Analysis sector comparison, Overview lap table | v2 attempts carry lap time, validity and pit flags only | No sector times in v2. `sessions/session_history.py` already parses Session History sector times for v1 imports. |
| Overview stints, tyre legend | None for v2 | No per-lap tyre compound or age. Car Status parsing exists in `udp/car_status.py`. |
| Library dates, Overview duration, notable events, summary | `sessions` rows hold id, lifecycle, acquisition and context only | No wall-clock start/end, no session timeline (penalties, safety car, fastest lap, retirements, pit stops). |
| Lap Analysis corner regions, track model line | Track-model catalog holds one Melbourne draft | No Jeddah or Sakhir model; region boundaries are not exposed to clients in a UI-friendly shape. |
| Drive radio history, event details, current activity | Engineer answers are returned to the caller only | No durable engineer activity log; no deterministic in-session calls. |
| Settings: Live and Review Assistance | Browser-local only | No backend preferences, so the engineer ignores them. |

Already supported by the backend but not yet wired in the web app (web follow-up,
not part of this plan): v2 engineer questions (`POST /sessions/{id}/ask`), opt-in
historical materialization for unprocessed laps (`materialize`, `analyze` with
`allow_materialization`), import job progress.

## Constraints (all workstreams)

- Read `AGENTS.md`, `docs/backend-contract-v1.md`, `docs/backend-services.md` and
  `docs/backend-agent-slices.md` first. Their invariants still hold:
  - Never rewrite published evidence, chunks, attempts, dispositions or reports.
    New data is additive or derived at read time.
  - Expensive or mutating operations require the control token and respect the
    existing admission limits. Reads stay bounded (rows, bytes, time).
  - No model calls triggered by packets. In-session calls are deterministic.
  - No receiver tuning, default profile changes or production deployment.
- Each workstream owns new modules. Routes go in a new
  `f1_engineer/api/<name>_routes.py` exposing `register_<name>_routes(app, ...)`.
  The only permitted edit to `f1_engineer/api/app.py` is one import and one
  registration call inside `create_app` (re-read the file immediately before editing;
  other agents add their own lines concurrently).
- Tests: new `tests/test_<name>.py` files, run with
  `uv run --frozen --extra dev --extra app pytest -q tests/test_<name>.py`.
  Use temporary copies of local databases in `data/` when real data helps; never
  open the originals for writing.
- No edits under `web/`. No commits, pushes or branch changes.

## Workstreams

### B1: Lap timing and tyre enrichment

New read-side derivation from the committed journal for a v2 session's player
attempts: sector 1-3 times (Session History or Lap Data), tyre visual/actual
compound and tyre age at lap end (Car Status). Exposed as
`GET /api/v2/session-evidence/sessions/{session_id}/laps` returning one row per
player attempt revision with `sectors_ms`, `tyre` and derived `stints`. Cached per
committed sequence; bounded reads.

### B2: Session timeline and summary

`GET /api/v2/session-evidence/sessions/{session_id}/summary`: wall-clock start/end
when derivable (journal or capture metadata; otherwise null with a reason),
session-time duration, weather changes, and notable events from Event packets
(penalties, safety car/VSC, fastest lap, retirements, flashbacks, pit entries for
the player). Also additive `started_at_utc`/`ended_at_utc` on the sessions list
only if derivable cheaply.

### B3: Track models for Jeddah and Sakhir, region API

Author draft track models for Jeddah and Sakhir from local recorded data using the
existing draft authoring path, including corner regions where the data supports
them; state what is reviewed vs draft. Expose a client-friendly
`GET /api/v1/track-models/{model_id}/regions` (or extend the existing catalog
detail) with region boundaries by lap distance, and a lookup by v2 session track
id.

### B4: Engineer activity log and deterministic calls

Durable, append-only engineer activity store (separate SQLite file next to the
main database) with question, response, proactive and issue entries, timestamps,
session id and lap. Record answers from the v1 and v2 ask endpoints. Add
deterministic in-session calls from the live runtime state (lap completed with
delta to session best, invalidated lap, pit entry/exit, telemetry gap or
recovery), filterable by topic. Expose `GET /api/v2/engineer/events?after=&limit=`.

### B5: Engineer preferences

`GET /api/v1/engineer/preferences` and `PUT` (control token) persisted atomically
as JSON next to the database, schema matching the Settings screen (assistance
topics, verbosity, interruption behaviour, analysis depth, include
interpretations, focus areas). Apply verbosity, depth and interpretations to the
ask paths' response shaping; expose topics for B4's call filter.

## Integration (coordinator)

Wire B5 topics into B4's filter, run the full Python suite, then plan the web
follow-up: proxy routes and screen wiring for B1-B5 plus the already-supported v2
ask and materialization.

## Continuation status (October 12, 2026)

Recovered from T3 thread `b66856c9-9f58-43e9-b8cb-f61c363725ff` after its
session limit. Existing work is preserved; no branch, commit, push, deployment,
receiver settings, or web source changes are made by this continuation.

- B1: lap enrichment service and route are registered in the application.
- B2: summary service and route are registered, with focused tests. Demand-profile
  scans use published logical-session boundaries rather than guessing them from
  selected packet types. Full-profile scans honor recorded restart/retired routing.
  Results cover only the committed journal prefix, and explicitly report truncation.
  UTC timestamps describe first/last observed capture times, not an independently
  verified real-world session start. Sessions-list date augmentation is not added.
- B3: region endpoint and compatible session lookup are implemented. Revision is
  required; session lookup matches packet format, track ID and track length. Draft
  models remain diagnostic-only. Jeddah/Sakhir models are **not authored**: the
  inspected local legacy archives contain no completed Jeddah/Sakhir authoring
  source; v2 snapshots include Sakhir but are not legacy draft-authoring inputs.
  A suitable recording import or reviewed model bundle is still required. No
  corner identity, geometry or distance boundaries are invented.
- B4/B5: persisted preferences are passed into both ask services; topic switches
  filter deterministic calls dynamically. Telemetry health notices remain enabled.
  Activity is stored separately from evidence. Interruption settings are persisted;
  actual audio interruption/queuing is a separate web/audio integration concern.

See `docs/backend-ui-web-follow-up.md` for the next integration scope.

### Validation

- `uv run --frozen --extra dev --extra app pytest -q`: 1,708 passed, 16 skipped,
  three deprecation warnings, 342.87 seconds. This run precedes the final removal
  of the unused summary probe helper and final lap-boundary refinement.
- Final focused run of `test_lap_enrichment`, `test_session_summary`,
  `test_track_regions`, `test_backend_ui_integration`, `test_engineer_activity`
  and `test_engineer_preferences`: 94 passed, one existing FastAPI/httpx
  deprecation warning, 33.46 seconds.
- `git diff --check`: passes. Tests use disposable stores; no production listener
  is started. Web build/browser checks and real-game/Linux acceptance are not run.
