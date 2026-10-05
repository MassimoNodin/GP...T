import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function loadTypeScriptModule(relativePath) {
  const source = readFileSync(resolve(webRoot, relativePath), "utf8");
  const javascript = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const commonJsModule = { exports: {} };
  new Function("module", "exports", javascript)(
    commonJsModule,
    commonJsModule.exports,
  );
  return commonJsModule.exports;
}

const { buildRecordedSpeechPlan } = loadTypeScriptModule(
  "src/lib/recorded-speech-plan.ts",
);
const {
  cancelLocalEvidenceSpeech,
  cancelLocalEvidenceSpeechForPage,
  speakLocalEvidence,
  subscribeToLocalSpeechPlayback,
  watchForLocalSpeechVoice,
} = loadTypeScriptModule("src/lib/local-speech-controller.ts");
const {
  applyLocalVoicePreferences,
  DEFAULT_LOCAL_VOICE_PREFERENCES,
  getLocalVoicePreferenceSnapshot,
  LOCAL_VOICE_PREFERENCE_MAX_BYTES,
  LOCAL_VOICE_PREFERENCE_STORAGE_KEY,
  subscribeToLocalVoicePreferences,
} = loadTypeScriptModule("src/lib/local-voice-preferences.ts");

const report = {
  schema_version: 1,
  analysis_version: "engineer-query-v1",
  artifact_kind: "engineer_query",
  intent: "attempt_summary",
  status: "partial",
  reason_codes: [],
  selected: { target_attempt_key: "target" },
  facts: [
    { kind: "speed", text: "Speed was 120 km/h.", source_fields: ["speed"] },
  ],
  omitted_fact_count: 0,
  warnings: [],
  omitted_warning_count: 0,
  provenance: {
    verification_scope: "trace",
    target: { trace_sha256: "a".repeat(64) },
  },
  diagnostic_only: true,
  coaching_eligible: false,
  ranking_eligible: false,
};

const plan = buildRecordedSpeechPlan({ kind: "engineer_query", report });
assert.equal(plan.ok, true);
const changedProvenance = structuredClone(report);
changedProvenance.provenance.target.trace_sha256 = "b".repeat(64);
const refreshedPlan = buildRecordedSpeechPlan({
  kind: "engineer_query",
  report: changedProvenance,
});
assert.equal(refreshedPlan.ok, true);
assert.notEqual(
  plan.plan.source_key,
  refreshedPlan.plan.source_key,
  "a provenance refresh must invalidate the active source key",
);

const sidedReport = structuredClone(report);
sidedReport.warnings = [
  {
    code: "reference_invalid",
    text: "Reference lap is invalid.",
    source_fields: ["valid"],
    sides: ["reference"],
  },
];
const sidedPlan = buildRecordedSpeechPlan({
  kind: "engineer_query",
  report: sidedReport,
});
assert.equal(sidedPlan.ok, true);
assert(
  sidedPlan.plan.chunks
    .join(" ")
    .includes("Reference qualification: Reference lap is invalid."),
);
const orderedReport = structuredClone(sidedReport);
orderedReport.omitted_fact_count = 2;
orderedReport.facts[0].text = "This is the first measured fact.";
const orderedPlan = buildRecordedSpeechPlan({
  kind: "engineer_query",
  report: orderedReport,
});
assert.equal(orderedPlan.ok, true);
const orderedPreview = orderedPlan.plan.chunks.join(" ");
assert(
  orderedPreview.indexOf("Reference qualification:") <
    orderedPreview.indexOf("additional measured facts were omitted"),
);
assert(
  orderedPreview.indexOf("additional measured facts were omitted") <
    orderedPreview.indexOf("speed: This is the first measured fact."),
);

const malformedPolicy = structuredClone(report);
malformedPolicy.selected.comparison_policy = 17;
assert.equal(
  buildRecordedSpeechPlan({ kind: "engineer_query", report: malformedPolicy })
    .ok,
  false,
  "a malformed comparison policy must be unavailable without throwing",
);
const cyclicIdentity = structuredClone(report);
cyclicIdentity.provenance.loop = cyclicIdentity.provenance;
assert.equal(
  buildRecordedSpeechPlan({ kind: "engineer_query", report: cyclicIdentity })
    .ok,
  false,
  "a cyclic report identity must be rejected safely",
);
const oversizedIdentity = structuredClone(report);
oversizedIdentity.provenance.huge = "x".repeat(70_000);
assert.equal(
  buildRecordedSpeechPlan({ kind: "engineer_query", report: oversizedIdentity })
    .ok,
  false,
  "an oversized report identity must be rejected",
);
const longFacts = structuredClone(report);
longFacts.facts = Array.from({ length: 5 }, (_, index) => ({
  kind: `measurement_${index}`,
  text: "data ".repeat(189).trim(),
  source_fields: ["measurement"],
}));
const characterLimitResult = buildRecordedSpeechPlan({
  kind: "engineer_query",
  report: longFacts,
});
assert.equal(characterLimitResult.ok, false);
assert.match(characterLimitResult.reason, /speech length limit/);
const tooManyChunks = structuredClone(longFacts);
tooManyChunks.facts.push({
  kind: "measurement_5",
  text: "data ".repeat(189).trim(),
  source_fields: ["measurement"],
});
const chunkLimitResult = buildRecordedSpeechPlan({
  kind: "engineer_query",
  report: tooManyChunks,
});
assert.equal(chunkLimitResult.ok, false);
assert.match(chunkLimitResult.reason, /utterance limit/);
const oversizedWord = structuredClone(report);
oversizedWord.facts[0].text = "x".repeat(241);
assert.equal(
  buildRecordedSpeechPlan({ kind: "engineer_query", report: oversizedWord }).ok,
  false,
  "a word that cannot fit within the utterance limit must be rejected",
);

class FakeSpeechSynthesis extends EventTarget {
  voices = [];
  utterances = [];
  cancelCount = 0;
  throwOnSpeak = false;
  throwOnGetVoices = false;

  getVoices() {
    if (this.throwOnGetVoices)
      throw new Error("synthetic voice discovery failure");
    return this.voices;
  }

  speak(utterance) {
    if (this.throwOnSpeak) throw new Error("synthetic speech failure");
    this.utterances.push(utterance);
  }

  cancel() {
    this.cancelCount += 1;
  }
}

class FakeLocalStorage {
  values = new Map();
  writeCount = 0;
  failRead = false;
  failWrite = false;

  getItem(key) {
    if (this.failRead) throw new Error("synthetic storage read failure");
    return this.values.get(key) ?? null;
  }

  setItem(key, value) {
    if (this.failWrite) throw new Error("synthetic storage write failure");
    this.writeCount += 1;
    this.values.set(key, value);
  }
}

class FakeUtterance {
  constructor(text) {
    this.text = text;
    this.voice = null;
    this.lang = "";
    this.onend = null;
    this.onerror = null;
  }
}

const synthesis = new FakeSpeechSynthesis();
globalThis.window = new EventTarget();
const storage = new FakeLocalStorage();
window.localStorage = storage;
window.setTimeout = globalThis.setTimeout.bind(globalThis);
window.clearTimeout = globalThis.clearTimeout.bind(globalThis);
window.speechSynthesis = synthesis;
globalThis.SpeechSynthesisUtterance = FakeUtterance;
const playbackEvents = [];
const unsubscribe = subscribeToLocalSpeechPlayback((playback) =>
  playbackEvents.push(playback),
);

const discovered = [];
const stopWatching = watchForLocalSpeechVoice(
  synthesis,
  "en-GB",
  (result) => discovered.push(result),
  1_000,
);
assert.equal(
  discovered.length,
  0,
  "an initially empty voice list stays pending",
);
const localVoice = {
  localService: true,
  lang: "en-GB",
  name: "Local test voice",
};
const remoteVoice = {
  localService: false,
  lang: "en-GB",
  name: "Remote voice",
};

storage.values.set(
  LOCAL_VOICE_PREFERENCE_STORAGE_KEY,
  JSON.stringify({ schema_version: 99, rate: 1.6, volume: 0.4 }),
);
const preferenceNotifications = [];
const unsubscribePreferences = subscribeToLocalVoicePreferences(() =>
  preferenceNotifications.push(getLocalVoicePreferenceSnapshot()),
);
assert.equal(getLocalVoicePreferenceSnapshot().status, "invalid");
assert.deepEqual(
  getLocalVoicePreferenceSnapshot().effective,
  DEFAULT_LOCAL_VOICE_PREFERENCES,
  "unknown saved versions use safe defaults",
);
assert.equal(storage.writeCount, 0, "loading an unknown version never overwrites it");
const appliedPreferences = { rate: 1.4, volume: 0.35 };
assert.deepEqual(applyLocalVoicePreferences(appliedPreferences), { ok: true });
assert.equal(getLocalVoicePreferenceSnapshot().status, "stored");
assert.deepEqual(getLocalVoicePreferenceSnapshot().effective, appliedPreferences);
assert.deepEqual(
  Object.keys(JSON.parse(storage.values.get(LOCAL_VOICE_PREFERENCE_STORAGE_KEY))).sort(),
  ["rate", "schema_version", "volume"],
  "the persisted versioned record contains only rate and volume",
);
assert(
  new TextEncoder().encode(storage.values.get(LOCAL_VOICE_PREFERENCE_STORAGE_KEY)).byteLength <= LOCAL_VOICE_PREFERENCE_MAX_BYTES,
);
assert(preferenceNotifications.length > 0, "applied values notify active consumers");

speakLocalEvidence(["configured voice"], localVoice, "configured-source", appliedPreferences);
assert.equal(synthesis.utterances.at(-1).rate, 1.4);
assert.equal(synthesis.utterances.at(-1).volume, 0.35);
const effectiveBeforeFailure = getLocalVoicePreferenceSnapshot().effective;
storage.failWrite = true;
assert.equal(applyLocalVoicePreferences({ rate: 0.8, volume: 0.2 }).ok, false);
assert.equal(getLocalVoicePreferenceSnapshot().status, "write_failed");
assert.deepEqual(
  getLocalVoicePreferenceSnapshot().effective,
  effectiveBeforeFailure,
  "failed persistence leaves effective preferences unchanged",
);
storage.failWrite = false;
assert.deepEqual(applyLocalVoicePreferences(DEFAULT_LOCAL_VOICE_PREFERENCES), { ok: true });
assert.deepEqual(
  getLocalVoicePreferenceSnapshot().effective,
  DEFAULT_LOCAL_VOICE_PREFERENCES,
  "Reset atomically applies the defaults",
);

const crossTabPreferences = { schema_version: 1, rate: 1.8, volume: 0.6 };
const storageEvent = new Event("storage");
Object.defineProperty(storageEvent, "key", { value: LOCAL_VOICE_PREFERENCE_STORAGE_KEY });
storage.values.set(LOCAL_VOICE_PREFERENCE_STORAGE_KEY, JSON.stringify(crossTabPreferences));
window.dispatchEvent(storageEvent);
assert.equal(getLocalVoicePreferenceSnapshot().status, "stored");
assert.deepEqual(
  getLocalVoicePreferenceSnapshot().effective,
  { rate: 1.8, volume: 0.6 },
  "a valid cross-tab storage event publishes the saved preferences",
);
speakLocalEvidence(["first configured chunk", "second configured chunk"], localVoice, "configured-chunks", getLocalVoicePreferenceSnapshot().effective);
assert.equal(synthesis.utterances.at(-1).rate, 1.8);
assert.equal(synthesis.utterances.at(-1).volume, 0.6);
synthesis.utterances.at(-1).onend();
assert.equal(synthesis.utterances.at(-1).text, "second configured chunk");
assert.equal(synthesis.utterances.at(-1).rate, 1.8);
assert.equal(synthesis.utterances.at(-1).volume, 0.6);

storage.failRead = true;
window.dispatchEvent(storageEvent);
assert.equal(getLocalVoicePreferenceSnapshot().status, "unavailable");
assert.deepEqual(getLocalVoicePreferenceSnapshot().effective, DEFAULT_LOCAL_VOICE_PREFERENCES);
storage.failRead = false;
storage.values.set(LOCAL_VOICE_PREFERENCE_STORAGE_KEY, "x".repeat(LOCAL_VOICE_PREFERENCE_MAX_BYTES + 1));
window.dispatchEvent(storageEvent);
assert.equal(getLocalVoicePreferenceSnapshot().status, "invalid");
assert.match(getLocalVoicePreferenceSnapshot().message, /oversized/);
const multibyteOversized = JSON.stringify({
  schema_version: 1,
  rate: 1,
  volume: 1,
  extra: "🏁".repeat(600),
});
assert(multibyteOversized.length < LOCAL_VOICE_PREFERENCE_MAX_BYTES);
assert(new TextEncoder().encode(multibyteOversized).byteLength > LOCAL_VOICE_PREFERENCE_MAX_BYTES);
storage.values.set(LOCAL_VOICE_PREFERENCE_STORAGE_KEY, multibyteOversized);
window.dispatchEvent(storageEvent);
assert.equal(getLocalVoicePreferenceSnapshot().status, "invalid");
assert.match(getLocalVoicePreferenceSnapshot().message, /oversized/);
storage.values.set(LOCAL_VOICE_PREFERENCE_STORAGE_KEY, "{bad json");
window.dispatchEvent(storageEvent);
assert.equal(getLocalVoicePreferenceSnapshot().status, "invalid");
assert.match(getLocalVoicePreferenceSnapshot().message, /malformed/);
assert.equal(storage.writeCount, 2, "invalid, malformed, and oversized records are never rewritten during load");
unsubscribePreferences();
synthesis.voices = [remoteVoice];
synthesis.dispatchEvent(new Event("voiceschanged"));
assert.equal(
  discovered.at(-1).status,
  "unavailable",
  "remote-only voices are rejected",
);
synthesis.voices = [remoteVoice, localVoice];
synthesis.dispatchEvent(new Event("voiceschanged"));
assert.equal(
  discovered.at(-1).status,
  "ready",
  "a delayed local voice is discovered",
);
assert.equal(discovered.at(-1).voice, localVoice);
synthesis.voices = [];
synthesis.dispatchEvent(new Event("voiceschanged"));
assert.equal(
  discovered.at(-1).status,
  "unavailable",
  "an empty voice list clears a previously ready local voice",
);
synthesis.voices = [localVoice];
synthesis.dispatchEvent(new Event("voiceschanged"));
assert.equal(discovered.at(-1).status, "ready");
synthesis.throwOnGetVoices = true;
synthesis.dispatchEvent(new Event("voiceschanged"));
assert.equal(
  discovered.at(-1).status,
  "unavailable",
  "voice discovery errors clear a previously ready local voice",
);
synthesis.throwOnGetVoices = false;
stopWatching();
const discoveryCount = discovered.length;
synthesis.dispatchEvent(new Event("voiceschanged"));
assert.equal(
  discovered.length,
  discoveryCount,
  "voice discovery cleans up its listener",
);

const localOnly = { localService: true, lang: "en-GB", name: "Local voice" };
const beforeReplacementSequence = synthesis.utterances.length;
speakLocalEvidence(["first", "second"], localOnly, "first-source");
assert.equal(playbackEvents.at(-1).status, "speaking");
const staleUtterance = synthesis.utterances.at(-1);
speakLocalEvidence(["replacement"], localOnly, "replacement-source");
staleUtterance.onend();
assert.equal(
  synthesis.utterances.length,
  beforeReplacementSequence + 2,
  "a stale callback cannot resume a replaced sequence",
);
cancelLocalEvidenceSpeech("replacement-source");
synthesis.utterances.at(-1).onend();
assert.equal(synthesis.utterances.length, beforeReplacementSequence + 2, "Stop prevents later chunks");
assert.equal(playbackEvents.at(-1).status, "stopped");

speakLocalEvidence(["same-source"], localOnly, "unmount-source");
cancelLocalEvidenceSpeech("unmount-source");
assert.equal(
  playbackEvents.at(-1).status,
  "stopped",
  "unmount cancellation stops its source",
);
speakLocalEvidence(["hidden-page"], localOnly, "hidden-source");
cancelLocalEvidenceSpeechForPage();
assert.equal(
  playbackEvents.at(-1).status,
  "stopped",
  "page hiding stops active speech",
);

const utteranceCount = synthesis.utterances.length;
speakLocalEvidence(["remote must not play"], remoteVoice, "remote-source");
assert.equal(
  synthesis.utterances.length,
  utteranceCount,
  "remote voices never play",
);
assert.equal(playbackEvents.at(-1).status, "failed");

speakLocalEvidence(["speech error"], localOnly, "error-source");
synthesis.utterances.at(-1).onerror();
assert.equal(
  playbackEvents.at(-1).status,
  "failed",
  "synthesis errors are reported",
);
synthesis.throwOnSpeak = true;
speakLocalEvidence(["start error"], localOnly, "start-error-source");
assert.equal(
  playbackEvents.at(-1).status,
  "failed",
  "synchronous synthesis errors are reported",
);
synthesis.throwOnSpeak = false;

speakLocalEvidence(["complete"], localOnly, "complete-source");
synthesis.utterances.at(-1).onend();
assert.equal(playbackEvents.at(-1).status, "complete");
unsubscribe();

console.log(
  "Recorded speech checks passed: provenance and speech bounds, local-only voice discovery and cancellation, atomic browser preference loading/apply/reset/failure behavior, effective rate/volume, and completion.",
);
