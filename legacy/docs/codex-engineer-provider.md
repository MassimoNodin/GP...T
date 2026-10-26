# Codex subscription engineer provider

The backend now defaults to Codex rather than Ollama. There are no UI changes.
The existing client transport validates Codex responses separately from pinned
Ollama responses; visible layouts/components and settings copy are unchanged.
The requested model is exactly `gpt-6-luna`. There is no alternate-model,
Ollama, or separately billed API-key fallback. Ollama remains available only
through an explicit `--engineer-ai-provider ollama` choice.

## Operator setup

Install the official Codex CLI on the host running the backend. Give the
backend executable access to it with `--codex-executable /absolute/path/to/codex`
when it is not on the service's PATH. Its login profile is separate from your
coding-agent profile and defaults to `<database-directory>/.codex-engineer`.
An explicit `--codex-home` can make this independent of capture directories.

Sign in once as the same OS user that runs the backend, with the intended
subscription account, using the official CLI flow:

```sh
CODEX_HOME=/absolute/path/to/engineer-profile codex login --device-auth
f1-engineer api --database /absolute/path/to/f1-engineer.sqlite3 \
  --codex-home /absolute/path/to/engineer-profile \
  --codex-executable /absolute/path/to/codex
```

On Windows, set `$env:CODEX_HOME = 'D:\path\to\engineer-profile'` before
`codex login --device-auth`. Do not copy auth tokens from another application,
commit the profile, or put credentials into the telemetry database. The
runtime rejects `config.toml` and `AGENTS.md` in its isolated profile, so
operator customizations cannot add tools/providers there.
Threads reporting inherited instruction files are also rejected before the
question is sent.

GET `/api/v1/engineer/runtime` performs a metadata-only readiness check:
subscription authentication must report `chatgpt`, and the model catalog
must contain the exact requested model. Readiness is not proof that an actual
inference will succeed: account limits, model entitlement, network access and
service availability can still reject a turn. Default rules-mode proactive
coaching runs no background inference. Explicit radio model mode enables the
bounded candidate-selection path described below.

## Request and safety behavior

Explicit AI requests and opt-in proactive selection use the shared process-wide
engineer admission gate. Foreground engineer questions, radio interpretation
and transcription cancel background selection before seeking admission.
Each request uses the documented Codex app-server stdio JSON-RPC protocol,
a new ephemeral thread, a temporary non-project working directory, a read-only
sandbox without tool network access, constrained JSON output, and disabled
shell/apps/hooks/browser/computer/multi-agent features. Server requests and
unexpected tool items fail closed. The integration is an intent router, not an
unrestricted coding agent or free-form strategy generator.

The provider checks ChatGPT authentication before starting a turn, forces the
OpenAI provider, removes API-key/base-URL environment overrides, and verifies
the returned model/provider before sending the question. Timeout is 25 seconds;
protocol output is capped at 2 MiB and lines at 256 KiB. Cancellation/timeout
terminates and reaps the request's process. Raw stderr, account email, auth
tokens and protocol errors are not exposed in API responses.

Only the current question and routing capabilities are sent to Codex by the ask router (the radio sends more; see below). Recorded
telemetry remains local; answers are rendered from admitted evidence, not
model-authored measurements. V2 braking questions now use supported numeric
brake-onset regions and uncertainty brackets. Unsupported focused measurements
abstain rather than returning an unrelated lap delta as an answer.

## Optional proactive selection

Radio rules mode computes and renders gap/pace notices locally. Model mode is
an explicit opt-in: the radio sends at most three measured candidates, containing
their kind/role/trend, gap or compound, numeric change and three lap samples,
to the authenticated proactive-selection endpoint. A hashed local scope and
closed candidate IDs bind the result; no raw trace, audio or driver name is
required. These compact telemetry-derived facts leave the host for inference
only in model mode, in addition to any explicit ask/voice traffic below.

The model returns only a supplied candidate ID or null. It cannot return speech,
numbers, tools or a tactical order. One request is admitted at a time, no more
often than every 15 seconds; the background inference deadline is six seconds.
Each selection has a separate ephemeral server/turn, not the persistent voice
interpreter connection. Foreground work cancels it, with up to 2.5 seconds for
subprocess cleanup before admission is retried. If cleanup has not released
the slot, the existing busy response remains fail-closed rather than running
two inferences concurrently. Late or unavailable results are silent.

The radio revalidates identity, development revision, live freshness, workload
and its speech budget after selection and again at dispatch. Near closing
developments bypass the background selector. The original candidate lifetime
is never extended. See the [joint validation guide](proactive-radio-validation.md).

## Radio voice interpretation

The standalone radio sends every spoken request to `POST /api/v2/engineer/radio-intent`
and `gpt-6-luna` decides what was asked. Luna only classifies: it returns one
JSON object (action, targets, facts, task options) from a closed schema and never
writes an answer or a number. The radio renders the validated intent into the
fixed phrases its existing parsers and controller already handle, so every
reading still comes from live telemetry.

Unlike the engineer ask router, this path sends more than the question:

- the transcript of the request;
- the session's driver names, race numbers and positions, the track name and
  reviewed corner labels, the active tracking tasks, the last three turns, and a
  glossary of radio terms and common mishearings, so Luna can repair
  misheard names and words.

Each request's schema limits driver names and task numbers to that live
session, and the reply is validated again against the same context. Exact
phrases such as "stop speaking" are handled locally and never wait on the
network. If Luna is busy, slow (8 seconds), or unavailable, the radio falls
back with a spoken "Say again." and logs `radio_intent_fallback` with the
reason; it never reinterprets failed requests with the fixed-phrase parser.
Exact local speech controls bypass the interpreter. Use `--intent-mode parser`
to explicitly select fixed phrases instead of Luna.

The backend keeps one Codex app-server process for radio requests (recycled
every 100 requests, login re-checked every minute) and starts a fresh
ephemeral thread for each one. Requests share the single AI admission slot with
transcription and `/engineer/ask`. Add your own mishearings or terms in
`data/radio-glossary.toml` (`[mishearings]` and `[terms]` tables, or pass
`--glossary-file`).

The radio also sends a plain-ASCII vocabulary hint (driver and corner names) to
the local whisper.cpp transcription as `--prompt`, via the `X-Speech-Hint`
header, to improve name recognition. It is validated (ASCII letters, digits and
basic punctuation, at most 300 bytes, never starting with a dash). Prompts can
occasionally be echoed on near-silent audio; disable the hint with
`--no-speech-hint` if transcripts start repeating names.

### OpenAI transcription (optional)

`f1-engineer api --speech-provider openai` replaces local whisper.cpp with OpenAI
`gpt-transcribe` for radio transcription. Unlike the engineer and radio
interpretation paths, this is not covered by the Codex subscription: it uses a
separately billed OpenAI API key, and each recorded clip (plus the driver and
corner hint) is sent to OpenAI. The key is read from an owner-only file
(`chmod 600`), by default `.openai-api-key` beside the database, or
`--openai-api-key-file`. It is never logged or returned by the API. The default
remains `--speech-provider local`.

## Validation and remaining work

The local CLI model catalog advertised `gpt-6-luna` on October 11, 2026.
Initial checks found no signed-in account and no Codex executable on the Ubuntu
service user's PATH. The October 11 deployment and subscription verification
below supersede those initial readiness findings. No credentials were
inspected or copied between profiles.

### Ubuntu verification, October 11, 2026

The operator signed in through the official device flow for the isolated
`/mnt/nvme/f1-engineer/codex-engineer-profile` profile. The service uses the
explicit Codex 0.156.1 executable, reports `ready` for exactly `gpt-6-luna`,
and retains `fallback_enabled=false` and `billing=codex_subscription`.

The sparse-observation frame correction was deployed after backing up the
three changed diagnostics files and confirming that acquisition was idle.
The existing evidence database and service configuration were preserved; no
journal replay or evidence rewrite was performed. The initial deployment used
a working-tree patch; the follow-up deployment uses the committed repository
version.

Paginated checks covered all 22 player attempts in the two saved Bahrain
Career race sessions. Fourteen returned available descriptive diagnostics,
with sparse frame transitions explicitly qualified. Eight remained unavailable
for pit, incomplete/gap, unobserved-start, identity or context qualifications.
A subscription-backed `Summarize my lap` request returned available in 3.227 s
(reported model latency 2,939 ms), using the admitted 107,565 ms lap and its
6,454 retained samples. No alternate model or local/API-key fallback was used.

Focused diagnostics, integration, Codex, provider, session-engineer and backend
API validation: Ubuntu 112 passed; Windows 111 passed with the already
documented timing-sensitive live-lease test deselected. The initial Windows
run reproduced that test's failure (108 passed, one failed before adding three
frame-wrap regressions). Live telemetry/model coexistence and browser UI
acceptance were not exercised by this verification.

Race summaries remain descriptive, not race strategy or a tyre-life forecast.
Full race stint aggregation, richer focused measurements, bounded historical
reconstruction, asynchronous request lifecycle, live-game AI/acquisition coexistence
validation and release/backup work remain separate follow-up slices.

Recorded Windows-to-Ubuntu telemetry and three concurrent subscription AI
requests passed the isolated coexistence check documented in
[telemetry and AI coexistence validation](telemetry-ai-coexistence-validation.md).
This does not promote recorded replay to live-game acceptance.

Official documentation: Codex Authentication and Codex App Server on the
OpenAI developer documentation site. The installed CLI's generated protocol
schemas were also used to verify the exact request/response shapes.

### Initial pre-deployment validation

Focused backend validation passed 315 tests with one existing timing-sensitive
test deselected. That 20 ms live-lease test was run separately and also failed
when the committed HEAD version of its implementation was loaded in memory;
its assumption that demand has posted before the deadline is not stable on
this machine. Production timeout and cleanup policies were not relaxed.
An additional inherited-instruction rejection case was subsequently added and
the complete Codex runtime safety test module was rerun.

Client response guards and focused transport TypeScript checking passed.
Full TypeScript checking is blocked by malformed generated files under
`.next/dev/types`; these generated artifacts were not changed. No full backend
suite, frontend production build, or live subscription inference ran for the
initial slice. The live receiver stayed active without a restart. Its observed counters
included zero app drops and two kernel drops; loss guards remain in force.
