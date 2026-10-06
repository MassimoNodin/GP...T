export const LOCAL_SPEECH_SAMPLE_RATE = 16_000;
export const LOCAL_SPEECH_MAX_SECONDS = 12;
export const LOCAL_SPEECH_MAX_SAMPLES =
  LOCAL_SPEECH_SAMPLE_RATE * LOCAL_SPEECH_MAX_SECONDS;
export const LOCAL_SPEECH_MAX_AUDIO_BYTES = 512 * 1_024;
export const LOCAL_SPEECH_MAX_TRANSCRIPT_BYTES = 4 * 1_024;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const REQUEST_ID_PATTERN =
  /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;

export type LocalSpeechStatus =
  | {
      status: "ready";
      runtime_version: "1.9.3";
      model_name: "tiny.en";
      model_sha256: string;
      runtime_id: string;
      maximum_clip_seconds: 12;
    }
  | { status: "unavailable"; reason: string };

export type LocalTranscriptionDraft = {
  request_id: string;
  audio_sha256: string;
  transcript: string;
  runtime_id: string;
};

export function buildCanonicalPcmWav(samples: Float32Array): Uint8Array | null {
  if (samples.length < 1 || samples.length > LOCAL_SPEECH_MAX_SAMPLES)
    return null;
  const payloadBytes = samples.length * 2;
  const wav = new Uint8Array(44 + payloadBytes);
  const view = new DataView(wav.buffer);
  writeAscii(wav, 0, "RIFF");
  view.setUint32(4, wav.byteLength - 8, true);
  writeAscii(wav, 8, "WAVE");
  writeAscii(wav, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, LOCAL_SPEECH_SAMPLE_RATE, true);
  view.setUint32(28, LOCAL_SPEECH_SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(wav, 36, "data");
  view.setUint32(40, payloadBytes, true);
  for (let index = 0; index < samples.length; index += 1) {
    const sample = samples[index];
    if (!Number.isFinite(sample)) return null;
    const bounded = Math.max(-1, Math.min(1, sample));
    const pcm =
      bounded < 0 ? Math.round(bounded * 32_768) : Math.round(bounded * 32_767);
    view.setInt16(44 + index * 2, pcm, true);
  }
  return wav;
}

export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const exactBuffer = bytes.buffer.slice(
    bytes.byteOffset,
    bytes.byteOffset + bytes.byteLength,
  ) as ArrayBuffer;
  const digest = await crypto.subtle.digest("SHA-256", exactBuffer);
  return [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

export function parseLocalSpeechStatus(
  value: unknown,
): LocalSpeechStatus | null {
  if (!isRecord(value) || value.api_version !== "v1" || value.status !== "ok")
    return null;
  const data = value.data;
  if (!isRecord(data)) return null;
  if (data.status === "unavailable") {
    return {
      status: "unavailable",
      reason:
        typeof data.reason === "string" && data.reason.length <= 96
          ? data.reason
          : "runtime_unavailable",
    };
  }
  if (
    data.status !== "ready" ||
    data.runtime !== "whisper.cpp" ||
    data.runtime_version !== "1.9.3" ||
    data.model_name !== "tiny.en" ||
    typeof data.model_sha256 !== "string" ||
    !SHA256_PATTERN.test(data.model_sha256) ||
    typeof data.runtime_id !== "string" ||
    !SHA256_PATTERN.test(data.runtime_id) ||
    data.language !== "en" ||
    data.placement !== "CPU" ||
    data.maximum_clip_seconds !== LOCAL_SPEECH_MAX_SECONDS ||
    data.audio_persisted !== false
  ) {
    return null;
  }
  return {
    status: "ready",
    runtime_version: "1.9.3",
    model_name: "tiny.en",
    model_sha256: data.model_sha256,
    runtime_id: data.runtime_id,
    maximum_clip_seconds: LOCAL_SPEECH_MAX_SECONDS,
  };
}

export function parseLocalTranscription(
  value: unknown,
  expectedRequestId: string,
  expectedAudioSha256: string,
  expectedRuntimeId: string,
): LocalTranscriptionDraft | null {
  if (
    !REQUEST_ID_PATTERN.test(expectedRequestId) ||
    !SHA256_PATTERN.test(expectedAudioSha256) ||
    !SHA256_PATTERN.test(expectedRuntimeId) ||
    !isRecord(value) ||
    value.api_version !== "v1" ||
    value.status !== "ok" ||
    !isRecord(value.data)
  ) {
    return null;
  }
  const data = value.data;
  if (
    data.schema_version !== 1 ||
    data.request_id !== expectedRequestId ||
    data.audio_sha256 !== expectedAudioSha256 ||
    typeof data.transcript !== "string" ||
    data.language !== "en" ||
    data.verified !== false ||
    !isRecord(data.runtime) ||
    data.runtime.name !== "whisper.cpp" ||
    data.runtime.version !== "1.9.3" ||
    data.runtime.model_name !== "tiny.en" ||
    data.runtime.runtime_id !== expectedRuntimeId ||
    data.runtime.placement !== "CPU" ||
    typeof data.runtime.model_sha256 !== "string" ||
    !SHA256_PATTERN.test(data.runtime.model_sha256)
  ) {
    return null;
  }
  const transcriptBytes = new TextEncoder().encode(data.transcript).byteLength;
  if (
    transcriptBytes < 1 ||
    transcriptBytes > LOCAL_SPEECH_MAX_TRANSCRIPT_BYTES ||
    !data.transcript.trim()
  ) {
    return null;
  }
  return {
    request_id: expectedRequestId,
    audio_sha256: expectedAudioSha256,
    transcript: data.transcript,
    runtime_id: expectedRuntimeId,
  };
}

function writeAscii(bytes: Uint8Array, offset: number, value: string) {
  for (let index = 0; index < value.length; index += 1) {
    bytes[offset + index] = value.charCodeAt(index);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}
