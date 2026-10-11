import { formatStorageBytes } from "@/lib/storage-usage.mjs";
import { fetchAcquisition, fetchStorage } from "@/features/review-data/backend";
import { settingsFixture, shellFixture } from "@/features/review-data/fixtures";
import type { ShellStatus } from "@/features/ui-foundation/AppShell";

export type SettingsModel = typeof settingsFixture & {
  pill: ShellStatus;
  telemetry: { state: string; tone: "green" | "amber" | "red" | "grey"; detail: string } | null;
  storageScopes: Array<{ label: string; value: string }> | null;
};

const SCOPE_LABELS: Record<string, string> = {
  database: "Session database",
  finalized_captures: "Recorded captures",
  recorder_staging: "Recorder staging",
  imported_traces: "Imported traces",
};

export async function loadSettings(): Promise<SettingsModel> {
  const [acquisition, storage] = await Promise.all([fetchAcquisition(), fetchStorage()]);
  const status = { ...settingsFixture.status };
  let telemetry: SettingsModel["telemetry"] = null;
  let header = settingsFixture.header;

  if (acquisition) {
    const receiving = acquisition.state === "receiving";
    const delay = Math.round((acquisition.last_publication_delay_s ?? 0) * 1000);
    telemetry = {
      state: receiving ? "Receiving" : acquisition.state[0].toUpperCase() + acquisition.state.slice(1),
      tone: receiving ? "green" : acquisition.state === "failed" || acquisition.state === "stopped" ? "red" : "amber",
      detail: receiving ? `${delay} ms publication delay` : "Listening on UDP port 20777",
    };
    status.game = {
      name: status.game.name,
      state: receiving ? `Connected (${delay} ms)` : "Not connected",
    };
    header = { items: [settingsFixture.header.items[0], receiving ? "Connected" : "Not connected"] };
  }

  let storageScopes: SettingsModel["storageScopes"] = null;
  if (storage) {
    let used = 0;
    storageScopes = Object.entries(storage.scopes).map(([key, scope]) => {
      if (scope.logical_bytes) used += scope.logical_bytes;
      return {
        label: SCOPE_LABELS[key] ?? key,
        value: scope.logical_bytes === null ? "Unavailable" : formatStorageBytes(scope.logical_bytes),
      };
    });
    const free = storage.volumes.recordings_location.free_bytes;
    status.storage = {
      used: `${formatStorageBytes(used)} used`,
      available: free === null ? "Free space unknown" : `${formatStorageBytes(free)} available`,
    };
  }

  return {
    ...settingsFixture,
    header,
    status,
    pill: shellFixture.engineerRunning,
    telemetry,
    storageScopes,
  };
}
