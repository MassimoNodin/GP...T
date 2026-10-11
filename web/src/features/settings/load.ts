import { describeStorageReason, formatStorageBytes } from "@/lib/storage-usage.mjs";
import { loadCurrentSession } from "@/features/drive-diagnostics/load";
import type { SessionContextModel } from "@/features/drive-diagnostics/session";
import { fetchStorage } from "@/features/review-data/backend";
import { settingsFixture, shellFixture } from "@/features/review-data/fixtures";
import type { ShellStatus } from "@/features/ui-foundation/AppShell";
import { forwardLocalRequest } from "@/lib/local-proxy";
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

export type StorageScopeView = {
  label: string;
  value: string;
  detail: string | null;
  /** Share of the measured recordings total, 0 to 1; null when unmeasured. */
  share: number | null;
  unavailable: boolean;
};

export type StorageView = {
  used: string;
  available: string;
  capacity: string | null;
  /** Fraction of the volume in use, 0 to 1; null when the volume is unknown. */
  volumeShare: number | null;
  scopes: StorageScopeView[];
  note: string;
  complete: boolean;
};

export type SettingsModel = typeof settingsFixture & {
  pill: ShellStatus;
  /** True when neither the runtime nor the receiver endpoint answered. */
  engineReachable: boolean;
  runtime: RuntimeView;
  receiver: ReceiverView;
  game: { name: string; state: string; tone: "green-text" | "amber-text" | "grey-text" };
  storage: StorageView | null;
  /** Sidebar footer description of the current or most recent session. */
  footerLines: string[];
};

const SCOPE_LABELS: Record<string, string> = {
  database: "Session database",
  finalized_captures: "Recorded captures",
  recorder_staging: "Recorder staging",
  imported_traces: "Imported traces",
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
  const [runtimeBody, receiverBody, storage] = await Promise.all([
    readJson("/api/v1/engineer/runtime"),
    readJson("/api/v2/session-evidence/status"),
    fetchStorage(),
  ]);
  const runtimeInfo = parseRuntimeInfo(runtimeBody);
  const receiverInfo = parseReceiverInfo(receiverBody);
  const session = await loadCurrentSession(receiverBody);
  return buildSettingsModel({ runtimeInfo, receiverInfo, storage, session });
}

export function buildSettingsModel({
  runtimeInfo,
  receiverInfo,
  storage,
  session = null,
}: {
  runtimeInfo: RuntimeInfo | null;
  receiverInfo: ReceiverInfo | null;
  storage: Awaited<ReturnType<typeof fetchStorage>>;
  /** Current or most recent v2 session; null keeps the reference game and footer. */
  session?: SessionContextModel | null;
}): SettingsModel {
  const engineReachable = Boolean(runtimeInfo || receiverInfo || storage);
  const receiver = describeReceiver(receiverInfo);
  const gameName = session?.facts.find(([label]) => label === "Game")?.[1];
  const game = describeGameTile(receiverInfo, gameName ? { ...settingsFixture.status.game, name: gameName } : settingsFixture.status.game);
  const status = { ...settingsFixture.status };

  // Microphone, audio output and track models have no backend source. Keep the
  // reference devices, but never claim they are ready while the engine is down.
  if (!engineReachable) {
    status.microphone = { ...status.microphone, state: "Status unavailable" };
    status.audio = { ...status.audio, state: "Status unavailable" };
    status.trackModels = { ...status.trackModels, state: "Status unavailable" };
  }

  let storageView: StorageView | null = null;
  if (storage) {
    let used = 0;
    let complete = true;
    const measured = Object.entries(storage.scopes).map(([key, scope]) => {
      const bytes = scope.logical_bytes;
      if (bytes === null) complete = false;
      else used += bytes;
      return { key, scope, bytes };
    });
    const scopes = measured.map(({ key, scope, bytes }): StorageScopeView => {
      const files = scope.regular_file_count;
      return {
        label: SCOPE_LABELS[key] ?? key,
        value: bytes === null ? describeStorageReason(scope.reason) : formatStorageBytes(bytes),
        detail: files === null ? null : `${files} ${files === 1 ? "file" : "files"}`,
        share: bytes === null ? null : used > 0 ? bytes / used : 0,
        unavailable: bytes === null,
      };
    });
    const volume = storage.volumes.recordings_location;
    const free = volume.free_bytes;
    const total = volume.total_bytes;
    storageView = {
      used: `${formatStorageBytes(used)}${complete ? "" : "+"} used`,
      available: free === null ? "Free space unknown" : `${formatStorageBytes(free)} available`,
      capacity: total === null ? null : `${formatStorageBytes(total)} volume`,
      volumeShare: total && free !== null ? (total - free) / total : null,
      scopes,
      note: storage.measurement_note,
      complete,
    };
    status.storage = { used: storageView.used, available: storageView.available };
  } else {
    status.storage = { used: "Unavailable", available: "Not measured" };
  }

  return {
    ...settingsFixture,
    footerLines: session?.footerLines ?? shellFixture.sessionFooter.lines,
    header: { items: [game.name, receiverInfo ? (game.state.startsWith("Connected") ? "Connected" : "Not connected") : "Engine unavailable"] },
    status,
    pill: runtimePill(runtimeInfo),
    engineReachable,
    runtime: describeRuntime(runtimeInfo),
    receiver,
    game,
    storage: storageView,
  };
}
