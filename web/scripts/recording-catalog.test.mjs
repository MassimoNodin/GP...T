import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(
  new URL("../src/lib/recording-catalog.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const catalog = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

function capture(captureId, overrides = {}) {
  return {
    capture_id: captureId,
    display_name: "capture.f1ecap",
    byte_size: 128,
    modified_at_utc: "2026-10-06T00:00:00+00:00",
    latest_job_id: null,
    latest_job_status: null,
    latest_job_run_id: null,
    available: true,
    download_version: "c".repeat(64),
    ...overrides,
  };
}

function page(overrides = {}) {
  return {
    items: [capture("a".repeat(32)), capture("b".repeat(32))],
    selected_capture: capture("c".repeat(32), {
      display_name: "off-page.f1ecap",
    }),
    total_count: 52,
    limit: 2,
    offset: 0,
    has_more: true,
    filters: {
      query: "%_",
      latest_job_status: "all",
      availability: "all",
      selected_capture_id: "c".repeat(32),
    },
    ...overrides,
  };
}

test("accepts a bounded page and preserves an exact off-page selection", () => {
  const parsed = catalog.readRecordingSourcePage(page());
  assert.equal(parsed.items.length, 2);
  assert.equal(parsed.total_count, 52);
  assert.equal(parsed.selected_capture.capture_id, "c".repeat(32));
  assert.equal(parsed.filters.query, "%_");
});

test("accepts empty pages and a selected capture included in the page", () => {
  const empty = catalog.readRecordingSourcePage(
    page({
      items: [],
      selected_capture: null,
      total_count: 0,
      has_more: false,
      filters: {
        query: "",
        latest_job_status: "all",
        availability: "all",
        selected_capture_id: null,
      },
    }),
  );
  assert.deepEqual(empty.items, []);

  const selected = capture("a".repeat(32));
  const included = catalog.readRecordingSourcePage(
    page({
      items: [selected],
      selected_capture: { ...selected },
      total_count: 1,
      limit: 25,
      has_more: false,
      filters: {
        query: "",
        latest_job_status: "all",
        availability: "all",
        selected_capture_id: selected.capture_id,
      },
    }),
  );
  assert.equal(included.selected_capture.capture_id, selected.capture_id);
});

test("rejects malformed, unbounded, duplicate, and mismatched selections", () => {
  assert.equal(catalog.readRecordingSourcePage(page({ limit: 51 })), null);
  assert.equal(
    catalog.readRecordingSourcePage(page({ total_count: 10_001 })),
    null,
  );
  assert.equal(
    catalog.readRecordingSourcePage(page({ has_more: false })),
    null,
  );
  assert.equal(
    catalog.readRecordingSourcePage(
      page({
        filters: {
          query: "",
          latest_job_status: "all",
          availability: "all",
          selected_capture_id: "d".repeat(32),
        },
      }),
    ),
    null,
  );
  assert.equal(
    catalog.readRecordingSourcePage(
      page({
        items: [capture("a".repeat(32)), capture("a".repeat(32))],
      }),
    ),
    null,
  );
  assert.equal(
    catalog.readRecordingSourcePage(
      page({
        items: [capture("a".repeat(32), { display_name: "../private.f1ecap" })],
      }),
    ),
    null,
  );
  assert.equal(
    catalog.readRecordingSourcePage(
      page({
        items: [capture("a".repeat(32), { download_version: "invalid" })],
      }),
    ),
    null,
  );
});
