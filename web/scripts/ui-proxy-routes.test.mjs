import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { mock, test } from "node:test";

// Route Handler tests for the JSON proxy routes added for the new UI areas.
// The real local-proxy.ts and proxy-json.ts run; only global `fetch` (the
// local engine) is stubbed, so same-origin checks, token handling, bounds and
// pass-through are exercised end to end.

const require = createRequire(import.meta.url);
const typescript = require("typescript");
const webRoot = new URL("../src/", import.meta.url);

function transpile(source, fileName) {
  return typescript.transpileModule(source, {
    fileName,
    compilerOptions: {
      module: typescript.ModuleKind.ESNext,
      target: typescript.ScriptTarget.ES2022,
    },
  }).outputText;
}

function toDataUrl(code) {
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}

async function loadUrl(path, replacements = {}) {
  let code = transpile(await readFile(new URL(path, webRoot), "utf8"), path);
  for (const [specifier, url] of Object.entries(replacements)) {
    code = code.replaceAll(`from "${specifier}"`, `from ${JSON.stringify(url)}`);
  }
  return toDataUrl(code);
}

const TOKEN = "t".repeat(48);
const workDir = await mkdtemp(join(tmpdir(), "ui-proxy-routes-"));
const tokenFile = join(workDir, "control-token");
await writeFile(tokenFile, `${TOKEN}\n`);
process.env.F1_ENGINEER_CONTROL_TOKEN_FILE = tokenFile;
delete process.env.F1_ENGINEER_API_TOKEN_FILE;
delete process.env.F1_ENGINEER_WEB_ORIGIN;
process.env.F1_ENGINEER_API_URL = "http://127.0.0.1:8765";

globalThis.__nextServer = require("next/server");
const nextShim = toDataUrl("export const NextResponse = globalThis.__nextServer.NextResponse;\n");
const transportUrl = await loadUrl("lib/local-api-transport.ts");
const authorizationUrl = await loadUrl("lib/api-authorization.ts");
const localProxyUrl = await loadUrl("lib/local-proxy.ts", {
  "./api-authorization": authorizationUrl,
  "./local-api-transport": transportUrl,
});
const proxyJsonUrl = await loadUrl("lib/proxy-json.ts", { "@/lib/local-proxy": localProxyUrl });
const navigationUrl = await loadUrl("lib/navigation.ts");
const proxy = await import(proxyJsonUrl);

const routeReplacements = {
  "@/lib/proxy-json": proxyJsonUrl,
  "@/lib/local-proxy": localProxyUrl,
  "@/lib/navigation": navigationUrl,
  "next/server": nextShim,
};
const route = async (path) => import(await loadUrl(`app/api/${path}`, routeReplacements));

const routes = {
  laps: await route("review/sessions/[session]/laps/route.ts"),
  summary: await route("review/sessions/[session]/summary/route.ts"),
  sessionModels: await route("review/sessions/[session]/track-models/route.ts"),
  materialize: await route("review/sessions/[session]/attempts/[revision]/materialize/route.ts"),
  ask: await route("review/sessions/[session]/ask/route.ts"),
  models: await route("track-models/route.ts"),
  regions: await route("track-models/[model_id]/regions/route.ts"),
  events: await route("engineer/events/route.ts"),
  preferences: await route("engineer/preferences/route.ts"),
  sources: await route("recording-sources/page/route.ts"),
  importCreate: await route("import-jobs/route.ts"),
  importCancel: await route("import-jobs/[job_id]/cancel/route.ts"),
  importRetry: await route("import-jobs/[job_id]/retry/route.ts"),
};

/* ----------------------------------------------------------------- stubs */

const originalFetch = globalThis.fetch;
let upstream = [];
let respondWith = async () => Response.json({}, { status: 500 });

globalThis.fetch = async (url, init = {}) => {
  const headers = new Headers(init.headers);
  let body = null;
  if (init.body !== undefined && init.body !== null) {
    body = typeof init.body === "string" ? init.body : new TextDecoder().decode(init.body);
  }
  const call = { url: String(url), method: init.method ?? "GET", headers, body, signal: init.signal };
  upstream.push(call);
  return respondWith(call);
};
test.after(() => {
  globalThis.fetch = originalFetch;
});

function engine(handler) {
  upstream = [];
  respondWith = handler;
}

const TRUSTED = { origin: "http://127.0.0.1:3000", "sec-fetch-site": "same-origin" };
const JSON_ENVELOPE = (data, status = 200, headers = {}) =>
  Response.json({ api_version: "v1", status: "ok", data }, { status, headers });
const UNAVAILABLE = (reason, status, headers = {}) =>
  Response.json({ api_version: "v1", status: "unavailable", data: null, reason }, { status, headers });

function get(path, headers = TRUSTED) {
  return new Request(`http://127.0.0.1:3000${path}`, { headers });
}
function send(path, { method = "POST", body, headers = {}, trusted = true, signal } = {}) {
  return new Request(`http://127.0.0.1:3000${path}`, {
    method,
    body,
    signal,
    headers: { ...(trusted ? TRUSTED : {}), ...headers },
  });
}
const jsonHeaders = { "content-type": "application/json" };
const params = (value) => ({ params: Promise.resolve(value) });
const hasToken = (call) => call.headers.get("authorization") === `Bearer ${TOKEN}`;
const tick = () => new Promise((resolve) => setImmediate(resolve));
async function until(condition) {
  for (let attempt = 0; attempt < 500 && !condition(); attempt += 1) await tick();
  assert.ok(condition(), "condition was not reached");
}

/* ------------------------------------------------------- reads (no token) */

const SESSION = "4a09407f07885540b29221e7636ec37f";

test("GET proxies are same-origin only and never carry the control token", async () => {
  engine(async () => JSON_ENVELOPE({ laps: [] }));
  const blocked = await routes.laps.GET(
    get(`/api/review/sessions/${SESSION}/laps`, { "sec-fetch-site": "cross-site" }),
    params({ session: SESSION }),
  );
  assert.equal(blocked.status, 403);
  assert.equal((await blocked.json()).reason, "same_origin_request_required");
  assert.equal(upstream.length, 0, "an untrusted read must not reach the engine");

  const noHeaders = await routes.laps.GET(
    new Request(`http://127.0.0.1:3000/api/review/sessions/${SESSION}/laps`),
    params({ session: SESSION }),
  );
  assert.equal(noHeaders.status, 403);

  const ok = await routes.laps.GET(get(`/api/review/sessions/${SESSION}/laps`), params({ session: SESSION }));
  assert.equal(ok.status, 200);
  assert.equal(ok.headers.get("cache-control"), "no-store");
  assert.equal(ok.headers.get("content-type"), "application/json; charset=utf-8");
  assert.deepEqual((await ok.json()).data, { laps: [] });
  assert.equal(upstream[0].method, "GET");
  assert.equal(upstream[0].url, `http://127.0.0.1:8765/api/v2/session-evidence/sessions/${SESSION}/laps`);
  assert.equal(hasToken(upstream[0]), false);
  assert.equal(upstream[0].headers.has("authorization"), false);
});

test("session routes validate ids and forward to the right v2 endpoints", async () => {
  engine(async () => JSON_ENVELOPE({}));
  for (const [handler, suffix] of [
    [routes.summary, "summary"],
    [routes.sessionModels, "track-models"],
  ]) {
    upstream = [];
    const ok = await handler.GET(get(`/api/review/sessions/${SESSION}/${suffix}`), params({ session: SESSION }));
    assert.equal(ok.status, 200);
    assert.equal(upstream[0].url, `http://127.0.0.1:8765/api/v2/session-evidence/sessions/${SESSION}/${suffix}`);
    upstream = [];
    const bad = await handler.GET(get("/api/x"), params({ session: "../secrets" }));
    assert.equal(bad.status, 404);
    assert.equal(upstream.length, 0);
  }
});

test("upstream status, ETag and 409 bodies pass through unchanged", async () => {
  engine(async () => UNAVAILABLE("summary_busy", 409, { etag: '"3"' }));
  const response = await routes.summary.GET(get(`/api/review/sessions/${SESSION}/summary`), params({ session: SESSION }));
  assert.equal(response.status, 409);
  assert.equal(response.headers.get("etag"), '"3"');
  assert.equal((await response.json()).reason, "summary_busy");
});

test("GET relays are bounded: oversized, non-JSON, slow and unreachable engines", async () => {
  const request = () => get("/api/x");
  engine(async () => new Response("x".repeat(100), { headers: { "content-type": "application/json" } }));
  const big = await proxy.proxyGet(request(), "/api/v1/x", { maxResponseBytes: 10 });
  assert.equal(big.status, 502);
  assert.equal((await big.json()).reason, "upstream_response_limit_exceeded");

  engine(async () => new Response("<html></html>", { headers: { "content-type": "text/html" } }));
  const html = await proxy.proxyGet(request(), "/api/v1/x");
  assert.equal(html.status, 502);
  assert.equal((await html.json()).reason, "upstream_response_invalid");

  engine(async () => {
    throw new TypeError("fetch failed");
  });
  const down = await proxy.proxyGet(request(), "/api/v1/x");
  assert.equal(down.status, 503);
  assert.equal((await down.json()).reason, "local_engine_unavailable");

  engine((call) => new Promise((_resolve, reject) => call.signal.addEventListener("abort", () => reject(new Error("aborted")))));
  const slow = await proxy.proxyGet(request(), "/api/v1/x", { timeoutMs: 20 });
  assert.equal(slow.status, 504);
  assert.equal((await slow.json()).reason, "upstream_deadline_exceeded");
});

test("a client disconnect aborts the upstream read", async () => {
  engine((call) => new Promise((_resolve, reject) => call.signal.addEventListener("abort", () => reject(new Error("aborted")))));
  const controller = new AbortController();
  const request = new Request("http://127.0.0.1:3000/api/x", { headers: TRUSTED, signal: controller.signal });
  const pending = proxy.proxyGet(request, "/api/v1/x", { timeoutMs: 5000 });
  await until(() => upstream.length === 1);
  controller.abort();
  const response = await pending;
  assert.equal(response.status, 499);
  assert.equal(upstream[0].signal.aborted, true);
});

test("track model routes validate the exact revision", async () => {
  engine(async () => JSON_ENVELOPE({}));
  const list = await routes.models.GET(get("/api/track-models"));
  assert.equal(list.status, 200);
  assert.equal(upstream[0].url, "http://127.0.0.1:8765/api/v1/track-models");

  upstream = [];
  const ok = await routes.regions.GET(get("/api/track-models/monaco-2024/regions?revision=3"), params({ model_id: "monaco-2024" }));
  assert.equal(ok.status, 200);
  assert.equal(upstream[0].url, "http://127.0.0.1:8765/api/v1/track-models/monaco-2024/regions?revision=3");

  for (const [url, id, status] of [
    ["/api/track-models/monaco-2024/regions", "monaco-2024", 422],
    ["/api/track-models/monaco-2024/regions?revision=0", "monaco-2024", 422],
    ["/api/track-models/monaco-2024/regions?revision=1&revision=2", "monaco-2024", 422],
    ["/api/track-models/monaco-2024/regions?revision=1&x=1", "monaco-2024", 422],
    ["/api/track-models/a%2Fb/regions?revision=1", "a/b", 404],
  ]) {
    upstream = [];
    const response = await routes.regions.GET(get(url), params({ model_id: id }));
    assert.equal(response.status, status, url);
    assert.equal(upstream.length, 0, url);
  }
});

test("engineer events and recording page routes rebuild a safe query", async () => {
  engine(async () => JSON_ENVELOPE({ events: [], next_after: 0 }));
  const ok = await routes.events.GET(get("/api/engineer/events?after=12&limit=200&session_id=abc-1"));
  assert.equal(ok.status, 200);
  assert.equal(upstream[0].url, "http://127.0.0.1:8765/api/v2/engineer/events?after=12&limit=200&session_id=abc-1");
  upstream = [];
  await routes.events.GET(get("/api/engineer/events"));
  assert.equal(upstream[0].url, "http://127.0.0.1:8765/api/v2/engineer/events");

  for (const query of ["limit=201", "limit=0", "after=-1", "after=x", "session_id=a%20b", "x=1", "limit=1&limit=2"]) {
    upstream = [];
    const response = await routes.events.GET(get(`/api/engineer/events?${query}`));
    assert.equal(response.status, 422, query);
    assert.equal(upstream.length, 0, query);
  }

  upstream = [];
  const page = await routes.sources.GET(get("/api/recording-sources/page?limit=25&offset=0&q=monaco&availability=available"));
  assert.equal(page.status, 200);
  assert.equal(
    upstream[0].url,
    "http://127.0.0.1:8765/api/v1/recording-sources/page?limit=25&offset=0&q=monaco&availability=available",
  );
  upstream = [];
  const unknown = await routes.sources.GET(get("/api/recording-sources/page?path=../../etc"));
  assert.equal(unknown.status, 422);
  assert.equal(upstream.length, 0);
});

/* ------------------------------------------------------------ mutations */

test("preferences PUT needs same-origin, forwards If-Match and keeps the token server-side", async () => {
  const document = JSON.stringify({ topics: {}, verbosity: "balanced" });
  engine(async () => UNAVAILABLE("never", 500));
  const blocked = await routes.preferences.PUT(
    send("/api/engineer/preferences", { method: "PUT", body: document, trusted: false, headers: { ...jsonHeaders, "if-match": '"7"' } }),
  );
  assert.equal(blocked.status, 403);
  assert.equal(upstream.length, 0);

  engine(async () => JSON_ENVELOPE({ revision: 8 }, 200, { etag: '"8"' }));
  const saved = await routes.preferences.PUT(
    send("/api/engineer/preferences", { method: "PUT", body: document, headers: { ...jsonHeaders, "if-match": '"7"' } }),
  );
  assert.equal(saved.status, 200);
  assert.equal(saved.headers.get("etag"), '"8"');
  assert.equal(upstream[0].method, "PUT");
  assert.equal(upstream[0].url, "http://127.0.0.1:8765/api/v1/engineer/preferences");
  assert.equal(upstream[0].headers.get("if-match"), '"7"');
  assert.equal(upstream[0].headers.get("content-type"), "application/json");
  assert.equal(upstream[0].body, document);
  assert.equal(hasToken(upstream[0]), true);
  assert.equal(saved.headers.has("authorization"), false);

  // GET of the same resource stays token-free.
  upstream = [];
  await routes.preferences.GET(get("/api/engineer/preferences"));
  assert.equal(upstream[0].method, "GET");
  assert.equal(hasToken(upstream[0]), false);
});

test("a 409 preferences conflict keeps its status, body and current ETag", async () => {
  engine(async () =>
    Response.json(
      { api_version: "v1", status: "unavailable", data: { revision: 9 }, reason: "preferences_revision_conflict" },
      { status: 409, headers: { etag: '"9"' } },
    ),
  );
  const response = await routes.preferences.PUT(
    send("/api/engineer/preferences", { method: "PUT", body: "{}", headers: { ...jsonHeaders, "if-match": '"7"' } }),
  );
  assert.equal(response.status, 409);
  assert.equal(response.headers.get("etag"), '"9"');
  assert.equal(response.headers.get("cache-control"), "no-store");
  const body = await response.json();
  assert.equal(body.data.revision, 9);
  assert.equal(body.reason, "preferences_revision_conflict");
});

test("mutation bodies are bounded and must be JSON", async () => {
  engine(async () => JSON_ENVELOPE({}));
  const large = "x".repeat(9 * 1024);
  const declared = await routes.preferences.PUT(
    send("/api/engineer/preferences", { method: "PUT", body: large, headers: { ...jsonHeaders } }),
  );
  assert.equal(declared.status, 413);

  const streamed = new Request("http://127.0.0.1:3000/api/engineer/preferences", {
    method: "PUT",
    headers: { ...TRUSTED, ...jsonHeaders },
    body: new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(large));
        controller.close();
      },
    }),
    duplex: "half",
  });
  streamed.headers.delete("content-length");
  assert.equal((await routes.preferences.PUT(streamed)).status, 413);

  const wrongType = await routes.preferences.PUT(
    send("/api/engineer/preferences", { method: "PUT", body: "a=b", headers: { "content-type": "application/x-www-form-urlencoded" } }),
  );
  assert.equal(wrongType.status, 415);
  const empty = await routes.preferences.PUT(send("/api/engineer/preferences", { method: "PUT", headers: jsonHeaders }));
  assert.equal(empty.status, 400);
  const badMatch = await routes.preferences.PUT(
    send("/api/engineer/preferences", { method: "PUT", body: "{}", headers: { ...jsonHeaders, "if-match": "7".repeat(65) } }),
  );
  assert.equal(badMatch.status, 422);
  assert.equal(upstream.length, 0, "rejected bodies never reach the engine");
});

test("materialize forwards a POST with the token and no body", async () => {
  engine(async () => JSON_ENVELOPE({ attempt: {}, samples: [] }));
  const REVISION = "rev:abc_1";
  const response = await routes.materialize.POST(
    send(`/api/review/sessions/${SESSION}/attempts/${REVISION}/materialize`, { body: "ignored", headers: jsonHeaders }),
    params({ session: SESSION, revision: REVISION }),
  );
  assert.equal(response.status, 200);
  assert.equal(
    upstream[0].url,
    `http://127.0.0.1:8765/api/v2/session-evidence/sessions/${SESSION}/attempts/${encodeURIComponent(REVISION)}/materialize`,
  );
  assert.equal(upstream[0].method, "POST");
  assert.equal(upstream[0].body, null);
  assert.equal(hasToken(upstream[0]), true);

  upstream = [];
  const blocked = await routes.materialize.POST(
    send("/x", { trusted: false }),
    params({ session: SESSION, revision: REVISION }),
  );
  assert.equal(blocked.status, 403);
  const invalid = await routes.materialize.POST(send("/x"), params({ session: SESSION, revision: "bad/rev" }));
  assert.equal(invalid.status, 404);
  assert.equal(upstream.length, 0);

  engine(async () => UNAVAILABLE("comparison_read_budget_busy", 409));
  const busy = await routes.materialize.POST(send("/x"), params({ session: SESSION, revision: REVISION }));
  assert.equal(busy.status, 409);
  assert.equal((await busy.json()).reason, "comparison_read_budget_busy");
});

test("ask forwards the question with the token and uses a long deadline only for future laps", async () => {
  const question = JSON.stringify({ target: "rev-1", question: "Where did I lose time?", use_model: false });
  engine(async () => JSON_ENVELOPE({ status: "answered" }));
  const ok = await routes.ask.POST(
    send(`/api/review/sessions/${SESSION}/ask`, { body: question, headers: jsonHeaders }),
    params({ session: SESSION }),
  );
  assert.equal(ok.status, 200);
  assert.equal(upstream[0].url, `http://127.0.0.1:8765/api/v2/session-evidence/sessions/${SESSION}/ask`);
  assert.equal(upstream[0].body, question);
  assert.equal(hasToken(upstream[0]), true);

  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    for (const [body, expireAfter, survives] of [
      [question, 35_000, 30_000],
      [JSON.stringify({ binding_id: "f".repeat(64), question: "next lap?" }), 285_000, 200_000],
    ]) {
      engine((call) => new Promise((_resolve, reject) => call.signal.addEventListener("abort", () => reject(new Error("aborted")))));
      const pending = routes.ask.POST(
        send(`/api/review/sessions/${SESSION}/ask`, { body, headers: jsonHeaders }),
        params({ session: SESSION }),
      );
      await until(() => upstream.length === 1);
      mock.timers.tick(survives);
      assert.equal(upstream[0].signal.aborted, false, `still waiting after ${survives} ms`);
      mock.timers.tick(expireAfter - survives);
      const response = await pending;
      assert.equal(upstream[0].signal.aborted, true);
      assert.equal(response.status, 504);
      assert.equal((await response.json()).reason, "upstream_deadline_exceeded");
    }
  } finally {
    mock.timers.reset();
  }
});

test("proxyMutation hands the bounded body to a deadline function", async () => {
  engine(async () => JSON_ENVELOPE({}));
  let seen = null;
  const response = await proxy.proxyMutation(
    send("/api/x", { body: '{"a":1}', headers: jsonHeaders }),
    "/api/v1/x",
    {
      body: "json",
      timeoutMs: (body) => {
        seen = new TextDecoder().decode(body);
        return 1000;
      },
    },
  );
  assert.equal(response.status, 200);
  assert.equal(seen, '{"a":1}');
});

/* ----------------------------------------------------------- import jobs */

const CAPTURE = "a".repeat(32);
const JOB = "b".repeat(32);
const jobRecord = { job_id: JOB, status: "queued", capture_id: CAPTURE };

test("import queue: the JSON branch answers with an envelope instead of redirecting", async () => {
  engine(async () => JSON_ENVELOPE(jobRecord, 202));
  const response = await routes.importCreate.POST(
    send("/api/import-jobs", {
      body: JSON.stringify({ capture_id: CAPTURE, queue_if_busy: true }),
      headers: { ...jsonHeaders, accept: "application/json" },
    }),
  );
  assert.equal(response.status, 202);
  assert.equal(response.headers.get("location"), null);
  const body = await response.json();
  assert.equal(body.status, "ok");
  assert.equal(body.data.job_id, JOB);
  assert.equal(upstream[0].url, "http://127.0.0.1:8765/api/v1/import-jobs");
  assert.deepEqual(JSON.parse(upstream[0].body), { capture_id: CAPTURE, queue_if_busy: true });
  assert.equal(hasToken(upstream[0]), true);

  // Form bodies work in JSON mode too.
  upstream = [];
  const form = new FormData();
  form.set("capture_id", CAPTURE);
  form.set("queue_if_busy", "true");
  const fromForm = await routes.importCreate.POST(send("/api/import-jobs", { body: form, headers: { accept: "application/json" } }));
  assert.equal(fromForm.status, 202);
  assert.deepEqual(JSON.parse(upstream[0].body), { capture_id: CAPTURE, queue_if_busy: true });
});

test("import queue JSON branch maps failures to the redirect branch's reason codes", async () => {
  const accept = { accept: "application/json", ...jsonHeaders };
  const create = (body) => routes.importCreate.POST(send("/api/import-jobs", { body: JSON.stringify(body), headers: accept }));

  engine(async () => UNAVAILABLE("another_import_is_in_progress", 409));
  const busy = await create({ capture_id: CAPTURE });
  assert.equal(busy.status, 409);
  assert.equal((await busy.json()).reason, "busy");

  engine(async () => UNAVAILABLE("import_queue_full", 409));
  assert.equal((await (await create({ capture_id: CAPTURE })).json()).reason, "import_queue_full");

  engine(async () => {
    throw new TypeError("fetch failed");
  });
  const down = await create({ capture_id: CAPTURE });
  assert.equal(down.status, 503);
  assert.equal((await down.json()).reason, "unavailable");

  upstream = [];
  const invalid = await create({ capture_id: "not-a-capture" });
  assert.equal(invalid.status, 422);
  assert.equal((await invalid.json()).reason, "capture_unavailable");
  assert.equal(upstream.length, 0);

  const untrusted = await routes.importCreate.POST(
    send("/api/import-jobs", { body: "{}", headers: accept, trusted: false }),
  );
  assert.equal(untrusted.status, 403);
  assert.equal((await untrusted.json()).reason, "same_origin_request_required");
  assert.equal(upstream.length, 0);
});

test("import queue: the form post still redirects with the same query parameters", async () => {
  engine(async () => JSON_ENVELOPE(jobRecord, 202));
  const form = new FormData();
  form.set("capture_id", CAPTURE);
  form.set("return_to", "recordings");
  const response = await routes.importCreate.POST(
    send("/api/import-jobs", { body: form, headers: { accept: "text/html,application/xhtml+xml,*/*;q=0.8" } }),
  );
  assert.equal(response.status, 303);
  const location = new URL(response.headers.get("location"));
  assert.equal(location.pathname, "/review");
  assert.equal(location.searchParams.get("import_job_id"), JOB);
  assert.deepEqual(JSON.parse(upstream[0].body), { capture_id: CAPTURE, queue_if_busy: false });

  engine(async () => UNAVAILABLE("another_local_operation_is_in_progress", 409));
  const busy = await routes.importCreate.POST(send("/api/import-jobs", { body: form }));
  assert.equal(busy.status, 303);
  assert.equal(new URL(busy.headers.get("location")).searchParams.get("import_error"), "busy");

  const untrusted = await routes.importCreate.POST(send("/api/import-jobs", { body: form, trusted: false }));
  assert.equal(untrusted.status, 403);
  assert.deepEqual(await untrusted.json(), { error: "same_origin_request_required" });
});

test("import cancel and retry: JSON branch statuses and reason codes", async () => {
  const accept = { accept: "application/json" };
  engine(async () => JSON_ENVELOPE({ ...jobRecord, status: "cancelled" }));
  const cancelled = await routes.importCancel.POST(send("/x", { headers: accept }), params({ job_id: JOB }));
  assert.equal(cancelled.status, 200);
  assert.equal((await cancelled.json()).data.status, "cancelled");
  assert.equal(upstream[0].url, `http://127.0.0.1:8765/api/v1/import-jobs/${JOB}/cancel`);
  assert.equal(hasToken(upstream[0]), true);

  engine(async () => UNAVAILABLE("import_job_not_waiting", 409));
  const running = await routes.importCancel.POST(send("/x", { headers: accept }), params({ job_id: JOB }));
  assert.equal(running.status, 409);
  assert.equal((await running.json()).reason, "job_not_waiting");

  engine(async () => JSON_ENVELOPE(jobRecord, 202));
  const retried = await routes.importRetry.POST(send("/x", { headers: accept }), params({ job_id: JOB }));
  assert.equal(retried.status, 202);
  assert.equal(upstream[0].url, `http://127.0.0.1:8765/api/v1/import-jobs/${JOB}/retry`);

  engine(async () => UNAVAILABLE("import_job_not_retryable", 409));
  const notRetryable = await routes.importRetry.POST(send("/x", { headers: accept }), params({ job_id: JOB }));
  assert.equal(notRetryable.status, 409);
  assert.equal((await notRetryable.json()).reason, "import_job_not_retryable");

  upstream = [];
  for (const handler of [routes.importCancel, routes.importRetry]) {
    const bad = await handler.POST(send("/x", { headers: accept }), params({ job_id: "../x" }));
    assert.equal(bad.status, 404);
    assert.equal((await bad.json()).reason, "job_unavailable");
    const blocked = await handler.POST(send("/x", { headers: accept, trusted: false }), params({ job_id: JOB }));
    assert.equal(blocked.status, 403);
  }
  assert.equal(upstream.length, 0);
});

test("import cancel and retry: form posts still redirect", async () => {
  engine(async () => JSON_ENVELOPE(jobRecord, 202));
  const retried = await routes.importRetry.POST(send("/x"), params({ job_id: JOB }));
  assert.equal(retried.status, 303);
  assert.equal(new URL(retried.headers.get("location")).searchParams.get("import_job_id"), JOB);

  engine(async () => UNAVAILABLE("import_job_not_waiting", 409));
  const cancelled = await routes.importCancel.POST(send("/x"), params({ job_id: JOB }));
  assert.equal(cancelled.status, 303);
  assert.equal(new URL(cancelled.headers.get("location")).searchParams.get("import_error"), "job_not_waiting");

  const untrusted = await routes.importCancel.POST(send("/x", { trusted: false }), params({ job_id: JOB }));
  assert.equal(untrusted.status, 403);
  assert.deepEqual(await untrusted.json(), { error: "same_origin_request_required" });
});

test("acceptsJson only matches an explicit application/json", () => {
  const withAccept = (value) => new Request("http://127.0.0.1:3000/x", { headers: value ? { accept: value } : {} });
  assert.equal(proxy.acceptsJson(withAccept("application/json")), true);
  assert.equal(proxy.acceptsJson(withAccept("application/json; charset=utf-8, text/plain")), true);
  assert.equal(proxy.acceptsJson(withAccept("text/html,application/xhtml+xml,*/*;q=0.8")), false);
  assert.equal(proxy.acceptsJson(withAccept("*/*")), false);
  assert.equal(proxy.acceptsJson(withAccept(null)), false);
});
