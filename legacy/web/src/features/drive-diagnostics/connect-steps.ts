import type { ReceiverAddress } from "./load";

// Steps between "engine running" and "telemetry live" for the Drive checklist.
// Pure, so the server render and tests share it.

export type ChecklistStep = { id: string; text: string; done: boolean; hint?: string };

/** "0.0.0.0" binds every interface, so the game needs this computer's own address. */
function addressText(receiver: ReceiverAddress): string {
  const host = receiver.host && receiver.host !== "0.0.0.0" ? receiver.host : "this computer's IP address";
  return `IP address ${host}, port ${receiver.port ?? 20777}`;
}

/** The steps between "engine running" and "telemetry live", ticked from live state. */
export function checklistSteps({
  reachable,
  acquisitionState,
  receiver,
}: {
  reachable: boolean;
  acquisitionState: string | null;
  receiver: ReceiverAddress;
}): ChecklistStep[] {
  const listening = ["listening", "starting", "stale", "receiving"].includes(acquisitionState ?? "");
  const receiving = acquisitionState === "receiving";
  return [
    { id: "engine", text: "Local engine running", done: reachable },
    {
      id: "receiver",
      text: `Receiver listening on UDP ${receiver.port ?? 20777}`,
      done: listening,
      hint: acquisitionState === "disabled" ? "Automatic acquisition is turned off for this engine." : undefined,
    },
    {
      id: "game",
      text: "UDP telemetry turned on in the game",
      done: receiving,
      hint: `In F1, open Settings, then Telemetry Settings. Turn UDP Telemetry on and set ${addressText(receiver)}.`,
    },
    { id: "track", text: "On track in any session", done: receiving },
  ];
}
