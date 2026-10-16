"use client";

import { useEffect, useState } from "react";
import { createEndpointPoller, createFailureFilter } from "@/features/live-status/poller";
import { microphoneState, parseSpeechStatus, type MicrophoneState, type SpeechState } from "./system-rows";

// Client sources for the browser-derived and speech System Status rows. Both
// start from a state the server also renders, then resolve in an effect.

/**
 * Microphone permission and the first audio input's label. Labels are only
 * exposed by the browser once permission is granted. Safari has no
 * `permissions.query` for the microphone; that reads as "not set up".
 */
export function useMicrophone(): MicrophoneState {
  const [state, setState] = useState<MicrophoneState>({ kind: "checking" });
  useEffect(() => {
    const devices = typeof navigator === "undefined" ? undefined : navigator.mediaDevices;
    if (!devices || typeof devices.enumerateDevices !== "function") {
      setState({ kind: "unsupported" });
      return;
    }
    let cancelled = false;
    let permission: PermissionStatus | null = null;

    async function refresh() {
      let permissionState: "granted" | "denied" | "prompt" | null = null;
      try {
        permission ??= await navigator.permissions.query({ name: "microphone" as PermissionName });
        permission.onchange = () => void refresh();
        permissionState = permission.state;
      } catch {
        permissionState = null;
      }
      let labels: string[] = [];
      try {
        const list = await devices!.enumerateDevices();
        labels = list.filter((device) => device.kind === "audioinput").map((device) => device.label);
      } catch {
        labels = [];
      }
      if (!cancelled) setState(microphoneState(permissionState, labels));
    }

    void refresh();
    const onDeviceChange = () => void refresh();
    devices.addEventListener?.("devicechange", onDeviceChange);
    return () => {
      cancelled = true;
      devices.removeEventListener?.("devicechange", onDeviceChange);
      if (permission) permission.onchange = null;
    };
  }, []);
  return state;
}

export const SPEECH_POLL_MS = 30_000;

/**
 * Speech runtime status from `/api/engineer/transcribe` (GET), polled every
 * 30 s while the tab is visible. Starts from the server-read value.
 */
export function useSpeechStatus(initial: SpeechState): SpeechState {
  const [state, setState] = useState(initial);
  useEffect(() => {
    const push = createFailureFilter<SpeechState>((next) => setState(next ?? { kind: "unreachable" }));
    const poller = createEndpointPoller({
      url: "/api/engineer/transcribe",
      parse: (body) => parseSpeechStatus(body),
      onResult: push,
      intervalMs: SPEECH_POLL_MS,
    });
    const apply = () => poller.setHidden(document.hidden);
    apply();
    document.addEventListener("visibilitychange", apply);
    return () => {
      document.removeEventListener("visibilitychange", apply);
      poller.stop();
    };
  }, []);
  return state;
}
