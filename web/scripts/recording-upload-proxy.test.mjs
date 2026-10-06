import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

let trusted = true;
let upstreamResponse;
let forwardedPath;
let forwardedInit;
let needsToken;
globalThis.__recordingUploadProxyDeps = {
  isTrustedLocalMutation: () => trusted,
  forwardLocalRequest: async (path, init, controlToken) => {
    forwardedPath = path;
    forwardedInit = init;
    needsToken = controlToken;
    return upstreamResponse;
  },
};

const routeSource = readFileSync(
  new URL("../src/app/api/recording-sources/upload/route.ts", import.meta.url),
  "utf8",
);
const isolatedRouteSource = routeSource.replace(
  /^import\s*\{\s*forwardLocalRequest\s*,\s*isTrustedLocalMutation\s*\}\s*from\s*["']@\/lib\/local-proxy["'];/m,
  "const { forwardLocalRequest, isTrustedLocalMutation } = globalThis.__recordingUploadProxyDeps;",
);
assert.notEqual(isolatedRouteSource, routeSource, "proxy dependency import was not replaced");
const compiled = ts.transpileModule(isolatedRouteSource, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const route = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

function request({
  body = "capture-bytes",
  contentType = "application/octet-stream",
  filename = "sample%20capture.f1ecap",
} = {}) {
  return new Request("http://127.0.0.1:3000/api/recording-sources/upload", {
    method: "POST",
    body,
    duplex: "half",
    headers: {
      "content-type": contentType,
      "x-capture-upload-filename": filename,
      "content-length": String(Buffer.byteLength(body)),
      origin: "http://127.0.0.1:3000",
      "sec-fetch-site": "same-origin",
    },
  });
}

test("same-origin upload proxy streams the body and keeps the token server-side", async () => {
  trusted = true;
  const incoming = request();
  upstreamResponse = Response.json(
    { api_version: "v1", status: "ok", data: { capture_id: "a".repeat(32) } },
    { status: 201 },
  );

  const response = await route.POST(incoming);

  assert.equal(response.status, 201);
  assert.equal(forwardedPath, "/api/v1/recording-sources/upload");
  assert.equal(forwardedInit.method, "POST");
  assert.equal(forwardedInit.body, incoming.body);
  assert.equal(forwardedInit.duplex, "half");
  assert.equal(forwardedInit.signal, incoming.signal);
  assert.equal(forwardedInit.headers.get("content-length"), "13");
  assert.equal(
    forwardedInit.headers.get("x-capture-upload-filename"),
    "sample%20capture.f1ecap",
  );
  assert.equal(needsToken, true);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal((await response.json()).status, "ok");
});

test("untrusted mutations and non-binary bodies never reach the local API", async () => {
  trusted = false;
  forwardedPath = null;
  const blocked = await route.POST(request());
  assert.equal(blocked.status, 403);

  trusted = true;
  const invalid = await route.POST(
    request({ contentType: "multipart/form-data" }),
  );
  assert.equal(invalid.status, 415);
  assert.equal(forwardedPath, null);
});

test("invalid upload names are rejected before forwarding", async () => {
  trusted = true;
  const invalid = await route.POST(request({ filename: "a".repeat(2_049) }));
  assert.equal(invalid.status, 422);
});

test("upstream upload conflicts keep their status and no-store headers", async () => {
  trusted = true;
  upstreamResponse = Response.json(
    { api_version: "v1", status: "unavailable", data: null, reason: "busy" },
    { status: 409 },
  );
  const response = await route.POST(request());
  assert.equal(response.status, 409);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal((await response.json()).reason, "busy");
});
