import type { SessionRecord } from "@/lib/api";
import { fetchAcquisition, fetchSessions, telemetryFooter, telemetryStatus } from "@/features/review-data/backend";
import { libraryFixture, shellFixture, type LibrarySession } from "@/features/review-data/fixtures";
import {
  formatDayTime,
  formatGame,
  formatSessionType,
  formatWeather,
} from "@/features/review-data/format";
import type { ShellFooter, ShellStatus } from "@/features/ui-foundation/AppShell";
import type { Tone } from "@/features/ui-foundation/primitives";
import { trackKeyFromName } from "@/features/ui-foundation/TrackMap";
import { trackPhotoUrl } from "@/features/ui-foundation/track-photo-url";

export type LibraryRow = LibrarySession & { photo: string | null };

export type LibraryModel = {
  live: boolean;
  status: ShellStatus;
  footer: ShellFooter;
  header: { items: string[]; trailing?: string };
  sessions: LibraryRow[];
};

function typeTone(sessionType: string | null | undefined): Tone {
  if (!sessionType) return "grey";
  if (sessionType.startsWith("race") || sessionType.startsWith("sprint")) return "green";
  if (sessionType.startsWith("practice")) return "amber";
  if (sessionType === "time_trial") return "purple";
  return "grey";
}

function statusOf(record: SessionRecord): { label: string; tone: Tone } {
  switch (record.run_status) {
    case "complete":
      return { label: "Complete", tone: "green" };
    case "running":
    case "queued":
    case "processing":
      return { label: "Processing", tone: "blue" };
    case "partial":
      return { label: "Partial", tone: "amber" };
    default:
      return { label: "Unavailable", tone: "red" };
  }
}

function toRow(record: SessionRecord): LibraryRow {
  const context = record.context;
  const trackName = context?.track_name ?? null;
  const track = trackKeyFromName(trackName);
  const weather = formatWeather(context?.weather_name);
  const status = statusOf(record);
  return {
    key: record.session_key,
    title: trackName ?? "Unknown circuit",
    track,
    type: { label: formatSessionType(context?.session_type), tone: typeTone(context?.session_type) },
    source: { label: "Game", tone: "blue" },
    date: formatDayTime(record.started_at_utc),
    meta: [formatGame(record.packet_format), ...(weather ? [weather] : [])],
    laps: record.lap_attempts,
    notes: "",
    status,
    href: status.label === "Complete" ? `/review/${encodeURIComponent(record.session_key)}` : null,
    photo: trackPhotoUrl(track),
  };
}

function connectionHeader(state: string): LibraryModel["header"] {
  return state === "receiving"
    ? libraryFixture.header
    : { items: ["Game not connected"], trailing: libraryFixture.header.trailing };
}

export async function loadLibrary(): Promise<LibraryModel> {
  const [sessions, acquisition] = await Promise.all([fetchSessions(), fetchAcquisition()]);
  const status = acquisition ? telemetryStatus(acquisition) : shellFixture.telemetryLive;
  const footer = acquisition
    ? telemetryFooter(acquisition, shellFixture.telemetryFooter)
    : shellFixture.telemetryFooter;
  if (!sessions || sessions.length === 0) {
    return {
      live: sessions !== null,
      status,
      footer,
      header: acquisition ? connectionHeader(acquisition.state) : libraryFixture.header,
      sessions: libraryFixture.sessions.map((session) => ({
        ...session,
        photo: trackPhotoUrl(session.track),
      })),
    };
  }
  return {
    live: true,
    status,
    footer,
    header: acquisition ? connectionHeader(acquisition.state) : { items: ["Local engine connected"] },
    sessions: sessions.map(toRow),
  };
}
