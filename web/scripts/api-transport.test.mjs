import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, afterEach, before, describe, mock, test } from "node:test";
import { pathToFileURL } from "node:url";
import ts from "typescript";

const originalFetch = globalThis.fetch;
const originalApiUrl = process.env.F1_ENGINEER_API_URL;
const originalTokenFile = process.env.F1_ENGINEER_CONTROL_TOKEN_FILE;
const originalApiTokenFile = process.env.F1_ENGINEER_API_TOKEN_FILE;
const missingTokenFile = "D:\\f1-engineer-missing-control-token";

let compiledDir;
let requestApi;
let requestApiPost;
let forwardLocalRequest;
let JSON_API_DEADLINE_MS;
let recordingCurrentGet;

function compile(source) {
  return ts
    .transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.ESNext,
        target: ts.ScriptTarget.ES2022,
      },
    })
    .outputText.replaceAll(
      'from "./local-api-transport"',
      'from "./local-api-transport.mjs"',
    ).replaceAll(
      'from "./api-authorization"',
      'from "./api-authorization.mjs"',
    );
}

function envelope(data = { ready: true }, status = "ok", reason = null) {
  return { api_version: "v1", status, data, reason };
}

function installFetch(responder) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    return responder(url, init, calls);
  };
  return calls;
}

async function flush() {
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
}

function stalledBody() {
  return new ReadableStream({
    cancel() {},
  });
}

describe("local JSON API transport", { concurrency: 1 }, () => {
  before(async () => {
    compiledDir = mkdtempSync(join(tmpdir(), "f1-api-transport-"));
    for (const name of ["local-api-transport.ts", "api-authorization.ts", "local-proxy.ts", "api.ts"]) {
      const source = readFileSync(
        new URL(`../src/lib/${name}`, import.meta.url),
        "utf8",
      );
      writeFileSync(
        join(compiledDir, name.replace(/\.ts$/, ".mjs")),
        compile(source),
      );
    }
    const routeSource = readFileSync(
      new URL("../src/app/api/recordings/current/route.ts", import.meta.url),
      "utf8",
    )
      .replace(
        'import { NextResponse } from "next/server";',
        "const NextResponse = { json(body, init) { return Response.json(body, init); } };",
      )
      .replace(
        'import { forwardLocalRequest } from "@/lib/local-proxy";',
        "const forwardLocalRequest = (...args) => globalThis.__recordingCurrentForward(...args);",
      );
    writeFileSync(
      join(compiledDir, "recording-current.mjs"),
      compile(routeSource),
    );

    const transport = await import(
      pathToFileURL(join(compiledDir, "local-api-transport.mjs")).href
    );
    const api = await import(pathToFileURL(join(compiledDir, "api.mjs")).href);
    const proxy = await import(
      pathToFileURL(join(compiledDir, "local-proxy.mjs")).href
    );
    const route = await import(
      pathToFileURL(join(compiledDir, "recording-current.mjs")).href
    );
    JSON_API_DEADLINE_MS = transport.JSON_API_DEADLINE_MS;
    requestApi = api.requestApi;
    requestApiPost = api.requestApiPost;
    forwardLocalRequest = proxy.forwardLocalRequest;
    recordingCurrentGet = route.GET;
  });

  after(() => {
    rmSync(compiledDir, { recursive: true, force: true });
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    if (originalApiUrl === undefined) delete process.env.F1_ENGINEER_API_URL;
    else process.env.F1_ENGINEER_API_URL = originalApiUrl;
    if (originalTokenFile === undefined) {
      delete process.env.F1_ENGINEER_CONTROL_TOKEN_FILE;
    } else {
      process.env.F1_ENGINEER_CONTROL_TOKEN_FILE = originalTokenFile;
    }
    delete globalThis.__recordingCurrentForward;
    if (originalApiTokenFile === undefined) delete process.env.F1_ENGINEER_API_TOKEN_FILE;
    else process.env.F1_ENGINEER_API_TOKEN_FILE = originalApiTokenFile;
    try {
      mock.timers.reset();
    } catch {
      // Timers are only mocked in the deadline test.
    }
  });

  test("accepted loopback bases keep their existing request URLs", async () => {
    const cases = [
      [undefined, "http://127.0.0.1:8765/api/v1/health"],
      ["http://127.0.0.1:8765", "http://127.0.0.1:8765/api/v1/health"],
      ["http://127.0.0.1:8765///", "http://127.0.0.1:8765/api/v1/health"],
      ["http://localhost:8765/", "http://localhost:8765/api/v1/health"],
      ["HTTP://127.0.0.1:8765", "HTTP://127.0.0.1:8765/api/v1/health"],
      ["http://[::1]:9876", "http://[::1]:9876/api/v1/health"],
      [
        "http://[0:0:0:0:0:0:0:1]:8765",
        "http://[0:0:0:0:0:0:0:1]:8765/api/v1/health",
      ],
      [
        "http://user:secret@127.0.0.1:8765",
        "http://user:secret@127.0.0.1:8765/api/v1/health",
      ],
      [
        "http://127.0.0.1:8765/prefix/",
        "http://127.0.0.1:8765/prefix/api/v1/health",
      ],
    ];
    for (const [configured, expected] of cases) {
      if (configured === undefined) delete process.env.F1_ENGINEER_API_URL;
      else process.env.F1_ENGINEER_API_URL = configured;
      const calls = installFetch(() =>
        Response.json(envelope({ origin: expected })),
      );
      const result = await requestApi("/api/v1/health");
      assert.equal(calls.length, 1, configured ?? "default");
      assert.equal(calls[0].url, expected);
      assert.equal(calls[0].init.method, "GET");
      assert.equal(calls[0].init.cache, "no-store");
      assert.equal(calls[0].init.redirect, "error");
      assert.equal(calls[0].init.signal.aborted, false);
      assert.equal(
        new Headers(calls[0].init.headers).get("authorization"),
        null,
      );
      assert.deepEqual(result, envelope({ origin: expected }));
    }
  });

  test("rejected schemes and hosts never call fetch", async () => {
    const rejected = [
      "https://127.0.0.1:8765",
      "https://localhost:8765",
      "https://[::1]:8765",
      "http://example.com:8765",
      "http://10.0.0.1:8765",
      "http://127.0.0.2:8765",
      "http://0.0.0.0:8765",
      "http://[::2]:8765",
      "http://[::ffff:127.0.0.1]:8765",
      "ws://127.0.0.1:8765",
      "file:///tmp/capture",
      "not a url",
      "",
      "http://user:secret@evil.example",
    ];
    for (const configured of rejected) {
      process.env.F1_ENGINEER_API_URL = configured;
      const calls = installFetch(() => {
        throw new Error("upstream was called");
      });
      assert.equal(await requestApi("/api/v1/health"), null, configured);
      assert.equal(
        await requestApiPost("/api/v1/health", { intent: "attempt_summary" }),
        null,
        configured,
      );
      assert.equal(calls.length, 0, configured);
    }
  });

  test("a path that rewrites the host is rejected before fetch", async () => {
    process.env.F1_ENGINEER_API_URL = "http://127.0.0.1:8765";
    const calls = installFetch(() => {
      throw new Error("upstream was called");
    });
    assert.equal(await requestApi("@evil.example/steal"), null);
    assert.equal(await requestApiPost("@evil.example/steal", { a: 1 }), null);
    assert.equal(calls.length, 0);
  });

  test("redirects fail closed without a second upstream call", async () => {
    process.env.F1_ENGINEER_API_URL = "http://127.0.0.1:8765";
    const calls = installFetch((_url, init) => {
      assert.equal(init.redirect, "error");
      return new Response(JSON.stringify(envelope()), {
        status: 302,
        headers: {
          location: "https://evil.example/steal",
          "content-type": "application/json",
        },
      });
    });
    assert.equal(await requestApi("/api/v1/health"), null);
    assert.equal(
      await requestApiPost("/api/v1/health", { intent: "attempt_summary" }),
      null,
    );
    assert.deepEqual(
      calls.map((call) => call.url),
      [
        "http://127.0.0.1:8765/api/v1/health",
        "http://127.0.0.1:8765/api/v1/health",
      ],
    );
    assert.equal(
      calls.some((call) => call.url.includes("evil.example")),
      false,
    );

    const thrown = installFetch((_url, init) => {
      if (init.redirect !== "error") {
        return Response.json(envelope({ escaped: true }));
      }
      throw new TypeError("redirect mode is not follow");
    });
    assert.equal(await requestApi("/api/v1/sessions"), null);
    assert.deepEqual(
      thrown.map((call) => call.url),
      ["http://127.0.0.1:8765/api/v1/sessions"],
    );
  });

  test("JSON helpers validate envelopes and keep the POST HTTP error fallback", async () => {
    process.env.F1_ENGINEER_API_URL = "http://127.0.0.1:8765";
    const unavailable = envelope(
      null,
      "unavailable",
      "recording_service_unavailable",
    );
    const cases = [
      {
        name: "valid error envelope",
        status: 503,
        body: unavailable,
        get: unavailable,
        post: unavailable,
      },
      {
        name: "non-json error",
        status: 500,
        body: "nope",
        raw: true,
        get: null,
        post: envelope(null, "unavailable", "http_status_500"),
      },
      {
        name: "malformed json error",
        status: 502,
        body: "{",
        raw: true,
        get: null,
        post: envelope(null, "unavailable", "http_status_502"),
      },
      {
        name: "wrong version",
        status: 200,
        body: { ...envelope(), api_version: "v2" },
        get: null,
        post: null,
      },
      {
        name: "missing data",
        status: 200,
        body: { api_version: "v1", status: "ok", reason: null },
        get: null,
        post: null,
      },
      {
        name: "bad reason",
        status: 200,
        body: { api_version: "v1", status: "ok", data: {}, reason: 12 },
        get: null,
        post: null,
      },
      {
        name: "bad status with http error",
        status: 500,
        body: { api_version: "v1", status: "nope", data: null, reason: null },
        get: null,
        post: envelope(null, "unavailable", "http_status_500"),
      },
      {
        name: "valid nullable data",
        status: 200,
        body: envelope(null),
        get: envelope(null),
        post: envelope(null),
      },
    ];
    for (const item of cases) {
      installFetch(() =>
        item.raw
          ? new Response(item.body, {
              status: item.status,
              headers: { "content-type": "text/plain" },
            })
          : Response.json(item.body, { status: item.status }),
      );
      assert.deepEqual(await requestApi("/api/v1/health"), item.get, item.name);
      const posted = await requestApiPost("/api/v1/engineer/query", {
        intent: "attempt_summary",
        target_attempt_key: "attempt-1",
      });
      assert.deepEqual(posted, item.post, item.name);
    }
  });

  test("POST keeps the JSON content type and does not attach a control token", async () => {
    process.env.F1_ENGINEER_API_URL = "http://127.0.0.1:8765";
    process.env.F1_ENGINEER_CONTROL_TOKEN_FILE = missingTokenFile;
    const payload = {
      intent: "attempt_summary",
      target_attempt_key: "attempt-1",
    };
    const calls = installFetch(() =>
      Response.json(envelope({ answered: true })),
    );
    const result = await requestApiPost("/api/v1/engineer/query", payload);
    assert.deepEqual(result, envelope({ answered: true }));
    assert.equal(calls[0].init.method, "POST");
    assert.equal(calls[0].init.body, JSON.stringify(payload));
    assert.equal(
      new Headers(calls[0].init.headers).get("content-type"),
      "application/json",
    );
    assert.equal(new Headers(calls[0].init.headers).get("authorization"), null);
  });

  test("a stalled response headers or JSON body fails closed at the deadline", async () => {
    process.env.F1_ENGINEER_API_URL = "http://127.0.0.1:8765";
    mock.timers.enable({ apis: ["setTimeout"] });
    const headerCalls = [];
    globalThis.fetch = (url, init) =>
      new Promise((_resolve, reject) => {
        headerCalls.push(String(url));
        const abort = () => reject(new DOMException("aborted", "AbortError"));
        if (init.signal.aborted) abort();
        else init.signal.addEventListener("abort", abort, { once: true });
      });
    const headersPending = requestApi("/api/v1/health");
    await flush();
    mock.timers.tick(JSON_API_DEADLINE_MS);
    assert.equal(await headersPending, null);
    assert.deepEqual(headerCalls, ["http://127.0.0.1:8765/api/v1/health"]);

    const bodyCalls = [];
    globalThis.fetch = async (url, init) => {
      bodyCalls.push({ url: String(url), status: 500 });
      assert.equal(init.redirect, "error");
      return new Response(stalledBody(), {
        status: 500,
        headers: { "content-type": "application/json" },
      });
    };
    const bodyPending = requestApiPost("/api/v1/engineer/query", {
      intent: "attempt_summary",
    });
    await flush();
    assert.equal(bodyCalls.length, 1);
    mock.timers.tick(JSON_API_DEADLINE_MS);
    assert.equal(await bodyPending, null);
    assert.equal(
      bodyCalls[0].url,
      "http://127.0.0.1:8765/api/v1/engineer/query",
    );
  });

  test("the proxy reuses the loopback policy without taking over caller control", async () => {
    const calls = installFetch(() => new Response("ok"));
    process.env.F1_ENGINEER_API_URL = "https://example.com";
    process.env.F1_ENGINEER_CONTROL_TOKEN_FILE = missingTokenFile;
    await assert.rejects(
      () => forwardLocalRequest("/api/v1/recording-sources/upload", {}, true),
      /loopback API URL/,
    );
    assert.equal(calls.length, 0);

    process.env.F1_ENGINEER_API_URL = "not a url";
    await assert.rejects(
      () => forwardLocalRequest("/api/v1/health"),
      TypeError,
    );
    assert.equal(calls.length, 0);

    process.env.F1_ENGINEER_API_URL = "http://127.0.0.1:8765";
    await assert.rejects(
      () => forwardLocalRequest("@evil.example/steal", {}, true),
      /loopback API URL/,
    );
    assert.equal(calls.length, 0);

    process.env.F1_ENGINEER_API_URL =
      "http://user:secret@127.0.0.1:8765/prefix/";
    const controller = new AbortController();
    const body = new ReadableStream();
    await forwardLocalRequest(
      "/api/v1/recording-sources/upload",
      {
        method: "POST",
        body,
        duplex: "half",
        signal: controller.signal,
      },
      false,
    );
    assert.equal(calls.length, 1);
    assert.equal(
      calls[0].url,
      "http://127.0.0.1:8765/api/v1/recording-sources/upload",
    );
    assert.equal(calls[0].init.method, "POST");
    assert.equal(calls[0].init.body, body);
    assert.equal(calls[0].init.duplex, "half");
    assert.equal(calls[0].init.signal, controller.signal);
    assert.equal(controller.signal.aborted, false);
    assert.equal(calls[0].init.cache, "no-store");
    assert.equal(calls[0].init.redirect, "error");
    assert.equal(new Headers(calls[0].init.headers).get("authorization"), null);
    assert.equal(calls[0].url.includes("secret"), false);
    assert.equal(calls[0].url.includes("/prefix"), false);
  });

  test("a configured companion token authenticates reads, JSON posts and streams", async () => {
    process.env.F1_ENGINEER_API_URL = "http://127.0.0.1:18765";
    const token = "companion-token-" + "x".repeat(32);
    const tokenFile = join(compiledDir, "companion-token");
    writeFileSync(tokenFile, token + "\n");
    process.env.F1_ENGINEER_API_TOKEN_FILE = tokenFile;
    process.env.F1_ENGINEER_CONTROL_TOKEN_FILE = missingTokenFile;
    const calls = installFetch(() => Response.json(envelope()));
    assert.deepEqual(await requestApi("/api/v1/sessions"), envelope());
    assert.deepEqual(await requestApiPost("/api/v1/engineer/query", {}), envelope());
    await forwardLocalRequest("/api/v1/recording-sources", {}, false);
    await forwardLocalRequest("/api/v1/recordings/start", { method: "POST" }, true);
    assert.equal(calls.length, 4);
    for (const call of calls) {
      assert.equal(new Headers(call.init.headers).get("authorization"), `Bearer ${token}`);
      assert.equal(call.init.redirect, "error");
      assert.equal(call.url.includes(token), false);
    }
  });

  test("missing or malformed companion credentials fail closed", async () => {
    const calls = installFetch(() => { throw new Error("upstream was called"); });
    const tokenFile = join(compiledDir, "invalid-companion-token");
    for (const token of [null, "short", "x".repeat(32) + "\n" + "y".repeat(32)]) {
      process.env.F1_ENGINEER_API_TOKEN_FILE = token === null ? missingTokenFile : tokenFile;
      if (token !== null) writeFileSync(tokenFile, token);
      assert.equal(await requestApi("/api/v1/sessions"), null);
      assert.equal(await requestApiPost("/api/v1/engineer/query", {}), null);
      await assert.rejects(() => forwardLocalRequest("/api/v1/recordings/current"));
    }
    assert.equal(calls.length, 0);
  });

  test("recording current adds api_version only on the unavailable envelope", async () => {
    const success = {
      status: "ok",
      data: { recording_id: "rec" },
      reason: null,
    };
    globalThis.__recordingCurrentForward = async () =>
      Response.json(success, { status: 200 });
    const ok = await recordingCurrentGet();
    assert.equal(ok.status, 200);
    assert.deepEqual(await ok.json(), success);

    globalThis.__recordingCurrentForward = async () => {
      throw new Error("down");
    };
    const failed = await recordingCurrentGet();
    assert.equal(failed.status, 503);
    assert.deepEqual(await failed.json(), {
      api_version: "v1",
      status: "unavailable",
      data: null,
      reason: "recording_service_unavailable",
    });

    globalThis.__recordingCurrentForward = async () =>
      new Response("nope", { status: 200 });
    const malformed = await recordingCurrentGet();
    assert.equal(malformed.status, 503);
    assert.equal((await malformed.json()).api_version, "v1");
  });
});
