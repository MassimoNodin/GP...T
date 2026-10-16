"use client";

import {
  buildCanonicalPcmWav,
  parseLocalSpeechStatus,
  parseLocalTranscription,
  sha256Hex,
} from "@/lib/local-speech-draft";
import {
  EMPTY_TRANSCRIPT_TEXT,
  SPEECH_SAMPLE_RATE,
  TRANSCRIPT_UNVERIFIED_TEXT,
  isSilent,
  resampleMono,
  transcribeFailureText,
  SILENT_TEXT,
} from "./audio";
import { speechReasonText } from "./connections";

export type SpeechTestOutcome =
  | { kind: "heard"; transcript: string; ms: number }
  | { kind: "error"; text: string };

async function readJson(response: Response): Promise<unknown> {
  try {
    return (await response.json()) as unknown;
  } catch {
    return null;
  }
}

function reasonOf(body: unknown): string | null {
  const reason = body !== null && typeof body === "object" ? (body as { reason?: unknown }).reason : null;
  return typeof reason === "string" ? reason.slice(0, 96) : null;
}

/**
 * Sends one recorded clip to the local speech recognizer and returns what it
 * heard. The clip is resampled to 16 kHz, wrapped as a canonical WAV and
 * checked against its SHA-256 on the way back. Never throws.
 */
export async function runSpeechTest(
  samples: Float32Array,
  sampleRate: number,
  signal?: AbortSignal,
): Promise<SpeechTestOutcome> {
  if (isSilent(samples)) return { kind: "error", text: SILENT_TEXT };
  const wav = buildCanonicalPcmWav(resampleMono(samples, sampleRate, SPEECH_SAMPLE_RATE));
  if (!wav) return { kind: "error", text: "The recording could not be prepared. Try again." };
  try {
    const statusResponse = await fetch("/api/engineer/transcribe", { cache: "no-store", signal });
    const statusBody = await readJson(statusResponse);
    const status = parseLocalSpeechStatus(statusBody);
    if (status === null) {
      return { kind: "error", text: transcribeFailureText(statusResponse.ok ? 503 : statusResponse.status, reasonOf(statusBody)) };
    }
    if (status.status !== "ready") return { kind: "error", text: speechReasonText(status.reason) };

    const audioSha256 = await sha256Hex(wav);
    const requestId = crypto.randomUUID();
    const started = performance.now();
    const response = await fetch("/api/engineer/transcribe", {
      method: "POST",
      headers: {
        "content-type": "audio/wav",
        "x-request-id": requestId,
        "x-audio-sha256": audioSha256,
      },
      body: new Uint8Array(wav),
      cache: "no-store",
      signal,
    });
    const body = await readJson(response);
    const ms = performance.now() - started;
    if (!response.ok) return { kind: "error", text: transcribeFailureText(response.status, reasonOf(body)) };
    const heard = (body as { data?: { transcript?: unknown } } | null)?.data?.transcript;
    if (typeof heard === "string" && heard.trim() === "") return { kind: "error", text: EMPTY_TRANSCRIPT_TEXT };
    const draft = parseLocalTranscription(body, requestId, audioSha256, status.runtime_id);
    if (!draft) return { kind: "error", text: TRANSCRIPT_UNVERIFIED_TEXT };
    return { kind: "heard", transcript: draft.transcript, ms };
  } catch {
    if (signal?.aborted) return { kind: "error", text: "The test was cancelled." };
    return { kind: "error", text: transcribeFailureText(null, null) };
  }
}
