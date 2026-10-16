import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const typescript = require("typescript");
const { createElement } = require("react");
const reactServer = await import(
  pathToFileURL(require.resolve("react-dom/server")).href
);
const jsxRuntimeUrl = pathToFileURL(require.resolve("react/jsx-runtime")).href;
const reactUrl = pathToFileURL(require.resolve("react")).href;
const sourceRoot = new URL("../src/", import.meta.url);

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

// Transpile the UI foundation modules into a temp dir so relative imports,
// react and the CSS module can be rewritten to something Node can load.
const workDir = await mkdtemp(join(tmpdir(), "ui-routes-"));
async function emit(name, code) {
  const path = join(workDir, name);
  await writeFile(path, code);
  return pathToFileURL(path).href;
}

const reactShim = await emit(
  "react.mjs",
  `import React from ${JSON.stringify(reactUrl)};
export default React;
export const { createContext, useContext, useEffect, useId, useRef, useState } = React;
`,
);
const cssStub = await emit(
  "css.mjs",
  "export default new Proxy({}, { get: (_, key) => String(key) });\n",
);

async function emitSource(path, replacements = {}) {
  const source = await readFile(new URL(path, sourceRoot), "utf8");
  let code = transpile(source, path.split("/").pop())
    .replaceAll('from "react/jsx-runtime"', `from ${JSON.stringify(jsxRuntimeUrl)}`)
    .replaceAll('from "react"', `from ${JSON.stringify(reactShim)}`);
  for (const [from, to] of Object.entries(replacements)) {
    code = code.replaceAll(`from "${from}"`, `from ${JSON.stringify(to)}`);
  }
  return emit(path.split("/").pop().replace(/\.tsx?$/, ".mjs"), code);
}

const iconsUrl = await emitSource("features/ui-foundation/icons.tsx");
const unitsUrl = await emitSource("features/ui-foundation/units.tsx");
const primitivesUrl = await emitSource("features/ui-foundation/primitives.tsx", {
  "./units": unitsUrl,
  "./icons": iconsUrl,
  "./primitives.module.css": cssStub,
});
const trackMapUrl = await emitSource("features/ui-foundation/TrackMap.tsx");
const primitives = await import(primitivesUrl);
const trackMap = await import(trackMapUrl);

const ITEMS = [
  { key: "overview", label: "Overview" },
  { key: "laps", label: "Laps" },
  { key: "setup", label: "Setup" },
];

function tabButtons(markup) {
  return [...markup.matchAll(/<button[^>]*role="tab"[^>]*>/g)].map((match) => match[0]);
}

test("Tabs renders a roving tabindex with only the selected tab tabbable", () => {
  const markup = reactServer.renderToStaticMarkup(
    primitives.Tabs({ items: ITEMS, value: "laps", label: "Sections" }),
  );
  assert.match(markup, /role="tablist"/);
  assert.match(markup, /aria-label="Sections"/);
  const buttons = tabButtons(markup);
  assert.equal(buttons.length, 3);
  assert.equal(buttons.filter((tag) => tag.includes('tabindex="0"')).length, 1);
  assert.ok(buttons[1].includes('aria-selected="true"') && buttons[1].includes('tabindex="0"'));
  for (const index of [0, 2]) {
    assert.ok(buttons[index].includes('tabindex="-1"'), buttons[index]);
    assert.ok(buttons[index].includes('aria-selected="false"'));
  }
});

test("Tabs keeps the first tab tabbable when the value matches no item", () => {
  const markup = reactServer.renderToStaticMarkup(
    primitives.Tabs({ items: ITEMS, value: "missing", label: "Sections" }),
  );
  const buttons = tabButtons(markup);
  assert.ok(buttons[0].includes('tabindex="0"'));
  assert.equal(buttons.filter((tag) => tag.includes('tabindex="0"')).length, 1);
});

test("Tabs supports optional ids and aria-controls", () => {
  const plain = reactServer.renderToStaticMarkup(
    primitives.Tabs({ items: ITEMS, value: "overview", label: "Sections" }),
  );
  assert.doesNotMatch(plain, /aria-controls|id=/);
  const wired = reactServer.renderToStaticMarkup(
    primitives.Tabs({
      items: ITEMS,
      value: "overview",
      label: "Sections",
      idPrefix: "view-tab",
      controls: (key) => `view-panel-${key}`,
    }),
  );
  assert.match(wired, /id="view-tab-laps"/);
  assert.match(wired, /aria-controls="view-panel-laps"/);
});

test("Tabs arrow, Home and End keys move focus and selection", () => {
  const chosen = [];
  const element = primitives.Tabs({
    items: ITEMS,
    value: "overview",
    label: "Sections",
    onChange: (key) => chosen.push(key),
  });
  const tabs = ITEMS.map(() => ({
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
      target: tabs[from],
      currentTarget: { querySelectorAll: () => tabs },
      preventDefault() {
        prevented = true;
      },
    });
    return prevented;
  }
  assert.equal(press("ArrowRight", 0), true);
  assert.equal(press("ArrowLeft", 0), true); // wraps to the last tab
  assert.equal(press("End", 0), true);
  assert.equal(press("Home", 2), true);
  assert.equal(press("ArrowRight", 2), true); // wraps to the first tab
  assert.equal(press("a", 0), false);
  // value stays "overview", so selecting the first tab again is a no-op.
  assert.deepEqual(chosen, ["laps", "setup", "setup"]);
  assert.deepEqual(
    tabs.map((tab) => tab.focused),
    [2, 1, 2],
  );
});

test("IconButton always carries an aria-label and title", () => {
  const markup = reactServer.renderToStaticMarkup(
    primitives.IconButton({ label: "More actions", children: null }),
  );
  assert.match(markup, /aria-label="More actions"/);
  assert.match(markup, /title="More actions"/);
  // Spread props cannot drop or replace the accessible name.
  const overridden = reactServer.renderToStaticMarkup(
    primitives.IconButton({
      label: "Zoom in",
      "aria-label": undefined,
      children: null,
    }),
  );
  assert.match(overridden, /aria-label="Zoom in"/);
});

test("Menu renders a labelled collapsed trigger and no panel until opened", () => {
  const markup = reactServer.renderToStaticMarkup(
    createElement(primitives.Menu, { label: "Row actions", trigger: null }, "Item"),
  );
  assert.match(markup, /aria-label="Row actions"/);
  assert.match(markup, /aria-expanded="false"/);
  assert.doesNotMatch(markup, /aria-controls|Item/);
});

test("Checkbox and Toggle define visible focus rings", async () => {
  const css = await readFile(
    new URL("features/ui-foundation/primitives.module.css", sourceRoot),
    "utf8",
  );
  assert.match(css, /\.checkbox input:focus-visible \+ \.checkboxBox \{[^}]*outline: 2px/);
  assert.match(css, /\.toggle:focus-visible \{[^}]*outline: 2px/);
});

test("every dashboard route segment has the intended state files", () => {
  const appRoot = fileURLToPath(new URL("app/", sourceRoot));
  const segments = ["drive", "review", "review/[session]", "review/[session]/laps", "settings"];
  for (const segment of segments) {
    for (const file of ["loading.tsx", "error.tsx"]) {
      assert.ok(existsSync(join(appRoot, segment, file)), `${segment}/${file}`);
    }
  }
  assert.ok(existsSync(join(appRoot, "review/[session]/not-found.tsx")), "session not-found");
  assert.ok(existsSync(join(appRoot, "not-found.tsx")), "root not-found");
});

test("error boundaries are client components that wire the retry prop", async () => {
  const appRoot = new URL("app/", sourceRoot);
  for (const segment of ["drive", "review", "review/[session]", "review/[session]/laps", "settings"]) {
    const source = await readFile(new URL(`${segment}/error.tsx`, appRoot), "utf8");
    assert.match(source, /^"use client";/, segment);
    assert.match(source, /retry=\{retry\}/, segment);
  }
  const notFound = await readFile(new URL("review/[session]/not-found.tsx", appRoot), "utf8");
  assert.match(notFound, /Session not found/);
  assert.match(notFound, /href="\/review"/);
});

test("skeleton shimmer only animates when reduced motion is not requested", async () => {
  const css = await readFile(new URL("features/route-states/route-states.module.css", sourceRoot), "utf8");
  const guard = css.indexOf("@media (prefers-reduced-motion: no-preference)");
  assert.ok(guard >= 0);
  assert.doesNotMatch(css.slice(0, guard), /animation:/);
  assert.match(css.slice(guard), /animation: shimmer/);
  assert.match(css, /var\(--divider\)/);
});

test("TrackMap returns distinct closed single-path outlines", () => {
  const paths = {
    monaco: trackMap.trackOutlinePath("monaco"),
    jeddah: trackMap.trackOutlinePath("jeddah"),
    sakhir: trackMap.trackOutlinePath("sakhir"),
    generic: trackMap.trackOutlinePath("unknown-track"),
  };
  assert.equal(new Set(Object.values(paths)).size, 4);
  for (const [name, d] of Object.entries(paths)) {
    assert.equal(d.match(/M/g)?.length, 1, `${name} must be a single subpath`);
    assert.match(d, /^M/, name);
    assert.match(d, /[zZ]$/, `${name} must be closed`);
  }
  assert.equal(trackMap.trackOutlinePath("bahrain"), paths.sakhir);
  assert.equal(trackMap.trackOutlinePath("constructor"), paths.generic);
  assert.equal(trackMap.trackKeyFromName("Jeddah"), "jeddah");
  assert.equal(trackMap.trackKeyFromName("Sakhir (Bahrain)"), "sakhir");
  assert.equal(trackMap.trackKeyFromName("Bahrain"), "bahrain");
});

test("TrackMap keeps shared stroke styling and viewBox for every outline", () => {
  const monaco = reactServer.renderToStaticMarkup(trackMap.TrackMap({ track: "monaco" }));
  for (const track of ["jeddah", "sakhir", "bahrain"]) {
    const markup = reactServer.renderToStaticMarkup(trackMap.TrackMap({ track }));
    assert.match(markup, /viewBox="0 0 120 80"/, track);
    assert.equal(
      markup.replace(/ d="[^"]*"/, ""),
      monaco.replace(/ d="[^"]*"/, ""),
      `${track} markup differs from monaco beyond the path`,
    );
  }
});
