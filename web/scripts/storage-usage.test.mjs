import assert from "node:assert/strict";
import test from "node:test";
import {
  describeStorageReason,
  formatStorageBytes,
  parseStorageUsageResponse,
  STORAGE_SCOPE_KEYS,
  STORAGE_VOLUME_KEYS,
} from "../src/lib/storage-usage.mjs";

const availableScope = {
  status: "available",
  logical_bytes: 128,
  regular_file_count: 2,
  excluded_entry_count: 1,
  reason: null,
};

function response(overrides = {}) {
  return {
    api_version: "v1",
    status: "ok",
    data: {
      measurement_started_at_utc: "2026-10-06T00:00:00.000Z",
      measurement_completed_at_utc: "2026-10-06T00:00:01.000Z",
      measurement_note: "logical file sizes",
      scopes: Object.fromEntries(STORAGE_SCOPE_KEYS.map((key) => [key, { ...availableScope }])),
      volumes: Object.fromEntries(
        STORAGE_VOLUME_KEYS.map((key) => [
          key,
          { status: "available", total_bytes: 1024, free_bytes: 512, reason: null },
        ]),
      ),
      ...overrides,
    },
  };
}

test("accepts a complete bounded response and discards unknown fields", () => {
  const parsed = parseStorageUsageResponse(response({ server_path: "ignored" }));
  assert.equal(parsed.scopes.database.logical_bytes, 128);
  assert.equal(parsed.volumes.database_location.free_bytes, 512);
  assert.equal("server_path" in parsed, false);
});

test("rejects malformed, incomplete, unsafe, or contradictory measurements", () => {
  const missingScope = response();
  delete missingScope.data.scopes.imported_traces;
  assert.equal(parseStorageUsageResponse(missingScope), null);

  const fractionalBytes = response();
  fractionalBytes.data.scopes.database.logical_bytes = 1.5;
  assert.equal(parseStorageUsageResponse(fractionalBytes), null);

  const oversizedBytes = response();
  oversizedBytes.data.scopes.database.logical_bytes = Number.MAX_SAFE_INTEGER + 1;
  assert.equal(parseStorageUsageResponse(oversizedBytes), null);

  const invalidVolume = response();
  invalidVolume.data.volumes.database_location.free_bytes = 2048;
  assert.equal(parseStorageUsageResponse(invalidVolume), null);

  const partialWithBytes = response();
  partialWithBytes.data.scopes.database = {
    status: "unavailable",
    logical_bytes: 12,
    regular_file_count: null,
    excluded_entry_count: null,
    reason: "entry_limit_exceeded",
  };
  assert.equal(parseStorageUsageResponse(partialWithBytes), null);

  assert.equal(parseStorageUsageResponse({ ...response(), status: "unavailable" }), null);
});

test("unknown reason keys always use the safe fallback text", () => {
  assert.equal(describeStorageReason("constructor"), "Measurement unavailable");
  assert.equal(describeStorageReason("__proto__"), "Measurement unavailable");
  assert.equal(describeStorageReason("entry_limit_exceeded"), "Scan limit reached; complete size unavailable");
});

test("formats logical sizes as binary units", () => {
  assert.equal(formatStorageBytes(0), "0 B");
  assert.equal(formatStorageBytes(1536), "1.5 KiB");
});
