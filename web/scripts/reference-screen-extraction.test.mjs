import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { runInThisContext } from "node:vm";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const repositoryRoot = fileURLToPath(new URL("../../", import.meta.url));
const appDirectory = path.join(repositoryRoot, "web/src/app");
const wrapperPath = path.join(appDirectory, "ReferenceScreens.tsx");
const baselineSource = execFileSync("git", [
  "show", "c89c785f999ea19aab6f4e33bad64c79dd099290:web/src/app/ReferenceScreens.tsx",
], { cwd: repositoryRoot, encoding: "utf8" });
const screens = [
  "Dashboard", "Live", "Engineer", "Compare",
  "Settings", "Recordings", "Track", "Sessions",
];
const primitives = [
  "FlagIT", "Panel", "PageHeading", "Badge", "ActionLink", "Metric", "ChipRow",
];
const normalize = (source) => source.replace(/\r\n/g, "\n");
const parse = (source) => ts.createSourceFile(
  "screen.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX,
);
const findFunction = (source, name) => source.statements.find(
  (statement) => ts.isFunctionDeclaration(statement) && statement.name.text === name,
);

test("screen and primitive bodies, signatures and server boundaries remain unchanged", () => {
  const baseline = parse(baselineSource);
  for (const name of ["ReferenceScreen", ...screens, ...primitives]) {
    const filename = name === "ReferenceScreen" ? wrapperPath
      : path.join(appDirectory, "screens", primitives.includes(name)
        ? "ScreenPrimitives.tsx" : `${name}Screen.tsx`);
    const extracted = parse(readFileSync(filename, "utf8"));
    const expected = findFunction(baseline, name);
    const actual = findFunction(extracted, name);
    assert.ok(actual, name);
    assert.equal(normalize(actual.body.getText(extracted)), normalize(expected.body.getText(baseline)), name);
    assert.deepEqual(
      actual.parameters.map((parameter) => normalize(parameter.getText(extracted))),
      expected.parameters.map((parameter) => normalize(parameter.getText(baseline))),
      name,
    );
    assert.ok(!extracted.statements.some((statement) =>
      ts.isExpressionStatement(statement) && ts.isStringLiteral(statement.expression)
        && statement.expression.text === "use client"), name);
  }
});

test("public screen type and default wrapper export remain unchanged", () => {
  const baseline = parse(baselineSource);
  const extracted = parse(readFileSync(wrapperPath, "utf8"));
  assert.equal(
    normalize(extracted.statements.find(ts.isTypeAliasDeclaration).getText(extracted)),
    normalize(baseline.statements.find(ts.isTypeAliasDeclaration).getText(baseline)),
  );
  assert.ok(findFunction(extracted, "ReferenceScreen").modifiers.some(
    (modifier) => modifier.kind === ts.SyntaxKind.DefaultKeyword,
  ));
  assert.equal(extracted.statements.filter(ts.isFunctionDeclaration).length, 1);
});

function loadReferenceScreen(sourceOverride) {
  const cache = new Map();
  const nativeRequire = createRequire(import.meta.url);
  function load(filename) {
    if (cache.has(filename)) return cache.get(filename).exports;
    const source = filename === wrapperPath && sourceOverride !== undefined
      ? sourceOverride : readFileSync(filename, "utf8");
    const compiled = ts.transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
        esModuleInterop: true,
      },
      fileName: filename,
    }).outputText;
    const loaded = { exports: {} };
    cache.set(filename, loaded);
    function requireModule(specifier) {
      if (specifier.endsWith("/DashboardLiveTelemetry")) {
        return {
          default: ({ preservedQuery }) => React.createElement("div", {
            "data-dashboard-live-query": preservedQuery,
          }),
          __esModule: true,
        };
      }
      if (specifier === "@/lib/navigation") {
        return load(path.join(repositoryRoot, "web/src/lib/navigation.ts"));
      }
      if (specifier.startsWith(".")) {
        return load(path.resolve(path.dirname(filename), `${specifier}.tsx`));
      }
      return nativeRequire(specifier);
    }
    runInThisContext(`(function(require, module, exports) {${compiled}\n})`, {
      filename,
    })(requireModule, loaded, loaded.exports);
    return loaded.exports;
  }
  return load(wrapperPath).default;
}

test("all eight screens render identical markup with preserved or blocked selection", () => {
  const baseline = loadReferenceScreen(baselineSource);
  const extracted = loadReferenceScreen();
  const queries = [
    "",
    "session_key=session-42&target_attempt_key=attempt-7&reference_choice=best",
    "test_data=1&test_session=monza&test_target=lap-2&test_reference=lap-1",
    "live_source=replay&live_operation_id=" + "a".repeat(32),
    "selection_transfer=blocked",
    "session_key=" + "x".repeat(513),
  ];
  for (const name of screens) {
    for (const preservedQuery of queries) {
      const props = { screen: name.toLowerCase(), preservedQuery };
      assert.equal(
        renderToStaticMarkup(React.createElement(extracted, props)),
        renderToStaticMarkup(React.createElement(baseline, props)),
        `${name}: ${preservedQuery}`,
      );
    }
  }
});
