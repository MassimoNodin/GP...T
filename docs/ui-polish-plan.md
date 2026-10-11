# UI polish plan

October 12, 2026. Follows the reference UI rebuild on branch
`feat/reference-ui-redesign` (commit c46a892). The five screens match
[the reference](assets/ui-reference.png). This plan makes them work against the
real backend instead of falling back to reference content.

## Audit

### Wired to the backend today

| Area | Source | Notes |
| --- | --- | --- |
| Header pill / footer (Library, Overview) | `GET /api/v2/session-evidence/status` | Server-rendered once; no live refresh. |
| Drive "Telemetry (Game Data)" row | same | Other system rows are reference content. |
| Session Library rows | `GET /api/v1/sessions` | v1 holds imported captures only and is empty on the live backend, so the reference list shows. |
| Overview lap progression, stats, lap selection | `GET /api/v1/laps` | Only reachable for v1 sessions. |
| Lap Analysis lap pickers, sector table | v1 laps + `timing_evidence` sectors | Telemetry traces are always reference content. |
| Engineer Questions | `POST /api/engineer/ask` (`attempt_summary`) | Only with v1 attempt keys. |
| Import Session | `POST /api/recording-sources/upload` then `/api/import-jobs` | Job progress not surfaced afterwards. |
| Settings storage tile, Data & Storage | `GET /api/v1/storage/usage` | Preferences are browser-local (localStorage). |
| Open in External Tool | client CSV export | |

### Reference content only

Radio history and event details; Drive session context; system status rows other
than telemetry; overview session summary, notable events, stints, hero details
(build, assists, damage, grip); lap analysis telemetry traces, regions, key
differences, data quality; device names; track model status.

### Key findings

1. **Real sessions live in v2.** The live backend has 9 automatically acquired
   sessions (Jeddah practices, Sakhir race) under
   `GET /api/v2/session-evidence/sessions` with attempts under
   `/sessions/{id}/attempts`. v1 `/api/v1/sessions` is empty. The UI never reads v2
   sessions, so users only ever see reference content in Review.
2. **Status is a one-time snapshot.** Pills, footers and the Drive status rows are
   read at render time and never refresh.
3. **Engineer runtime is available** (`GET /api/v1/engineer/runtime`, proxied at
   `/api/engineer/runtime`) but unused; Drive and Settings show reference values.
4. **No loading, error or not-found states** for any route.
5. **Review header pill is hard-coded** to "Replay Mode" even when nothing replays.
6. **Reference content mixes with real data without disclosure** on real sessions
   (for example, Monaco traces on a Jeddah lap). The user chose reference fallback;
   real sessions must never show another session's measurements.
7. **Responsive layouts are untested below 900px.** Only the five reference
   window sizes were checked.
8. Known reference inconsistencies kept intentionally: the fixture lap chart
   follows the drawn curve, not the lap numbers; the Lap Analysis region band
   follows the drawing.

## Workstreams

Each workstream owns a disjoint write scope. Shared files (`package.json`,
`src/app/globals.css`, `src/lib/**`, API proxy routes, docs) are integrator-owned:
request changes instead of editing them, except where a scope below grants it.

### W1: v2 session evidence in Review (data layer)

Scope: `web/src/features/review-data/**`, `web/src/features/session-library/load.ts`,
`web/src/features/session-overview/load.ts`, `web/src/features/lap-analysis/load.ts`,
new tests under `web/scripts/review-data-*.test.mjs`.

- Read v2 sessions and player attempts server-side (`forwardLocalRequest`), map them
  to the existing view models, and merge with v1 imported sessions in the library.
- Use stable v2 session ids in `/review/[session]` URLs; keep v1 session keys working.
- Lap times, validity and sectors from the v2 attempt payload. Player laps only
  (`role` is the player), unless the payload says otherwise.
- On real sessions, unsupported sections must not show another circuit's reference
  data: use the session's own values, or a neutral "not available" state.
- Library rows: source label from acquisition origin (live game vs import), status
  from session lifecycle (ended, interrupted, active).

### W2: Lap Analysis real telemetry (after W1)

Scope: `web/src/features/lap-analysis/**` except `load.ts` (edit `load.ts` only after W1 lands).

- Replace reference traces with real speed, throttle, brake and steering for the
  target and reference attempts when the backend can supply them; otherwise show a
  clear unavailable state on real sessions.
- Regions and distance range from the session's track model when available.
- Sector deltas, key differences and data quality from real evidence or omitted.

### W3: Live status and Drive Diagnostics

Scope: `web/src/features/drive-diagnostics/**`, new `web/src/features/live-status/**`,
`web/src/features/ui-foundation/AppShell.tsx` and its CSS module, new tests.

- Client polling of `/api/session-evidence/status` (reuse `createAcquisitionPoller`
  from `src/lib/acquisition-status.ts`) for the header pill, sidebar footer and the
  Drive telemetry row; pause when the tab is hidden.
- Engineer Runtime and Question Processing rows from `/api/engineer/runtime`.
- Drive Session Context from the current or most recent v2 session.
- Header pill on Review screens reflects actual state rather than a fixed
  "Replay Mode" label.

### W4: Settings

Scope: `web/src/features/settings/**`, new tests.

- Engineer runtime (model, placement, status) and telemetry receiver details in
  Connections from real endpoints.
- Make the browser-local scope of preferences explicit after Apply. Report save
  failures. No pretend runtime configuration.
- Responsive check at 390px.

### W5: Route states, accessibility and responsiveness

Scope: new `web/src/app/**/loading.tsx`, `error.tsx`, `not-found.tsx`,
`web/src/features/ui-foundation/primitives*`, `TrackPhoto*`, `TrackMap.tsx`,
`icons.tsx`, new `web/scripts/ui-routes.test.mjs`.

- Loading skeletons in the reference visual language, error boundaries with retry,
  a not-found page for unknown sessions.
- Keyboard and focus audit of primitives (tabs arrow-key navigation, menus close on
  Escape and outside click), visible focus, labelled icon-only controls.
- Additional circuit outlines (at least Jeddah and Sakhir) for TrackMap.
- Report responsive issues in feature screens to their owners rather than editing them.

### W6: Menus and responsive Review screens

Scope: `web/src/features/session-library/**` (except `load.ts`),
`SessionOverview.tsx` and its CSS module, backward-compatible `primitives*` changes.

- Replace native `<details>` menus with the W5 `Menu` primitive.
- Fix Library and Overview layouts at 390px and 768px without changing the
  reference window sizes.

### Integration (integrator)

- Review screens use the live telemetry pill and footer on real sessions; the
  reference session keeps the reference "Replay Mode" pill.
- Real-session footers name the session's game, not the reference platform.
- Settings uses the live engineer pill, and its footer and game label come from the
  current or most recent v2 session.

## Rules for all workstreams

- Read `AGENTS.md`, `web/AGENTS.md` and the relevant installed Next.js docs in
  `web/node_modules/next/dist/docs/` before writing code.
- Preserve the reference look at the reference window sizes (1010, 968, 1064, 1982
  and 1098 px wide).
- SVG only for icons and logos. Match surrounding code style.
- Backend access during development: read-only GETs only. No POSTs to the backend,
  no service starts or restarts, no UDP replay, no data deletion.
- No commits, pushes or branch changes. The integrator commits.
- Validate with `npx tsc --noEmit -p .`, the relevant `npm run test:*` scripts, and
  the running dev server at http://127.0.0.1:3001.
- Report: changed files, validation results, integration requests, remaining gaps.
