import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from "node:test";
const typescript = createRequire(import.meta.url)("typescript");
const source = await readFile(new URL("../src/features/settings/radio-settings.ts", import.meta.url), "utf8");
const code = typescript.transpileModule(source, { compilerOptions: { module: typescript.ModuleKind.ESNext } }).outputText;
const { radioStatus, browserMaySpeak } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
test("radio status distinguishes offline, waiting, and applied revisions", () => {
  assert.match(radioStatus({ connected: false }, 2), /not connected/);
  assert.match(radioStatus({ connected: true, applied_revision: "8:1" }, 2), /waiting/);
  assert.match(radioStatus({ connected: true, applied_revision: "8:2" }, 2), /applied \(rev 2\)/);
});
test("radio temporarily suppresses browser speech without changing stored choice", () => {
  assert.equal(browserMaySpeak(true, true), false);
  assert.equal(browserMaySpeak(true, false), true);
  assert.equal(browserMaySpeak(false, false), false);
});
