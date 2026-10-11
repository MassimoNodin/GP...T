# Backend evidence services

These additions implement the backend slices without changing the frontend,
receiver settings, default detail profile, or production deployment. The service
contract is in backend-contract-v1.md and the assignment outline is in
backend-agent-slices.md.

## Operations

All expensive operations require the configured control token in the Authorization
Bearer header, even when app-wide authentication is disabled. They share the
existing two-request comparison admission limit. Session IDs are logical evidence
session IDs, not the game's numeric session UID.

| Method and suffix under /api/v2/session-evidence | Purpose |
| --- | --- |
| GET /services/status | Copied materialization and cache counters; no secrets |
| POST /sessions/{session}/attempts/{revision}/materialize | Reconstruct a deferred attempt from a committed journal prefix |
| POST /sessions/{session}/reference?target={revision}&reference={optional_revision} | Select or validate an eligible diagnostic reference |
| POST /sessions/{session}/analyze?target={revision}&reference={optional_revision} | Return measured numeric-distance regions and trace differences |
| POST /sessions/{session}/ask | Explicit diagnostic engineer request |

Analysis accepts comparison_policy=observed_session_distance (default) or
practice_qualifying, and allow_materialization=false (default). Opt-in historical
materialization covers only explicitly requested target/reference revisions. It
never reconstructs the entire candidate inventory to find a reference.

Ask accepts a JSON object with question (1-2,000 characters), either target or
binding_id, optional reference, allow_materialization (default false), and
use_model (default false). Unknown fields and ambiguous target/binding selections
are rejected. A binding-only request explicitly asks for future evidence from
that current tenure; it never silently replaces a specified historical target.

Model use is optional intent routing through the existing private Ollama runtime,
not permission to invent driving claims. Evidence must be admitted before routing.
Without model use, responses are deterministic diagnostics. Incomplete evidence
returns pending/unavailable, not guessed advice.

## Evidence and reference behavior

Original attempts, chunks, dispositions and retained reports remain unchanged.
Materialization uses a separate temporary database and returns a request-scoped
trace with source revision, source manifest hash, journal range/hash, processor
version and derived manifest provenance. That trace does not become a durable
revision or turn the original deferred trace into a published source revision.
Fetch it again through materialize, or opt in within analysis/ask. Normal evidence
GET continues to report the original state.

The first implementation replays the committed generation prefix using the
existing full processor, including lifecycle/gap boundaries, and extracts only
an exactly matching logical occurrence, attempt and driver tenure. This is a
conservative correctness baseline, not a scalable bulk backfill system. It can
reject long or dense real recordings under the frozen budgets. There is no
automatic retry, journal retention change, durable job queue, or promised speedup.

Diagnostic reference selection currently requires player evidence from the same
verified driver/car/session/epoch/format and compatible known practice/qualifying
or Time Trial context. Complete observed valid pit-free positive timed laps are
required. Qualified/deferred/incomplete laps are not automatic usable references;
explicit invalid references are rejected without replacement. Time Trial also
requires stable known conditions. Opponent historical trace materialization is
supported separately; opponent coaching/reference ranking is not enabled.

Reports use numeric distance regions, not invented named corners. Every answer
and report remains diagnostic-only, with coaching_eligible=false and
ranking_eligible=false. Successful measurements do not establish fuel/tyre/traffic
control or a causal recommendation.

## Resource bounds and shutdown

Historical replay: one active operation, no queue; at most 100,000 journal entries,
128 MiB source payload/metadata, 256 MiB temporary files, and 30 seconds. Oversized,
corrupt, incomplete or unsupported-version sources fail closed. Source and derived
trace reads retain the existing 20,000-row / 32 MiB limits.

The process-local measurement LRU retains at most 32 entries / 16 MiB serialized
JSON, with two in-flight keys and a 30-second duplicate-wait cap. It returns copied
values, revalidates eligibility before cache hits, and does not cache live reference
selection. Serialized-byte bounds are not total Python heap bounds.

Ordinary engineer requests have a 30-second overall deadline. Explicit future
requests have a 270-second deadline and wait at most 240 seconds so a whole flying
lap can complete, retaining Gate A's existing 300-second TTL without renewal.
Clients requesting future evidence must configure an appropriate HTTP timeout.
Request bodies must arrive within 30 seconds. Release is queued on exit.
Queued release is not proof of owner-applied cleanup. Inspect reported task/cleanup
state when acquisition stops or the command queue rejects a release. Existing
runtime shutdown remains responsible for terminating its tasks.

Timeout/cancellation is cooperative between replay entries; individual SQLite or
decode operations cannot be preempted. Framework threadpool work may finish after
a disconnected async caller, but materialization admission remains held until
that bounded worker exits. No new dedicated executor, scheduler or receiver thread
is introduced.

## Verification and remaining gates

Run focused service tests and then the complete Python suite with app/dev extras.
Use disposable evidence stores and fixtures; never point tests or replay at the
production listener/database. Inspect changed files and preserve unrelated work.
Linux recovery/load verification and an authorized real-game coexistence run
remain release gates. Unit and synthetic tests do not certify zero packet loss,
model runtime readiness, or production acceptance. UI selection is deferred.
