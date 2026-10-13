"use client";

import { joinBlocks, rmsOf } from "./audio";

// Microphone capture for the level meter and the speech test. One stream feeds
// an AnalyserNode (meter) and, only while a test is recording, a capture node
// whose blocks are kept in memory. Nothing is stored or sent from here.

export type MicSession = {
  sampleRate: number;
  /** Begins keeping audio blocks. */
  startRecording(): void;
  /** Stops keeping blocks and returns what was captured (at most `maxSeconds`). */
  finishRecording(maxSeconds: number): Float32Array;
  close(): void;
};

type AudioContextConstructor = typeof AudioContext;

const WORKLET_SOURCE = `
class CaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) this.port.postMessage(channel.slice());
    return true;
  }
}
registerProcessor("settings-capture", CaptureProcessor);
`;

const METER_INTERVAL_MS = 66;

/**
 * Opens `deviceId` (or the default input) and reports its level through
 * `onLevel` about 15 times a second. Rejects with the browser's error.
 */
export async function openMic(deviceId: string | null, onLevel: (rms: number) => void): Promise<MicSession> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      deviceId: deviceId ? { exact: deviceId } : undefined,
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: false,
    },
  });
  const Context: AudioContextConstructor | undefined =
    window.AudioContext ?? (window as unknown as { webkitAudioContext?: AudioContextConstructor }).webkitAudioContext;
  if (!Context) {
    for (const track of stream.getTracks()) track.stop();
    throw new DOMException("Web Audio is not available", "NotSupportedError");
  }
  const context = new Context();
  if (context.state === "suspended") void context.resume().catch(() => undefined);
  const source = context.createMediaStreamSource(stream);
  const analyser = context.createAnalyser();
  analyser.fftSize = 1024;
  source.connect(analyser);

  let recording = false;
  let blocks: Float32Array[] = [];
  const keep = (block: Float32Array) => {
    if (recording) blocks.push(block);
  };

  let worklet: AudioWorkletNode | null = null;
  let processor: ScriptProcessorNode | null = null;
  let moduleUrl: string | null = null;
  const silent = context.createGain();
  silent.gain.value = 0;
  silent.connect(context.destination);
  try {
    if (!context.audioWorklet) throw new Error("no worklet");
    moduleUrl = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: "text/javascript" }));
    await context.audioWorklet.addModule(moduleUrl);
    worklet = new AudioWorkletNode(context, "settings-capture");
    worklet.port.onmessage = (event: MessageEvent<Float32Array>) => keep(event.data);
    source.connect(worklet);
    worklet.connect(silent);
  } catch {
    // Older browsers (or a CSP that blocks blob modules): fall back to a script processor.
    processor = context.createScriptProcessor(4096, 1, 1);
    processor.onaudioprocess = (event) => keep(event.inputBuffer.getChannelData(0).slice());
    source.connect(processor);
    processor.connect(silent);
  } finally {
    if (moduleUrl) URL.revokeObjectURL(moduleUrl);
  }

  const buffer = new Float32Array(analyser.fftSize);
  let closed = false;
  let lastReport = 0;
  let frame = 0;
  const tick = (now: number) => {
    if (closed) return;
    if (now - lastReport >= METER_INTERVAL_MS) {
      lastReport = now;
      analyser.getFloatTimeDomainData(buffer);
      onLevel(rmsOf(buffer));
    }
    frame = window.requestAnimationFrame(tick);
  };
  frame = window.requestAnimationFrame(tick);

  return {
    sampleRate: context.sampleRate,
    startRecording() {
      blocks = [];
      recording = true;
    },
    finishRecording(maxSeconds: number) {
      recording = false;
      const samples = joinBlocks(blocks, maxSeconds * context.sampleRate);
      blocks = [];
      return samples;
    },
    close() {
      if (closed) return;
      closed = true;
      window.cancelAnimationFrame(frame);
      recording = false;
      blocks = [];
      if (worklet) {
        worklet.port.onmessage = null;
        worklet.disconnect();
      }
      if (processor) {
        processor.onaudioprocess = null;
        processor.disconnect();
      }
      source.disconnect();
      for (const track of stream.getTracks()) track.stop();
      void context.close().catch(() => undefined);
    },
  };
}
