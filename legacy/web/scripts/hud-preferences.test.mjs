import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const storage = new Map();
const eventListeners = new Map();
let readsBlocked = false;
let writesBlocked = false;
let writeCount = 0;
globalThis.window = {
  localStorage: {
    getItem(key) {
      if (readsBlocked) throw new Error("storage read blocked");
      return storage.get(key) ?? null;
    },
    setItem(key, value) {
      if (writesBlocked) throw new Error("storage write blocked");
      writeCount += 1;
      storage.set(key, value);
    },
  },
  addEventListener(type, listener) {
    const listeners = eventListeners.get(type) ?? new Set();
    listeners.add(listener);
    eventListeners.set(type, listeners);
  },
  removeEventListener(type, listener) {
    eventListeners.get(type)?.delete(listener);
  },
};

const source = readFileSync(
  new URL("../src/lib/local-hud-preferences.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const preferences = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

function dispatchStorage(key) {
  for (const listener of eventListeners.get("storage") ?? []) {
    listener({ key });
  }
}

test("HUD preferences write atomically, preserve unknown versions, and sync tabs", () => {
  const unsubscribe = preferences.subscribeToLocalHudPreferences(() => {});
  const key = preferences.LOCAL_HUD_PREFERENCE_STORAGE_KEY;
  assert.equal(preferences.getLocalHudPreferenceSnapshot().status, "available");

  const saved = {
    alignment: "bottom-right",
    background_opacity: 0.75,
    text_scale: 1.2,
    theme: "light",
  };
  assert.deepEqual(preferences.applyLocalHudPreferences(saved), { ok: true });
  assert.equal(writeCount, 1);
  assert.deepEqual(JSON.parse(storage.get(key)), {
    schema_version: 1,
    ...saved,
  });
  assert.deepEqual(
    preferences.getLocalHudPreferenceSnapshot().effective,
    saved,
  );

  const fromAnotherTab = {
    schema_version: 1,
    alignment: "top-right",
    background_opacity: 0.85,
    text_scale: 1,
    theme: "dark",
  };
  storage.set(key, JSON.stringify(fromAnotherTab));
  dispatchStorage(key);
  assert.equal(
    preferences.getLocalHudPreferenceSnapshot().effective.alignment,
    "top-right",
  );

  const unknownVersion = JSON.stringify({
    schema_version: 9,
    alignment: "top-left",
    background_opacity: 0.9,
    text_scale: 1,
    theme: "dark",
  });
  storage.set(key, unknownVersion);
  const writesBeforeUnknown = writeCount;
  dispatchStorage(key);
  assert.equal(preferences.getLocalHudPreferenceSnapshot().status, "invalid");
  assert.equal(storage.get(key), unknownVersion);
  assert.equal(writeCount, writesBeforeUnknown);

  assert.deepEqual(
    preferences.applyLocalHudPreferences(preferences.DEFAULT_HUD_PREFERENCES),
    { ok: true },
  );
  assert.equal(writeCount, writesBeforeUnknown + 1);

  const oversized = "x".repeat(preferences.LOCAL_HUD_PREFERENCE_MAX_BYTES + 1);
  storage.set(key, oversized);
  dispatchStorage(key);
  assert.equal(preferences.getLocalHudPreferenceSnapshot().status, "invalid");
  assert.equal(storage.get(key), oversized);

  readsBlocked = true;
  dispatchStorage(key);
  assert.equal(
    preferences.getLocalHudPreferenceSnapshot().status,
    "unavailable",
  );
  readsBlocked = false;

  writesBlocked = true;
  const beforeFailedWrite =
    preferences.getLocalHudPreferenceSnapshot().effective;
  const failed = preferences.applyLocalHudPreferences(saved);
  assert.equal(failed.ok, false);
  assert.deepEqual(
    preferences.getLocalHudPreferenceSnapshot().effective,
    beforeFailedWrite,
  );
  writesBlocked = false;
  unsubscribe();
  delete globalThis.window;
});
