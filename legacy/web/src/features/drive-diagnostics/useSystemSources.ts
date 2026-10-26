"use client";

import { useEffect, useState } from "react";
import { createEndpointPoller, createFailureFilter } from "@/features/live-status/poller";
import { parseSpeechStatus, type MicrophoneState, type SpeechState } from "./system-rows";
import { radioMicrophoneTile } from "@/features/settings/radio-settings";
import { useRadioPresence } from "@/features/settings/useRadioPresence";

export function useMicrophone(): MicrophoneState {
  const presence = useRadioPresence();
  return { kind: "radio", device: radioMicrophoneTile(presence) };
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
