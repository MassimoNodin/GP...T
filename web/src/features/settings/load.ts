import { loadCurrentSession } from "@/features/drive-diagnostics/load";
import type { SessionContextModel } from "@/features/drive-diagnostics/session";
import { fetchPreferences, fetchStorage, fetchTrackModels } from "@/features/review-data/backend";
import type { ReadResult } from "@/features/review-data/backend";
import { settingsFixture } from "@/features/review-data/fixtures";
import type { PreferencesRead } from "@/features/review-data/preferences";
import type { TrackModelSummary } from "@/features/review-data/regions";
import type { ShellStatus } from "@/features/ui-foundation/AppShell";
import { forwardLocalRequest } from "@/lib/local-proxy";
import { describeSpeech, type SpeechView } from "./connections";
import {
  describeGameTile,
  describeReceiver,
  describeRuntime,
  parseReceiverInfo,
  parseRuntimeInfo,
  type ReceiverInfo,
  type ReceiverView,
  type RuntimeInfo,
  type RuntimeView,
} from "./display";
import { buildStorageView, type StorageView } from "./storage-view";
import { describeTrackModels, type TrackModelsView } from "./track-models";

export type { StorageScopeView, StorageView } from "./storage-view";

export type SettingsModel = typeof settingsFixture & {
  pill: ShellStatus;
  /** True when no engine endpoint answered. */
  engineReachable: boolean;
  runtime: RuntimeView;
  receiver: ReceiverView;
  game: { name: string; state: string; tone: "green-text" | "amber-text" | "grey-text" };
  storage: StorageView | null;
  /** Sidebar footer description of the current or most recent session. */
  footerLines: string[];
  /** Saved engineer preferences as read on the server. */
  preferences: PreferencesRead;
  speech: SpeechView;
  trackModels: TrackModelsView;
  /** Outline key for the Track Models tile (current session's circuit, else generic). */
  trackKey: string;
};

async function readJson(path: string): Promise<unknown> {
  try {
    const response = await forwardLocalRequest(path, { signal: AbortSignal.timeout(2500) });
    // The runtime endpoint answers 503 with a full envelope while unavailable.
    return await response.json();
  } catch {
    return null;
  }
}

export function runtimePill(info: RuntimeInfo | null): ShellStatus {
  const view = describeRuntime(info);
  if (!view.available) return { tone: "red", label: "Engineer offline" };
  if (view.tone === "green") return { tone: "green", label: "Engineer running" };
  return { tone: view.tone === "red" ? "red" : "amber", label: `Engineer ${view.statusLabel.toLowerCase()}` };
}

export async function loadSettings(): Promise<SettingsModel> {
  const [runtimeBody, receiverBody, speechBody, storage, preferences, trackModels] = await Promise.all([
    readJson("/api/v1/engineer/runtime"),
    readJson("/api/v2/session-evidence/status"),
    readJson("/api/v1/engineer/transcribe"),
    fetchStorage(),
    fetchPreferences(),
    fetchTrackModels({ timeoutMs: 2500 }),
  ]);
  const runtimeInfo = parseRuntimeInfo(runtimeBody);
  const receiverInfo = parseReceiverInfo(receiverBody);
  const session = await loadCurrentSession(receiverBody);
  return buildSettingsModel({
    runtimeInfo,
    receiverInfo,
    storage,
    session,
    preferences,
    speechBody,
    trackModels,
  });
}

export function buildSettingsModel({
  runtimeInfo,
  receiverInfo,
  storage,
  session = null,
  preferences = { state: "unreachable" },
  speechBody = null,
  trackModels = { state: "unreachable" },
}: {
  runtimeInfo: RuntimeInfo | null;
  receiverInfo: ReceiverInfo | null;
  storage: Awaited<ReturnType<typeof fetchStorage>>;
  /** Current or most recent v2 session; null keeps the reference game and footer. */
  session?: SessionContextModel | null;
  preferences?: PreferencesRead;
  /** Body of `GET /api/v1/engineer/transcribe`; null when it could not be read. */
  speechBody?: unknown;
  trackModels?: ReadResult<TrackModelSummary[]>;
}): SettingsModel {
  const speech = describeSpeech(speechBody);
  const engineReachable = Boolean(
    runtimeInfo ||
      receiverInfo ||
      storage ||
      preferences.state !== "unreachable" ||
      speech.state !== "unreachable" ||
      trackModels.state !== "unreachable",
  );
  const receiver = describeReceiver(receiverInfo);
  const gameName = session?.facts.find(([label]) => label === "Game")?.[1];
  const game = describeGameTile(receiverInfo, gameName ? { ...settingsFixture.status.game, name: gameName } : settingsFixture.status.game);
  const status = { ...settingsFixture.status };

  // Microphone, audio output and the track model tile are derived in the
  // browser or from the catalog; the reference values are never shown as fact.
  let storageView: StorageView | null = null;
  if (storage) {
    storageView = buildStorageView(storage);
    status.storage = { used: storageView.used, available: storageView.available };
  } else {
    status.storage = { used: "Unavailable", available: "Not measured" };
  }

  return {
    ...settingsFixture,
    // Never the reference session: without one, say what is missing instead.
    footerLines: session?.footerLines ?? (engineReachable ? ["No session yet", "Waiting for the game"] : ["Start the local engine", ""]),
    header: { items: [game.name, receiverInfo ? (game.state.startsWith("Connected") ? "Connected" : "Not connected") : "Engine unavailable"] },
    status,
    pill: runtimePill(runtimeInfo),
    engineReachable,
    runtime: describeRuntime(runtimeInfo),
    receiver,
    game,
    storage: storageView,
    preferences,
    speech,
    trackModels: describeTrackModels(trackModels),
    trackKey: session?.track ?? "generic",
  };
}
