# UI new areas: design and implementation plan

October 12, 2026. Design architect plan for the views that
[the reference](assets/ui-reference.png) leads to but never draws. It follows
`docs/ui-polish-plan.md` (screen rebuild), `docs/backend-ui-support-plan.md` and
`docs/backend-ui-web-follow-up.md` (new backend endpoints). Nothing in `web/`
changes as part of this document. An engineer implements it work package by work
package (section 5).

Ground rules carried into every section:

- `AGENTS.md` (repository) and `web/AGENTS.md`: SVG for every icon and logo; reuse
  `web/src/features/ui-foundation/icons.tsx`; decorative SVGs are `aria-hidden`;
  icon-only controls carry the label on the control. Read the installed Next.js
  16.3 docs in `web/node_modules/next/dist/docs/` before using an API. Relevant
  ones already checked for this plan: Route Handlers (`01-app/03-api-reference/03-file-conventions/route.md`,
  typed with the global `RouteContext<'/api/...'>`), `error.js` (`retry` prop is
  stable since 16.3), `catchError` from `next/error` (component-level error
  boundary with `retry()`), `useRouter().replace` for URL state.
- Honest data: a real session never shows another session's or the reference's
  measurements. Reference (fixture) content appears only when the local engine is
  entirely unreachable or for the fixture session `monaco-2024-race`, and is then
  always disclosed (section 3.0.2).
- Mutating backend calls (control token) happen only in server code: Route
  Handlers under `web/src/app/api/**` using `forwardLocalRequest(path, init, true)`
  after `isTrustedLocalMutation(request)`. The token never reaches the browser.

Contents

1. Audit
2. Design language recap and new shared primitives
3. Area designs (3.0 cross-cutting, 3.1 Session Overview, 3.2 Lap Analysis,
   3.3 Session Library, 3.4 Drive Diagnostics, 3.5 Settings)
4. Reference guidance and overrides of the current implementation
5. Implementation sequence (work packages)
6. Backend and proxy dependencies
7. Open questions (with defaults)
8. Risks and out of scope

---

## 1. Audit

Severity: **B** blocking (wrong or misleading for real data, or a dead control),
**I** important (missing purposeful content), **P** polish. Data: **E** real
endpoint exists, **D** derivable client-side or in a loader from data already
read, **N** no source.

Verified against the code. Items marked "correction" amend the brief.

| # | Area | Current state (verified) | Missing | Sev | Data |
| --- | --- | --- | --- | --- | --- |
| 1 | Overview › Setup & Conditions | `SessionOverview.tsx` renders only `<Hero>` for `view === "setup"` | Conditions timeline, session facts, car setup, rules | I | E: `/sessions/{id}/summary` weather + session; v1 `LapRecord.player_car_setup_context`; N for v2 setup, assists, damage, build |
| 2 | Overview › Laps | `LapsTable`: Lap, Time, Delta to best, Valid. Row click selects; no link to Lap Analysis | Sectors, tyre, pit, sort, filter, row actions | I | E: `GET /api/v2/session-evidence/sessions/{id}/laps` (`sectors_ms`, `tyre`, `pit`); v1 `timing_evidence` sectors |
| 3 | Overview › Stints | Repeats `StintCard`; `stints: null` for every real session | Per-stint table, pit markers, pace | I | E: `/laps` → `stints[]`; D: avg/best/degradation from laps |
| 4 | Overview › Events | Repeats compact `EventsCard` | Full timeline, filters, lap jumps | I | E: `/summary` → `events[]`, `totals`; D: `evidenceEvents()` |
| 5 | Overview › Compare Laps | Mode tab only appends "vs Lap X" (always the best lap) | Target + reference choice, hand-off | B | D |
| 6 | Overview › Summary / Events / Stints | Correction: v2 sessions already get a derived summary (best lap, invalid count) and `evidenceEvents`; v1 sessions get the stub. Stints always stub | Loaded/partial/empty/unavailable states, richer derived summary | I | E + D |
| 7 | Overview › Hero details | v1 `realModel` shows Game Build, Assists, Damage = "—" and Track Grip "—"; v2 already swaps to Game/Track Length/Time Driven | Consistent unavailable treatment; v1 should use the v2 field set | I | D |
| 8 | Lap Analysis › tabs | `data-view` only re-filters the Overview grid (`.content[data-view=…]`) | Purposeful layouts per tab | I | D (traces) + E (regions) |
| 8a | Lap Analysis › Compare "Time Delta / Distance" | Extra finding: only changes fixture tick spacing; no-op on real data. Implemented with `Tabs` (wrong role) | Delta channel; radio semantics | B | D |
| 9 | Lap Analysis › regions | Fixture only; real sessions show "Corner regions are not available" | Region UI from track models incl. draft | I | E: `/sessions/{id}/track-models`, `/api/v1/track-models/{id}/regions?revision=` |
| 10 | Lap Analysis › Engineer Questions | v1 `attempt_summary` only; **blocked for every v2 lap** (`attemptKey: null`), which is every live session. Real answers get a Scope/Limitations stub. Feedback is local state, lost on reload | v2 ask, full answer anatomy, thread, errors, chips, history | B | E: `POST /api/v2/session-evidence/sessions/{id}/ask` (token); `GET /api/v2/engineer/events?session_id=` |
| 11 | Lap Analysis › Open in External Tool | Correction: present in the header as `ExportLapsButton` (CSV of lap and sector times). Label promises "open", it downloads | Menu with explicit exports | P | D + E (capture download proxy exists) |
| 11a | Lap Analysis › unprocessed laps | Extra finding: deferred v2 laps show "Detailed telemetry has not been processed" with no action | Explicit "Process telemetry" opt-in | I | E: `POST …/attempts/{rev}/materialize` (token) |
| 12 | Library › Import | Split button exists (red, chevron) but the chevron opens a hand-rolled `role="menu"` without arrow keys; one file; no upload %, no job tracking after "queued", no cancel/retry, error text from query string | Full import flow | B | E: upload, import-jobs, `GET /import-jobs/{id}` (progress bytes), queue, cancel, retry |
| 13 | Library › rows | Notes blank for real rows; v2 date "Date not recorded • #id"; no pagination; row menu = 2 links. **When the engine answers with zero sessions the fixture list is shown with `live: true`** | Honest empty state, derived highlights, pagination, richer menu | B | D; N for delete/rename/notes (no endpoints) |
| 13a | Library › header pill | Extra finding: unreachable backend falls back to `shellFixture.telemetryLive` (green "Telemetry live") | Red "Backend unavailable" | B | D |
| 14 | Drive › Radio History / Event Details | Always `driveFixture.radio` | Live feed, empty, streaming, unread, anatomy | B | E: `GET /api/v2/engineer/events` |
| 15 | Drive › System Status | Microphone, Speech Recognition, Audio Output, Proactive Assistance keep reference values when the engine answers. Correction: Speech Recognition has a source (`GET /api/v1/engineer/transcribe`, proxied at `/api/engineer/transcribe`). Microphone and audio are browser-side (the browser records WAV for transcription and would speak) | Honest per-row sources; activity states | B | E + browser APIs; D for Proactive (preferences) |
| 16 | Drive › Session Context | Falls back to the Monaco fixture when no v2 session has a track name | Empty and "last session" states | I | E |
| 17 | Settings › Assistance | localStorage only; note says the runtime ignores it | Backend persistence with revision conflicts | B | E: `GET/PUT /api/v1/engineer/preferences` |
| 18 | Settings › other tabs | Voice offers fake options (Activation, "Calm/Energetic" voices); devices are reference values; Track Models tile hard-codes the Monaco outline and "v2.1 Up to date"; Retention is a static note | Real device/voice tools, connection test, storage chart, track model manager | I | Browser APIs; E: `/api/v1/track-models`, storage, transcribe, recording sources |
| 19 | Cross-cutting | Tabs never wire `aria-controls` panels on Overview/Lap Analysis; no toasts; no shared empty-state component (each module has its own `.unavailable` paragraph); tokens are light-only with many hard-coded hex values in modules | Shared patterns | I | — |

---

## 2. Design language recap

### 2.1 Tokens (from `web/src/app/globals.css`)

Surfaces `--bg #f5f7f9`, `--surface #fff`, `--surface-main #fcfdfd`,
`--surface-muted #f3f5f7`, `--surface-hover #f7f9fb`; lines `--border #e7ebef`,
`--border-strong #dce1e6`, `--divider #eef1f4`. Text `--text #15171c`,
`--text-2 #353a42`, `--muted #737a85`, `--faint #a1a8b1`. Tones, each with a
`-text` and `-soft` variant: red `#e1141c`, green `#1fa463`, amber `#e6a521`,
blue `#2c6cf0`, purple `#9b34ef`, plus `--grey-soft`. Compounds
`--stint-soft/-medium/-hard`. Radii 6/8/12. Shadows `--shadow-card`,
`--shadow-raised`. Shell `--header-h 60px`, `--sidebar-w 156px`. Font Inter
13px/1.4, `.tabular` for numbers, `.sr-only`.

Semantic colours already used ad hoc in modules: target lap `#3f63ee`
(`TARGET_COLOR`), reference lap `#b14cf0` (`REFERENCE_COLOR`), selected row
`#ecf3fe`, chart grid `#eef1f4`/`#f0f2f5`, axis text `#8d949d`, panel shadow
`0 8px 24px rgba(16,24,40,.12)`.

**Add to `globals.css` (WP0)** so new code never hard-codes these:

```css
--target: #3f63ee;  --reference: #b14cf0;
--gain: var(--green-text); --loss: var(--red-text);
--selected-bg: #ecf3fe;  --chart-grid: #eef1f4; --chart-axis: #8d949d;
--stint-inter: #3fae5a;  --stint-wet: #2f7fe0;
--shadow-overlay: 0 8px 24px rgba(16, 24, 40, 0.12);
--z-menu: 4; --z-header: 5; --z-drawer: 20; --z-toast: 30;
--motion-fast: 120ms; --motion-base: 180ms; --motion-slow: 240ms;
--ease-out: cubic-bezier(0.2, 0.8, 0.2, 1);
```

Existing `TARGET_COLOR`/`REFERENCE_COLOR` constants stay (SVG attributes) but
must equal the tokens; `ui-primitives.test.mjs` asserts this.

### 2.2 Spacing and type scale

Spacing in use: 4, 6, 8, 10, 12, 14, 16, 18, 20, 22 px. Card padding 15/16 px;
grid gaps 14 (vertical stacks) and 16 (columns); page header bottom margin 20.
New areas use only these values.

| Role | Size / weight | Where |
| --- | --- | --- |
| Page title | 20.5 / 700, -0.015em | `PageHeader` |
| Hero title | 16 / 700 | Overview hero |
| Card title | 14 / 600 | `Card` |
| Section label | 12.5 / 600, `#2b3037` | `.fieldLabel`, `.label` |
| Body | 12.5–13 / 400 | rows, answers |
| Secondary | 12 / 400, `--muted` or `#848b94` | sublines, help |
| Micro | 11–11.5 | timestamps, axis, notes |
| Stat value | 15 / 600 tabular | hero stats |

### 2.3 Component patterns to reuse

- **Card** (`primitives.tsx`): 1px `--border`, radius 8, title row with `aside`.
- **Tabs**: grey track `#f1f3f6`, raised white selected tab with 2px red underline,
  roving tabindex. Sizes `md` 40px, `sm` 34px. Use only for view switching that
  owns a panel.
- **Filter chips** (Drive `.filter`): 30px, grey fill, selected = white raised with
  `--border-strong`, `aria-pressed`. Promote to a primitive (2.4).
- **Badge** 22px (type/source), **StatusBadge** 27px with glyph (status),
  **DeltaPill** 60px min (green faster / red slower / grey equal).
- **StatusGlyph**: 14–16px filled circle with check/alert/dot/info/user, square,
  flag, ring. Use for every status dot; never CSS-drawn dots in new code (the
  existing `.key i` legend swatches are legend keys, not icons, and may stay).
- **Buttons**: `primary` red (one per view), `secondary` white, `muted` grey,
  `outline-blue` (New Question). Height 34. `IconButton` 34×34 with required label.
- **Select** 37px with chevron SVG. **Checkbox** blue 17px. **Toggle** blue switch.
- **Menu** disclosure: Escape, outside click, focus-out close; `MenuItem`.
- **Rows**: list rows 41px (Radio) with `--divider` bottom; selected = `--selected-bg`
  rounded 6; table rows 8px/10px padding.
- **Key/value**: Drive `.facts`, Settings `.kv` (label left muted, value right).
- **Unavailable text**: `.unavailable` (12.5px `#7d848d`) in three modules.
  Replaced by `EmptyState`/`NotAvailable` below.
- **Skeleton**: `Bone`, `SkeletonCard` in `features/route-states/Skeleton.tsx`
  (shimmer only with `prefers-reduced-motion: no-preference`). Reuse for
  deferred sections.

### 2.4 New shared primitives

All in `web/src/features/ui-foundation/`. Each gets a section in the new
`primitives-extra.module.css` unless noted, is exported from the file named, and
is covered by `web/scripts/ui-primitives.test.mjs` (server-render markup + ARIA
assertions using the `ui-routes.test.mjs` transpile pattern).

**NotAvailable** (`states.tsx`): the single "data unavailable" treatment for a
value.

```ts
export function NotAvailable(props: { reason: string; compact?: boolean }): JSX.Element
```
Renders `—` in `--faint`, `title={reason}`, plus `<span class="sr-only">Not
available: {reason}</span>`. `compact` drops the title (dense tables, where the
column header carries a footnote instead). Never used for values that can never
exist for the session type; those rows are omitted (3.0.2).

**EmptyState** (`states.tsx`)

```ts
type EmptyStateProps = {
  title: string;
  body?: ReactNode;
  icon?: ReactNode;            // decorative SVG from icons.tsx
  tone?: "neutral" | "warning" | "error";
  size?: "inline" | "card" | "page";   // inline: 12.5px text only; card: centred 32px icon; page: RouteError look
  action?: ReactNode;          // Button or Link
  role?: "status" | "alert";   // alert only for error tone
};
```
States: neutral (grey icon disc `--grey-soft`), warning (amber disc), error (red
disc). `card` size: padding 28px 20px, icon disc 40px, title 13.5/600, body 12.5
muted, max-width 360 centred.

**InlineAlert** (`states.tsx`): banner inside a card or above a grid.

```ts
type InlineAlertProps = { tone: "info" | "warning" | "error" | "success"; title?: string;
  children: ReactNode; actions?: ReactNode; onDismiss?: () => void };
```
Soft background of the tone, `StatusGlyph` (info/alert/check), 12.5px text,
radius 8, padding 10px 14px. `role="alert"` for error, `role="status"` otherwise.
Dismiss is an `IconButton label="Dismiss"` with `CloseIcon`.

**DataTable** (`DataTable.tsx` + `DataTable.module.css`)

```ts
type Column<R> = {
  key: string; header: string; width?: string;           // CSS grid track, e.g. "64px" | "minmax(0,1fr)"
  align?: "start" | "end" | "center";
  numeric?: boolean;                                      // tabular-nums, right aligned
  sort?: (a: R, b: R) => number;                          // makes the header a sort button
  render: (row: R) => ReactNode;
  footnote?: string;                                      // shown under the table, referenced by aria-describedby
};
type DataTableProps<R> = {
  caption: string;                    // sr-only <caption>
  columns: Column<R>[]; rows: R[]; rowKey: (row: R) => string;
  selectedKey?: string | null; onSelect?: (row: R) => void;
  rowTone?: (row: R) => "muted" | "warning" | undefined;   // invalid laps muted
  defaultSort?: { key: string; dir: "asc" | "desc" };
  loading?: boolean; loadingRows?: number;                // Bone rows
  empty?: ReactNode;                                      // EmptyState when rows = []
  stickyHeader?: boolean; maxHeight?: number;
  density?: "regular" | "compact";                        // 40px | 34px rows
};
```
Semantic `<table>`; sortable headers are `<button>` inside `<th aria-sort>`.
Selection: first cell holds a `<button aria-pressed>` (keeps current Laps-table
behaviour); `tr[aria-selected]` styling `--selected-bg`. Keyboard: Tab reaches
the selection button of each row; Up/Down move focus between row selection
buttons; Enter/Space selects. Hover `--surface-hover`. Header 12px `--muted`
500, cells 12.5px `--text-2`, dividers `--divider`.

**Segmented** (`Segmented.tsx`): radio group for modes that do not own a panel
(Compare Time Delta/Distance, Single/Compare Laps, Events scope). Same look as
`Tabs` (reuse `.tabs/.tab` classes) but `role="radiogroup"`, buttons
`role="radio" aria-checked`, arrow keys move and select.

```ts
type SegmentedProps<K extends string> = { items: Array<{ key: K; label: string }>; value: K;
  onChange: (key: K) => void; label: string; size?: "md" | "sm"; className?: string };
```

**FilterChips** (`FilterChips.tsx`): the Drive filter style promoted.

```ts
type FilterChipsProps<K extends string> = { items: Array<{ key: K; label: string; count?: number }>;
  value: K; onChange: (key: K) => void; label: string };     // role="group" + aria-pressed
```
Label renders as `Questions (6)`; counts are tabular.

**ProgressBar** (`ProgressBar.tsx`)

```ts
type ProgressBarProps = { value: number | null; label: string; tone?: "blue" | "green" | "amber" | "red";
  size?: "sm" | "md" };      // 4px | 6px; null = indeterminate
```
`role="progressbar"` with `aria-valuenow/min/max` (omitted when indeterminate)
and `aria-label`. Track `--grey-soft`, fill tone. Indeterminate: 30% segment
sliding (reduced motion: static 30% at 50% opacity).

**StatTile** (`StatTile.tsx`): value/label block used by hero stats, Inputs summary
and Settings.

```ts
type StatTileProps = { label: string; value: ReactNode; sub?: ReactNode; delta?: { ms?: number; text: string };
  icon?: ReactNode; unavailableReason?: string };   // unavailableReason → NotAvailable value
```

**Timeline** (`Timeline.tsx`): vertical list with left icon column, lap column,
time column, label, chips, trailing action. Used by Overview Events and the
Setup weather list.

```ts
type TimelineItem = { id: string; icon: ReactNode; lap: number | null; time: string | null;
  label: ReactNode; chips?: ReactNode; action?: ReactNode; emphasis?: "highlight" | "warning" };
type TimelineProps = { items: TimelineItem[]; label: string; dense?: boolean; empty?: ReactNode };
```
`<ol aria-label>`, rows 37px (dense) / 44px, same grid as `.events li`
(`14px 64px minmax(0,1fr)` plus optional `72px` time and auto action column).

**Drawer** (`Drawer.tsx`): right side panel for Import activity, track-model
regions, question history and (below 1024px) Drive Event Details.

```ts
type DrawerProps = { open: boolean; onClose: () => void; title: string; width?: number; // default 420
  children: ReactNode; footer?: ReactNode };
```
Native `<dialog>` opened with `showModal()` (focus trap and Escape for free),
`aria-labelledby` the title, close `IconButton label="Close"`. Backdrop
`rgba(16,24,40,.24)`. Slides 16px + fades in `--motion-base`; no motion under
reduced motion. Focus returns to the opener.

**ConfirmDialog** (`Drawer.tsx`, same file): small centred `<dialog>` for
destructive or expensive confirmations (overwrite settings, process telemetry,
stop recording). Props `{ open, title, body, confirmLabel, tone: "danger" | "default", onConfirm, onCancel }`.

**Toast** (`Toast.tsx`): provider + hook.

```ts
type ToastInput = { tone: "success" | "info" | "warning" | "error"; title: string; body?: string;
  action?: { label: string; href?: string; onClick?: () => void }; durationMs?: number };
export function ToastProvider({ children }: { children: ReactNode }): JSX.Element;
export function useToast(): { push(t: ToastInput): string; dismiss(id: string): void };
```
Mounted once inside `AppShell` (client island `ToastProvider` wrapping
`children` of `<main>`; WP0 adds it). Bottom-right, 16px from the main surface
edge, width 340, max 3 visible (oldest dropped). Card look + left 3px tone bar +
`StatusGlyph`. Auto-dismiss 5 s (success/info), errors persist. Timer pauses on
hover/focus. Region `<section aria-label="Notifications">`; each toast
`role="status"`, errors `role="alert"`. Below 768px: full-width bottom stack.

**Dropzone** (`Dropzone.tsx`)

```ts
type DropzoneProps = { accept: string[];               // [".f1ecap"]
  maxFiles: number; onFiles: (files: File[]) => void; onReject: (reason: "type" | "count") => void;
  children: ReactNode; overlayLabel: string;           // "Drop .f1ecap captures to import"
  disabled?: boolean };
```
Wraps a region; while a drag with files is over it, shows an absolutely
positioned overlay: 2px dashed `--blue`, `--blue-soft` 85% fill, `UploadIcon` 28,
label 13.5/600. Drag events only; the keyboard path is always the file picker
button (the dropzone itself is not focusable).

**SplitButton** (`SplitButton.tsx`): primary action + chevron menu built on `Menu`.

```ts
type SplitButtonProps = { label: string; onClick: () => void; menuLabel: string;   // "More import options"
  children: ReactNode;                   // MenuItem list
  variant?: "primary" | "secondary"; disabled?: boolean; disabledReason?: string; busy?: boolean };
```
Reproduces the reference red split button (`.importButton` styles move here).
`Menu` gains an optional `triggerClassName` already; add arrow-key roving
between `MenuItem`s (Up/Down/Home/End) to `Menu` itself, which also fixes the
row menus.

**Icons to add to `icons.tsx`** (24-unit grid, stroke 1.7, decorative by
default): `UploadIcon`, `DownloadIcon`, `CloseIcon`, `RefreshIcon`, `PlayIcon`,
`PauseIcon`, `StopIcon`, `CopyIcon`, `HistoryIcon` (clock with counter-arrow),
`SendIcon`, `PitIcon` (wrench), `FlagIcon` (chequered outline), `CarIcon` (top
view outline for overtakes/collisions), `WeatherIcon({ kind: "clear" |
"light_cloud" | "overcast" | "light_rain" | "heavy_rain" | "storm" })`,
`TyreIcon({ compound })` (outer ring filled with the compound token, inner
white ring; letter is not drawn, the label is always visible text beside it),
`CaptureFileIcon` (document with waveform), `ExpandIcon`. Also
`TypingIndicator` (three dots, animated only with no-preference) and
`LevelMeter` are feature-local, not icons.

---

## 3. Area designs

Wireframe notation: characters such as `◉ ✓ ⚠ ☀ ⬇ ↻ ▶ ⇄ ▲ ▸ ●` in the ASCII
sketches are placeholders for the named SVG icons and glyphs (`TyreIcon`,
`StatusGlyph`, `WeatherIcon`, `DownloadIcon`, `RefreshIcon`, `PlayIcon`,
`SwapIcon`, `PitIcon`, chevrons, legend dots). They must never be rendered as
text in the UI.

### 3.0 Cross-cutting

#### 3.0.1 Section loading model

Three tiers, used consistently:

1. **Route** (`loading.tsx` → `RouteLoading` skeleton) while the server loader runs.
   `SkeletonBody` variants gain the new tab layouts (WP12).
2. **Deferred section**: data the backend may compute slowly (`/summary`,
   `/laps` enrichment, track-model regions). The server loader tries once with a
   1.5 s budget; if not ready it passes `{ state: "deferred" }` and the client
   card fetches the same data through the proxy route, showing `SkeletonCard`
   rows inside the real card frame (title visible). 409 busy responses retry
   twice at 1 s and 3 s, then show the partial state.
3. **Inline action** (ask, process telemetry, apply settings, import): button
   enters a busy state (label changes, `aria-busy`, disabled) with a
   `ProgressBar` or `TypingIndicator`; result reported inline and, for actions
   that outlive the view, by toast.

Card-level failures use `catchError` from `next/error` (wrap each feature card
that renders untrusted backend shapes) with an `EmptyState tone="error" size="card"`
fallback: title "This section could not be displayed", body "Try again, or
reload the page.", action `Button` "Try again" → `retry()`.

#### 3.0.2 Data-unavailable rules

- **Value missing but could exist** → `NotAvailable` with a reason
  (e.g. "Sector times were not in the recorded packets").
- **Value can never exist for this source** → omit the row/column; if a whole
  card would be empty, show `EmptyState size="card"` explaining why once
  (e.g. "Car setup is not recorded for live sessions yet.").
- **Section partially available** → content plus `InlineAlert tone="warning"`
  at the top of the card (e.g. scan truncated).
- **Engine unreachable** → reference fallback only on screens that already use
  it (Library, Drive), always with an `InlineAlert tone="info"` banner at the
  top of `<main>`: title "Reference data", body "The local engine is not
  reachable, so example sessions from the reference design are shown. Start the
  engine to see your own data." Reference and live data never share a card.
- Reason codes are humanised through one table, `web/src/features/review-data/reasons.ts`
  (`humanReason(code: string | null): string`), which absorbs `REASONS` from
  `lap-telemetry.ts`, the sector/tyre reasons from `/laps`, summary
  `time_reason`, import failures and preference errors. Unknown codes: replace
  `_` with spaces, sentence case.

#### 3.0.3 Header status pill (all states)

| Mode | Tone | Label | Shown when |
| --- | --- | --- | --- |
| any | grey | `Loading…` | route loading |
| telemetry | green | `Telemetry live` | acquisition `receiving` |
| telemetry | amber | `Waiting for game` | `starting`/`listening` |
| telemetry | amber | `Telemetry stale` | `stale` |
| telemetry | grey | `Telemetry disabled` | `disabled` |
| telemetry | red | `Telemetry offline` | `stopped`/`failed` |
| telemetry | red | `Backend unavailable` | status unreadable (also replaces the green fixture fallback in Library, item 13a) |
| engineer | green | `Engineer running` | runtime `ready` |
| engineer | amber | `Engineer not configured` | `not_configured` |
| engineer | red | `Engineer unavailable` / `Engineer offline` | other / unreadable |
| fixture | amber | `Replay Mode` | fixture session only (reference) |
| replay | amber | `Replay Mode` + chevron | `GET /api/replays/current` reports `playing`/`paused` (optional package WP11b); menu: capture name, Pause/Resume, Stop |
| error | red | `Unavailable` | `RouteError` |

The chevron renders only when a menu exists; the pill then becomes a `Menu`
trigger with `aria-label="Replay controls"`. Pill changes are announced by the
existing `role="status"`.

#### 3.0.4 Sidebar footer states

`[glyph] Label` then two lines. Telemetry screens: `[game, receiver detail]`
(`Ready` / `Listening on UDP 20777` / `No recent packets` / `Not receiving`).
Engineer screens: `[track, "Type • Lifecycle"]` of the current or last session.
No session: `["No session yet", "Waiting for the game"]`. Unreachable:
`Engine offline`, lines `["Start the local engine", ""]`. Hidden below 860px
(existing).

#### 3.0.5 Responsive behaviour

New code uses four breakpoints: **≥1440** (design width), **1280**, **1024**,
**768**; the existing shell collapse at 860 and phone tweaks at 600 stay.

| Screen | ≥1440 | 1280 | 1024 | 768 |
| --- | --- | --- | --- | --- |
| Overview tabs | 545fr/323fr columns | same | single column; Lap Selection wraps | tabs scroll horizontally; tables become horizontally scrollable inside the card (min-width 640) |
| Lap Analysis | main + 388px questions column | questions column moves below (existing 1280 rule) | Telemetry readout moves above chart | pickers stack; Track Map list below map |
| Library | 5-column rows | same | notes column hidden, highlights move under meta | existing 900/600 layouts |
| Drive | 2×2 grid | same | Event Details opens in a `Drawer` from the row; list full width | single column (existing) |
| Settings | 2 columns | same | 2 columns, tiles wrap to 3+2 | single column, tiles 2 per row |

Screenshots at 1440, 1280, 1024 and 768 for every changed screen are part of
acceptance (section 5).

#### 3.0.6 Keyboard and screen readers

- Every `Tabs` instance that switches views passes `idPrefix` and `controls`;
  each panel is `<div role="tabpanel" id aria-labelledby tabIndex={0}>`.
  View is mirrored in the URL (`?view=`) with `router.replace(…, { scroll: false })`.
- Mode toggles use `Segmented` (radiogroup), filters use `FilterChips`
  (`aria-pressed`), never `Tabs`.
- Charts are `role="img"` with a summary label; every chart has an equivalent
  table or readout reachable by keyboard (Laps table, cursor readout, region table).
- Live regions: one polite `sr-only` announcer per screen for async results
  ("Answer received", "Import complete", "Settings saved"); streaming radio only
  announces `priority: "high"` events.
- Focus returns to the invoking control after menus, drawers and dialogs.
- Shortcuts (only when focus is not in a text field): Library `/` focuses search;
  Lap Analysis chart cursor Left/Right 10 m, Shift 100 m, Home/End lap ends,
  Escape clears the brushed range.

#### 3.0.7 Dark mode

Tokens are defined only for light, and modules hard-code roughly 150 hex values
(`#fff`, `#f1f3f6`, `#2b3037`, …). Dark mode is **out of scope** for this plan.
New code must use tokens only (lint by review; `ui-primitives.test.mjs` greps new
CSS modules for raw `#` colours outside `globals.css`), so a later dark theme
is a token swap plus a migration of existing modules.

#### 3.0.8 Motion

`--motion-fast` hover/press colour changes; `--motion-base` menus, chips,
drawer, accordion; `--motion-slow` toasts in/out. `--ease-out` everywhere.
Allowed: opacity, transform ≤16px, background highlight of new rows (fade from
`--blue-soft` to transparent over 1.2 s). Not allowed: animating chart paths,
layout heights of tables, auto-scrolling lists while the user is reading
(radio follows newest only when the newest row is selected). All animation is
wrapped in `@media (prefers-reduced-motion: no-preference)`.

#### 3.0.9 Toast usage

Toasts only for results that can arrive after the user moved on: import
complete/failed, settings saved (when leaving the tab), copy session ID, answer
feedback saved, telemetry processed. Inline status remains the primary channel.

---

### 3.1 Session Overview (`/review/[session]`)

Files: split `SessionOverview.tsx` into `SessionOverview.tsx` (toolbar, tab
state, panels), `OverviewView.tsx` (existing overview grid), `LapsView.tsx`,
`StintsView.tsx`, `EventsView.tsx`, `SetupView.tsx`, `LapSelection.tsx`,
`Hero.tsx`; data helpers in `overview-data.ts` (pure, tested). `?view=` in the
URL (`overview|laps|stints|events|setup`); `?lap=` selected lap,
`?mode=compare&target=&reference=` for compare mode.

#### 3.1.0 New model fields (`session-overview/load.ts`)

```ts
export type OverviewLap = {
  id: string; lap: number; ms: number; valid: boolean; revisionId?: string;
  sectors: [number, number, number] | null;          // v2 /laps sectors_ms, v1 timing_evidence
  sectorsReason: string | null;                      // humanised by reasons.ts
  tyre: { compound: Compound; label: string; actual: string | null; ageLaps: number | null } | null;
  pit: boolean; startObserved: boolean | null;
  traceAvailable: boolean;                           // false → "Telemetry not processed" flag
};
export type Compound = "soft" | "medium" | "hard" | "inter" | "wet" | "super_soft" | "dry" | "unknown";
export type OverviewStint = {
  index: number; compound: Compound; label: string; actual: string | null;
  startLap: number; endLap: number; lapCount: number;
  startAge: number | null; endAge: number | null;
};
export type DeferredSection<T> =
  | { state: "loaded"; value: T; partialReason: string | null }
  | { state: "deferred" }                      // client fetches through the proxy
  | { state: "unavailable"; reason: string };  // engine says no / unreachable
export type OverviewModel = /* existing */ & {
  enrichment: DeferredSection<{ stints: OverviewStint[] }>;   // laps are merged into `laps` when loaded
  summaryData: DeferredSection<SessionSummary>;               // from review-data/summary.ts
  setup: CarSetupView | null;                                 // v1 only
  source: "fixture" | "v1" | "v2";
};
```

`SessionSummary`, `SummaryEvent`, `WeatherPoint` are defined in
`review-data/summary.ts` (section 6). Stints for the fixture come from
`overviewFixture.stints` mapped to `OverviewStint` (Soft 1–12, Medium 13–54,
Hard 55–78).

#### 3.1.1 Hero (all tabs except Laps) and unavailable values

Copy exactly from the reference for the fixture. For real sessions:

- v1 and v2 use the same field set (override item 7): stats `Laps`, `Best Lap`,
  `Conditions`, `Track Temp`; details `Game`, `Track Length`, `Time Driven`,
  `Rule Set`, `Data Source`. Fields with no value use `NotAvailable` with these
  reasons: Best Lap "No valid timed lap", Conditions "Weather was not reported",
  Track Temp "Track temperature was not reported", Track Length "Track length
  was not reported", Rule Set "Rule set was not reported".
- Data Source: `Game (Live)` for v2 `acquisition` live, `Import` for v1,
  `Game (Live, partial)` when attempts were truncated.
- Hero line 1 for v2 when `/summary` gives `started_at_utc`: `Race • 4 Jun 2024, 15:00`
  (observed capture time, footnote via `title`: "Time the engine first observed
  this session"). Otherwise `Race • Date not recorded • #3f2a9c1b`.
- Status badge chevron is removed for real sessions (no menu exists); fixture keeps it.

#### 3.1.2 Overview tab (existing grid) states

| Card | Loaded | Partial | Empty | Unavailable / loading |
| --- | --- | --- | --- | --- |
| Lap Time Progression | chart (existing) | `dataNote` amber line (existing copy) | `EmptyState inline` "No timed laps were recorded for this session." | route skeleton |
| Session Summary | text + up to 5 glyph items | items + amber item for each limitation | "No timed laps were recorded, so there is nothing to summarise yet." | deferred: 3 bone lines; unreachable: "A summary is not available for this session." |
| Stint Structure | bar + legend | bar + `InlineAlert warning` "Tyre data is missing for laps 12–14." | "Tyre data was not recorded for this session." (v1) | deferred skeleton bar |
| Notable Events | compact list, max 8, "View all" link → Events tab | list + footnote "Showing the first 8 of 23 events." | "No notable events were found in this session." | deferred: 4 bone rows |

Derived summary for real sessions (`overview-data.ts › deriveSummary`), in this order, each item only when its data exists:

1. Text: `{n} timed laps recorded{ across {k} stints}. Best lap {time} on lap {lap}{, {delta} faster than the median lap}.`
2. green `Best lap {time} (lap {lap})`
3. amber `{n} laps invalidated by the game`
4. blue-info `{n} pit stops` (from `summary.player.pit_stop_count`)
5. amber `{n} safety car periods` (count of `safety_car`/`virtual_safety_car` events with `detail.event === "deployed"`)
6. grey `Weather changed {n} times` (weather points with different `weather`)

Summary glyph tones are limited to green/amber/red (existing `StatusGlyph`
mapping); blue-info items use `glyph="info"`.

#### 3.1.3 Laps tab

Purpose: the full lap list as a working table that feeds Lap Analysis.

```
┌ Laps (78) ──────────────────────────────────────────────────────────────────┐
│ [All 78][Valid 74][Invalid 4][Pit 3]          Show: [Lap Time ▾] [☐ Hide in/out laps] │
├────┬──────────┬─────────┬────────┬────────┬────────┬──────────┬─────────┬─────┤
│Lap▲│ Time     │ Δ Best  │ S1     │ S2     │ S3     │ Tyre     │ Flags   │     │
│ 28 │ 1:13.420 │ +0.578  │ 18.420 │ 35.128 │ 19.872 │ ◉ M · 15 │         │ [↗] │
│ 57 │ 1:12.842 │ Best    │ 18.630*│ 34.186*│ 20.026 │ ◉ H · 3  │         │ [↗] │
│ 21 │ 1:36.1…  │ +23.3   │   —    │   —    │   —    │ ◉ M      │ Pit     │ [↗] │
└────┴──────────┴─────────┴────────┴────────┴────────┴──────────┴─────────┴─────┘
  * purple = best of session in that column.  — Sector times were not in the recorded packets.
```

- Grid (DataTable widths): `56px 96px 84px 72px 72px 72px minmax(110px,1fr) minmax(90px,1fr) 44px`.
  At 1024 S1–S3 collapse into one `Sectors` column showing `18.4 · 35.1 · 19.9`;
  at 768 the table scrolls horizontally (min-width 640).
- Filter chips (`FilterChips`, label "Filter laps"): `All`, `Valid`, `Invalid`,
  `Pit` with counts. Checkbox `Hide in/out laps` hides `pit` laps and laps with
  `startObserved === false`. Default: All, unchecked.
- Sort: Lap (default asc) and Time; sector columns sortable too.
- Cells: Time tabular; Δ Best uses `DeltaPill` (`Best` purple text for the best
  lap, `NotAvailable` for invalid laps: "Invalid laps are not compared");
  session-best sector values `--purple-text` 600; Tyre = `TyreIcon` 13px +
  `Medium · 15 laps` (age when known) + actual compound in `title` (`C3`);
  Flags = `Badge` tokens: `Invalid` (amber), `Pit` (grey), `Start not observed`
  (grey), `Not processed` (grey, `traceAvailable === false`).
- Row tone: invalid laps `muted` (text `--muted`).
- Row selection button `Lap 28` selects the lap for the Lap Selection card and the
  progression chart (existing behaviour). Last column `IconButton
  label="Analyse lap 28"` with `OpenDocumentIcon` linking to
  `/laps?target=28&reference={bestId}`. In compare mode a row `Menu
  label="Actions for lap 28"` replaces it with `Set as target`, `Set as reference`,
  `Analyse lap 28`.
- Footnotes (DataTable `footnote`): one line per distinct `sectorsReason`/`tyreReason`
  present, e.g. "— Sector times were not in the recorded packets (laps 1–3)."
- States: deferred enrichment → table renders with lap times immediately and
  `Bone` cells in S1–S3/Tyre columns; enrichment unavailable → those columns
  are hidden and an `InlineAlert info` reads "Sector and tyre details are not
  available for this session." Empty laps → `EmptyState card` "No laps were
  completed in this session."
- Show menu ("Show:") is shared with the Overview chart: `Lap Time`,
  `Delta to Best` (adds a chart mode), `Sector 1`/`2`/`3` (only when sectors exist).

#### 3.1.4 Stints tab

```
┌ Stint Structure ──────────────────────────────────────────────────────────┐
│ ● Soft (12)   ● Medium (42)   ● Hard (24)                                 │
│ [■■■■■■|■■■■■■■■■■■■■■■■■■■■■■|■■■■■■■■■■■]   ▲pit   ▲pit                 │
│ 1   10   20   30   40   50   60   70   78                                 │
└───────────────────────────────────────────────────────────────────────────┘
┌ Stints ───────────────────────────────────────┐ ┌ Stint Pace ──────────────┐
│# Compound   Laps     Age    Best     Avg  Deg │ │ lap time vs lap in stint │
│1 ◉ Soft     1–12 (12) 0→12  1:13.2  1:13.6 +0.05│ │ one line per stint,     │
│2 ◉ Medium  13–54 (42) 0→42  1:12.9  1:13.4 +0.02│ │ compound colour          │
│3 ◉ Hard    55–78 (24) 0→24  1:12.8  1:13.1 +0.01│ └─────────────────────────┘
└───────────────────────────────────────────────┘
```

- Grid: bar card full width; below `minmax(0, 545fr) minmax(0, 323fr)` like Overview.
- Pit markers: 10px `PitIcon` above the bar at each stint boundary that has a
  pit lap, with `title="Pit stop, lap 12"`; the bar `aria-label` lists stints and
  pit laps.
- Table columns: `#`, `Compound` (TyreIcon + visual label, actual in title),
  `Laps` (`13–54 (42)`), `Tyre age` (`0 → 42` or NotAvailable "Tyre age was not
  reported"), `Best`, `Average` (valid, non-pit laps), `Degradation`
  (`+0.021 s/lap`, least-squares slope of valid non-pit lap times against lap
  number, only when ≥ 4 such laps; else NotAvailable "Needs at least four clean
  laps"). Degradation tone: red if > +0.05, green if < 0, else text.
- Stint Pace chart: reuse `LapTimeChart` scales; x = lap in stint (1..n), one
  polyline per stint in its compound token, dots 2.9r; legend = stint table.
  `role="img"` label "Lap times by stint".
- States: v1/no tyre data → both cards replaced by one `EmptyState card` "Tyre
  data was not recorded for this session." with body "Stints come from the game's
  car status and session history packets." Partial (some laps without tyre) →
  gaps drawn as `--grey-soft` segments and `InlineAlert warning` "Tyre data is
  missing for laps {ranges}." Truncated enrichment → `InlineAlert warning`
  "Only part of this session could be read, so later stints may be missing."

#### 3.1.5 Events tab

```
┌ Events (23) ──────────────────────────────────────────────────────────────┐
│ [All 23][Race control 6][Penalties 3][Pit 3][My laps 7][Incidents 4]  [☐ Only events involving me] │
├──────┬────────┬──────────────────────────────────────────────┬────────────┤
│ ◎    │ Lap 1  │ 00:00  Lights out                            │            │
│ ⚠    │ Lap 12 │ 18:22  Player: +5s time penalty (corner cutting) │ View lap │
│ ◎    │ Lap 20 │ 31:05  Safety car: deployed                  │            │
│ ✓    │ Lap 57 │ 1:24:10 Session best (1:12.842)              │ View lap   │
└──────┴────────┴──────────────────────────────────────────────┴────────────┘
 Showing 23 of 31 events. 8 minor events (speed traps, other drivers' overtakes) are not listed.
```

- Source merge (`overview-data.ts › mergeEvents`): `/summary` events +
  `evidenceEvents()` (session best, invalid, pit lap, not completed). Dedupe by
  `(lap, kind)`; prefer summary label. Fixture uses `overviewFixture.events`.
- Categories (`categoryOf(kind)`): Race control = `lights_out, session_started,
  session_ended, chequered_flag, safety_car, virtual_safety_car,
  formation_lap_safety_car, red_flag, drs_enabled, drs_disabled`; Penalties =
  `penalty, drive_through_served, stop_go_served`; Pit = `pit_entry, pit_exit`
  and evidence pit laps; My laps = evidence events + `fastest_lap` where
  `is_player`; Incidents = `collision, retirement, flashback, finish,
  race_winner`; Overtakes appear under All only and only when `is_player`.
- Icons: race control `FlagIcon`, penalties `StatusGlyph red alert`, pit
  `PitIcon`, my laps `StatusGlyph green check` (best = highlight green text,
  existing), invalid `StatusGlyph amber alert`, incidents `CarIcon`.
- Time column: session time relative to the first event, `mm:ss` or `h:mm:ss`;
  `NotAvailable` when `session_time_s` is null.
- "View lap" `Link` (secondary small, 28px) shown when the lap is a timed player
  lap: `/review/{s}/laps?target={lap}&reference={best}`.
- "Only events involving me" filters to `is_player` plus race-control events.
- Footer copy: `Showing {listed} of {observed} events.` plus
  ` {omittedMinor} minor events (speed traps, other drivers' overtakes) are not listed.`
  when > 0. Truncated scan: `InlineAlert warning` "The event scan stopped early
  ({reason}), so later events may be missing."
- States: deferred → 6 bone rows; none → `EmptyState card` "No notable events were
  found in this session."; filter with zero → inline "No {category} events."
- Overview compact card links here with `View all events` (text button, 12px blue).

#### 3.1.6 Setup & Conditions tab

The reference does not draw this tab. Layout mirrors the Overview grid so it
feels like a sibling view:

```
[Hero card — unchanged]
┌ Conditions ─────────────────────────┐ ┌ Session ───────────────────┐
│ Now/End: ☀ Clear · Air 24°C · Track 38°C │ │ Type        Race              │
│ Timeline                                 │ │ Rule set    2024              │
│ 00:00 ☀ Clear     24°C / 38°C            │ │ Laps        78 scheduled       │
│ 41:10 ☁ Light cloud 23°C / 35°C          │ │ Duration    1:42:18 (2:00:00 scheduled) │
└──────────────────────────────────────────┘ │ Started     4 Jun 2024, 15:00 │
┌ Car Setup (at session start) ────────────┐ │ Ended       4 Jun 2024, 16:42 │
│ Aerodynamics  Front wing 24 · Rear wing 18│ │ Track       Monaco · 3,337 m  │
│ Transmission  On-throttle 55% · Off 50%   │ │ Game        F1 24             │
│ Suspension geometry  Camber -3.5/-2.0 …   │ └───────────────────────────────┘
│ Suspension  …  Brakes  Bias 56% · Pressure 95% │ ┌ Assists & Damage ──────────┐
│ Tyres  FL 23.5 FR 23.5 RL 21.5 RR 21.5 psi│ │ EmptyState inline           │
└──────────────────────────────────────────┘ └───────────────────────────────┘
```

- Grid `minmax(0,545fr) minmax(0,323fr)`, gap 16; single column at 1024.
- Conditions: `Timeline dense` of `summary.weather` points with `WeatherIcon`,
  label (`formatWeather`), `Air {a}°C · Track {t}°C`; header line = last point.
  v1/no summary: single row from session context weather; none →
  `EmptyState inline` "Weather was not reported for this session."
- Session card: `.kv` rows; Started/Ended from `/summary`
  `started_at_utc`/`ended_at_utc` with `formatDayTime`; when null use
  `NotAvailable` with `humanReason(time_reason)` (e.g. "The session is still
  active"). Duration = `session_time.duration_s`; scheduled from
  `scheduled_duration_s`.
- Car Setup: v1 only, from the first lap's `player_car_setup_context.at_start.setup`
  (grouped exactly as shown; units: wings integer, diff %, camber/toe degrees
  2 dp, suspension/ARB/ride height integers, brake %, tyre psi 1 dp, fuel kg 1 dp).
  If `observed_change_count > 0`: `InlineAlert info` "The setup changed {n} times
  during the session; values shown are from the start." v2 → `EmptyState card`
  title "Car setup is not recorded for live sessions yet", body "Setup values are
  available for imported captures."
- Assists & Damage: no source anywhere → `EmptyState inline` "Assist and damage
  settings are not included in the game's telemetry." (Never show the reference
  "Assists Off / Damage Full" on real sessions.)

#### 3.1.7 Lap Selection and Compare Laps

Reference (copy exactly): card title "Lap Selection", segmented `Single Lap /
Compare Laps`, stepper `‹ Lap 28 1:13.420 +0.612 ›`, blue info box
"Session best: 1:12.842 (Lap 57) / Comparisons are only available for laps with
complete telemetry data." Note the reference delta "+0.612" is arithmetic the
fixture already corrects to +0.578; keep the correction.

Compare mode layout:

```
Lap Selection
[Single Lap|Compare Laps]  ● Target  ‹ Lap 28  1:13.420 ›  [⇄]  ● Reference ‹ Lap 57  1:12.842  Session best › │ i Lap 28 is +0.578 slower than Lap 57.  [Compare in Lap Analysis]
```

- Mode control → `Segmented` (override: currently `Tabs`).
- Two `LapStepper`s (extract from `LapPicker` in Lap Analysis into
  `ui-foundation/LapStepper.tsx`, shared by both screens): label with colour dot
  (`--target`, `--reference`), prev/next `IconButton`s, the value is a button that
  opens a `Menu` listing all laps (`Lap 28 · 1:13.420 · Invalid`), filtered to
  valid laps first then a divider "Other laps".
- Swap `IconButton label="Swap target and reference"` with the new `SwapIcon`
  (WP0).
- Defaults: target = selected lap; reference = session best; if target is the best,
  reference = second fastest valid lap.
- Info box text in compare mode: `Lap {t} is {+0.578} {slower|faster} than Lap {r}.`
  plus second line warnings: `Lap {n} was invalidated; the comparison may be
  misleading.` / `Lap {n} has no processed telemetry; Lap Analysis can process it.`
- Primary-looking link (secondary style, `OpenDocumentIcon`) `Compare in Lap
  Analysis` → `/laps?target=&reference=`. The toolbar "Open in Lap Analysis"
  uses the same pair in compare mode.
- Progression chart in compare mode draws the reference as a purple ring (r 6,
  stroke 2) in addition to the blue selected dot, and the tooltip adds
  `vs Lap 57`.
- URL: `?mode=compare&target=28&reference=57`.

---

### 3.2 Lap Analysis (`/review/[session]/laps`)

Files: split `LapAnalysis.tsx` into `LapAnalysis.tsx` (header, pickers, tabs,
URL), `views/OverviewView.tsx`, `views/TelemetryView.tsx`,
`views/SectorsView.tsx`, `views/TrackMapView.tsx`, `views/InputsView.tsx`,
`views/QualityView.tsx`, `RegionPanel.tsx`, `EngineerQuestions.tsx`,
`ExternalToolMenu.tsx`; pure helpers `regions.ts`, `inputs-stats.ts`,
`delta.ts`, `answer.ts` next to `lap-telemetry.ts`.

URL state: `?target=&reference=&view=&region=&from=&to=` (`from/to` = brushed
distance range in metres).

#### 3.2.0 New model fields (`lap-analysis/load.ts`, `lap-telemetry.ts`)

```ts
// lap-telemetry.ts additions
export type ChannelKey = "speed" | "throttle" | "brake" | "steering" | "gear";   // gear optional
export type LapTrace =
  | { state: "available"; samples: number; length: number;
      channels: Partial<Record<ChannelKey, ChannelRuns>>;
      coverage: Partial<Record<ChannelKey, { metres: number; runs: number }>>;   // for Data Quality
      path: Array<[number, number, number]> | null }   // [distance, x, z] when samples carry world_position_x_m/z_m
  | { state: "unavailable"; reason: string; code: string | null; processable: boolean };
export type DeltaSeries = { points: TracePoint[]; endMs: number } | { unavailable: string };

// load.ts additions
export type RegionSource =
  | { state: "available"; model: RegionModel }
  | { state: "none"; reason: string }          // humanised
  | { state: "reference" };                    // fixture regions
export type LapAnalysisModel = /* existing */ & {
  regions: RegionSource;
  laps: AnalysisLap[];             // AnalysisLap.sectors now filled for v2 from /laps
  canAsk: boolean;                 // v1 attempt key or v2 revision present
  canProcess: boolean;             // v2 session (materialize available)
};
```

`processable` is true when the v2 attempt's detail state is `deferred`
(`traceAvailable === false` and the code is `not_processed`). `gear` parsing:
v2 sample `gear` when present; v1 preview has no gear (channel omitted).
`path`: parse `world_position_x_m`/`world_position_z_m` (or `world_position_m`
tuple) from v2 samples when present; verify the v2 sample shape during WP4
(the v1 parquet trace schema v2 has these fields; the v2 evidence sample shape
must be checked against a real response before relying on it).

#### 3.2.1 Shared header (all tabs; reference layout copied exactly)

`PageHeader` "Lap Analysis" + subtitle (exact reference copy), Target/Reference
pickers (now `LapStepper`), tab strip `Overview, Telemetry, Sectors & Corners,
Track Map, Inputs, Data Quality` (`Tabs size="sm"`, wired to panels), and
`Compare` + `Segmented` `Time Delta | Distance`.

Compare semantics (decision): `Time Delta` adds a **Delta** row at the top of
every distance chart (cumulative time difference target − reference, metres on
x, seconds on y, red above zero = target losing, green below), and Key
Differences quote time. `Distance` hides the Delta row (pure overlay). Default
`Time Delta` (reference shows it selected). With no reference lap the control
is disabled with `title="Select a reference lap to compare"`.

Delta derivation (`delta.ts › deltaSeries(target, reference, lapMsT, lapMsR)`):
resample both speed traces onto a 5 m grid over the shared covered range,
integrate `dt = Δd / v` (v floored at 5 km/h), scale each lap's cumulative time
so its final value equals its official lap time (removes sampling drift), and
return `tT(d) − tR(d)`. Unavailable when either speed trace covers < 95% of the
lap length: reason "Delta needs complete speed traces for both laps." Chart
label "Delta (s), derived from speed".

#### 3.2.2 Overview tab

Reference layout, copied exactly at ≥1440 (track card 220–260px, chart, side
column with Sector Comparison, Key Differences, Data Quality). Changes for real
data: region panel from 3.2.7; Key Differences scoped to the selected region
(title `Key Differences (Selected Region)` or `(Full Lap)`); Sector Comparison
from `/laps` sectors (v2) or `timing_evidence` (v1); Delta row per 3.2.1.

#### 3.2.3 Telemetry tab

Purpose: read every channel against distance with a precise cursor.

```
┌ [Speed][Delta][Throttle][Brake][Steering][Gear]      Range: 2,340–2,980 m [Reset] ┐
│ Readout: 2,640 m │ ● Lap 28  132 km/h · Thr 0% · Brk 41% · Str -62% · G3 │ ● Lap 57  146 km/h … │ Δ +0.214 s │
├────────────────────────────────────────────────────────────────────────────┤
│ Delta   ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~                │
│ Speed   ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~   (cursor line) │
│ Throttle / Brake / Steering / Gear rows                                     │
│ Distance (m)  0  500  1,000 …                                               │
├────────────────────────────────────────────────────────────────────────────┤
│ mini map strip: lap overview 0 ───[■■ brushed window ■■]─── 5,412 m         │
└────────────────────────────────────────────────────────────────────────────┘
```

- Single full-width card (`.content[data-view="telemetry"]` already 1 column).
  Chart width fills the card; rows: Delta 70px, Speed 140, Throttle 90, Brake 90,
  Steering 100, Gear 70. `TelemetryCharts` gains props `cursor`, `onCursor`,
  `range`, `onRange`, `delta`, `rowHeights`.
- Channel chips (`FilterChips` multi-select variant: `aria-pressed` per chip,
  at least one must stay on). Gear chip hidden when no gear data.
- Cursor: pointer move sets the cursor; click pins it; the chart group is
  focusable (`tabIndex=0`, `aria-label="Telemetry cursor, use arrow keys"`) and
  Left/Right move 10 m, Shift ±100 m, Home/End. Readout above the chart is the
  accessible equivalent (`aria-live="off"`; updated text in a `<output>`).
- Brush: drag on the overview strip (or Shift+drag on the chart) selects a range;
  zooms all rows; `Reset` button returns to full lap. Selected region (3.2.7)
  sets the range too.
- States: traces loading (pickers moved ahead of the server) → chart frame with
  `Bone` rows and "Loading telemetry for Lap 31…"; unavailable → `EmptyState card`
  with the humanised reason and, when `processable`, the Process action (3.2.9);
  reference missing → target only + note (existing copy).

#### 3.2.4 Sectors & Corners tab

```
┌ Sectors ─────────────────────────────────────────────────────────────────┐
│ Sector │ Lap 28  │ Lap 57  │ Δ       │ Session best │ Δ to best       │
│ S1     │ 18.420  │ 18.630  │ -0.210  │ 18.390 (L61) │ +0.030          │
│ S2 … S3 … Lap                                                            │
└──────────────────────────────────────────────────────────────────────────┘
┌ Corners (Monaco r3 · Draft) ─────────────────────────────────────────────┐
│ Corner  │ Range        │ Min speed 28/57 │ Δ   │ Time in corner │ Δ time │ Brake point │ Throttle 90% │
│ T1      │ 120–310 m    │ 92 / 95 km/h    │ -3  │ 4.21 / 4.13 s  │ +0.08  │ 6 m later   │ 4 m earlier  │
│ T6–T8 ▸ │ …                                                              │
└──────────────────────────────────────────────────────────────────────────┘
```

- Sectors table: `DataTable compact`; columns as drawn; session best per sector
  from all laps' sectors (purple). Unavailable sectors → `NotAvailable`
  ("Sector times were not in the recorded packets").
- Corners table (`regions.ts › cornerMetrics(region, targetTrace, refTrace, delta)`):
  entry speed at `startM`, minimum speed and its distance, exit speed at `endM`,
  time in corner (integrated), Δ time = delta(end) − delta(start), brake point =
  first brake ≥ 10% in `[startM − 150, apexM ?? midpoint]`, throttle pickup =
  first throttle ≥ 90% after the minimum-speed point. Distance differences read
  "6 m later" / "4 m earlier" / "same". Rows grouped by `complexId` with an
  expandable parent row (`T6–T8 ▸`, `aria-expanded`).
- Selecting a corner row sets the region (URL `region=`) and jumps to Telemetry
  only via an explicit row action `IconButton label="Show T6 in telemetry"`.
- Without a track model: Corners card shows `EmptyState card` title "No corner
  regions for this circuit" with body from the region reason and a secondary
  action "Measure a distance range" that switches to Telemetry with the brush
  hint. Draft model: card title suffix `Badge amber "Draft"` + `InlineAlert info`
  "Draft regions are approximate and have not been reviewed. Use them as distance
  ranges, not corner names."

#### 3.2.5 Track Map tab

```
┌ Track Map ─────────────────────────────────────┐ ┌ Regions ──────────────┐
│                                                │ │ Legend: ■ Gained ■ Lost ■ No data │
│        (large circuit, 520px)                   │ │ T1      +0.08 s  ▸    │
│   regions coloured by Δ time; selected outlined │ │ T2–T3  -0.12 s  ▸    │
│   start/finish marker, direction arrow          │ │ …                     │
│ Measured line from Lap 28 │ Schematic outline   │ └───────────────────────┘
└────────────────────────────────────────────────┘
```

- Grid `minmax(0, 2fr) minmax(260px, 1fr)`; stacked at 1024.
- Map source (decision): if `targetTrace.path` exists, draw the measured line
  (project x/z to the viewBox, keep aspect), footnote "Line drawn from Lap 28's
  recorded positions."; else the `TrackMap` outline with footnote "Schematic
  outline; region positions are approximate." Region segments are placed by
  distance (measured line: exact; schematic: `getPointAtLength(d / trackLength *
  pathLength)`).
- Heat colouring per region by Δ time: ≤ -0.05 s `--gain`, ≥ +0.05 s `--loss`,
  between `--muted`, no data `--grey-soft`; stroke width 5 over a 2px base line.
  Selected region: 7px stroke + 2px white halo. Without a reference lap the map
  stays neutral and the legend reads "Select a reference lap to colour regions
  by time difference."
- Interaction: the Regions list is the accessible control (buttons with
  `aria-pressed`); map segments are `aria-hidden` mirrors that also respond to
  click/hover (tooltip `T6 · +0.08 s`). Zoom: `IconButton label="Fit selected
  region"`.
- Without regions: the map shows the full line coloured by the running Δ (8
  equal-distance bins), list replaced by the no-model `EmptyState`.

#### 3.2.6 Inputs tab

```
┌ Input Summary ──────────────────────────────────────────────────────────┐
│ StatTiles (Lap 28 / Lap 57 / Δ):                                        │
│ Full throttle 61% / 64% (-3)  Coasting 4% / 3%  Braking zones 9 / 9      │
│ Braking distance 812 m / 790 m  Peak brake 100% / 100%  Steering activity 31 / 27 °/100m │
└─────────────────────────────────────────────────────────────────────────┘
┌ Throttle / Brake / Steering / Gear charts (Telemetry component, inputs set) ┐
```

- `inputs-stats.ts › inputStats(trace, range)` over the selected range:
  full throttle share (≥ 98%), coasting share (throttle < 5% and brake < 5%),
  braking zones (brake ≥ 10% runs separated by > 20 m), total braking distance,
  peak brake, steering activity (sum |Δsteering %| per 100 m), gear changes count
  when gear exists.
- Tiles: 6 `StatTile`s in a `repeat(auto-fit, minmax(150px,1fr))` grid; each tile
  value `61% / 64%` with sub `DeltaPill`-style text coloured by "better" direction
  only where unambiguous (throttle share, coasting); others neutral text.
- Heading copy: "Inputs over {Full lap | T6–T8 (2,340–2,980 m)}".
- States: missing channel → tile `unavailableReason` "Throttle was not recorded".

#### 3.2.7 Regions for real data (Overview track card, Sectors, Track Map)

Loader: v2 → `GET /api/v2/session-evidence/sessions/{id}/track-models`; pick
`validated` over `draft`, then highest revision; regions come in the same
response. v1 → `GET /api/v1/track-models` + existing `trackModelLabel` match →
`GET /api/v1/track-models/{model_id}/regions?revision=`. Fixture → `reference`.

Region panel (track card, below the map, reference layout):

- `Selected Region` `Select`: `Full lap` + regions labelled `{label}` with range
  in the option text `T6 · 2,340–2,980 m`; complexes listed first as
  `T6–T8 (complex)`.
- `Distance`: `2,340 m – 2,980 m` / `Full lap · 0 m – 3,337 m`.
- Buttons `Full Lap` (secondary) and `Clear` (muted) as in the reference; Clear
  also clears a brushed range.
- `Quick Jumps`: chips for complexes (`T1-5`, `T6-10` style built from
  `complexId` groups, max 6) and, only for the fixture, `S1 S2 S3` (sector
  distance boundaries are not available for real sessions; do not show them).
- Draft model: `Badge amber "Draft model"` next to "Selected Region" and the
  footnote "Draft regions are approximate."
- No model: replace Select with `EmptyState inline` "No corner regions for this
  circuit." + text button "Select a range on the chart" (switches to Telemetry,
  focuses the brush). Reason line from `RegionSource.reason`.
- Data Quality line: `Track model: Monaco r3 (draft)` / `No track model for this circuit`.

#### 3.2.8 Data Quality tab

```
┌ Lap 28 (target) ─────────────────────┐ ┌ Lap 57 (reference) ──────────────────┐
│ ✓ Valid per the game                  │ │ …                                      │
│ ✓ Start observed  · ⚠ Includes pit lane│ │                                        │
│ Samples 4,812 · Distance 3,330 / 3,337 m (99.8%) │                             │
│ Channel coverage                       │                                        │
│ Speed     ████████████ 100%  1 run     │                                        │
│ Throttle  ████████████ 100%            │                                        │
│ Brake     ███████████░  96%  3 gaps    │                                        │
│ Steering  ████████████ 100%            │                                        │
│ Gear      not recorded                 │                                        │
│ Source: Live session evidence (v2)     │                                        │
└────────────────────────────────────────┘ └────────────────────────────────────────┘
┌ Session data ────────────────────────────────────────────────────────────┐
│ Track model · Lap list completeness (truncated?) · Sector source · Tyre source │
└──────────────────────────────────────────────────────────────────────────┘
```

- Two equal columns, stacked at 1024. Coverage bars are `ProgressBar size="sm"`
  (green ≥ 98%, amber ≥ 80%, red below) with `label="Brake coverage 96%"`.
- Coverage from `LapTrace.coverage` (metres covered / lap length; runs − 1 = gaps).
- Session card rows: `Track model`, `Laps read` ("All laps" / "First 1,200
  attempts only"), `Sector times` (source `Session history` / `Lap data` /
  NotAvailable reason), `Tyre data` (source or reason), `Engine` (reachable).
- Fixture keeps the reference three lines in the Overview card; this tab shows
  the fixture equivalent built from the same structure.

#### 3.2.9 Processing unprocessed telemetry (explicit opt-in)

When a selected lap's trace is `processable`, the empty state reads: title
"Telemetry for Lap 31 has not been processed", body "Processing reads this lap
from the recorded journal. It takes a few seconds and does not change the
recording.", primary `Button` "Process telemetry". Click → `POST
/api/review/sessions/{s}/attempts/{rev}/materialize` (server proxy, token) →
returns a parsed `LapTrace` (request-scoped; not persisted by the backend), the
client stores it in component state for this page view. Busy (409) →
"The telemetry reader is busy. Try again in a moment." with `Try again`.
Success toast "Lap 31 telemetry processed for this view." No bulk processing.

#### 3.2.10 Engineer Questions

Reference anatomy (copy exactly for the fixture): card title, outline-blue
`+ New Question`, blue question bubble right-aligned with time below, green
avatar + "Engineer" + answer paragraph, labelled sections `Scope`, `Evidence`,
`Related Laps`, `Limitations`, footer `Answered` green pill and `Was this
helpful?` with thumb up/down.

Full layout:

```
Engineer Questions                         [History] [+ New Question]
┌ Suggested ─────────────────────────────────────────────┐
│ (Where did I lose time to Lap 57?) (Summarise Lap 28) (How did my braking compare?) │
└────────────────────────────────────────────────────────┘
                    [ Why did I lose time in Turns 6 to 8 on this lap? ]  18:45
(●) Engineer
    You lost 0.318 seconds …
    Scope           Full lap (0 m – 3,337 m)
    Evidence        Speed trace, throttle input, brake input, steering input, time delta
    Related Laps    Target: Lap 28 (1:13.420)
                    Reference: Lap 57 (1:12.842)
    Limitations     • Fuel load and tyre condition were not established.
                    • Regions are distance ranges, not named corners.
    Model           llama3.1:8b · 2.4 s                 (only when a model answered)
    [✓ Answered]                              Was this helpful? [ThumbUp][ThumbDown]
— next turn —
┌ composer ──────────────────────────────────────────────┐
│ Ask about Lap 28 vs Lap 57…                      [Send]│
│ ☐ Process unprocessed telemetry if needed (shown only when needed) │
└────────────────────────────────────────────────────────┘
```

Behaviour:

- Endpoint choice: v2 laps → `POST /api/review/sessions/{s}/ask` body
  `{ question, target: revisionId, reference: revisionId | null,
  allow_materialization: boolean, use_model: runtimeReady }`; v1 laps →
  existing `/api/engineer/ask` (`attempt_summary`, or `region_comparison` when a
  region and a v1 track model are selected). Fixture → canned answer (existing).
- `use_model` is true only when the engineer runtime reports `ready` (read once by
  the loader); otherwise the deterministic measurement answers and the Model row
  reads "Measured answer (no model used)".
- Composer is always visible at the bottom of the card once a question was asked
  (the reference shows the empty state with only the button; "New Question"
  focuses the composer). Enter sends, Shift+Enter newline, max 2,000 chars with a
  counter at 1,800+. Region note under the composer when a region is selected:
  "Answers cover the full lap; the selected region is not sent." (v2 ask has no
  region parameter.)
- `Checkbox` "Process unprocessed telemetry if needed" appears under the composer
  only when the target or reference lap is `processable`; default unchecked; it
  sets `allow_materialization`. Unchecked questions about such laps return the
  backend's unavailable reason, shown with the hint "Tick ‘Process unprocessed
  telemetry’ to include this lap."
- Suggested chips (max 3, `FilterChips` look, buttons): with reference
  `Where did I lose time to Lap {r}?`, `How did my braking compare?`,
  `Summarise Lap {t}`; without reference `Summarise Lap {t}`, `Was Lap {t} valid?`,
  `What limits this lap's data?`. Clicking sends immediately. Shown when the
  thread is empty and after the latest answer.
- Turn states:

| State | Visual | Copy |
| --- | --- | --- |
| pending | avatar + `TypingIndicator`; `Cancel` text button after 3 s | `Measuring Lap 28 against Lap 57…` / `Reading Lap 28…` |
| answered | full anatomy; footer green `Answered` | message from backend |
| partial | anatomy; footer amber `Partial answer` | message + Limitations first |
| unsupported | answer text; footer grey `Not supported` | backend message, plus "Try one of the suggested questions." |
| unavailable | amber `Unavailable` pill; `Try again` | `humanReason(reason)`; e.g. "No eligible reference lap is available." |
| busy (409) | amber; `Try again` | "The engineer is busy with another request. Try again in a moment." |
| timeout (504 / 35 s client) | red; `Try again` | "The engineer took too long to answer. Try again." |
| not authorised (403) | red | "The web app is not allowed to ask questions. Check the engine's control token." |
| offline | red; `Try again` | "The local engine is unavailable." |
| cancelled | grey | "Question cancelled." |

- Answer mapping (`answer.ts › answerView(result, laps)` → `AnswerView`, section 6):
  Scope = `Full lap ({first} m – {last} m)` from `report.distance_m`, or
  `Lap {t} (race lap diagnostic)` for race-lap reports; Evidence = channel names
  from `report.channels` keys + "time delta" when `report.delta_time` exists +
  "braking zones" when present, joined with commas; Related Laps = target and
  reference from `selection` mapped to lap numbers via `revisionId`; Limitations =
  `report.qualifications` through the qualification copy table (below), max 4
  with "+{n} more" disclosure; v1: facts → Evidence lines, warnings → Limitations.
- Qualification copy (unknown codes humanised):
  `fuel_and_tyre_conditions_not_established` "Fuel load and tyre condition were
  not established."; `later_braking_is_not_automatically_better` "Braking later
  is not automatically better."; `numeric_distance_regions_not_named_corners`
  "Regions are distance ranges, not named corners."; `measurement_not_reference_ranking`
  "This is a measurement, not a ranking of laps."; `invalid_or_unknown_game_validity`
  "One of the laps was invalid or its validity is unknown."; `pit_encountered`
  "One of the laps includes the pit lane."; `track_geometry_not_fully_established`
  "Track geometry was not fully established."; `different_lifecycle_epochs`
  "The laps come from different parts of the session."; `acquisition_gap`
  "Telemetry has a gap in one of the laps."; `fuel_load_uncontrolled`,
  `tyre_condition_uncontrolled`, `traffic_uncontrolled` "Fuel, tyres and traffic
  were not controlled."
- Multi-turn: turns stack oldest first; the thread scrolls (max-height = card
  height − header − composer); new answers scroll into view only if the user is
  at the bottom. Each request is independent; a muted line under the first turn:
  "Each question is answered on its own; earlier answers are not used as context."
  Thread persists per `session+target+reference` in `sessionStorage`
  (`race-engineer.thread.{session}.{t}.{r}`, last 20 turns).
- History: `IconButton label="Question history"` (`HistoryIcon`) opens a `Drawer`
  "Question history" listing `question` events for this session from
  `GET /api/engineer/events?session_id=` (newest first: question text, time, lap);
  selecting one shows its related `response` text read-only with the note
  "From the engineer log; evidence sections are not stored." and a `Ask again`
  button.
- Was this helpful?: thumbs are `IconButton`s with `aria-pressed`. Up → pressed
  green, inline "Thanks for the feedback." Down → pressed red and a chip row
  `Inaccurate`, `Not specific`, `Missing data`, `Other` (single select) → "Thanks.
  Your feedback is saved on this computer." Stored in `localStorage`
  `race-engineer.answer-feedback.v1` (cap 200 entries: question, session, laps,
  rating, reason, time). No backend endpoint exists (section 7, Q4).
- Blocked states: fixture session with no backend → existing copy. Real session
  without any lap → button disabled, `title="Select a lap first"`.

#### 3.2.11 Open in External Tool

Keep the reference label and position (header context action). Make it a
`Menu` with a text trigger (`OpenDocumentIcon` + "Open in External Tool" +
chevron) and items:

1. `Lap times (CSV)` — current export, adds `valid`, `pit`, `tyre`, `tyre_age`.
2. `Telemetry for Lap 28 vs 57 (CSV)` — client-side from loaded traces:
   `distance_m, target_speed_kph, target_throttle, …, reference_*, delta_s` on the
   5 m grid. Disabled when traces are not loaded (`disabled` item with reason).
3. `Original capture (.f1ecap)` — v1 sessions with a capture id only, via the
   existing `/api/recording-sources/{capture_id}/download` proxy.

Copy for the menu panel footnote: "Files open in spreadsheet or telemetry tools."

---

### 3.3 Session Library (`/review`)

Files: `SessionLibrary.tsx` (list, filters, pagination), `ImportSessionButton.tsx`
→ rewritten on `SplitButton`, new `imports/ImportProvider.tsx` (client state
machine + polling), `imports/ImportRow.tsx`, `imports/ImportActivityDrawer.tsx`,
`imports/import-state.ts` (pure reducer, tested), `SessionRowMenu.tsx`.

#### 3.3.1 Import flow end to end

Entry points: `Import Session` (opens the file picker, `multiple`, accept
`.f1ecap`), chevron menu, and dropping files anywhere on the list area
(`Dropzone`, max 5 files).

Chevron menu (`SplitButton`, items): `Import capture file (.f1ecap)…`,
`Import from recordings folder…` (opens `ImportActivityDrawer` on the
"Recordings folder" section), `Import activity` (drawer, "Recent imports"
section), divider, `Refresh library`.

State machine per file (`import-state.ts`):

```ts
export type ImportItem = {
  key: string;                       // local uuid
  fileName: string; bytes: number;
  phase:
    | { kind: "uploading"; sent: number }                         // XHR upload progress
    | { kind: "queuing" }
    | { kind: "queued"; jobId: string; position: number | null }
    | { kind: "processing"; jobId: string; percent: number | null; stage: string }  // progress.bytes_read / total_bytes
    | { kind: "complete"; jobId: string; sessionHref: string | null }
    | { kind: "failed"; jobId: string | null; reason: string; retryable: boolean }
    | { kind: "cancelled"; jobId: string | null };
};
```

- Upload uses `XMLHttpRequest` (fetch has no upload progress) to the existing
  `/api/recording-sources/upload`; Cancel during upload → `xhr.abort()` →
  `cancelled`.
- Queue: `POST /api/import-jobs` with `Accept: application/json` (WP1 adds a JSON
  branch to the existing redirecting route; form posts keep redirecting) and
  `queue_if_busy=true`.
- Poll `GET /api/import-jobs/{id}` every 1 s while visible (2 s hidden) until a
  terminal status; queued position from `GET /api/import-jobs/queue`.
- `percent = round(bytes_read / total_bytes * 100)` when both > 0; stage =
  humanised `phase`.
- Complete → `router.refresh()`, toast success "Monaco Grand Prix imported" with
  action `Open session`; the in-flight row is replaced by the real row (matched by
  `result.session_key` when present, else removed after refresh).
- Cancel: queued jobs → `POST /api/import-jobs/{id}/cancel` (JSON mode); running
  jobs cannot be cancelled (backend 409 `job_not_waiting`), so the button is
  hidden while processing. Retry: failed/interrupted/cancelled with a job id →
  `POST /api/import-jobs/{id}/retry`.
- Persistence across reloads: the loader reads `GET /api/v1/recording-sources/page`
  (server-side) and turns sources whose `latest_job_status` is `queued`/`running`
  into in-flight rows, and `failed`/`interrupted` from the last 24 h (by
  `modified_at_utc`) into failed rows.

In-flight rows sit at the top of the list, same row grid as sessions:

```
┌[CaptureFileIcon]┬ monaco-race-0604.f1ecap ─────────┬────┬ Uploading 42% ─────────┬ [Cancel] ┐
│ 133×84 grey     │ Import · 482 MB                   │ —  │ ▓▓▓▓▓▓░░░░░░ (ProgressBar) │          │
└─────────────────┴───────────────────────────────────┴────┴─────────────────────────┴──────────┘
```

Status badge copy and tone: `Uploading 42%` blue, `Queued (2nd)` grey,
`Processing 68%` blue (reference string), `Processing` blue (no percent),
`Import failed` red, `Cancelled` grey. Notes column shows the stage line
(`Decoding packets`, `Building laps`) or the failure reason. Row actions:
uploading/queued `Button muted "Cancel"`; failed `Button "Retry"` + `IconButton
label="Dismiss"`; complete rows disappear.

Failure messages (`reasons.ts`): `recording_upload_content_type_invalid` /
non-`.f1ecap` "Only .f1ecap capture files can be imported.";
`recording_upload_filename_invalid` "The file name could not be used. Rename the
file and try again."; `recording_upload_service_unavailable` / network "The local
engine is unavailable."; `busy` "Another import or recording is running. The
capture was queued." (shown only if queueing failed); `capture_unavailable` "The
uploaded capture could not be found."; `unavailable` "The import could not be
started."; job `failure_reason` humanised, prefixed "Import failed: ".

Dropzone overlay copy: "Drop .f1ecap captures to import". Rejections (toast,
error): "Only .f1ecap capture files can be imported." / "Import up to 5 files at
a time."

`ImportActivityDrawer` (420px): two sections. "Recent imports" = this tab's
items + reload-restored ones, newest first, each with status, time, actions.
"Recordings folder" = `recording-sources/page` items with
`latest_job_status === null` (never imported): name, size, modified date,
`Import` button. Empty: "No captures waiting in the recordings folder."

Disabled split button (engine unreachable): `title` "Start the local engine to
import captures" and the drawer is unavailable; keyboard focus still reaches the
button (use `aria-disabled` instead of `disabled` so the reason is discoverable).

#### 3.3.2 Rows, dates, notes, menu, pagination

- Notes column for real rows (no notes backend): derived highlights,
  12px muted, two lines max: `Best 1:32.104 (lap 7)` then `3 invalid · 2 pit laps`
  (from attempts already read by `evidenceRows`). Empty when no timed laps:
  "No timed laps".
- Dates for v2 rows: `Date not recorded` (faint) `• #3f2a9c1b`. Do not call
  `/summary` per row (follow-up doc). The backend reader caches
  `started_at_utc` from every `/summary` it reads (Overview visits) in
  `review-data/backend.ts` (in-memory, 256 entries); rows with a cached date show
  it normally. Sorting stays by journal recency.
- Row menu (`SessionRowMenu`, `Menu` with `MenuItem`s): `Open overview`,
  `Open lap analysis`, `Compare best laps` (→ `/laps` with the two fastest valid
  laps when ≥ 2 exist), divider, `Copy session ID` (toast "Session ID copied"),
  `Download capture` (v1 rows with a capture). No delete, rename or notes items
  (no backend; section 7, Q3). Unopenable rows keep the existing note.
- Pagination: client-side, 20 rows per page; under the list a centred secondary
  `Button` `Show 20 more` and the count line `Showing 20 of 63 sessions`.
  Filters and search apply before pagination; changing them resets to 20. No
  virtualisation (≤ 500 sessions by the loader's page budget).
- Empty states: engine reachable, zero sessions → `EmptyState size="page"` icon
  `ReviewIcon`, title "No sessions yet", body "Drive with telemetry enabled, or
  import a .f1ecap capture.", action = the Import split button. Filters match
  nothing → `EmptyState card` "No sessions match these filters." + `Button muted`
  "Clear filters". Engine unreachable → fixture rows + the "Reference data"
  banner (3.0.2).
- Bulk actions: none (no mutating session endpoints).

---

### 3.4 Drive Diagnostics (`/drive`)

Files: `DriveDiagnostics.tsx` (layout), `RadioHistory.tsx`, `EventDetails.tsx`,
`radio.ts` (pure: event mapping, outcome, counts, unread), `useRadioFeed.ts`
(polling hook), `SystemStatus.tsx`, `system-rows.ts` (browser + backend rows),
`useReadAloud.ts`. `live-status/map.ts` keeps the engine/telemetry rows.

#### 3.4.1 Radio History (live)

Data: loader reads `GET /api/v2/engineer/events?limit=50` (newest 50,
oldest-first in the response; reverse for display). Client polls
`/api/engineer/events?after={next_after}&limit=50` every 2 s while visible,
prepends new rows, keeps at most 200 (the API has no "before" cursor; older
history is not reachable from the UI, see section 8).

`RadioEvent` mapping (`radio.ts › fromEngineerEvent`):

| Backend | Row label (kind column) | Glyph | Outcome |
| --- | --- | --- | --- |
| `proactive` | `Proactive` green | square | `Played` (spoken by read-aloud in this tab) / `Logged` |
| `question` | `You` blue | user | `Responded` when a `response` with `related_id = id` exists, `Waiting` within 30 s, else `No answer` |
| `response` | `Engineer` dark | flag | `Answered` / `Partial` / `Unavailable` from `detail.status` |
| `issue` | `Issue` red | alert | `Logged` |

Time column `HH:MM` local from `created_at_utc`. Filters: `FilterChips` with the
reference labels `All (24)`, `Questions (6)`, `Responses (6)`, `Proactive (8)`,
`Issues (4)`; counts over loaded events.

Unread: rows with `id > lastSeenId` (initial max id at page load) get a 3px
`--blue` left bar and 600-weight time; the card title gets `Badge blue "3 new"`.
A row becomes read when selected; `Mark all read` text button in the card head
appears with unread > 0. Arrival highlight fades per 3.0.8.

Follow mode: if the newest row is selected when new events arrive, selection
moves to the newest; otherwise selection stays and the list does not scroll.

Read aloud (decision, browser-local): `Toggle label="Read calls aloud"` in the
Radio History head (default off). When on, new `proactive` and `response`
events are spoken with `speakLocalEvidence` from `web/src/lib/local-speech-controller.ts`
using the voice/rate/volume from Settings › Voice. Interruption follows the
backend preference: `allow` → `high` priority cancels current speech; `queue` →
queue; `never` → drop while speaking (row outcome `Not played`).

States:

- Engine unreachable: fixture radio + "Reference data" banner (3.0.2).
- Activity endpoint 503: `EmptyState card` tone warning, title "Radio history is
  unavailable", body "The engineer activity log could not be read."
- No events: `EmptyState card` icon `MicrophoneIcon`, title "No radio yet", body
  "Engineer calls and your questions appear here as they happen."
- Filter empty: inline "No {questions|responses|proactive messages|issues} yet."
- Loading (client-only refetch after reconnect): 6 bone rows.

#### 3.4.2 Event Details anatomy (live)

Same sections and order as the reference: header (glyph, title, `HH:MM:SS`,
outcome badge), `Message`, `Trigger`, `Evidence / Context` (bullets), `Output`,
`Timings`, `Evidence Limitations` (with `InfoIcon`).

| Section | Proactive (`live_rules`) | Question | Response | Issue |
| --- | --- | --- | --- | --- |
| Title | Proactive Message | Driver Question | Engineer Response | Issue |
| Trigger | rule copy: `lap_invalidated` "Lap invalidated by the game"; `pit_lap` "Lap included a pit stop"; `first_timed_lap` "First timed lap of the session"; `personal_best` "New personal best"; `lap_completed` "Lap completed"; telemetry `connected` "Telemetry connected", `recovery` "Telemetry recovered", `failed` "Telemetry listener stopped", other "Telemetry state changed" | "Asked from Lap Analysis" (`v1_ask`/`v2_ask`) | "Answer to: {question text}" | `detail.reason` humanised |
| Evidence / Context | `Lap {lap}`, `Lap time {t}`, `Delta to best {Δ}` / `Previous best {t}`, `Topic: {topic}`, `Priority: {p}` | `Session #{id}`, `Lap {lap}` | `Route: {route}`, `Focus: {focus}`, `Status: {status}` | `HTTP {status}` when present |
| Output | "Spoken on this computer" / "Shown in Radio History" | "Sent to the engineer" | same as proactive | "Logged for review" |
| Timings | "Logged at {HH:MM:SS}" | "Logged at …" | "Answered {n.n} s after the question" (created_at difference) | "Logged at …" |
| Limitations | lap rules: "Lap results come from published lap attempts; pit messages describe the completed lap, not the moment of pit entry." | — | `status === "partial"`: "The answer was partial: {reason}." | — |

Sections with no content are omitted (no empty headings). Below 1024px the
details open in a `Drawer` titled with the event title.

#### 3.4.3 System Status rows (honest sources)

| Row | Source | States (status · detail) |
| --- | --- | --- |
| Engineer Runtime | runtime poll (existing) | existing |
| Telemetry (Game Data) | acquisition poll (existing) | existing |
| Microphone Input | browser `navigator.permissions.query({name:"microphone"})` + `enumerateDevices` | `Allowed` green · device label or "Default microphone"; `Not set up` grey · "Allow in Settings"; `Blocked` red · "Blocked by the browser"; `Unsupported` grey · "This browser has no microphone access" |
| Speech Recognition | `GET /api/engineer/transcribe` (poll 30 s) | `Ready` green · "Local · CPU"; `Unavailable` amber · humanised reason; unreachable grey |
| Question Processing | runtime (existing) | existing |
| Audio Output | `speechSynthesis` local voice discovery (`watchForLocalSpeechVoice`) | `Available` green · voice name; `Speaking` green · voice name (while read-aloud plays); `No local voice` grey · "Install a system voice"; `Unsupported` grey |
| Proactive Assistance | preferences (loader) | `Enabled` green · "{n} of 8 topics"; `Off` grey · "All topics are off"; `Unknown` grey · "Preferences unreadable" |

Browser-derived rows render `Checking…` grey on the server and first client
render (hydration-safe), then resolve in an effect. The card gets one footnote:
"Microphone and audio are this computer's devices, used by the browser."

#### 3.4.4 Current Activity and waveform

| State | Condition | Tone | Text | Waveform |
| --- | --- | --- | --- | --- |
| Speaking | read-aloud speaking | green | "Speaking: {first 40 chars}…" | animated red bars |
| Answering | question event < 30 s old without response | blue | "Answering your question…" | `TypingIndicator` |
| Following | telemetry `receiving`, runtime ready | green | "Following live telemetry" | static bars at 40% opacity |
| Waiting | `listening`/`starting` | amber | "Waiting for the game" | flat line |
| Stale | `stale` | amber | "Telemetry stale" | flat line |
| Idle | no live session | grey | "Idle — no live session" | flat grey line |
| Unavailable | engine unreachable | grey | "Unavailable" | none |

`WaveformIcon` gains `mode: "animated" | "static" | "flat"` and `tone`. Bars
animate (scaleY) only under no-preference motion.

#### 3.4.5 Session Context

- Live session: as today plus `Lap` and `Track` rows are omitted (no source),
  pill red "Live game data".
- No live but a past session: subtitle `{Type} • Last session`, pill grey
  "No live data", footnote link "Open in Review" → `/review/{id}`.
- No session at all: card body `EmptyState card` with a faint generic
  `TrackMap` as icon (decorative), title "No session yet", body "Start a session
  in F1 with UDP telemetry sent to this computer on port 20777.", action `Link`
  "Connection settings" → `/settings?section=connections`.
- Engine unreachable: reference context + banner.

---

### 3.5 Settings (`/settings`)

`?section=assistance|voice|connections|data|advanced` mirrors the tab
(`Tabs` wired to panels). The "Current Configuration & Status" card stays on
every tab (reference).

#### 3.5.1 Preference persistence (Assistance tab)

Mapping (labels exactly as the reference; keys from
`f1_engineer/analysis/engineer_preferences.py`):

| Label | Key |
| --- | --- |
| Tyre management / Track conditions / Race strategy / Driving technique / Gap information / Rule reminders / Setup suggestions / General information | `topics.tyre_management`, `track_conditions`, `race_strategy`, `driving_technique`, `gap_information`, `rule_reminders`, `setup_suggestions`, `general_information` |
| Verbosity Concise / Balanced / Detailed | `verbosity: concise / balanced / detailed` |
| Interruption Allow interruptions / Queue until finished / Never interrupt | `interruption: allow / queue / never` |
| Analysis Depth Quick / Standard / In-depth | `depth: quick / standard / in_depth` |
| Include Interpretations | `interpretations` |
| Driving consistency / Comparative analysis / Corner analysis / Setup impact / Tyre performance / Data quality notes | `focus_areas.driving_consistency`, `comparative_analysis`, `corner_analysis`, `setup_impact`, `tyre_performance`, `data_quality_notes` |

Flow: loader reads `GET /api/v1/engineer/preferences` (server) → `{ doc,
revision }`. Apply → `PUT /api/engineer/preferences` with the six keys only
(the backend rejects unknown keys) and `If-Match: "{revision}"`.

Apply row (reference position and copy for the clean state):

| State | Button | Note (role=status) |
| --- | --- | --- |
| clean, loaded | `Apply Changes` disabled | "Most settings apply immediately. Some connection settings require a restart." (reference) |
| dirty | `Apply Changes` enabled + `Button muted "Discard"` | "You have unsaved changes." |
| saving | `Applying…` disabled, `aria-busy` | "Saving to the engine…" |
| saved | `Changes Applied` disabled (2 s) | "Saved to the engine. The next engineer call uses these settings." |
| conflict 409 | enabled | `InlineAlert warning` above the row: title "Settings changed elsewhere", body "Another window saved revision {n} while you were editing.", actions `Load latest` (discard local, adopt server doc) and `Overwrite` (`ConfirmDialog` "Replace the saved settings with yours?" → PUT with `If-Match: "{n}"`) |
| invalid 422 | enabled | red: "The engine rejected these settings ({field}). Reload the page and try again." |
| forbidden 403 | disabled | red: "The web app is not allowed to change engine settings. Check the engine's control token." |
| unreadable (GET 503) | enabled | `InlineAlert error` "Saved settings could not be read ({reason}). Defaults are shown; applying replaces the saved file." |
| unreachable | all controls disabled | `InlineAlert info` "Settings cannot be loaded because the local engine is not reachable." |

- Migration: if `localStorage["race-engineer.settings.v1"]` exists and differs from
  the server doc, show once `InlineAlert info` "Settings from an earlier version
  are saved in this browser." with `Use these settings` (fills the form, dirty)
  and `Dismiss` (removes the key).
- Leaving the tab or page while dirty: `beforeunload` prompt; switching tabs keeps
  the draft (state lives above the tab panels).
- Success toast only when the user switches tab before the save completes.

#### 3.5.2 Voice & Controls (redesign)

Remove `Question Activation` and the invented voices "Calm/Energetic" (no
implementation exists). Two columns:

```
┌ Microphone ───────────────────────────┐ ┌ Engineer Voice ─────────────────────┐
│ Input device  [Shure MV7        ▾]    │ │ Voice   [Microsoft Aria (local) ▾]   │
│ Level  ▮▮▮▮▮▮▮▯▯▯▯▯▯▯▯▯▯▯▯▯           │ │ Rate    [—●————] 1.0×                │
│ [Test microphone]  Speak for up to 5 s│ │ Volume  [————●—] 90%                 │
│ We heard: "box box this lap"  (0.8 s) │ │ [▶ Play sample]                      │
│ Speech recognition: Ready · Local CPU │ │ ☐ Read engineer calls aloud on Drive │
└───────────────────────────────────────┘ │ Interruptions follow Assistance ›    │
                                          └──────────────────────────────────────┘
```

- Microphone states: not requested → `Button "Allow microphone access"` and text
  "The browser asks for permission once."; denied → `InlineAlert error`
  "Microphone access is blocked. Allow it in the browser's site settings, then
  reload."; no devices → "No microphone was found."; ready → device `Select`
  (labels from `enumerateDevices`), live `LevelMeter` (20 segments, WebAudio
  `AnalyserNode` RMS, `role="meter"` `aria-valuenow`), `Test microphone`.
- Test: records up to 5 s at 16 kHz → `buildCanonicalPcmWav` from
  `web/src/lib/local-speech-draft.ts` → `POST /api/engineer/transcribe` →
  "We heard: “{transcript}”" + timing. Recording state: red `StatusGlyph dot` +
  `Recording… 3 s` + `Stop`; transcribing: `Transcribing…`; errors: humanised
  speech reason, e.g. "Speech recognition is not set up on the engine."
- Voice: `Select` of `speechSynthesis` local voices (`localService === true`),
  rate 0.8–1.4 step 0.1, volume 0–100%; persisted with
  `applyLocalVoicePreferences` (`web/src/lib/local-voice-preferences.ts`; extend
  with `voiceURI` and `readAloud`). `Play sample` speaks "Lap 28, one thirteen
  four two zero, plus zero point five seven eight to your best." Saved
  immediately; note "Saved in this browser." No Apply button on this tab.
- Unsupported browser APIs: the card shows `EmptyState inline` "This browser does
  not support {microphone access|speech output}."

#### 3.5.3 Connections

- Game Telemetry card: existing rows + `Button "Test connection"`: watches
  `received` from the acquisition poll for 10 s; result `InlineAlert success`
  "Receiving data: 1,204 packets in 10 s." or `warning` "No packets in 10 s. In
  F1, set UDP Telemetry On, IP {host}, port {port}, send rate 20 Hz or higher."
  Progress: `ProgressBar` over the 10 s.
- Engineer Runtime card: existing.
- Speech Recognition card (new): from `/api/engineer/transcribe` GET: status,
  runtime version, model, placement CPU, "Audio is not stored" (from
  `audio_persisted: false`).
- Local Engine card: existing.

#### 3.5.4 Data & Storage

```
┌ Storage ────────────────────────────────┐ ┌ Captures ───────────────────────────┐
│ (donut 120px) 42.6 GB used              │ │ Name            Size   Imported  ⬇  │
│               128 GB available · 512 GB │ │ monaco-0604…    482 MB Complete  [⬇]│
│ ■ Session database   1.2 GB  ▓▓░░        │ │ …  (DataTable, 10 rows, Show more)   │
│ ■ Recorded captures 38.9 GB  ▓▓▓▓▓▓▓     │ └─────────────────────────────────────┘
│ ■ Recorder staging   0 B                 │ ┌ Retention ───────────────────────────┐
│ ■ Imported traces    2.5 GB  ▓░          │ │ Keep everything. Nothing is deleted  │
│ Measured 18:42 · [↻ Refresh]             │ │ automatically.                      │
└──────────────────────────────────────────┘ └─────────────────────────────────────┘
```

- `StorageDonut` (settings-local SVG, 4 scopes in `--blue`, `--purple`,
  `--amber`, `--green` at 100/70% tints, unmeasured scope hatched grey);
  `role="img"` label lists the scopes. Legend rows keep the current bars.
- Refresh → client `GET /api/storage/usage`.
- Captures: `GET /api/v1/recording-sources/page` via the new proxy; download via
  the existing proxy; status column = `latest_job_status` badge; `Import` for
  never-imported captures (same handler as the Library drawer).
- Retention: read-only (no backend). Copy: "Keep everything. Nothing is deleted
  automatically. Recordings stay in the recordings folder until you remove them."
  Cleanup actions are not offered (no delete endpoints; section 7, Q3).

#### 3.5.5 Advanced Tools

- Track Models manager (`DataTable`): Circuit, Model, Revision, Regions
  (`region_count`), Status (`Badge green "Reviewed"` for `validated`, `amber
  "Draft"`), Origin (`Packaged` / `Local draft` / `Reviewed bundle` /
  `Unknown`), Length (`3,337 m`). Row action `View regions` → `Drawer` listing
  regions (label, range, apex, direction) from the regions proxy. Empty: "No
  track models are installed." Footnote: "Draft models give approximate distance
  ranges; they are not reviewed corner definitions."
- Diagnostic Tools card: keep the current CLI command list (it is accurate).
- **Optional** "Diagnostic recording & replay" (collapsed disclosure, `Badge grey
  "Optional"`; only built if Q1 is approved): recording group status
  (`/api/recording-groups/current`) with Start/Pause/Resume/Stop buttons
  (`ConfirmDialog` for Stop), replay status (`/api/replays/current`) with
  Pause/Resume/Stop and the pill menu from 3.0.3. All existing proxies.

#### 3.5.6 Current Configuration & Status tiles

Exact reference layout (five tiles). Sources: Microphone and Audio Output from
the browser rows in 3.4.3 (`Ready` / `Not set up` / `Blocked`; never the
reference "Shure MV7" on a real machine), Game Connection (existing), Storage
(existing), Track Models: `{n} models`, badge `{k} reviewed · {d} draft` or
`None installed`; icon = `TrackMap` of the current session's circuit (generic
outline when none) instead of the hard-coded Monaco outline.

---

## 4. Reference guidance and overrides

Copy exactly (fixture and real where data exists): every visible label in the
five screens; tab names and order; Lap Analysis header and picker anatomy;
Engineer Questions anatomy and footer; Radio History filter labels, column
order and Event Details section order; Settings Assistance layout and Apply
note; red split Import button; status badge strings `Complete`, `Partial`,
`Processing 68%`, `Unavailable`; Lap Selection info box.

The reference gives no guidance for: Setup & Conditions, Laps/Stints/Events
tabs, Lap Analysis tabs other than Overview, import progress, empty states, all
Settings tabs except Assistance. Designs above reuse the nearest drawn pattern
(Overview grid for Overview tabs, Radio list for timelines, Settings card+kv for
config) so they read as the same product.

Overrides of the current implementation:

1. Library shows fixtures when the engine answers with zero sessions → empty state.
2. Library pill falls back to green "Telemetry live" when unreachable → red.
3. Compare "Time Delta/Distance", Overview "Single/Compare" use `Tabs` → `Segmented`.
4. Overview and Lap Analysis tabs lack panels → wire `aria-controls`.
5. v1 hero shows permanently unknowable fields → v2 field set.
6. Settings Voice tab invents options and device names → real browser devices.
7. Settings Track Models tile hard-codes Monaco and "v2.1 Up to date" → catalog.
8. `ImportSessionButton` hand-rolled `role="menu"` → `SplitButton` on `Menu`.
9. Engineer Questions blocked for v2 laps → v2 ask proxy.
10. Reference arithmetic stays corrected (+0.578, not +0.612); do not "fix" back.
11. "Open in External Tool" stays labelled per the reference but becomes an
    explicit export menu.

---

## 5. Implementation sequence

Acceptance for every package, in `web/`:

- `npx tsc --noEmit -p .`
- all 33 existing `npm run test:*` scripts plus the package's new script(s)
- `npm run build`
- screenshots at 1440, 1280, 1024, 768 of each changed view with the fixture
  session (`/review/monaco-2024-race`) and one real v2 session, attached to the
  hand-off; reference widths (1010, 968, 1064, 1982, 1098) still match for
  overview tabs that the reference draws.

`web/package.json` is integrator-owned: each package lists the script line to
add. New tests follow the existing transpile pattern
(`scripts/lap-analysis.test.mjs` for pure modules, `scripts/ui-routes.test.mjs`
for server rendering, `scripts/recording-upload-proxy.test.mjs` for Route
Handlers with a stubbed upstream).

### Phase 0 — shared prerequisites (2 agents in parallel)

**WP0 Foundation primitives** (owner: `web/src/features/ui-foundation/**`,
`web/src/app/globals.css`, `web/src/features/route-states/**`)
- Create: `states.tsx` (NotAvailable, EmptyState, InlineAlert), `DataTable.tsx`
  + `.module.css`, `Segmented.tsx`, `FilterChips.tsx`, `ProgressBar.tsx`,
  `StatTile.tsx`, `Timeline.tsx`, `Drawer.tsx` (Drawer, ConfirmDialog),
  `Toast.tsx`, `Dropzone.tsx`, `SplitButton.tsx`, `LapStepper.tsx`,
  `primitives-extra.module.css`.
- Edit: `icons.tsx` (icons in 2.4 + `SwapIcon`, `WaveformIcon` modes),
  `primitives.tsx` (`Menu` arrow keys), `AppShell.tsx` (mount `ToastProvider`),
  `globals.css` (tokens 2.1).
- Test: `scripts/ui-primitives.test.mjs` → `"test:ui-primitives": "node --test scripts/ui-primitives.test.mjs"`:
  ARIA roles/states for each primitive, Segmented radiogroup keys, DataTable
  `aria-sort`, Toast roles, token equality with `TARGET_COLOR`/`REFERENCE_COLOR`,
  no raw hex in new modules.
- Accept: existing `ui-foundation`/`ui-routes` tests pass unchanged.

**WP1 Data readers and proxy routes** (owner: `web/src/features/review-data/**`,
`web/src/lib/proxy-json.ts`, new `web/src/app/api/**` files listed in
section 6, and the JSON branch in `api/import-jobs/**`)
- Create: `review-data/enrichment.ts`, `summary.ts`, `regions.ts`
  (types + parsers), `reasons.ts`, `engineer-events.ts`, `preferences.ts`
  (doc ↔ form mapping, `If-Match` helpers), `lib/proxy-json.ts`
  (`proxyGet(path, { timeoutMs })`, `proxyMutation(request, path, init)` with
  `isTrustedLocalMutation` + token + no-store headers + bounded body).
- Edit: `review-data/backend.ts` (readers `fetchLapEnrichment`, `fetchSummary`,
  `fetchSessionTrackModels`, `fetchTrackModels`, `fetchRegions`,
  `fetchEngineerEvents`, `fetchPreferences`, `fetchRecordingSources`, summary
  date cache); `api/import-jobs/route.ts`, `[job_id]/cancel`, `[job_id]/retry`
  (JSON response when `Accept: application/json`).
- Tests: `scripts/review-data-backend-ui.test.mjs` (parsers with captured
  payload shapes: laps/stints, summary events/weather/time_reason, regions,
  events paging, preferences round trip) and `scripts/ui-proxy-routes.test.mjs`
  (403 without same-origin, token only on mutations, 409 passthrough with ETag,
  JSON import branch, redirect branch unchanged).
- Accept: `test:import-queue`, upload/download proxy tests unchanged.

### Phase 1 — five feature lanes in parallel (after WP0 and WP1)

Within a lane, packages are sequential; lanes never edit each other's files.

**Lane A — Session Overview** (`web/src/features/session-overview/**`,
`web/src/app/review/[session]/page.tsx`)
- **WP2 Overview tabs**: split files (3.1), `overview-data.ts`, Laps, Stints,
  Events, Setup views, deferred sections, load.ts enrichment/summary/setup.
  Test `scripts/session-overview.test.mjs` (`test:session-overview`): stint
  stats and degradation, event merge/categories, derived summary, hero field set,
  laps filters/sort. Accept: tabs render panels with ids; v1 hero has no
  Build/Assists/Damage.
- **WP3 Lap Selection compare**: `LapSelection.tsx` with `LapStepper`, swap,
  URL, compare markers on chart. Extend `session-overview.test.mjs`. Accept:
  compare link carries both ids; Segmented semantics.

**Lane B — Lap Analysis** (`web/src/features/lap-analysis/**`,
`web/src/app/review/[session]/laps/page.tsx`)
- **WP4 Views and regions**: first commit `refactor: split lap analysis views`
  (pure move), then Telemetry/Sectors/Track Map/Inputs/Quality views, `delta.ts`,
  `regions.ts`, `inputs-stats.ts`, trace coverage/path/gear parsing, region
  loader, process-telemetry action. Test `scripts/lap-analysis-views.test.mjs`
  (`test:lap-analysis-views`): delta integration and normalisation, corner
  metrics on synthetic traces, input stats, coverage, region model pick
  (validated over draft). Accept: existing `test:lap-analysis` passes.
- **WP5 Engineer Questions and export**: `EngineerQuestions.tsx`, `answer.ts`,
  `ExternalToolMenu.tsx`, thread persistence, history drawer, feedback store.
  Test `scripts/engineer-answer.test.mjs` (`test:engineer-answer`): v2 and v1
  result mapping, qualification copy, error-state mapping per HTTP status, CSV
  telemetry export columns. Accept: v2 laps can ask; fixture answer unchanged.

**Lane C — Session Library** (`web/src/features/session-library/**`)
- **WP6 Import flow**: `imports/*`, SplitButton, Dropzone, drawer, XHR upload,
  polling, restore from recording sources. Test `scripts/library-import.test.mjs`
  (`test:library-import`): reducer transitions incl. cancel/retry/failed reasons,
  percent from progress, restore mapping.
- **WP7 Rows and pagination**: highlights, dates cache use, row menu,
  pagination, empty states, unreachable banner, pill fallback fix. Extend
  `library-import.test.mjs` with load.ts cases (zero sessions → empty, not fixture).

**Lane D — Drive Diagnostics** (`web/src/features/drive-diagnostics/**`,
`web/src/features/live-status/map.ts`)
- **WP8 Radio history and details**: `radio.ts`, `useRadioFeed.ts`,
  `RadioHistory.tsx`, `EventDetails.tsx`, unread/follow, read-aloud.
  Test `scripts/drive-radio.test.mjs` (`test:drive-radio`): event mapping,
  outcomes (question ↔ response pairing), counts, unread, trigger copy.
- **WP9 System status and context**: `system-rows.ts`, browser rows,
  speech status, proactive row, activity states, waveform modes, context empty
  states. Extend `drive-radio.test.mjs` and `live-status.test.mjs` cases.

**Lane E — Settings** (`web/src/features/settings/**`,
`web/src/app/settings/page.tsx`)
- **WP10 Preferences persistence**: server load, PUT flow, conflict/error
  states, migration banner, `?section=`. Extend `scripts/settings.test.mjs`:
  label ↔ key mapping, dirty detection, 409/422/403/503 view states.
- **WP11 Other tabs**: Voice (mic, meter, test, voice preview), Connections test
  + speech card, Data & Storage donut + captures, Advanced track models manager,
  status tiles. New `scripts/settings-tabs.test.mjs` (`test:settings-tabs`):
  donut shares, track model table mapping, tile sources, connection-test result
  text. **WP11b (optional, gated on Q1)**: diagnostic recording/replay section
  and replay pill menu.

### Phase 2 — integration (1 agent)

**WP12 Cross-cutting** (`AppShell.tsx`, `ShellParts.tsx`, `LiveShell.tsx`,
`route-states/Skeleton.tsx`, `package.json`): pill/footer states (3.0.3/3.0.4),
skeleton variants for new tabs, responsive pass at the four widths, keyboard and
screen-reader audit (axe in the browser), screenshots, `package.json` scripts.
Accept: full validation list above; manual checks of focus return, Escape in
drawers/menus, reduced motion.

Shared prerequisites flagged: WP0 before any lane UI; WP1 before lanes A, B, C,
D (events), E (preferences, track models). Lanes can start their pure helpers
(`overview-data.ts`, `delta.ts`, `radio.ts`, `import-state.ts`) in parallel with
Phase 0 because those have no primitive dependencies.

---

## 6. Backend and proxy dependencies

All new routes use `web/src/lib/proxy-json.ts`. Reads check
`isTrustedLocalRead`; mutations check `isTrustedLocalMutation` and forward with
the control token (`forwardLocalRequest(path, init, true)`), server-side only.
Responses `Cache-Control: no-store`.

| Area | Web route or loader (new unless noted) | Method | Backend | Token |
| --- | --- | --- | --- | --- |
| Overview laps, stints; Lap Analysis sectors | loaders via `backend.ts`; `api/review/sessions/[session]/laps/route.ts` (deferred) | GET | `/api/v2/session-evidence/sessions/{id}/laps` | no |
| Overview summary, events, setup conditions, dates | loader; `api/review/sessions/[session]/summary/route.ts` | GET | `/api/v2/session-evidence/sessions/{id}/summary` | no |
| v1 car setup | loader (existing `fetchLaps`) | GET | `/api/v1/laps` | no |
| Regions (v2) | loader; `api/review/sessions/[session]/track-models/route.ts` | GET | `/api/v2/session-evidence/sessions/{id}/track-models` | no |
| Regions (v1), Settings drawer | `api/track-models/[model_id]/regions/route.ts` | GET | `/api/v1/track-models/{id}/regions?revision=` | no |
| Track model list | loader + `api/track-models/route.ts` | GET | `/api/v1/track-models` | no |
| Process telemetry | `api/review/sessions/[session]/attempts/[revision]/materialize/route.ts` | POST | `/api/v2/session-evidence/sessions/{id}/attempts/{rev}/materialize` | **yes** |
| Engineer questions (v2) | `api/review/sessions/[session]/ask/route.ts` (deadline 35 s; cancellation propagates via `request.signal`) | POST | `/api/v2/session-evidence/sessions/{id}/ask` | **yes** |
| Engineer questions (v1) | existing `api/engineer/ask` | POST | `/api/v1/engineer/ask` | **yes** (existing) |
| Radio history, question history | loader + `api/engineer/events/route.ts` (`after`, `limit` ≤ 200, `session_id`) | GET | `/api/v2/engineer/events` | no |
| Preferences | loader + `api/engineer/preferences/route.ts` | GET, PUT | `/api/v1/engineer/preferences` (PUT forwards `If-Match`, returns `ETag`) | PUT **yes** |
| Speech status, mic test | existing `api/engineer/transcribe` | GET, POST | `/api/v1/engineer/transcribe` | POST existing |
| Import upload | existing `api/recording-sources/upload` | POST | `/api/v1/recording-sources/upload` | **yes** (existing) |
| Import queue/cancel/retry | existing routes + JSON branch | POST | `/api/v1/import-jobs`, `/{id}/cancel`, `/{id}/retry` | **yes** (existing) |
| Import status | existing `api/import-jobs/[job_id]`, `queue` | GET | `/api/v1/import-jobs/{id}`, `/queue` | no |
| Captures list (Library restore, Settings) | loader + `api/recording-sources/page/route.ts` | GET | `/api/v1/recording-sources/page` | no |
| Capture download | existing | GET | `/api/v1/recording-sources/{id}/download` | existing |
| Storage refresh | existing `api/storage/usage` | GET | `/api/v1/storage/usage` | no |
| Optional recording/replay | existing `api/recording-groups/**`, `api/replays/**` | GET/POST | existing | existing |

New TypeScript models (in `web/src/features/review-data/`):

```ts
// enrichment.ts
export type TyreInfo = { visualCompound: number; actualCompound: number; visualLabel: string | null;
  actualLabel: string | null; ageLaps: number | null; source: "car_status" | "session_history_stints" };
export type EnrichedLap = { revisionId: string; lapNumber: number | null; attemptNumber: number | null;
  lapTimeMs: number | null; disposition: string; superseded: boolean; valid: boolean; gameValid: boolean | null;
  pit: boolean; startObserved: boolean | null; sectorsMs: [number, number, number] | null;
  sectorsSource: "session_history" | "lap_data" | null; sectorsReason: string | null;
  tyre: TyreInfo | null; tyreReason: string | null };
export type EnrichedStint = { index: number; visualLabel: string | null; actualLabel: string | null;
  startLap: number; endLap: number; lapCount: number; startAge: number | null; endAge: number | null };
export type LapEnrichment = { laps: EnrichedLap[]; stints: EnrichedStint[]; truncated: boolean; truncation: string | null };

// summary.ts
export type SummaryEvent = { kind: string; label: string; sessionTimeS: number | null; lap: number | null;
  carIndex: number | null; isPlayer: boolean; code: string | null; detail: Record<string, unknown> };
export type WeatherPoint = { sessionTimeS: number | null; weather: string | null;
  trackTemperatureC: number | null; airTemperatureC: number | null };
export type SessionSummary = { startedAtUtc: string | null; endedAtUtc: string | null; lastObservedAtUtc: string | null;
  timeReason: string | null; durationS: number | null; scheduledDurationS: number | null; totalLaps: number | null;
  pitStopCount: number | null; lastLapNumber: number | null; weather: WeatherPoint[]; events: SummaryEvent[];
  totals: { listed: number; observed: number; omittedMinor: number; omittedBudget: number; truncated: boolean };
  scan: { complete: boolean; stopReason: string | null } };

// regions.ts
export type TrackRegion = { id: string; label: string; startM: number; endM: number; apexM: number | null;
  direction: string | null; complexId: string | null };
export type RegionModel = { modelId: string; revision: number; trackName: string; validation: "draft" | "validated";
  origin: string | null; regionCount: number; regions: TrackRegion[] };

// engineer-events.ts
export type EngineerEvent = { id: number; createdAtUtc: string; kind: "proactive" | "question" | "response" | "issue";
  topic: string | null; text: string; sessionId: string | null; lap: number | null;
  source: "v1_ask" | "v2_ask" | "live_rules" | "system"; relatedId: number | null;
  priority: "low" | "normal" | "high"; detail: Record<string, unknown> | null };
export type EngineerEventPage = { events: EngineerEvent[]; nextAfter: number };

// preferences.ts
export type PreferencesDoc = { topics: Record<TopicKey, boolean>; verbosity: "concise" | "balanced" | "detailed";
  interruption: "allow" | "queue" | "never"; depth: "quick" | "standard" | "in_depth"; interpretations: boolean;
  focus_areas: Record<FocusKey, boolean> };
export type StoredPreferences = { doc: PreferencesDoc; revision: number; schemaVersion: number };
export type PreferencesSaveResult =
  | { ok: true; stored: StoredPreferences }
  | { ok: false; kind: "conflict"; currentRevision: number }
  | { ok: false; kind: "invalid"; field: string | null; reason: string }
  | { ok: false; kind: "forbidden" | "unavailable" | "unreachable"; reason: string };

// lap-analysis/answer.ts
export type AnswerView = { status: "answered" | "partial" | "unsupported" | "unavailable"; message: string;
  scope: string[]; evidence: string[]; relatedLaps: Array<{ role: "Target" | "Reference"; lap: number; time: string }>;
  limitations: string[]; model: { name: string; latencyMs: number | null } | null; reason: string | null };
```

Field notes verified in the backend: summary events carry `kind, label,
session_time_s, lap, car_index, is_player, detail, code` (labels are produced
with a `{car}` template and finalised in `_finalize_events`; the parser falls
back to "A driver" if a placeholder survives). The activity endpoint returns the
newest `limit` events without `after` and oldest-first pages with it.
Preferences PUT accepts exactly the six keys; `If-Match` accepts `"7"` or `7`.

---

## 7. Open questions (with defaults so work is not blocked)

1. **Diagnostic recording/replay in Settings?** Default: not built; WP11b stays
   parked, CLI guidance remains.
2. **Read engineer calls aloud in the browser?** Default: yes, opt-in toggle on
   Drive, off by default, browser-local (3.4.1). It is the only way the
   Interruption preference and the Audio Output row become real.
3. **Session delete/rename/notes and retention?** Default: not offered (no
   endpoints); Library notes column shows derived highlights; Retention is
   read-only. Revisit when the backend adds mutation endpoints.
4. **Answer feedback storage?** Default: local only (`localStorage`), with copy
   saying so. A backend feedback log can be added later without UI change.
5. **Model use for recorded-session questions?** Default: `use_model` when the
   runtime reports `ready`, otherwise measured answers.

---

## 8. Risks and out of scope

Risks:

- **Backend load from loaders.** `/laps` and `/summary` scan the journal and can
  answer 409; the 1.5 s server budget plus deferred client fetch keeps pages
  fast, but repeated tab switches must reuse the 10 s cache in `backend.ts`.
  The Library already reads attempts for every session (`evidenceRows`): with
  500 sessions this is up to 6,000 requests on a cold cache. WP7 should cap
  attempt reads to the first rendered page and load highlights for the rest on
  "Show more".
- **Single trace reader.** The backend admits one trace read; Telemetry and
  Inputs must not refetch traces per tab. Traces stay in client state keyed by
  revision; materialize shares the same admission and can return 409.
- **Derived delta accuracy.** Speed-integrated delta is an estimate; it is
  normalised to official lap times and labelled "derived from speed".
- **v2 sample positions unverified.** Measured track lines depend on the v2
  sample shape; schematic fallback is the default until verified.
- **Schematic map positions** are approximate (hand-drawn outlines, not to
  scale); copy says so.
- **Activity history depth.** No "before" cursor: only the newest 200 events are
  reachable in Radio History.
- **Browser APIs.** Microphone labels require permission; `speechSynthesis`
  voices vary per OS; Safari lacks `permissions.query` for microphone (treat as
  `Not set up`).
- **Merge pressure on shared files.** `backend.ts` and `package.json` are owned by
  WP1/WP12 only; lanes request changes instead of editing.

Out of scope:

- Dark mode (3.0.7), internationalisation, phone layouts below 600 px beyond the
  existing rules.
- Push-to-talk questions from the Drive screen and any voice activation.
- Authoring or editing track models (draft build proxy exists but is not
  surfaced), reviewed corner names, coaching or ranking claims.
- Session deletion, renaming, notes, retention enforcement and storage cleanup.
- Backend changes of any kind; production deployment; real-game acceptance.
