import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(
  new URL("../src/lib/import-queue.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { readImportQueueEnvelope } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

const queue = {
  waiting_count: 0,
  waiting_jobs: [],
  running_job: null,
  blocking_reservation: "idle",
};

test("accepts the supported v1 queue envelope", () => {
  assert.deepEqual(
    readImportQueueEnvelope(true, {
      api_version: "v1",
      status: "ok",
      data: queue,
    }),
    queue,
  );
});

test("rejects a missing or incompatible API version", () => {
  assert.equal(
    readImportQueueEnvelope(true, { status: "ok", data: queue }),
    null,
  );
  assert.equal(
    readImportQueueEnvelope(true, {
      api_version: "v2",
      status: "ok",
      data: queue,
    }),
    null,
  );
});

test("rejects failed HTTP responses and invalid payloads", () => {
  const envelope = { api_version: "v1", status: "ok", data: queue };
  assert.equal(readImportQueueEnvelope(false, envelope), null);
  assert.equal(
    readImportQueueEnvelope(true, { ...envelope, status: "unavailable" }),
    null,
  );
  assert.equal(
    readImportQueueEnvelope(true, {
      ...envelope,
      data: { ...queue, waiting_count: 17 },
    }),
    null,
  );
});
