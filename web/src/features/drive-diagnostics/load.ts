import { fetchAcquisition } from "@/features/review-data/backend";
import { driveFixture, type SystemRow } from "@/features/review-data/fixtures";
import { trackPhotoUrl } from "@/features/ui-foundation/track-photo-url";

export type DriveModel = typeof driveFixture & { photo: string | null };

const telemetryRows: Record<string, Pick<SystemRow, "status" | "tone">> = {
  receiving: { status: "Receiving", tone: "green" },
  listening: { status: "Listening", tone: "amber" },
  starting: { status: "Starting", tone: "amber" },
  stale: { status: "Stale", tone: "amber" },
  stopped: { status: "Stopped", tone: "red" },
  failed: { status: "Failed", tone: "red" },
  disabled: { status: "Disabled", tone: "grey" },
};

export async function loadDrive(): Promise<DriveModel> {
  const acquisition = await fetchAcquisition();
  const system = driveFixture.system.map((row) => {
    if (row.label !== "Telemetry (Game Data)" || !acquisition) return row;
    const mapped = telemetryRows[acquisition.state];
    const delayMs = Math.round((acquisition.last_publication_delay_s ?? 0) * 1000);
    return {
      ...row,
      ...mapped,
      detail: acquisition.state === "receiving" ? `${delayMs} ms` : "UDP 20777",
    };
  });
  return {
    ...driveFixture,
    system,
    photo: trackPhotoUrl(driveFixture.context.track),
  };
}
