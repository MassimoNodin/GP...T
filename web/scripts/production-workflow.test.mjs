import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const routes = [
  ["page.tsx", "dashboard-workflow", "AttemptQualityPanel"],
  ["sessions/page.tsx", "sessions-workflow", "SessionsRunEvidence"],
  ["recordings/page.tsx", "recording-workflow", "RecordingInbox"],
  ["engineer/page.tsx", "engineer-workflow", "EngineerQuestionComposer"],
  ["settings/page.tsx", "settings-workflow", "SettingsStorageUsage"],
];

function sourceFile(relativePath) {
  return readFileSync(
    new URL(`../src/app/${relativePath}`, import.meta.url),
    "utf8",
  );
}

function jsxElements(source) {
  const parsed = ts.createSourceFile(
    "page.tsx",
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
  const elements = [];
  function visit(node) {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node))
      elements.push(node);
    ts.forEachChild(node, visit);
  }
  visit(parsed);
  return elements;
}

for (const [relativePath, workflowId, evidenceComponent] of routes) {
  test(`${relativePath} keeps real evidence primary and synthetic mode isolated`, () => {
    const source = sourceFile(relativePath);
    const elements = jsxElements(source);
    assert.doesNotMatch(
      source,
      /ReferenceScreen|reference-page-content|ref-workflow-(details|content)/,
    );
    assert.ok(
      elements.some((node) => node.tagName.getText() === evidenceComponent),
    );
    const main = elements.find((node) => node.tagName.getText() === "main");
    assert.ok(main);
    assert.match(main.getText(), /id="main-content"/);
    assert.match(main.getText(), /tabIndex=\{-1\}/);
    const workflow = elements.find((node) =>
      node.attributes.properties.some(
        (attribute) =>
          ts.isJsxAttribute(attribute) &&
          attribute.name.getText() === "id" &&
          attribute.initializer &&
          ts.isStringLiteral(attribute.initializer) &&
          attribute.initializer.text === workflowId,
      ),
    );
    assert.ok(workflow, "existing workflow fragment remains addressable");
    assert.notEqual(workflow.tagName.getText(), "details");
    const branch = source.indexOf("if (syntheticPage) return syntheticPage;");
    assert.ok(
      branch >= 0 && branch < source.indexOf('<div className="app-shell">'),
    );
    const productionRead = source.indexOf(
      "requestApi<",
      source.indexOf("export default"),
    );
    if (productionRead >= 0) assert.ok(branch < productionRead);
  });
}

test("shared shell retains observed status without invented quota or window controls", () => {
  const source = sourceFile("AppHeader.tsx");
  assert.match(source, /recordingHeaderEvidence/);
  assert.match(source, /Storage details in Settings/);
  assert.doesNotMatch(
    source,
    /42\.6 GB|200 GB|21\.3%|topbar-window-controls|All data stays on your computer/,
  );
  assert.match(source, /Check runtime capabilities in Settings/);
});
