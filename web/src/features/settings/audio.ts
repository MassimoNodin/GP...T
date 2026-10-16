// Pure audio helpers for the Voice & Controls tab: level meter mapping,
// downsampling for the speech test and the exact copy shown for its results.

/* ------------------------------------------------------------------ meter */

export const METER_SEGMENTS = 20;
const METER_FLOOR_DB = -60;

/** Root-mean-square of a block of samples (0 to 1). */
export function rmsOf(samples: ArrayLike<number>): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let index = 0; index < samples.length; index += 1) {
    const value = samples[index];
    sum += value * value;
  }
  return Math.sqrt(sum / samples.length);
}

/** Largest absolute sample (0 to 1). */
export function peakOf(samples: ArrayLike<number>): number {
  let peak = 0;
  for (let index = 0; index < samples.length; index += 1) {
    const value = Math.abs(samples[index]);
    if (value > peak) peak = value;
  }
  return peak;
}

/** Meter position 0 to 1 on a -60 dB to 0 dB scale. */
export function levelFraction(rms: number): number {
  if (!Number.isFinite(rms) || rms <= 0) return 0;
  const db = 20 * Math.log10(Math.min(1, rms));
  return Math.min(1, Math.max(0, (db - METER_FLOOR_DB) / -METER_FLOOR_DB));
}

/** Number of lit segments (0 to 20). */
export function litSegments(rms: number): number {
  return Math.round(levelFraction(rms) * METER_SEGMENTS);
}

/** Colour band of a segment: the top of the scale warns of clipping. */
export function segmentTone(index: number): "green" | "amber" | "red" {
  if (index >= 18) return "red";
  if (index >= 14) return "amber";
  return "green";
}

/* --------------------------------------------------------------- recording */

export const TEST_SECONDS = 5;
export const SPEECH_SAMPLE_RATE = 16_000;
const SILENCE_PEAK = 0.01;

/** True when nothing audible was captured, so there is no point sending it. */
export function isSilent(samples: ArrayLike<number>): boolean {
  return peakOf(samples) < SILENCE_PEAK;
}

/**
 * Downmix-free resample of mono samples to `toRate`: box-averages when
 * reducing the rate, linear interpolation otherwise.
 */
export function resampleMono(samples: Float32Array, fromRate: number, toRate = SPEECH_SAMPLE_RATE): Float32Array {
  if (!(fromRate > 0) || !(toRate > 0) || samples.length === 0) return new Float32Array(0);
  if (fromRate === toRate) return samples;
  const ratio = fromRate / toRate;
  const length = Math.max(1, Math.floor(samples.length / ratio));
  const output = new Float32Array(length);
  if (ratio > 1) {
    for (let index = 0; index < length; index += 1) {
      const from = Math.floor(index * ratio);
      const to = Math.min(samples.length, Math.max(from + 1, Math.floor((index + 1) * ratio)));
      let sum = 0;
      for (let at = from; at < to; at += 1) sum += samples[at];
      output[index] = sum / (to - from);
    }
    return output;
  }
  for (let index = 0; index < length; index += 1) {
    const position = index * ratio;
    const left = Math.floor(position);
    const right = Math.min(samples.length - 1, left + 1);
    const weight = position - left;
    output[index] = samples[left] * (1 - weight) + samples[right] * weight;
  }
  return output;
}

/** Joins recorded blocks and trims to `maxSamples`. */
export function joinBlocks(blocks: readonly Float32Array[], maxSamples: number): Float32Array {
  let total = 0;
  for (const block of blocks) total += block.length;
  const length = Math.min(total, Math.max(0, Math.floor(maxSamples)));
  const output = new Float32Array(length);
  let offset = 0;
  for (const block of blocks) {
    if (offset >= length) break;
    const take = Math.min(block.length, length - offset);
    output.set(take === block.length ? block : block.subarray(0, take), offset);
    offset += take;
  }
  return output;
}

/* ------------------------------------------------------------------- copy */

/** `0.8 s` for a duration in milliseconds. */
export function formatSeconds(ms: number): string {
  return `${(Math.max(0, ms) / 1000).toFixed(1)} s`;
}

export function heardText(transcript: string): string {
  return `We heard: “${transcript.trim()}”`;
}

export const SILENT_TEXT = "No sound was picked up. Check the input device and speak closer to the microphone.";

/** Text for a failed transcription request (HTTP status and the proxy or engine reason code). */
export function transcribeFailureText(status: number | null, reason: string | null): string {
  if (status === null) return "The local engine is not reachable.";
  if (status === 403 || status === 401) {
    return "The web app is not allowed to use speech recognition. Check the engine's control token.";
  }
  if (status === 413) return "The recording was too large to send. Try a shorter test.";
  if (status === 409 || status === 429) return "Speech recognition is busy. Try again in a moment.";
  if (status === 504 || reason === "engineer_speech_deadline_exceeded") {
    return "Speech recognition took too long. Try again.";
  }
  if (status === 503 || status === 502 || reason === "engineer_speech_unavailable") {
    return "Speech recognition is not set up on the engine.";
  }
  return "Speech recognition could not process the recording. Try again.";
}

export const TRANSCRIPT_UNVERIFIED_TEXT = "The engine's answer could not be verified. Try again.";
export const EMPTY_TRANSCRIPT_TEXT = "Speech recognition did not hear any words. Try again.";

/* ----------------------------------------------------------------- voice */

export const RATE_MIN = 0.8;
export const RATE_MAX = 1.4;
export const RATE_STEP = 0.1;

/** Nearest 0.1 step inside the offered range. */
export function snapRate(value: number): number {
  if (!Number.isFinite(value)) return 1;
  const stepped = Math.round(value / RATE_STEP) * RATE_STEP;
  return Math.round(Math.min(RATE_MAX, Math.max(RATE_MIN, stepped)) * 10) / 10;
}

export function rateLabel(rate: number): string {
  return `${rate.toFixed(1)}×`;
}

export function volumeLabel(volume: number): string {
  return `${Math.round(Math.min(1, Math.max(0, volume)) * 100)}%`;
}

export const SAMPLE_PHRASE = "Lap 28, one thirteen four two zero, plus zero point five seven eight to your best.";

export type VoiceOption = { voiceURI: string; name: string; lang: string };

/** Local voices only (no network voices), English first, then by name. */
export function localVoiceOptions(
  voices: ReadonlyArray<{ voiceURI: string; name: string; lang: string; localService: boolean }>,
): VoiceOption[] {
  return voices
    .filter((voice) => voice.localService === true && voice.voiceURI && voice.name)
    .map((voice) => ({ voiceURI: voice.voiceURI, name: voice.name, lang: voice.lang }))
    .sort((a, b) => {
      const aEnglish = a.lang.toLowerCase().startsWith("en") ? 0 : 1;
      const bEnglish = b.lang.toLowerCase().startsWith("en") ? 0 : 1;
      return aEnglish - bEnglish || a.name.localeCompare(b.name);
    });
}

/** The voice to use: the saved choice when still installed, otherwise the first (English) voice. */
export function pickVoice(options: readonly VoiceOption[], voiceURI: string | null): VoiceOption | null {
  if (voiceURI) {
    const saved = options.find((option) => option.voiceURI === voiceURI);
    if (saved) return saved;
  }
  return options[0] ?? null;
}
