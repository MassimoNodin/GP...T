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
service availability can still reject a turn. No background inference runs.

## Request and safety behavior

Only explicit AI requests use the shared process-wide engineer admission gate.
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

Only the current question and routing capabilities are sent to Codex. Recorded
telemetry remains local; answers are rendered from admitted evidence, not
model-authored measurements. V2 braking questions now use supported numeric
brake-onset regions and uncertainty brackets. Unsupported focused measurements
abstain rather than returning an unrelated lap delta as an answer.

## Validation and remaining work

The local CLI model catalog advertised `gpt-6-luna` on October 11, 2026.
The local account probe did not report a signed-in account. No subscription
inference has been validated, and no credentials were inspected/copied.
Codex was not found on the Ubuntu service user's PATH during this check.
The Ubuntu production receiver is not switched to this provider until the
isolated profile is signed in and a controlled inference is validated.

Race summaries remain descriptive, not race strategy or a tyre-life forecast.
Full race stint aggregation, richer focused measurements, bounded historical
reconstruction, asynchronous request lifecycle, live AI/acquisition coexistence
validation and release/backup work remain separate follow-up slices.

Official documentation: Codex Authentication and Codex App Server on the
OpenAI developer documentation site. The installed CLI's generated protocol
schemas were also used to verify the exact request/response shapes.

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
suite, frontend production build, or live subscription inference ran for this
slice. The live receiver stayed active without a restart. Its observed counters
included zero app drops and two kernel drops; loss guards remain in force.
