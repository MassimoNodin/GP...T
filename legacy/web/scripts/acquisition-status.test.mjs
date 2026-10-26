import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(
  new URL("../src/lib/acquisition-status.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { parseAcquisitionStatus, createAcquisitionPoller } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const sample = {
  state: "receiving",
  received: 100,
  journaled: 99,
  processed: 96,
  dropped: 1,
  kernel_dropped: null,
  socket_errors: 0,
  queue_depth: 1,
  queue_limit: 1024,
  pending_publication: 3,
  last_publication_delay_s: 0.025,
};
const flush = async () => {
  for (let turn = 0; turn < 12; turn++) await Promise.resolve();
};

test("preserves unknown versus zero kernel losses and strips backend details", () => {
  assert.deepEqual(
    parseAcquisitionStatus({
      ...sample,
      error: "private path",
      host: "private host",
    }),
    sample,
  );
  assert.equal(
    parseAcquisitionStatus({ ...sample, kernel_dropped: 0 }).kernel_dropped,
    0,
  );
  assert.equal(
    parseAcquisitionStatus({ ...sample, state: "stale" }).state,
    "stale",
  );
  assert.equal(parseAcquisitionStatus({ state: "disabled" }).received, null);
});

test("rejects malformed and unbounded diagnostics", () => {
  for (const value of [
    null,
    [],
    {},
    { state: "unavailable" },
    { ...sample, received: -1 },
    { ...sample, journaled: 1.5 },
    { ...sample, processed: Number.MAX_SAFE_INTEGER + 1 },
    { ...sample, kernel_dropped: undefined },
    { ...sample, kernel_dropped: -1 },
    { ...sample, last_publication_delay_s: Infinity },
    { ...sample, queue_limit: 4097 },
    { ...sample, queue_depth: 1025 },
  ]) {
    assert.equal(parseAcquisitionStatus(value), null);
  }
});

test("polls read-only, clears failed responses, and recovers", async (context) => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  const values = [];
  let calls = 0;
  const poller = createAcquisitionPoller({
    onStatus: (value) => values.push(value),
    fetchStatus: async (url, init) => {
      assert.equal(url, "/api/session-evidence/status");
      assert.equal(init.cache, "no-store");
      assert.ok(init.signal instanceof AbortSignal);
      calls += 1;
      return { ok: calls !== 2, json: async () => sample };
    },
  });
  context.after(() => poller.stop());
  poller.setHidden(false);
  await flush();
  assert.deepEqual(values.at(-1), sample);
  context.mock.timers.tick(1000);
  await flush();
  assert.equal(values.at(-1), null);
  context.mock.timers.tick(1000);
  await flush();
  assert.deepEqual(values.at(-1), sample);
});

test("hiding aborts and clears values; late responses cannot replace resumed status", async (context) => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  const values = [];
  let resolveOld;
  let oldSignal;
  let calls = 0;
  const poller = createAcquisitionPoller({
    onStatus: (value) => values.push(value),
    fetchStatus: (_url, init) => {
      calls += 1;
      if (calls === 1) {
        oldSignal = init.signal;
        return new Promise((resolve) => {
          resolveOld = resolve;
        });
      }
      return Promise.resolve({ ok: true, json: async () => sample });
    },
  });
  context.after(() => poller.stop());
  context.mock.timers.tick(10000);
  assert.equal(calls, 0);
  poller.setHidden(false);
  poller.setHidden(true);
  assert.equal(oldSignal.aborted, true);
  assert.equal(values.at(-1), null);
  poller.setHidden(false);
  await flush();
  assert.deepEqual(values.at(-1), sample);
  resolveOld({ ok: true, json: async () => ({ ...sample, received: 2 }) });
  await flush();
  assert.deepEqual(values.at(-1), sample);
  poller.stop();
  context.mock.timers.tick(10000);
  await flush();
  assert.equal(calls, 2);
});

test("deadline clears stalled body and retries without accepting its late result", async (context) => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  const values = [];
  let resolveBody;
  let calls = 0;
  let signal;
  const poller = createAcquisitionPoller({
    onStatus: (value) => values.push(value),
    fetchStatus: async (_url, init) => {
      calls += 1;
      signal = init.signal;
      return {
        ok: true,
        json:
          calls === 1
            ? () =>
                new Promise((resolve) => {
                  resolveBody = resolve;
                })
            : async () => sample,
      };
    },
  });
  context.after(() => poller.stop());
  poller.setHidden(false);
  await flush();
  context.mock.timers.tick(5000);
  await flush();
  assert.equal(signal.aborted, true);
  assert.equal(values.at(-1), null);
  context.mock.timers.tick(1000);
  await flush();
  assert.deepEqual(values.at(-1), sample);
  resolveBody({ ...sample, received: 2 });
  await flush();
  assert.deepEqual(values.at(-1), sample);
});
