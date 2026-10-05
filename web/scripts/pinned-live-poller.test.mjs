import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(
  new URL("../src/lib/pinned-live-poller.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const pollerModule = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

const initialSnapshot = {
  source: "replay",
  operation_id: "a".repeat(32),
  state: "paused",
  live_telemetry: { status: "fresh", speed_kph: 220 },
  live_lap_timing: { status: "fresh", lap_number: 2 },
};
const nextSnapshot = { ...initialSnapshot, state: "playing" };

class FakeScheduler {
  now = 0;
  nextId = 0;
  tasks = [];

  schedule = (callback, delayMs) => {
    const task = {
      id: ++this.nextId,
      at: this.now + delayMs,
      callback,
      cancelled: false,
    };
    this.tasks.push(task);
    return task;
  };

  cancel = (task) => {
    if (task) task.cancelled = true;
  };

  async advanceBy(milliseconds) {
    const target = this.now + milliseconds;
    while (true) {
      this.tasks.sort(
        (first, second) => first.at - second.at || first.id - second.id,
      );
      const task = this.tasks.find(
        (candidate) => !candidate.cancelled && candidate.at <= target,
      );
      if (!task) break;
      task.cancelled = true;
      this.now = task.at;
      task.callback();
      await flushPromises();
    }
    this.now = target;
    await flushPromises();
  }

  pendingCount() {
    return this.tasks.filter((task) => !task.cancelled).length;
  }
}

function hangingFetch(_input, { signal }) {
  return new Promise((_resolve, reject) => {
    signal.addEventListener("abort", () => reject(new Error("aborted")), {
      once: true,
    });
  });
}

function activeRead(_source, _operationId, value) {
  return value === "next"
    ? { status: "ready", snapshot: nextSnapshot }
    : { status: "ready", snapshot: initialSnapshot };
}

function makePoller({ scheduler, fetchCurrent, initial = initialSnapshot }) {
  return pollerModule.createPinnedLivePoller({
    initialStatus: "ready",
    initialSnapshot: initial,
    source: "replay",
    operationId: initialSnapshot.operation_id,
    readCurrent: activeRead,
    isActive: (snapshot) =>
      snapshot.state === "playing" || snapshot.state === "paused",
    fetchCurrent,
    schedule: scheduler.schedule,
    cancelSchedule: scheduler.cancel,
    requestDeadlineMs: 5000,
    activeIntervalMs: 500,
    retryIntervalMs: 1000,
  });
}

test("hidden tabs abort reads, clear values, and resume only the same pin", async () => {
  const scheduler = new FakeScheduler();
  const signals = [];
  let calls = 0;
  const poller = makePoller({
    scheduler,
    fetchCurrent: (_input, { signal }) => {
      calls += 1;
      signals.push(signal);
      if (calls === 1) return hangingFetch("", { signal });
      return Promise.resolve({ ok: true, json: async () => "next" });
    },
  });

  poller.start();
  await scheduler.advanceBy(500);
  assert.equal(calls, 1);
  poller.setHidden(true);
  assert.equal(signals[0].aborted, true);
  assert.equal(poller.getState().status, "suspended");
  assert.equal(poller.getState().snapshot, null);
  await flushPromises();
  assert.equal(poller.getState().status, "suspended");

  poller.setHidden(false);
  await scheduler.advanceBy(0);
  assert.equal(calls, 2);
  assert.equal(poller.getState().snapshot.state, "playing");
  poller.stop();
});

test("a cancelled JSON read cannot publish or create a second poll chain", async () => {
  const scheduler = new FakeScheduler();
  let resolveOldJson;
  const oldJson = new Promise((resolve) => {
    resolveOldJson = resolve;
  });
  const signals = [];
  let calls = 0;
  const poller = makePoller({
    scheduler,
    fetchCurrent: (_input, { signal }) => {
      calls += 1;
      signals.push(signal);
      return calls === 1
        ? Promise.resolve({ ok: true, json: () => oldJson })
        : Promise.resolve({ ok: true, json: async () => "next" });
    },
  });

  poller.start();
  await scheduler.advanceBy(500);
  poller.setHidden(true);
  poller.setHidden(false);
  await scheduler.advanceBy(0);
  assert.equal(poller.getState().snapshot.state, "playing");
  assert.equal(signals[0].aborted, true);

  resolveOldJson("old");
  await flushPromises();
  assert.equal(poller.getState().snapshot.state, "playing");
  assert.equal(scheduler.pendingCount(), 1);
  poller.stop();
});

test("request deadline clears values and schedules a bounded retry", async () => {
  const scheduler = new FakeScheduler();
  const signals = [];
  const poller = makePoller({
    scheduler,
    fetchCurrent: (_input, { signal }) => {
      signals.push(signal);
      return hangingFetch("", { signal });
    },
  });

  poller.start();
  await scheduler.advanceBy(500);
  assert.equal(signals.length, 1);
  await scheduler.advanceBy(5000);
  assert.equal(signals[0].aborted, true);
  assert.equal(poller.getState().status, "error");
  assert.equal(poller.getState().snapshot, null);
  assert.equal(scheduler.pendingCount(), 1);
  poller.stop();
  assert.equal(scheduler.pendingCount(), 0);
});

test("unmount aborts an in-flight request and prevents later publication", async () => {
  const scheduler = new FakeScheduler();
  const signals = [];
  let updates = 0;
  const poller = makePoller({
    scheduler,
    fetchCurrent: (_input, { signal }) => {
      signals.push(signal);
      return hangingFetch("", { signal });
    },
  });
  poller.subscribe(() => (updates += 1));
  poller.start();
  await scheduler.advanceBy(500);
  assert.equal(signals.length, 1);
  const publishedBeforeStop = updates;
  poller.stop();
  assert.equal(signals[0].aborted, true);
  await flushPromises();
  assert.equal(updates, publishedBeforeStop);
  assert.equal(scheduler.pendingCount(), 0);
});

test("invalid links never start a request or react to tab visibility", async () => {
  const scheduler = new FakeScheduler();
  let calls = 0;
  const poller = pollerModule.createPinnedLivePoller({
    initialStatus: "invalid",
    initialSnapshot: null,
    source: null,
    operationId: null,
    readCurrent: activeRead,
    isActive: () => true,
    fetchCurrent: async () => {
      calls += 1;
      return { ok: true, json: async () => "next" };
    },
    schedule: scheduler.schedule,
    cancelSchedule: scheduler.cancel,
  });
  poller.start();
  poller.setHidden(true);
  poller.setHidden(false);
  await scheduler.advanceBy(5000);
  assert.equal(calls, 0);
  assert.equal(poller.getState().status, "invalid");
});

async function flushPromises() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}
