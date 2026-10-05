import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

let upstreamResponse;
let forwardedInit;
globalThis.__recordingDownloadProxyDeps = {
  isTrustedLocalRead: () => true,
  forwardLocalRequest: async (_path, init, needsControlToken) => {
    forwardedInit = init;
    assert.equal(needsControlToken, true);
    return upstreamResponse;
  },
};

const routeSource = readFileSync(
  new URL(
    "../src/app/api/recording-sources/[capture_id]/download/route.ts",
    import.meta.url,
  ),
  "utf8",
).replace(
  'import { forwardLocalRequest, isTrustedLocalRead } from "@/lib/local-proxy";',
  "const { forwardLocalRequest, isTrustedLocalRead } = globalThis.__recordingDownloadProxyDeps;",
);
const compiled = ts.transpileModule(routeSource, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const route = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

test("downstream cancellation cancels the forwarded capture stream", async () => {
  let cancelled = false;
  upstreamResponse = new Response(
    new ReadableStream({
      cancel() {
        cancelled = true;
      },
    }),
    {
      status: 200,
      headers: {
        "Content-Disposition":
          "attachment; filename=\"capture.f1ecap\"; filename*=UTF-8''sample.f1ecap",
        "Content-Length": "4",
      },
    },
  );
  const request = new Request(
    "http://127.0.0.1:3000/api/recording-sources/" +
      "a".repeat(32) +
      "/download?version=" +
      "b".repeat(64),
    { headers: { "sec-fetch-site": "same-origin" } },
  );

  const response = await route.GET(request, {
    params: Promise.resolve({ capture_id: "a".repeat(32) }),
  });

  assert.equal(response.status, 200);
  assert.equal(forwardedInit.signal, request.signal);
  await response.body.cancel();
  assert.equal(cancelled, true);
});
