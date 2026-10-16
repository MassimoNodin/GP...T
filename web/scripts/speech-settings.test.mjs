import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const typescript = require("typescript");
const sourceRoot = new URL("../src/", import.meta.url);

async function moduleUrl(path, replacements = {}) {
  let code = typescript.transpileModule(await readFile(new URL(path, sourceRoot), "utf8"), {
    compilerOptions: { module: typescript.ModuleKind.ESNext, target: typescript.ScriptTarget.ES2022 },
  }).outputText;
  for (const [specifier, url] of Object.entries(replacements)) {
    code = code.replaceAll('from "' + specifier + '"', "from " + JSON.stringify(url));
  }
  return "data:text/javascript;base64," + Buffer.from(code).toString("base64");
}

const draftUrl = await moduleUrl("lib/local-speech-draft.ts");
const speech = await import(draftUrl);
const settings = await import(await moduleUrl("features/settings/speech-settings.ts"));
const reasonsUrl = await moduleUrl("features/review-data/reasons.ts");
const connections = await import(await moduleUrl("features/settings/connections.ts", {
  "@/lib/local-speech-draft": draftUrl, "@/features/review-data/reasons": reasonsUrl,
}));
const mapUrl = await moduleUrl("features/live-status/map.ts");
const systems = await import(await moduleUrl("features/drive-diagnostics/system-rows.ts", {
  "@/features/live-status/map": mapUrl, "@/features/review-data/reasons": reasonsUrl,
}));
const envelope = (data) => ({ api_version: "v1", status: "ok", data, reason: null });
const sha256 = "a".repeat(64);
const requestId = "12345678-1234-4abc-8abc-123456789abc";

function localStatus(model = "tiny.en", placement = "CPU") {
  return { status: "ready", runtime: "whisper.cpp", runtime_version: "1.9.3", model_name: model,
    model_sha256: sha256, runtime_id: sha256, language: "en", placement, maximum_clip_seconds: 12,
    audio_persisted: false };
}

function cloudStatus() {
  return { ...localStatus(), runtime: "OpenAI", runtime_version: null, model_name: "gpt-transcribe",
    model_sha256: null, runtime_id: "openai", placement: "cloud", maximum_clip_seconds: null };
}

function transcription(status) {
  return envelope({ schema_version: 1, request_id: requestId, audio_sha256: sha256, transcript: "Copy",
    language: "en", verified: false, runtime: { name: status.runtime, version: status.runtime_version,
      model_name: status.model_name, model_sha256: status.model_sha256, runtime_id: status.runtime_id,
      placement: status.placement } });
}

test("all four pinned local variants parse and describe their real placement", () => {
  for (const model of ["tiny.en", "large-v3"]) for (const placement of ["CPU", "CUDA"]) {
    const status = localStatus(model, placement);
    const parsed = speech.parseLocalSpeechStatus(envelope(status));
    assert.equal(parsed.status, "ready");
    assert.equal(parsed.model_name, model);
    assert.equal(parsed.placement, placement);
    assert.ok(speech.parseLocalTranscription(transcription(status), requestId, sha256, parsed));
    const view = connections.describeSpeech(envelope(status));
    assert.match(view.footnote, /Nothing is sent to a cloud service/);
    assert.equal(view.rows.find((row) => row.label === "Placement").value,
      placement === "CPU" ? "Local · CPU" : "Local · GPU (CUDA)");
  }
});

test("OpenAI nullable status metadata falls back to twelve seconds and accepts cloud transcript", () => {
  const raw = cloudStatus();
  const parsed = speech.parseLocalSpeechStatus(envelope(raw));
  assert.equal(parsed.runtime_id, "openai");
  assert.equal(parsed.maximum_clip_seconds, 12);
  const body = transcription({ ...raw, runtime_version: "v1", model_sha256: "not_applicable" });
  assert.ok(speech.parseLocalTranscription(body, requestId, sha256, parsed));
  assert.ok(speech.parseLocalTranscription(body, requestId, sha256, "openai"));
  const view = connections.describeSpeech(envelope(raw));
  assert.match(view.footnote, /Audio is sent to OpenAI/);
  assert.doesNotMatch(view.footnote, /Nothing is sent/);
  assert.equal(view.rows.find((row) => row.label === "Placement").value, "OpenAI · cloud");
  assert.deepEqual(speech.parseLocalSpeechStatus(envelope({ status: "not_configured", reason: "openai_key_file_missing" })),
    { status: "unavailable", reason: "openai_key_file_missing" });
});

test("unsupported or corrupt runtime identities and mismatched started-against status are rejected", () => {
  for (const override of [{ runtime_version: "1.8.0" }, { model_name: "base" }, { placement: "GPU" },
    { runtime_id: "openai" }, { model_sha256: null }, { audio_persisted: true }]) {
    assert.equal(speech.parseLocalSpeechStatus(envelope({ ...localStatus(), ...override })), null);
  }
  const started = speech.parseLocalSpeechStatus(envelope(localStatus()));
  assert.equal(speech.parseLocalTranscription(transcription(localStatus("large-v3")), requestId, sha256, started), null);
  assert.equal(speech.parseLocalTranscription(transcription(localStatus("tiny.en", "CUDA")), requestId, sha256, started), null);
  assert.equal(speech.parseLocalTranscription(transcription(cloudStatus()), requestId, sha256, started), null);
  const cloud = speech.parseLocalSpeechStatus(envelope(cloudStatus()));
  assert.equal(speech.parseLocalTranscription(transcription({ ...cloudStatus(), model_name: "different" }), requestId, sha256, cloud), null);
});

function document() {
  return envelope({ settings: { provider: "local", local: { model: "large-v3", device: "cuda" } }, revision: 3,
    options: ["tiny.en", "large-v3"].flatMap((model) => ["cpu", "cuda"].map((device) => ({
      provider: "local", model, device, status: model === "large-v3" && device === "cuda" ? "ready" : "not_installed",
      reason: model === "large-v3" && device === "cuda" ? null : "runtime_not_configured",
    }))).concat([{ provider: "openai", status: "not_configured", reason: "openai_key_file_missing" }]) });
}

test("settings parsing validates revisions, option coverage, selection readiness and setup commands", () => {
  const parsed = settings.parseSpeechDocument(document());
  assert.equal(parsed.revision, 3);
  assert.equal(settings.selectionReady(parsed.settings, parsed.options), true);
  assert.equal(settings.selectionReady({ ...parsed.settings, provider: "openai" }, parsed.options), false);
  assert.match(settings.setupCommand("large-v3", "cuda"), /--model large-v3 --device cuda --database/);
  for (const revision of [-1, 0.1, "3", NaN]) {
    const body = document(); body.data.revision = revision;
    assert.equal(settings.parseSpeechDocument(body), null);
  }
  const duplicate = document(); duplicate.data.options[1] = duplicate.data.options[0];
  assert.equal(settings.parseSpeechDocument(duplicate), null);
  assert.match(settings.speechSaveFailure(409), /changed elsewhere.*Reload/);
  assert.match(settings.speechSaveFailure(403), /not authorised/);
});

test("key metadata parser selects only safe fields and describes configured suffix", () => {
  const status = settings.parseKeyStatus(envelope({ configured: true, last4: "abcd", updated_at_utc: "2026-10-16T00:00:00Z",
    verified: "ok", key: "must never be forwarded" }));
  assert.equal(settings.keyStatusLabel(status), "Configured · ends in abcd");
  assert.equal("key" in status, false);
  assert.equal(settings.keyStatusLabel({ configured: false, last4: null }), "Not set");
  assert.equal(settings.parseKeyStatus(envelope({ configured: true, last4: "too-long", updated_at_utc: null, verified: null })), null);
});

test("new controls clear secret input, use conflict guards and authenticated mutation proxies", async () => {
  const keyCard = await readFile(new URL("features/settings/ApiKeysCard.tsx", sourceRoot), "utf8");
  assert.match(keyCard, /type="password"/);
  assert.match(keyCard, /setKey\(""\)/);
  assert.doesNotMatch(keyCard, /setKey\((?:parsed|status)\./);
  const card = await readFile(new URL("features/settings/SpeechRecognitionCard.tsx", sourceRoot), "utf8");
  assert.match(card, /if-match/);
  assert.match(card, /disabled=\{busy \|\| conflict/);
  assert.match(card, /settings\?section=connections/);
  for (const path of ["app/api/engineer/speech/route.ts", "app/api/secrets/openai-api-key/route.ts"]) {
    const route = await readFile(new URL(path, sourceRoot), "utf8");
    assert.match(route, /proxyMutation/);
    assert.match(route, /maxRequestBytes: 2048/);
  }
});

test("Drive speech rows recognise cloud and CUDA without a false local claim", () => {
  assert.deepEqual(systems.parseSpeechStatus(envelope(cloudStatus())), { kind: "ready", placement: "cloud" });
  assert.equal(systems.speechRow({ kind: "ready", placement: "cloud" }).detail, "OpenAI · cloud");
  assert.equal(systems.speechRow({ kind: "ready", placement: "CUDA" }).detail, "Local · GPU (CUDA)");
  assert.equal(speech.speechPlacementLabel("cloud"), "OpenAI · cloud");
  assert.equal(speech.speechPlacementLabel("CUDA"), "Local · GPU (CUDA)");
});
