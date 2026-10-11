# UI replacement foundation contract v1

F0 freeze, October 11, 2026. Integrator-owned. Future interfaces below are
reservations, not implemented APIs. Existing backend-contract-v1.md bounds apply.

## Reference and stage gate

The supplied five-panel image is preserved byte-for-byte as
`docs/assets/ui-reference.png` (1586 x 992 pixels, 2,116,688 bytes). Provenance:
user attachment image_19ce9d72-4ff9-4ba3-8feb-b60633e510a7, received in this thread.
SHA-256: `3b8be6059d11b793205c1efeb3f44c05428fa50e90cdb899c7f838264062b2ce`.
F0 is complete and F1 may proceed against this asset and approved differences.
Do not recreate the old UI or interpret illustrative content as telemetry evidence.
No B/U/I/V slices, branches, commits, pushes, deployment, production startup or
replay transmission. Existing routes and navigation remain untouched.

## Verified capability inventory

Supported means source exists, not hardware acceptance. Adaptation means new UI
mapping is needed. New means the requested end-to-end capability is absent.
Paths below are relative to f1_engineer/.

| Requirement | Status | Source and limits |
| --- | --- | --- |
| v1 recorded/imported review | Supported; adaptation | api/app.py: /api/v1/sessions, /laps, /processing-runs; capture/run/session/attempt IDs differ |
| v2 automatic acquisition/retained evidence | Supported; adaptation | processing/runtime.py, processing/evidence.py; /api/v2/session-evidence/status, /sessions and attempt/trace routes; logical occurrence/revision identities |
| Comparison/reference/measurement | Supported; adaptation | v1 /compare/laps, /compare/regions; v2 session /compare, /reference, /analyze, /materialize; diagnostic-only, no causal/coaching eligibility |
| Recorded/revision-bound Ask | Supported; adaptation | v1 /engineer/ask, v2 session /ask; not arbitrary session-wide review or proactive radio |
| Transcription | Supported | analysis/local_speech.py; v1 /engineer/transcribe; canonical 16kHz WAV, 12 seconds/512KiB, request ID/hash, no persisted audio |
| Standalone Windows audio | New | Browser speech controllers do not supply device ownership; B1 required |
| Proactive radio/lifecycle/history | New | B2/B3 required; future-binding diagnostic Ask is not a radio service |
| Opponent diagnostics | Adaptation | v2 session /race-diagnostics; admitted observations, not opponent coaching |
| Tyre/strategy coaching, setup-impact causes, external tools | Unavailable | Measurements/model text alone do not establish these capabilities |
| Upload/import/progress/retry/cancel/duplicates | Supported; adaptation | api/recording_upload.py, api/import_controller.py; v1 /recording-sources/upload, /import-jobs and job subresources |
| Export | Supported narrowly; adaptation | api/recording_download.py; source /download; CLI exports do not establish general v2 export endpoints |
| Storage usage | Supported | v1 /storage/usage; no retention/deletion promise |
| Retention/confirmed deletion | New | No retained-session deletion/settings API; append-only evidence protected by triggers; detail-task DELETE cancels demand only |
| Diagnostic capture/replay | Supported; gated | api/recording_controller.py, api/replay_controller.py; v1 recording/group/replay routes; operation reservation, never started by review |
| Persistent applied settings | New/adaptation | CLI/config status and browser preferences do not establish apply/restart persistence |

v1 uses capture_id, processing run, session and attempt_key. v2 uses generation,
logical session/epoch, attempt/revision and verified driver-tenure binding. Repeated
numeric game UIDs/lap numbers cannot identify an occurrence. /legacy-sessions is
an archive bridge, not identity equivalence. Never coerce v2 evidence into v1 Ask.

Source reads: 20,000 rows/32MiB. Materialization: one active/no queue, 100,000
journal entries, 128MiB input, 256MiB temporary files, 30 seconds; ephemeral trace
does not replace immutable source revisions. Reference scan: 512 candidates max.
Measurement LRU: 32 entries/16MiB, two in-flight keys. v2 Ask: 2,000 characters,
8KiB body, 30-second ordinary deadline; explicit future-binding deadline 270
seconds with at most 240 seconds waiting. v1 Ask: 1,024 UTF-8 question bytes,
8KiB body, 40KiB response, 60 seconds. Adapters enforce source-specific bounds.
Control/expensive routes require the existing bearer token even without app-wide
auth; read authorization depends on global auth configuration. Never expose
tokens in markup/URLs/fixtures. New contracts do not override existing admission.

## Shared schema and exports

Integrator reserves web/src/lib/ui-contracts.ts and the named types below. No
worker shadow types. Wire version ui.v1; IDs opaque 1-512-character strings,
timestamps UTC ISO-8601, unknown values null rather than zero. Adapters own parsing.

- Capability: state supported/adaptation_needed/unavailable, reason nullable,
  evidence_path nullable. Only supported enables a production action.
- Capability keys: retained_review, lap_comparison, revision_questions,
  session_questions, region_questions, live_questions, proactive_radio,
  standalone_audio, radio_history, opponent_diagnostics, tyre_coaching,
  strategy_coaching, setup_impact, external_tools, import, recording_export,
  evidence_export, retention, deletion, runtime_settings, diagnostic_capture, replay.
- UiError: code, message, retryable, request_id nullable.
- ResourceState<T>: loading; empty(reason); available(data); partial(data,reasons);
  stale(data,freshness); disconnected(last_data nullable); failed(error,last_data
  nullable); unsupported(reason). Presentation never fetches/retries itself.
- Freshness: state fresh/stale/unknown, observed_at nullable, evaluated_at,
  stale_after_ms nullable, reason nullable. Runtime supplies actual observation
  and threshold, never browser arrival. Missing timestamp/threshold means unknown.
- RuntimeHealth: runtime_id, acquisition_state starting/listening/receiving/stale/
  stopped/failed/disabled/unknown; radio_state and audio_state ready/busy/degraded/
  unavailable/unknown; freshness, capabilities. Transport loss never stops runtime.
- SessionIdentity: v1(kind=v1, session_key, run_id, capture_id nullable)
  or v2(kind=v2, session_id, generation_id), all adapter-verified. Map v1
  SessionRecord.session_key/run_id directly; resolve capture_id from source
  linkage, never from session_uid. Map v2 sessions.id/generation directly.
- LapIdentity: session plus v1(kind=v1, attempt_key) or
  v2(kind=v2, attempt_id, revision_id, lifecycle_epoch, binding_id nullable).
  Epoch belongs to admitted attempt/binding evidence, not a guessed session field.
  Resolve revision/attempt IDs through metadata; never equate them by string shape.
- SourceProvenance: acquisition_origin live_udp/recorded_file/unknown,
  import_history(operation_id, imported_at)[], active_replay(operation_id,state)
  nullable. Import/replay never overwrite acquisition origin.
- Eligibility: state eligible/ineligible/unknown, reasons[]. EvidenceQuality:
  game_validity valid/invalid/unknown, completeness complete/partial/missing/unknown,
  analysis/comparison/coaching each Eligibility. Completeness implies none of them.
- SelectedSessionContext: identity, display_name, source, availability, freshness,
  quality. Render separately from runtime health, including absent selection.

Fixture envelope: schema_version=ui.v1, fixture=true, id, active_destination
review/drive/settings, runtime ResourceState<RuntimeHealth>, selected
ResourceState<SelectedSessionContext> nullable. Visibly label Fixture. Missing
capabilities fail closed; fixture success never establishes operational acceptance.

## Comparison and question interfaces

ComparisonContext: target LapIdentity, reference LapIdentity nullable, policy
observed_session_distance/practice_qualifying, policy_version, eligibility,
exclusions[], model_revision nullable, delta_baseline=target_minus_reference,
delta_unit=seconds. Explicit incompatible references fail without replacement.
Preserve backend driver/car/session/epoch/format and condition checks.
73.420 - 72.842 = +0.578 seconds: positive means target slower. Sector totals
must match the declared baseline/precision; disclose discrepancies, never invent
a correction. Unknown sectors stay null.

QuestionScope discriminates session/lap/pair/region with immutable session identity,
target/reference where applicable, comparison policy and model revision. Region
uses finite numeric start/end metres with start < end, not invented corner names.
QuestionRequest: request_id, scope, question, allow_materialization, use_model.
QuestionResult: request_id, scope, state pending/answered/partial/unsupported/
cancelled/failed, observations[], interpretations[], limitations[], evidence_links[],
error nullable. Interpretations include uncertainty and supporting evidence links.
Unsupported scopes are not silently downgraded. Selection changes cannot retarget
a turn; check request ID and scope before attaching results. Cancelled/detached
late results cannot resurrect a turn. Transport abort is not server cancellation.
Review has no audio side effects by default and never owns acquisition/admission.

## Radio, settings and data contracts (new, not implemented)

RadioEvent: event_id, runtime_id, sequence, revision, session nullable, binding_id
nullable, request_id nullable, trigger, state, occurred_at, updated_at, expires_at
nullable, reason nullable, evidence_links[], limitations[], timings_ms, text
nullable. States queued/running/ready/playing/completed/interrupted/expired/failed/
unsupported/cancelled/suppressed. Sequence is a monotonic safe integer per runtime;
revision increases on lifecycle updates. Terminals cannot become active again.
B2 owns priority/admission and cancellation/expiry on tenure/session change.

B3 producer exports: append_event(event), update_event(event_id, expected_revision,
update), read_events(runtime_id, cursor, limit), project_health(now). Retention
default seven days and 10,000 events per runtime, oldest-first eviction when either
bound is reached; no raw audio. Reads default 50/max 100, opaque stable cursor.
Response includes next_cursor, oldest_available_sequence, history_truncated and
retention policy. Reconnect merges event_id/highest revision, orders by sequence.
Expired cursor requires explicit bounded resync/history-gap notice. These are new
policy requirements, not claims about current persistence; B3 must verify restart.

SettingsSnapshot: revision, scope browser_local/runtime_persistent, configured,
applied, restart_required[], capabilities. Apply uses expected revision and returns
applied/restart_required/failed/conflict with per-field errors. Browser preferences
cannot claim runtime persistence. Secrets are redacted, never fixture/URL content.

DataOperation: operation_id, kind import/export/delete/replay, state queued/running/
completed/duplicate/cancelled/failed/unsupported, progress(completed,total nullable,
unit) nullable, session nullable, error nullable. Retry records explicit prior
operation linkage; cancel reports acknowledgement. Duplicate import preserves
source identity. Deletion requires explicit confirmation of exact session/artifact
scope, expected revision and irreversible effect; no endpoint currently exists.
Retention must report configured/applied policy, never a cosmetic toggle.

B1/B2 AudioRequest: request_id, runtime_id, session/binding identity, expires_at,
content_type, bounded audio bytes; canonical WAV within existing transcription
limits (12 seconds, 512KiB), existing hash/request validation, authenticated server
transport. B1 owns device capture/output; B2 binding/admission; B3 events, no audio.
No browser-owned microphone/speech in the replacement foundation.

## Endpoint and route reservations

Existing endpoints stay unchanged. Reserved future /api/v2/ui namespace:
GET /capabilities, /runtime, /radio/events?runtime_id=&cursor=&limit=,
/sessions?cursor=&limit=, /sessions/{session_key},
/sessions/{session_key}/laps/{lap_key}, /settings, /operations/{operation_id};
POST /questions, /questions/{request_id}/cancel, /settings, /operations/import,
/operations/export, /operations/{operation_id}/cancel. F1 must not call these
unimplemented URLs. No deletion/audio route enabled before security/capability
gates. Integrator owns authenticated transport and source-specific adapters.

Future page paths /review, /review/[sessionKey],
/review/[sessionKey]/laps/[lapKey], /drive, /settings. Selectors are adapter-issued
opaque URL-safe keys, resolved to full verified identities. Lap query parameters:
reference, policy, regionStartM, regionEndM. No reference means single-lap analysis,
not automatic substitution. Invalid selectors/regions are explicitly unavailable.
No old-route cutover until I1. F1 preview only: /ui-foundation, visibly Fixture.

## F1 exports and exact write scopes

Reserve web/src/features/ui-foundation/index.ts exports FoundationShell,
PrimaryNavigation, RuntimeContext, SelectedSessionContext, StatusBadge, StatePanel,
Panel, Button, Field, FixtureBanner and corresponding *Props types. Alias the
shared SelectedSessionContext type when importing into its component. Shell takes
active destination review/drive/settings, runtime state, selected state nullable,
and children. Navigation receives links; it neither infers routes nor calls APIs.
Controls receive callbacks, disabled reason and accessible labels. Use native
semantics, visible focus and explicit status announcements. No fetching, device
APIs, orchestration or feature business logic. Tokens namespaced; existing pages
must not inherit replacement styles. Palette/spacing/typography await the image.

| Worker | Exclusive writes | Exact dedicated tests |
| --- | --- | --- |
| F1 visual | web/src/features/ui-foundation/** | web/scripts/ui-foundation.test.mjs |
| F1 acceptance, after component handoff | web/scripts/ui-foundation-browser.mjs | same file only |
| U1, not started | web/src/features/drive-diagnostics/** | web/scripts/drive-diagnostics.test.mjs |
| U2, not started | web/src/features/session-library/** | web/scripts/session-library.test.mjs |
| U3, not started | web/src/features/session-overview/** | web/scripts/session-overview.test.mjs |
| U4, not started | web/src/features/lap-analysis/** | web/scripts/lap-analysis.test.mjs |
| U5, not started | web/src/features/review-engineer/** | web/scripts/review-engineer.test.mjs |
| U6, not started | web/src/features/settings/** | web/scripts/ui-settings.test.mjs |
| B1, not started | f1_engineer/companion/** | tests/test_companion_contract.py |
| B2, not started | f1_engineer/radio/** | tests/test_radio_contract.py |
| B3, not started | f1_engineer/radio_diagnostics/** | tests/test_radio_diagnostics_contract.py |
| B4, not started | f1_engineer/review_contracts/** | tests/test_review_contracts.py |
| V1, not started | web/scripts/ui-replacement/**, docs/audits/ui-replacement/** | tests/test_ui_replacement_integration.py |

Integrator exclusively owns shared types/transport, reference asset,
web/src/app/ui-foundation/page.tsx, global imports/layout, package scripts, routes/
navigation, Python registrations/migrations/auth and integration. Workers request
cross-scope changes. No modifications to existing shared primitives. For UI parity
work, use gpt-6.1-sol only, per the October 11, 2026 user update. If that model is
not exposed by the dispatcher, the GPT-6.1 Sol primary works directly; no Luna or
other-model fallback. Typography and geometry require rendered reference checks;
the raster reference alone does not establish the original font family.

## Acceptance and preservation

F0 source audit distinguishes actual capabilities from reserved ones. Preserve
pre-existing migration-baseline.md, ui-development-slices.md, ui-mock-differences.md
and unused-evidence.sqlite3 work. F1 validation: focused export/render/state tests,
keyboard/focus/status and disabled-reason checks, desktop and 390px screenshots,
no horizontal overflow, separate runtime/selection context, no production calls,
TypeScript check and production build. Use T3 preview for browser inspection.
The original reference is required before F1 implementation/visual comparison.
After F1 stop for user review; no backend/page slices or cutover.

Validation on October 11, 2026: git diff --check passed (tracked changes only);
focused pytest for test_backend_services_api.py, test_local_speech.py,
test_session_comparison_policy.py and test_import_jobs.py: 80 passed in 95.49s,
one pre-existing Starlette/httpx deprecation warning. These tests validate existing
capability paths, not new contracts, radio hardware or F1 visuals. F1 tests,
TypeScript/build and browser acceptance have not run: no F1 implementation exists.
