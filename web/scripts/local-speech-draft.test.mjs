import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(
  new URL("../src/lib/local-speech-draft.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const speech = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

test("speech UI distinguishes browser capture from backend transcription", () => {
  const component = readFileSync(
    new URL("../src/app/EngineerSpeechDraft.tsx", import.meta.url),
    "utf8",
  );
  assert.ok(
    component.includes(
      "Your browser records the clip; the configured backend transcribes it.",
    ),
  );
  assert.ok(!component.includes("Clips are transcribed on this computer."));
});

function readyStatus(overrides = {}) {
  return {
    api_version: "v1",
    status: "ok",
    data: {
      status: "ready",
      runtime: "whisper.cpp",
      runtime_version: "1.9.3",
      model_name: "tiny.en",
      model_sha256: "a".repeat(64),
      runtime_id: "b".repeat(64),
      language: "en",
      placement: "CPU",
      maximum_clip_seconds: 12,
      audio_persisted: false,
      ...overrides,
    },
    reason: null,
  };
}

function response(overrides = {}) {
  return {
    api_version: "v1",
    status: "ok",
    data: {
      schema_version: 1,
      request_id: "12345678-1234-4abc-8abc-123456789abc",
      audio_sha256: "c".repeat(64),
      transcript: "What does this lap show?",
      language: "en",
      verified: false,
      runtime: {
        name: "whisper.cpp",
        version: "1.9.3",
        model_name: "tiny.en",
        model_sha256: "a".repeat(64),
        runtime_id: "b".repeat(64),
        placement: "CPU",
      },
      ...overrides,
    },
    reason: null,
  };
}

test("builds exact canonical mono 16 kHz PCM WAV and clips sample values", () => {
  const wav = speech.buildCanonicalPcmWav(
    new Float32Array([-1, -0.5, 0, 0.5, 1]),
  );
  assert.ok(wav);
  assert.equal(wav.byteLength, 54);
  assert.equal(new TextDecoder().decode(wav.slice(0, 4)), "RIFF");
  assert.equal(new TextDecoder().decode(wav.slice(8, 16)), "WAVEfmt ");
  assert.equal(new DataView(wav.buffer).getUint32(24, true), 16_000);
  assert.equal(new DataView(wav.buffer).getUint16(22, true), 1);
  assert.equal(new DataView(wav.buffer).getInt16(44, true), -32_768);
  assert.equal(new DataView(wav.buffer).getInt16(52, true), 32_767);
});

test("bounds the sample buffer at twelve seconds and rejects invalid floats", () => {
  const maximum = speech.buildCanonicalPcmWav(
    new Float32Array(speech.LOCAL_SPEECH_MAX_SAMPLES),
  );
  assert.equal(maximum?.byteLength, 384_044);
  assert.equal(
    speech.buildCanonicalPcmWav(
      new Float32Array(speech.LOCAL_SPEECH_MAX_SAMPLES + 1),
    ),
    null,
  );
  assert.equal(
    speech.buildCanonicalPcmWav(new Float32Array([Number.NaN])),
    null,
  );
});

test("accepts only a pinned English CPU local runtime status", () => {
  const parsed = speech.parseLocalSpeechStatus(readyStatus());
  assert.equal(parsed?.status, "ready");
  assert.equal(
    speech.parseLocalSpeechStatus(readyStatus({ placement: "GPU" })),
    null,
  );
  assert.equal(
    speech.parseLocalSpeechStatus({
      api_version: "v1",
      status: "ok",
      data: { status: "unavailable", reason: "runtime_not_configured" },
    })?.status,
    "unavailable",
  );
});

test("accepts a matched unverified transcript and rejects stale identities", () => {
  const parsed = speech.parseLocalTranscription(
    response(),
    "12345678-1234-4abc-8abc-123456789abc",
    "c".repeat(64),
    "b".repeat(64),
  );
  assert.equal(parsed?.transcript, "What does this lap show?");
  assert.equal(
    speech.parseLocalTranscription(
      response({ audio_sha256: "d".repeat(64) }),
      "12345678-1234-4abc-8abc-123456789abc",
      "c".repeat(64),
      "b".repeat(64),
    ),
    null,
  );
  assert.equal(
    speech.parseLocalTranscription(
      response({ verified: true }),
      "12345678-1234-4abc-8abc-123456789abc",
      "c".repeat(64),
      "b".repeat(64),
    ),
    null,
  );
  assert.equal(
    speech.parseLocalTranscription(
      response({ transcript: "x".repeat(4_097) }),
      "12345678-1234-4abc-8abc-123456789abc",
      "c".repeat(64),
      "b".repeat(64),
    ),
    null,
  );
});
