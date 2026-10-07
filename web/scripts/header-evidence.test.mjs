import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const typescript = require("typescript");
const reactServer = await import(
  pathToFileURL(require.resolve("react-dom/server")).href
);
const jsxRuntimeUrl = pathToFileURL(require.resolve("react/jsx-runtime")).href;
const sourceRoot = new URL("../src/", import.meta.url);

function transpile(source, fileName, jsx = false) {
  const result = typescript.transpileModule(source, {
    fileName,
    compilerOptions: {
      module: typescript.ModuleKind.ESNext,
      target: typescript.ScriptTarget.ES2022,
      ...(jsx ? { jsx: typescript.JsxEmit.ReactJSX } : {}),
    },
  });
  return result.outputText;
}

async function sourceText(path) {
  return readFile(new URL(path, sourceRoot), "utf8");
}

const navigationSource = transpile(
  await sourceText("lib/navigation.ts"),
  "navigation.ts",
);
const navigationUrl = `data:text/javascript;base64,${Buffer.from(navigationSource).toString("base64")}`;

const evidenceSource = transpile(
  await sourceText("lib/header-evidence.ts"),
  "header-evidence.ts",
);
const evidenceUrl = `data:text/javascript;base64,${Buffer.from(evidenceSource).toString("base64")}`;

const apiStubUrl = `data:text/javascript,${encodeURIComponent(`export async function requestApi(path) { return globalThis.__headerEvidenceRequestApi(path); }`)}`;
let headerSource = await sourceText("app/AppHeader.tsx");
headerSource = headerSource
  .replace('from "@/lib/navigation"', `from ${JSON.stringify(navigationUrl)}`)
  .replace(
    'from "@/lib/header-evidence"',
    `from ${JSON.stringify(evidenceUrl)}`,
  )
  .replace('from "@/lib/api"', `from ${JSON.stringify(apiStubUrl)}`)
  .replace('from "react/jsx-runtime"', `from ${JSON.stringify(jsxRuntimeUrl)}`);
const headerModuleSource = transpile(
  headerSource,
  "AppHeader.tsx",
  true,
).replace('from "react/jsx-runtime"', `from ${JSON.stringify(jsxRuntimeUrl)}`);
const resolvedHeaderUrl = `data:text/javascript;base64,${Buffer.from(headerModuleSource).toString("base64")}`;
const { default: AppHeader } = await import(resolvedHeaderUrl);
const { recordingHeaderEvidence } = await import(evidenceUrl);

const validEnvelope = (data) => ({
  api_version: "v1",
  status: "ok",
  data,
  reason: null,
  extra: "permitted",
});

test("recording header evidence recognizes the supported job states", () => {
  const nullPrototypeJob = Object.assign(Object.create(null), {
    recording_id: "0123456789abcdef0123456789abcdef",
    status: "recording",
    job_extra: "permitted",
  });
  const nullPrototypeEnvelope = Object.assign(Object.create(null), {
    api_version: "v1",
    status: "ok",
    data: nullPrototypeJob,
    reason: null,
    envelope_extra: "permitted",
  });
  assert.deepEqual(recordingHeaderEvidence(nullPrototypeEnvelope), {
    active: true,
    label: "Recording active",
  });

  const cases = [
    [null, { active: false, label: "Recording inactive" }],
    [
      { recording_id: "0123456789abcdef0123456789abcdef", status: "starting" },
      { active: true, label: "Recording starting" },
    ],
    [
      { recording_id: "0123456789abcdef0123456789abcdef", status: "recording" },
      { active: true, label: "Recording active" },
    ],
    [
      { recording_id: "0123456789abcdef0123456789abcdef", status: "stopping" },
      { active: true, label: "Recording stopping" },
    ],
    [
      { recording_id: "0123456789abcdef0123456789abcdef", status: "complete" },
      { active: false, label: "Recording inactive" },
    ],
    [
      { recording_id: "0123456789abcdef0123456789abcdef", status: "failed" },
      { active: false, label: "Recording failed" },
    ],
    [
      {
        recording_id: "0123456789abcdef0123456789abcdef",
        status: "interrupted",
      },
      { active: false, label: "Recording interrupted" },
    ],
  ];

  for (const [data, expected] of cases) {
    assert.deepEqual(recordingHeaderEvidence(validEnvelope(data)), expected);
  }
});

test("malformed envelopes, jobs, statuses, and recording IDs are unavailable", () => {
  const validJob = {
    recording_id: "0123456789abcdef0123456789abcdef",
    status: "recording",
  };
  const malformed = [
    undefined,
    null,
    "response",
    [],
    new Date(),
    Object.assign(Object.create({ api_version: "v1" }), {
      status: "ok",
      data: validJob,
      reason: null,
    }),
    Object.assign(Object.create({ status: "ok" }), {
      api_version: "v1",
      data: validJob,
      reason: null,
    }),
    Object.assign(Object.create({ reason: null }), {
      api_version: "v1",
      status: "ok",
      data: validJob,
    }),
    Object.assign(Object.create({ data: validJob }), {
      api_version: "v1",
      status: "ok",
      reason: null,
    }),
    { ...validEnvelope(validJob), api_version: "v2" },
    { ...validEnvelope(validJob), status: "unavailable" },
    { ...validEnvelope(validJob), reason: "unavailable" },
    { api_version: "v1", status: "ok", data: validJob },
    { api_version: "v1", status: "ok", reason: null },
    { api_version: "v1", status: "ok", data: undefined, reason: null },
    { ...validEnvelope("recording") },
    { ...validEnvelope([]) },
    { ...validEnvelope(new Date()) },
    {
      ...validEnvelope(
        Object.assign(Object.create({ recording_id: validJob.recording_id }), {
          status: "recording",
        }),
      ),
    },
    {
      ...validEnvelope(
        Object.assign(Object.create({ status: "recording" }), {
          recording_id: validJob.recording_id,
        }),
      ),
    },
    { ...validEnvelope({ recording_id: validJob.recording_id }) },
    { ...validEnvelope({ status: "recording" }) },
    { ...validEnvelope({ ...validJob, status: "Recording" }) },
    { ...validEnvelope({ ...validJob, status: "recording-extra" }) },
    { ...validEnvelope({ ...validJob, status: "starting " }) },
    ...[
      "0123456789abcdef0123456789abcde",
      "0123456789abcdef0123456789abcdef0",
      "0123456789ABCDEF0123456789ABCDEF",
      "0123456789abcdef0123456789abcdeg",
      "",
      null,
      123,
    ].map((recording_id) => validEnvelope({ ...validJob, recording_id })),
    ...["queued", "complete ", "FAILED", "", null, 3].map((status) =>
      validEnvelope({ ...validJob, status }),
    ),
  ];

  for (const response of malformed) {
    assert.deepEqual(recordingHeaderEvidence(response), {
      active: false,
      label: "Recording status unavailable",
    });
  }
});

async function renderHeader({ query = "", payload, reject = false } = {}) {
  const calls = [];
  globalThis.__headerEvidenceRequestApi = async (path) => {
    calls.push(path);
    if (reject) throw new Error("request failed");
    return payload;
  };
  const markup = await reactServer.renderToStaticMarkup(
    await AppHeader({ active: "dashboard", preservedQuery: query }),
  );
  return { calls, markup };
}

function assertNeutralAcquisitionDot(markup) {
  assert.match(
    markup,
    /class="topbar-dot-green" style="background:#94a3b8" aria-hidden="true"/,
  );
}

function assertGreenAcquisitionDot(markup) {
  assert.match(markup, /class="topbar-dot-green" aria-hidden="true"/);
  assert.doesNotMatch(markup, /class="topbar-dot-green" style=/);
}

test("header uses synthetic evidence without querying production API", async () => {
  const { calls, markup } = await renderHeader({
    query: "test_data=populated",
    payload: validEnvelope({
      recording_id: "0123456789abcdef0123456789abcdef",
      status: "recording",
    }),
  });
  assert.deepEqual(calls, []);
  assert.match(markup, /Synthetic example data/);
  assert.match(markup, /Synthetic examples; storage not measured/);
  assert.match(
    markup,
    /local-status-dot" style="background:#94a3b8;box-shadow:none" aria-hidden="true"/,
  );
  assert.doesNotMatch(
    markup,
    /rail-storage-track|21\.3%|All data stays on your computer|Local Mode|42\.6 GB|200 GB|privacy/i,
  );
});

test("invalid and empty synthetic selectors never query the production API", async () => {
  for (const query of ["test_data=invalid", "test_data="]) {
    const { calls, markup } = await renderHeader({ query });
    assert.deepEqual(calls, []);
    assert.match(markup, /Synthetic example data/);
    assert.match(markup, /Synthetic examples; storage not measured/);
    assert.doesNotMatch(
      markup,
      /rail-storage-track|21\.3%|All data stays on your computer|Local Mode/i,
    );
  }
});

test("production header renders evidence labels and keeps display claims grounded", async () => {
  const active = await renderHeader({
    payload: validEnvelope({
      recording_id: "0123456789abcdef0123456789abcdef",
      status: "recording",
    }),
  });
  assert.deepEqual(active.calls, ["/api/v1/recordings/current"]);
  assert.match(active.markup, /Recording active/);
  assert.match(active.markup, /Check runtime capabilities in Settings/);
  assert.match(active.markup, /Local-first design/);
  assertGreenAcquisitionDot(active.markup);
  assert.doesNotMatch(
    active.markup,
    /Local Mode|UDP Recording|\bquota\b|privacy|42\.6 GB|200 GB/i,
  );

  const inactive = await renderHeader({ payload: validEnvelope(null) });
  assert.match(inactive.markup, /Recording inactive/);

  const malformed = await renderHeader({
    payload: { status: "ok", data: null },
  });
  assert.match(malformed.markup, /Recording status unavailable/);
  assertNeutralAcquisitionDot(malformed.markup);

  for (const status of ["failed", "interrupted"]) {
    const inactiveTerminal = await renderHeader({
      payload: validEnvelope({
        recording_id: "0123456789abcdef0123456789abcdef",
        status,
      }),
    });
    assert.match(inactiveTerminal.markup, new RegExp(`Recording ${status}`));
    assertNeutralAcquisitionDot(inactiveTerminal.markup);
  }

  for (const status of ["starting", "recording", "stopping"]) {
    const validatedActive = await renderHeader({
      payload: validEnvelope({
        recording_id: "0123456789abcdef0123456789abcdef",
        status,
      }),
    });
    assertGreenAcquisitionDot(validatedActive.markup);
  }

  const rejected = await renderHeader({ reject: true });
  assert.match(rejected.markup, /Recording status unavailable/);
});

test("Settings preserves query state and targets storage", async () => {
  const { markup } = await renderHeader({
    query: "session_key=run%3A42&recording_q=latest",
    payload: validEnvelope(null),
  });
  assert.match(
    markup,
    /href="\/settings\?session_key=run%3A42&amp;recording_q=latest#storage"/,
  );
  const testDataHref = markup.match(/<a class="ref-button" href="([^"]+)"/);
  assert.equal(testDataHref?.[1], "/settings?test_data=populated");
  assert.doesNotMatch(testDataHref?.[1] ?? "", /session_key/);
});

test("blocked selection transfer disables navigation and test-data action", async () => {
  const { markup } = await renderHeader({
    query: "selection_transfer=blocked",
    payload: validEnvelope(null),
  });
  assert.match(markup, /Selection is too large to carry safely/);
  assert.match(
    markup,
    /aria-disabled="true"[^>]*aria-label="Settings"|aria-label="Settings"[^>]*aria-disabled="true"/,
  );
  assert.match(markup, /Test data[^<]*<\/span>/);
  assert.doesNotMatch(markup, /href="\/settings\?test_data=populated"/);
  const interactiveMarkup = markup.replace(
    /<a class="skip-link"[^>]*>.*?<\/a>/,
    "",
  );
  assert.equal((interactiveMarkup.match(/<a\b/g) ?? []).length, 0);
});
