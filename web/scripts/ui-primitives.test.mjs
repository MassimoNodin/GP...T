import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, posix } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const typescript = require("typescript");
const { createElement } = require("react");
const reactServer = await import(pathToFileURL(require.resolve("react-dom/server")).href);
const jsxRuntimeUrl = pathToFileURL(require.resolve("react/jsx-runtime")).href;
const reactUrl = pathToFileURL(require.resolve("react")).href;
const sourceRootUrl = new URL("../src/", import.meta.url);
const sourceRoot = fileURLToPath(sourceRootUrl);

const render = (element) => reactServer.renderToStaticMarkup(element);

function transpile(source, fileName) {
  return typescript.transpileModule(source, {
    fileName,
    compilerOptions: {
      module: typescript.ModuleKind.ESNext,
      target: typescript.ScriptTarget.ES2022,
      jsx: typescript.JsxEmit.ReactJSX,
    },
  }).outputText;
}

// Transpile a module and everything it imports (relative and "@/" imports) into
// a temp dir, stubbing react, next/link and CSS modules so Node can load them.
const workDir = await mkdtemp(join(tmpdir(), "ui-primitives-"));
async function emit(name, code) {
  const path = join(workDir, name);
  await writeFile(path, code);
  return pathToFileURL(path).href;
}

const reactShim = await emit(
  "react.mjs",
  `import React from ${JSON.stringify(reactUrl)};
export default React;
export const { Fragment, createContext, createElement, useCallback, useContext, useEffect, useId, useMemo, useRef, useState } = React;
`,
);
const cssStub = await emit("css.mjs", "export default new Proxy({}, { get: (_, key) => String(key) });\n");
const linkStub = await emit(
  "link.mjs",
  `import React from ${JSON.stringify(reactUrl)};
export default function Link({ href, children, ...rest }) { return React.createElement("a", { href, ...rest }, children); }
`,
);

const loaded = new Map();
function resolveSource(rel) {
  for (const ext of ["", ".tsx", ".ts"]) {
    if (existsSync(join(sourceRoot, rel + ext)) && /\.tsx?$/.test(rel + ext)) return rel + ext;
  }
  throw new Error(`cannot resolve ${rel}`);
}

function load(rel) {
  const file = resolveSource(rel);
  if (!loaded.has(file)) loaded.set(file, doLoad(file));
  return loaded.get(file);
}

async function doLoad(file) {
  const source = await readFile(join(sourceRoot, file), "utf8");
  let code = transpile(source, file.split("/").pop());
  const specs = [...new Set([...code.matchAll(/from "([^"]+)"/g)].map((match) => match[1]))];
  for (const spec of specs) {
    let target;
    if (spec === "react") target = reactShim;
    else if (spec === "react/jsx-runtime") target = jsxRuntimeUrl;
    else if (spec === "next/link") target = linkStub;
    else if (spec.endsWith(".css")) target = cssStub;
    else if (spec.startsWith("@/")) target = await load(spec.slice(2));
    else if (spec.startsWith(".")) target = await load(posix.join(posix.dirname(file), spec));
    else continue;
    code = code.replaceAll(`from "${spec}"`, `from ${JSON.stringify(target)}`);
  }
  return emit(`${file.replaceAll("/", "__")}.mjs`, code);
}

const importModule = async (rel) => import(await load(rel));
const dir = "features/ui-foundation";
const icons = await importModule(`${dir}/icons.tsx`);
const primitives = await importModule(`${dir}/primitives.tsx`);
const states = await importModule(`${dir}/states.tsx`);
const segmentedMod = await importModule(`${dir}/Segmented.tsx`);
const chipsMod = await importModule(`${dir}/FilterChips.tsx`);
const progressMod = await importModule(`${dir}/ProgressBar.tsx`);
const tileMod = await importModule(`${dir}/StatTile.tsx`);
const timelineMod = await importModule(`${dir}/Timeline.tsx`);
const tableMod = await importModule(`${dir}/DataTable.tsx`);
const drawerMod = await importModule(`${dir}/Drawer.tsx`);
const toastMod = await importModule(`${dir}/Toast.tsx`);
const dropMod = await importModule(`${dir}/Dropzone.tsx`);
const splitMod = await importModule(`${dir}/SplitButton.tsx`);
const stepperMod = await importModule(`${dir}/LapStepper.tsx`);
const roving = primitives;

const NEW_TSX = [
  "states",
  "DataTable",
  "Segmented",
  "FilterChips",
  "ProgressBar",
  "StatTile",
  "Timeline",
  "Drawer",
  "Toast",
  "Dropzone",
  "SplitButton",
  "LapStepper",
].map((name) => `${dir}/${name}.tsx`);
const NEW_CSS = [`${dir}/primitives-extra.module.css`, `${dir}/DataTable.module.css`];

// --- Tokens and conventions -------------------------------------------------

test("globals.css defines the plan tokens", async () => {
  const css = await readFile(new URL("app/globals.css", sourceRootUrl), "utf8");
  for (const token of [
    "--target: #3f63ee",
    "--reference: #b14cf0",
    "--gain: var(--green-text)",
    "--loss: var(--red-text)",
    "--selected-bg: #ecf3fe",
    "--chart-grid: #eef1f4",
    "--chart-axis: #8d949d",
    "--stint-inter: #3fae5a",
    "--stint-wet: #2f7fe0",
    "--shadow-overlay: 0 8px 24px rgba(16, 24, 40, 0.12)",
    "--z-menu: 4",
    "--z-header: 5",
    "--z-drawer: 20",
    "--z-toast: 30",
    "--motion-fast: 120ms",
    "--motion-base: 180ms",
    "--motion-slow: 240ms",
    "--ease-out: cubic-bezier(0.2, 0.8, 0.2, 1)",
  ]) {
    assert.ok(css.includes(token), token);
  }
});

test("TARGET_COLOR and REFERENCE_COLOR equal the --target and --reference tokens", async () => {
  const css = await readFile(new URL("app/globals.css", sourceRootUrl), "utf8");
  const charts = await readFile(new URL("features/lap-analysis/TelemetryCharts.tsx", sourceRootUrl), "utf8");
  const token = (name) => css.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`))?.[1];
  assert.equal(charts.match(/export const TARGET_COLOR = "([^"]+)"/)?.[1].toLowerCase(), token("target"));
  assert.equal(charts.match(/export const REFERENCE_COLOR = "([^"]+)"/)?.[1].toLowerCase(), token("reference"));
});

test("new CSS modules and primitives use tokens, not raw hex colours", async () => {
  for (const file of [...NEW_CSS, ...NEW_TSX]) {
    const source = await readFile(new URL(file, sourceRootUrl), "utf8");
    assert.doesNotMatch(source, /#[0-9a-fA-F]{3,8}\b/, `${file} contains a raw hex colour`);
  }
});

test("new primitives use no emoji or unicode icon characters", async () => {
  const forbidden = /[←-⇿⌀-⏿■-➿⤀-⥿\u{1f000}-\u{1faff}️]/u;
  for (const file of NEW_TSX) {
    const source = await readFile(new URL(file, sourceRootUrl), "utf8");
    assert.doesNotMatch(source, forbidden, `${file} contains an icon-like character`);
  }
});

test("new animation is wrapped in a no-motion-preference media query", async () => {
  const css = await readFile(new URL(`${dir}/primitives-extra.module.css`, sourceRootUrl), "utf8");
  const guard = css.indexOf("@media (prefers-reduced-motion: no-preference)");
  assert.ok(guard >= 0);
  assert.doesNotMatch(css.slice(0, guard), /animation:/);
  const globals = await readFile(new URL("app/globals.css", sourceRootUrl), "utf8");
  assert.match(globals, /@media \(prefers-reduced-motion: no-preference\) \{\s*\.wave-bar/);
});

// --- Icons ------------------------------------------------------------------

test("every new icon is a decorative SVG by default and labelled when given a title", () => {
  const simple = [
    "SwapIcon",
    "UploadIcon",
    "DownloadIcon",
    "CloseIcon",
    "RefreshIcon",
    "PlayIcon",
    "PauseIcon",
    "StopIcon",
    "CopyIcon",
    "HistoryIcon",
    "SendIcon",
    "PitIcon",
    "FlagIcon",
    "CarIcon",
    "CaptureFileIcon",
    "ExpandIcon",
  ];
  for (const name of simple) {
    const markup = render(createElement(icons[name], {}));
    assert.match(markup, /^<svg /, name);
    assert.match(markup, /viewBox="0 0 24 24"/, name);
    assert.match(markup, /aria-hidden="true"/, name);
    assert.doesNotMatch(markup, /<text|<image/, name);
    const labelled = render(createElement(icons[name], { title: "Named" }));
    assert.match(labelled, /role="img"/, name);
    assert.match(labelled, /aria-label="Named"/, name);
    assert.doesNotMatch(labelled, /aria-hidden/, name);
  }
});

test("WeatherIcon and TyreIcon cover every kind and compound", () => {
  const kinds = ["clear", "light_cloud", "overcast", "light_rain", "heavy_rain", "storm"];
  const drawings = new Set(kinds.map((kind) => render(createElement(icons.WeatherIcon, { kind }))));
  assert.equal(drawings.size, kinds.length);
  for (const markup of drawings) assert.match(markup, /aria-hidden="true"/);

  const expected = {
    soft: "--stint-soft",
    medium: "--stint-medium",
    hard: "--stint-hard",
    inter: "--stint-inter",
    wet: "--stint-wet",
  };
  for (const [compound, token] of Object.entries(expected)) {
    const markup = render(createElement(icons.TyreIcon, { compound }));
    assert.ok(markup.includes(`fill="var(${token})"`), compound);
    assert.match(markup, /aria-hidden="true"/);
    assert.doesNotMatch(markup, /<text/);
  }
  assert.match(render(createElement(icons.TyreIcon, { compound: "unknown" })), /var\(--grey-soft\)/);
});

test("WaveformIcon keeps its original drawing by default and adds modes", () => {
  const original = render(createElement(icons.WaveformIcon, { size: 40 }));
  assert.equal(original.match(/<rect/g).length, 10);
  assert.match(original, /fill="var\(--red\)"/);
  assert.doesNotMatch(original, /wave-bar|style=/);
  const animated = render(createElement(icons.WaveformIcon, { mode: "animated", tone: "green" }));
  assert.equal(animated.match(/class="wave-bar"/g).length, 9);
  assert.match(animated, /var\(--green\)/);
  const flat = render(createElement(icons.WaveformIcon, { mode: "flat", tone: "grey" }));
  assert.equal(flat.match(/<rect/g).length, 1);
  assert.match(flat, /var\(--faint\)/);
  const dim = render(createElement(icons.WaveformIcon, { dim: true }));
  assert.equal(dim.match(/opacity="0.4"/g).length, 9);
  for (const markup of [original, animated, flat, dim]) assert.match(markup, /aria-hidden="true"/);
});

// --- States -----------------------------------------------------------------

test("NotAvailable renders a dash with a tooltip and a screen reader reason", () => {
  const markup = render(createElement(states.NotAvailable, { reason: "Sector times were not recorded" }));
  assert.match(markup, /title="Sector times were not recorded"/);
  assert.match(markup, /<span aria-hidden="true">—<\/span>/);
  assert.match(markup, /<span class="sr-only">Not available: Sector times were not recorded<\/span>/);
  const compact = render(createElement(states.NotAvailable, { reason: "No data", compact: true }));
  assert.doesNotMatch(compact, /title=/);
  assert.match(compact, /Not available: No data/);
});

test("EmptyState sizes, tones and roles", () => {
  const card = render(createElement(states.EmptyState, { title: "No laps", body: "Nothing here." }));
  assert.match(card, /data-size="card"/);
  assert.match(card, /role="status"/);
  assert.match(card, /<svg[^>]*aria-hidden="true"/);
  assert.match(card, /No laps/);
  const inline = render(createElement(states.EmptyState, { title: "None", size: "inline" }));
  assert.doesNotMatch(inline, /<svg/);
  const error = render(createElement(states.EmptyState, { title: "Broken", tone: "error" }));
  assert.match(error, /role="alert"/);
  const notAlert = render(createElement(states.EmptyState, { title: "Hm", tone: "warning", role: "alert" }));
  assert.match(notAlert, /role="status"/);
  const page = render(createElement(states.EmptyState, { title: "Gone", size: "page", action: createElement("a", { href: "/x" }, "Go") }));
  assert.match(page, /<h2[^>]*>Gone<\/h2>/);
  assert.match(page, /href="\/x"/);
});

test("InlineAlert uses alert for errors, status otherwise, and a labelled dismiss button", () => {
  for (const [tone, role] of [
    ["error", "alert"],
    ["warning", "status"],
    ["info", "status"],
    ["success", "status"],
  ]) {
    const markup = render(createElement(states.InlineAlert, { tone, title: "Heads up" }, "Body text"));
    assert.match(markup, new RegExp(`role="${role}"`), tone);
    assert.match(markup, new RegExp(`data-tone="${tone}"`));
    assert.match(markup, /Heads up/);
    assert.doesNotMatch(markup, /aria-label="Dismiss"/);
  }
  const dismissible = render(createElement(states.InlineAlert, { tone: "info", onDismiss() {} }, "x"));
  assert.match(dismissible, /<button[^>]*aria-label="Dismiss"/);
});

// --- Segmented, FilterChips -------------------------------------------------

const MODES = [
  { key: "delta", label: "Time Delta" },
  { key: "distance", label: "Distance" },
  { key: "other", label: "Other" },
];

test("Segmented is a radiogroup with a roving tabindex", () => {
  const markup = render(
    createElement(segmentedMod.Segmented, { items: MODES, value: "distance", onChange() {}, label: "Compare" }),
  );
  assert.match(markup, /role="radiogroup"/);
  assert.match(markup, /aria-label="Compare"/);
  const radios = [...markup.matchAll(/<button[^>]*role="radio"[^>]*>/g)].map((match) => match[0]);
  assert.equal(radios.length, 3);
  assert.ok(radios[1].includes('aria-checked="true"') && radios[1].includes('tabindex="0"'));
  for (const index of [0, 2]) {
    assert.ok(radios[index].includes('aria-checked="false"') && radios[index].includes('tabindex="-1"'));
  }
  assert.doesNotMatch(markup, /role="tab"|aria-selected/);
});

test("Segmented arrow keys move focus and selection in every direction", () => {
  const chosen = [];
  const element = segmentedMod.Segmented({
    items: MODES,
    value: "delta",
    label: "Compare",
    onChange: (key) => chosen.push(key),
  });
  const radios = MODES.map(() => ({
    focused: 0,
    focus() {
      this.focused += 1;
    },
  }));
  function press(key, from) {
    let prevented = false;
    element.props.onKeyDown({
      key,
      altKey: false,
      ctrlKey: false,
      metaKey: false,
      target: radios[from],
      currentTarget: { querySelectorAll: () => radios },
      preventDefault() {
        prevented = true;
      },
    });
    return prevented;
  }
  assert.equal(press("ArrowRight", 0), true);
  assert.equal(press("ArrowDown", 1), true);
  assert.equal(press("ArrowLeft", 0), true); // wraps to the last
  assert.equal(press("ArrowUp", 0), true);
  assert.equal(press("End", 0), true);
  assert.equal(press("Home", 2), true); // already selected: no change callback
  assert.equal(press("x", 0), false);
  assert.deepEqual(chosen, ["distance", "other", "other", "other", "other"]);
});

test("Segmented can be disabled with a reason", () => {
  const markup = render(
    createElement(segmentedMod.Segmented, {
      items: MODES,
      value: "delta",
      onChange() {},
      label: "Compare",
      disabled: true,
      disabledReason: "Select a reference lap to compare",
    }),
  );
  assert.match(markup, /aria-disabled="true"/);
  assert.match(markup, /title="Select a reference lap to compare"/);
  assert.equal(markup.match(/disabled=""/g).length, 3);
});

test("Segmented styling reuses the tab classes and styles aria-checked", async () => {
  const css = await readFile(new URL(`${dir}/primitives.module.css`, sourceRootUrl), "utf8");
  assert.match(css, /\.tab:is\(\[aria-selected="true"\], \[aria-checked="true"\]\) \{/);
});

test("FilterChips is a labelled group of aria-pressed buttons with tabular counts", () => {
  const items = [
    { key: "all", label: "All", count: 12 },
    { key: "q", label: "Questions", count: 6 },
    { key: "r", label: "Responses" },
  ];
  const markup = render(createElement(chipsMod.FilterChips, { items, value: "q", onChange() {}, label: "Filter radio" }));
  assert.match(markup, /role="group"/);
  assert.match(markup, /aria-label="Filter radio"/);
  const buttons = [...markup.matchAll(/<button[^>]*>/g)].map((match) => match[0]);
  assert.deepEqual(
    buttons.map((tag) => tag.includes('aria-pressed="true"')),
    [false, true, false],
  );
  assert.match(markup, /Questions<span class="chipCount"> \(6\)<\/span>/);
  assert.doesNotMatch(markup.split("Responses")[1] ?? "", /chipCount/);
  const multi = render(
    createElement(chipsMod.FilterChips, { items, value: ["all", "r"], onChange() {}, label: "Channels" }),
  );
  assert.equal(multi.match(/aria-pressed="true"/g).length, 2);
});

// --- ProgressBar, StatTile, Timeline ----------------------------------------

test("ProgressBar exposes progressbar semantics and omits values when indeterminate", () => {
  const determinate = render(createElement(progressMod.ProgressBar, { value: 42.4, label: "Uploading", tone: "green" }));
  assert.match(determinate, /role="progressbar"/);
  assert.match(determinate, /aria-label="Uploading"/);
  assert.match(determinate, /aria-valuenow="42"/);
  assert.match(determinate, /aria-valuemin="0"/);
  assert.match(determinate, /aria-valuemax="100"/);
  assert.match(determinate, /width:42.4%/);
  assert.match(determinate, /data-tone="green"/);
  const clamped = render(createElement(progressMod.ProgressBar, { value: 180, label: "x", size: "sm" }));
  assert.match(clamped, /aria-valuenow="100"/);
  assert.match(clamped, /data-size="sm"/);
  const indeterminate = render(createElement(progressMod.ProgressBar, { value: null, label: "Waiting" }));
  assert.match(indeterminate, /role="progressbar"/);
  assert.match(indeterminate, /aria-label="Waiting"/);
  assert.doesNotMatch(indeterminate, /aria-valuenow|aria-valuemin|aria-valuemax|width:/);
  assert.match(indeterminate, /data-indeterminate="true"/);
});

test("StatTile shows a value, delta pill and sub line, or NotAvailable", () => {
  const tile = render(
    createElement(tileMod.StatTile, { label: "Best Lap", value: "1:12.842", sub: "Lap 57", delta: { ms: 578, text: "+0.578" } }),
  );
  assert.match(tile, /Best Lap/);
  assert.match(tile, /1:12\.842/);
  assert.match(tile, /data-tone="red"[^>]*>\+0\.578/);
  assert.match(tile, /Lap 57/);
  const unavailable = render(
    createElement(tileMod.StatTile, { label: "Track Temp", value: "ignored", unavailableReason: "Track temperature was not reported" }),
  );
  assert.doesNotMatch(unavailable, /ignored/);
  assert.match(unavailable, /Not available: Track temperature was not reported/);
  const iconTile = render(createElement(tileMod.StatTile, { label: "x", value: 1, icon: createElement(icons.PitIcon, {}) }));
  assert.match(iconTile, /<span class="tileIcon" aria-hidden="true"><svg/);
});

test("Timeline renders a labelled ordered list with optional time and action columns", () => {
  const items = [
    { id: "a", icon: createElement(icons.FlagIcon, {}), lap: 1, time: "00:00", label: "Lights out" },
    {
      id: "b",
      icon: createElement(icons.PitIcon, {}),
      lap: 12,
      time: null,
      label: "Pit stop",
      chips: createElement("span", null, "Soft to Hard"),
      action: createElement("a", { href: "/laps" }, "View lap"),
      emphasis: "highlight",
    },
  ];
  const markup = render(createElement(timelineMod.Timeline, { items, label: "Session events", dense: true }));
  assert.match(markup, /^<ol[^>]*aria-label="Session events"/);
  assert.match(markup, /data-dense="true"/);
  assert.match(markup, /--timeline-columns:14px 64px 72px minmax\(0, 1fr\) auto/);
  assert.equal(markup.match(/<li /g).length, 2);
  assert.match(markup, /data-emphasis="highlight"/);
  assert.match(markup, /Lap 12/);
  assert.match(markup, /href="\/laps"/);
  const plain = render(createElement(timelineMod.Timeline, { items: [{ id: "c", icon: null, lap: null, time: null, label: "x" }], label: "Plain" }));
  assert.match(plain, /--timeline-columns:14px 64px minmax\(0, 1fr\)/);
  const empty = render(createElement(timelineMod.Timeline, { items: [], label: "None", empty: createElement("p", null, "Nothing") }));
  assert.equal(empty, "<p>Nothing</p>");
});

// --- DataTable --------------------------------------------------------------

const LAPS = [
  { id: "l1", lap: 1, ms: 74000, valid: true },
  { id: "l2", lap: 2, ms: 72842, valid: true },
  { id: "l3", lap: 3, ms: 73100, valid: false },
];
const COLUMNS = [
  { key: "lap", header: "Lap", width: "56px", numeric: true, sort: (a, b) => a.lap - b.lap, render: (row) => `Lap ${row.lap}` },
  { key: "time", header: "Time", width: "96px", numeric: true, sort: (a, b) => a.ms - b.ms, render: (row) => String(row.ms) },
  { key: "flags", header: "Flags", width: "minmax(90px, 1fr)", render: (row) => (row.valid ? "" : "Invalid"), footnote: "Invalid laps are not compared." },
];

function table(props = {}) {
  return render(
    createElement(tableMod.DataTable, {
      caption: "Laps",
      columns: COLUMNS,
      rows: LAPS,
      rowKey: (row) => row.id,
      ...props,
    }),
  );
}

test("DataTable renders a semantic table with caption, scoped headers and aria-sort", () => {
  const markup = table({ defaultSort: { key: "time", dir: "desc" } });
  assert.match(markup, /<caption class="sr-only">Laps<\/caption>/);
  assert.equal(markup.match(/<th /g).length, 3);
  assert.match(markup, /scope="col"/);
  const headers = [...markup.matchAll(/<th [^>]*>/g)].map((match) => match[0]);
  assert.ok(headers[0].includes('aria-sort="none"'));
  assert.ok(headers[1].includes('aria-sort="descending"'));
  assert.doesNotMatch(headers[2], /aria-sort/); // not sortable
  assert.match(markup, /<th [^>]*><button type="button" class="sortButton">Lap/);
  // Rows follow the default sort: slowest first.
  const order = [...markup.matchAll(/>(7\d{4})</g)].map((match) => match[1]);
  assert.deepEqual(order, ["74000", "73100", "72842"]);
  assert.match(markup, /min-width:\d+px/);
  const ascending = table({ defaultSort: { key: "time", dir: "asc" } });
  assert.match([...ascending.matchAll(/<th [^>]*>/g)][1][0], /aria-sort="ascending"/);
});

test("DataTable selection puts an aria-pressed button in the first cell and marks the row", () => {
  const markup = table({ onSelect() {}, selectedKey: "l2" });
  const buttons = [...markup.matchAll(/<button[^>]*data-row-button[^>]*>/g)].map((match) => match[0]);
  assert.equal(buttons.length, 3);
  assert.deepEqual(
    buttons.map((tag) => tag.includes('aria-pressed="true"')),
    [false, true, false],
  );
  const rows = [...markup.matchAll(/<tr [^>]*>/g)].map((match) => match[0]);
  assert.deepEqual(
    rows.map((tag) => tag.includes('aria-selected="true"')),
    [false, true, false],
  );
  assert.match(markup, /Lap 2<\/button>/);
  const readonly = table();
  assert.doesNotMatch(readonly, /data-row-button|aria-selected|aria-pressed/);
});

test("DataTable ties footnotes to their headers and supports tone, empty and loading", () => {
  const markup = table({ rowTone: (row) => (row.valid ? undefined : "muted") });
  const note = markup.match(/<p id="([^"]+)" class="footnote">Invalid laps are not compared\.<\/p>/);
  assert.ok(note);
  assert.ok(markup.includes(`aria-describedby="${note[1]}"`));
  assert.equal(markup.match(/data-tone="muted"/g).length, 1);
  const empty = table({ rows: [], empty: createElement("p", null, "No laps") });
  assert.match(empty, /<td colSpan="3"[^>]*><p>No laps<\/p><\/td>|<td[^>]*colSpan="3"[^>]*><p>No laps<\/p>/);
  const loading = table({ loading: true, loadingRows: 2, empty: createElement("p", null, "No laps") });
  assert.match(loading, /aria-busy="true"/);
  assert.doesNotMatch(loading, /No laps/);
  assert.equal(loading.match(/<tr [^>]*aria-hidden="true"/g).length, 2);
  const sticky = table({ stickyHeader: true, maxHeight: 200, density: "compact" });
  assert.match(sticky, /data-sticky="true"/);
  assert.match(sticky, /max-height:200px/);
  assert.match(sticky, /data-density="compact"/);
});

test("DataTable helpers sort stably and read column minimums", () => {
  const rows = [
    { id: "a", k: 1 },
    { id: "b", k: 1 },
    { id: "c", k: 0 },
  ];
  const columns = [{ key: "k", header: "K", sort: (x, y) => x.k - y.k, render: () => null }];
  assert.deepEqual(tableMod.sortRows(rows, columns, { key: "k", dir: "asc" }).map((row) => row.id), ["c", "a", "b"]);
  assert.deepEqual(tableMod.sortRows(rows, columns, { key: "k", dir: "desc" }).map((row) => row.id), ["a", "b", "c"]);
  assert.equal(tableMod.sortRows(rows, columns, null), rows);
  assert.equal(tableMod.columnMinWidth("56px"), 56);
  assert.equal(tableMod.columnMinWidth("minmax(110px,1fr)"), 110);
  assert.equal(tableMod.columnMinWidth("1fr"), 0);
  assert.equal(tableMod.columnMinWidth(undefined), 0);
});

test("nextRovingIndex handles orientation, wrapping and home/end", () => {
  const next = roving.nextRovingIndex;
  assert.equal(next("ArrowDown", 0, 3), 1);
  assert.equal(next("ArrowDown", 2, 3), 0);
  assert.equal(next("ArrowDown", 2, 3, "vertical", false), 2);
  assert.equal(next("ArrowUp", 0, 3), 2);
  assert.equal(next("ArrowUp", 0, 3, "vertical", false), 0);
  assert.equal(next("ArrowDown", -1, 3), 0);
  assert.equal(next("ArrowUp", -1, 3), 2);
  assert.equal(next("Home", 2, 3), 0);
  assert.equal(next("End", 0, 3), 2);
  assert.equal(next("ArrowRight", 0, 3), null);
  assert.equal(next("ArrowRight", 0, 3, "horizontal"), 1);
  assert.equal(next("ArrowDown", 0, 3, "horizontal"), null);
  assert.equal(next("ArrowLeft", 0, 3, "both"), 2);
  assert.equal(next("a", 0, 3), null);
  assert.equal(next("ArrowDown", 0, 0), null);
});

// --- Menu, SplitButton, LapStepper ------------------------------------------

test("Menu keeps its disclosure markup, supports disabled, and wires arrow-key navigation", async () => {
  const markup = render(createElement(primitives.Menu, { label: "Actions", trigger: null, disabled: true }, "Item"));
  assert.match(markup, /aria-expanded="false"/);
  assert.match(markup, /disabled=""/);
  const source = await readFile(new URL(`${dir}/primitives.tsx`, sourceRootUrl), "utf8");
  assert.match(source, /nextRovingIndex\(event\.key, current, items\.length\)/);
  assert.match(source, /event\.key === "ArrowDown" \? "first" : "last"/);
});

test("SplitButton renders the main action and a labelled menu trigger", () => {
  const markup = render(
    createElement(
      splitMod.SplitButton,
      { label: "Import Session", onClick() {}, menuLabel: "More import options" },
      createElement(primitives.MenuItem, null, "Refresh library"),
    ),
  );
  assert.match(markup, /data-variant="primary"/);
  assert.match(markup, /<button[^>]*class="splitMain"[^>]*>Import Session<\/button>/);
  assert.match(markup, /aria-label="More import options"/);
  assert.match(markup, /aria-expanded="false"/);
  assert.doesNotMatch(markup, /Refresh library/);
  const disabled = render(
    createElement(
      splitMod.SplitButton,
      { label: "Import", onClick() {}, menuLabel: "More", disabled: true, disabledReason: "The engine is offline" },
      null,
    ),
  );
  assert.match(disabled, /title="The engine is offline"/);
  assert.match(disabled, /aria-describedby="[^"]+"/);
  assert.match(disabled, /<span id="[^"]+" class="sr-only">The engine is offline<\/span>/);
  assert.equal(disabled.match(/disabled=""/g).length, 2);
  const busy = render(createElement(splitMod.SplitButton, { label: "Importing", onClick() {}, menuLabel: "More", busy: true }, null));
  assert.match(busy, /aria-busy="true"/);
  assert.match(busy, /data-busy="true"/);
});

test("LapStepper labels its group, buttons and lap menu, and disables edge steps", () => {
  const laps = [
    { id: "l1", lap: 1, ms: 74000, valid: true },
    { id: "l2", lap: 2, ms: 72842, valid: true },
    { id: "l3", lap: 3, ms: 73100, valid: false },
  ];
  const markup = render(createElement(stepperMod.LapStepper, { label: "Target", laps, value: "l1", onChange() {}, note: "Session best" }));
  assert.match(markup, /role="group"/);
  assert.match(markup, /aria-label="Target"/);
  assert.match(markup, /aria-label="Previous target"[^>]*disabled=""|disabled=""[^>]*aria-label="Previous target"/);
  assert.match(markup, /aria-label="Next target"/);
  assert.doesNotMatch(markup.match(/<button[^>]*aria-label="Next target"[^>]*>/)[0], /disabled/);
  assert.match(markup, /aria-label="Target: Lap 1, 1:14\.000\. Choose another lap"/);
  assert.match(markup, /Lap 1/);
  assert.match(markup, /1:14\.000/);
  assert.match(markup, /Session best/);
  assert.match(markup, /aria-live="polite"/);
  const last = render(createElement(stepperMod.LapStepper, { label: "Reference", laps, value: "l3", onChange() {}, tone: "reference" }));
  assert.match(last.match(/<button[^>]*aria-label="Next reference"[^>]*>/)[0], /disabled/);
  assert.match(last, /var\(--reference\)/);
  const none = render(createElement(stepperMod.LapStepper, { label: "Reference", laps: [], value: null, onChange() {} }));
  assert.match(none, /None/);
  assert.equal(none.match(/disabled=""/g).length, 2);
  assert.equal(stepperMod.formatStepperTime(72842), "1:12.842");
  assert.equal(stepperMod.formatStepperTime(61005), "1:01.005");
});

// --- Drawer, ConfirmDialog --------------------------------------------------

test("Drawer renders nothing when closed and a labelled modal dialog when open", () => {
  const closed = render(createElement(drawerMod.Drawer, { open: false, onClose() {}, title: "Import activity" }, "Body"));
  assert.equal(closed, "");
  const open = render(
    createElement(drawerMod.Drawer, { open: true, onClose() {}, title: "Import activity", width: 500, footer: "Footer" }, "Body"),
  );
  assert.match(open, /^<dialog[^>]*aria-labelledby="([^"]+)"/);
  const id = open.match(/aria-labelledby="([^"]+)"/)[1];
  assert.ok(open.includes(`id="${id}">Import activity</h2>`));
  assert.match(open, /--drawer-width:500px/);
  assert.match(open, /<button[^>]*aria-label="Close"/);
  assert.match(open, /Body/);
  assert.match(open, /<footer[^>]*>Footer<\/footer>/);
  assert.match(render(createElement(drawerMod.Drawer, { open: true, onClose() {}, title: "T" }, "x")), /--drawer-width:420px/);
});

test("ConfirmDialog is an alertdialog with title, description and two buttons", () => {
  const props = { title: "Replace settings?", body: "This overwrites the saved file.", confirmLabel: "Replace", onConfirm() {}, onCancel() {} };
  assert.equal(render(createElement(drawerMod.ConfirmDialog, { ...props, open: false })), "");
  const markup = render(createElement(drawerMod.ConfirmDialog, { ...props, open: true, tone: "danger" }));
  assert.match(markup, /role="alertdialog"/);
  const labelled = markup.match(/aria-labelledby="([^"]+)"/)[1];
  const described = markup.match(/aria-describedby="([^"]+)"/)[1];
  assert.ok(markup.includes(`id="${labelled}">Replace settings?</h2>`));
  assert.ok(markup.includes(`id="${described}">This overwrites the saved file.</div>`));
  assert.match(markup, />Cancel<\/button>/);
  assert.match(markup, /data-variant="primary"[^>]*>Replace<\/button>/);
  const neutral = render(createElement(drawerMod.ConfirmDialog, { ...props, open: true }));
  assert.match(neutral, /data-variant="outline-blue"[^>]*>Replace<\/button>/);
});

// --- Toast ------------------------------------------------------------------

test("ToastProvider renders a labelled notifications region around its children", () => {
  const markup = render(createElement(toastMod.ToastProvider, null, createElement("p", null, "page")));
  assert.match(markup, /<p>page<\/p>/);
  assert.match(markup, /<section[^>]*aria-label="Notifications"/);
});

test("ToastCard uses alert for errors and status otherwise, with labelled dismiss", () => {
  for (const [tone, role] of [
    ["error", "alert"],
    ["warning", "status"],
    ["info", "status"],
    ["success", "status"],
  ]) {
    const markup = render(
      createElement(toastMod.ToastCard, {
        toast: { id: "t1", tone, title: "Import complete", body: "Monaco is ready" },
        onDismiss() {},
      }),
    );
    assert.match(markup, new RegExp(`role="${role}"`), tone);
    assert.match(markup, /Import complete/);
    assert.match(markup, /Monaco is ready/);
    assert.match(markup, /aria-label="Dismiss notification"/);
  }
  const link = render(
    createElement(toastMod.ToastCard, {
      toast: { id: "t2", tone: "success", title: "Done", action: { label: "Open session", href: "/review/abc" } },
      onDismiss() {},
    }),
  );
  assert.match(link, /<a[^>]*href="\/review\/abc"[^>]*>Open session<\/a>/);
  const button = render(
    createElement(toastMod.ToastCard, {
      toast: { id: "t3", tone: "info", title: "Copied", action: { label: "Undo", onClick() {} } },
      onDismiss() {},
    }),
  );
  assert.match(button, /<button[^>]*>Undo<\/button>/);
});

test("toast durations, stacking limit and the no-provider fallback", () => {
  assert.equal(toastMod.toastDuration({ tone: "success" }), 5000);
  assert.equal(toastMod.toastDuration({ tone: "info" }), 5000);
  assert.equal(toastMod.toastDuration({ tone: "error" }), null);
  assert.equal(toastMod.toastDuration({ tone: "error", durationMs: 8000 }), 8000);
  assert.equal(toastMod.toastDuration({ tone: "success", durationMs: 0 }), null);
  let list = [];
  for (const id of ["a", "b", "c", "d"]) list = toastMod.addToast(list, { id, tone: "info", title: id });
  assert.deepEqual(list.map((toast) => toast.id), ["b", "c", "d"]);

  function Probe() {
    const api = toastMod.useToast();
    return createElement("p", null, `${typeof api.push}:${api.push({ tone: "info", title: "x" })}:${typeof api.dismiss}`);
  }
  assert.equal(render(createElement(Probe)), "<p>function::function</p>");
});

test("AppShell mounts ToastProvider around the page content", async () => {
  const shell = await readFile(new URL(`${dir}/AppShell.tsx`, sourceRootUrl), "utf8");
  assert.match(shell, /import \{ ToastProvider \} from "\.\/Toast";/);
  assert.match(shell, /<main id="main" className=\{styles\.main\} tabIndex=\{-1\}>\s*<ToastProvider>\s*<UnitsProvider>\{children\}<\/UnitsProvider>\s*<SessionWatcher \/>\s*<\/ToastProvider>\s*<\/main>/);
});

// --- Dropzone ---------------------------------------------------------------

test("classifyDrop accepts matching files and rejects wrong types or too many", () => {
  const file = (name, type = "") => ({ name, type });
  const accept = [".f1ecap"];
  const good = [file("a.f1ecap"), file("B.F1ECAP")];
  assert.deepEqual(dropMod.classifyDrop(good, accept, 5), { ok: true, files: good });
  assert.deepEqual(dropMod.classifyDrop([file("a.f1ecap"), file("notes.txt")], accept, 5), { ok: false, reason: "type" });
  assert.deepEqual(dropMod.classifyDrop(Array.from({ length: 6 }, (_, i) => file(`${i}.f1ecap`)), accept, 5), {
    ok: false,
    reason: "count",
  });
  assert.deepEqual(dropMod.classifyDrop([], accept, 5), { ok: false, reason: "type" });
  assert.equal(dropMod.classifyDrop([file("x.wav", "audio/wav")], ["audio/*"], 1).ok, true);
  assert.equal(dropMod.classifyDrop([file("x.wav", "audio/wav")], ["audio/wav"], 1).ok, true);
  assert.equal(dropMod.classifyDrop([file("x.png", "image/png")], ["audio/*"], 1).ok, false);
});

test("Dropzone wraps its children, is not focusable and shows no overlay at rest", () => {
  const markup = render(
    createElement(dropMod.Dropzone, { accept: [".f1ecap"], maxFiles: 5, onFiles() {}, onReject() {}, overlayLabel: "Drop .f1ecap captures to import" }, createElement("p", null, "List")),
  );
  assert.match(markup, /^<div class="dropzone"><p>List<\/p><\/div>$/);
  assert.doesNotMatch(markup, /tabindex|Drop \.f1ecap/);
});

// --- WP12 follow-ups ---------------------------------------------------------

test("SplitButton ariaDisabled keeps the main button focusable with its reason", () => {
  const markup = render(
    createElement(
      splitMod.SplitButton,
      { label: "Import", onClick() {}, menuLabel: "More", disabled: true, ariaDisabled: true, disabledReason: "The engine is offline" },
      null,
    ),
  );
  const main = markup.match(/<button[^>]*class="splitMain"[^>]*>/)?.[0] ?? "";
  assert.match(main, /aria-disabled="true"/);
  assert.doesNotMatch(main, /\sdisabled(=|\s|>)/, "not natively disabled, so it stays in the tab order");
  assert.match(main, /aria-describedby="[^"]+"/);
  assert.match(markup, /class="sr-only">The engine is offline</);
});

test("Timeline time accepts a node and shows the column when any item has one", () => {
  const markup = render(
    createElement(timelineMod.Timeline, {
      label: "Events",
      items: [
        { id: "a", icon: null, lap: 1, time: "01:02", label: "A" },
        { id: "b", icon: null, lap: 2, time: createElement("span", { className: "na" }, "n/a"), label: "B" },
      ],
    }),
  );
  assert.match(markup, /class="timelineTime">01:02</);
  assert.match(markup, /class="timelineTime"><span class="na">n\/a<\/span>/);
  const none = render(
    createElement(timelineMod.Timeline, { label: "E", items: [{ id: "c", icon: null, lap: 1, time: null, label: "C" }] }),
  );
  assert.doesNotMatch(none, /timelineTime/);
});

test("LapStepper labelDot accepts a CSS colour for the label cell", () => {
  const laps = [{ id: "l1", lap: 1, ms: 90000, valid: true }];
  const green = render(
    createElement(stepperMod.LapStepper, { label: "Reference Lap", laps, value: "l1", onChange() {}, tone: "reference", labelDot: "var(--green)" }),
  );
  assert.match(green, /<circle[^>]*fill="var\(--green\)"/);
  assert.match(green, /<circle[^>]*fill="var\(--reference\)"/, "the lap dot keeps the tone colour");
  const plain = render(createElement(stepperMod.LapStepper, { label: "Target", laps, value: "l1", onChange() {} }));
  assert.equal((plain.match(/<circle/g) ?? []).length, 1);
});

test("display units convert text and flip delta signs only on request", async () => {
  const units = await importModule(`${dir}/units.tsx`);
  const metric = units.DEFAULT_UNITS;
  const imperial = { speed: "mph", temperature: "f", delta: "faster-positive" };
  assert.equal(units.convertText("Clear 26°C", metric), "Clear 26°C");
  assert.equal(units.convertText("Clear 26°C", imperial), "Clear 79°F");
  assert.equal(units.convertText("Air 24°C · Track 38°C", imperial), "Air 75°F · Track 100°F");
  assert.equal(units.convertText("Lower minimum speed (-12 km/h)", imperial), "Lower minimum speed (-7 mph)");
  assert.equal(units.convertText("318 km/h Thr 100%", imperial), "198 mph Thr 100%");
  assert.equal(units.displayDelta("+0.578", metric), "+0.578");
  assert.equal(units.displayDelta("+0.578", imperial), "-0.578");
  assert.equal(units.displayDelta("-0.210", imperial), "+0.210");
  assert.equal(units.displayDelta("Personal best", imperial), "Personal best");
  assert.deepEqual(units.parseUnits("not json"), metric);
  assert.deepEqual(units.parseUnits(JSON.stringify({ speed: "mph" })), { ...metric, speed: "mph" });
});
