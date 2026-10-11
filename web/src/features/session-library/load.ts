import type { SessionRecord } from "@/lib/api";
import {
  fetchAcquisition,
  fetchEvidenceAttempts,
  fetchEvidenceSessions,
  fetchSessions,
  telemetryFooter,
  telemetryStatus,
} from "@/features/review-data/backend";
import {
  evidenceLaps,
  evidenceLibraryFields,
  evidenceSessionContext,
  sessionRecency,
  typeTone,
  type EvidenceSession,
} from "@/features/review-data/session-evidence";
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

/** v2 sessions the engine acquired from the game, newest journal position first. */
async function evidenceRows(sessions: EvidenceSession[]): Promise<LibraryRow[]> {
  const results = await Promise.all(sessions.map((session) => fetchEvidenceAttempts(session.id)));
  return sessions
    .map((session, index) => {
      const attempts = results[index]?.attempts ?? [];
      const context = evidenceSessionContext(session, attempts);
      const track = trackKeyFromName(context?.trackName);
      const fields = evidenceLibraryFields(session, context, evidenceLaps(attempts).length);
      return { recency: sessionRecency(attempts), row: { ...fields, track, photo: trackPhotoUrl(track) } };
    })
    .sort((a, b) => b.recency - a.recency)
    .map((entry) => entry.row);
}

function connectionHeader(state: string): LibraryModel["header"] {
  return state === "receiving"
    ? libraryFixture.header
    : { items: ["Game not connected"], trailing: libraryFixture.header.trailing };
}

export async function loadLibrary(): Promise<LibraryModel> {
  const [sessions, evidence, acquisition] = await Promise.all([
    fetchSessions(),
    fetchEvidenceSessions(),
    fetchAcquisition(),
  ]);
  const status = acquisition ? telemetryStatus(acquisition) : shellFixture.telemetryLive;
  const footer = acquisition
    ? telemetryFooter(acquisition, shellFixture.telemetryFooter)
    : shellFixture.telemetryFooter;
  const imported = sessions ?? [];
  const acquired = evidence ?? [];
  if (imported.length === 0 && acquired.length === 0) {
    return {
      live: sessions !== null || evidence !== null,
      status,
      footer,
      header: acquisition ? connectionHeader(acquisition.state) : libraryFixture.header,
      sessions: libraryFixture.sessions.map((session) => ({
        ...session,
        photo: trackPhotoUrl(session.track),
      })),
    };
  }
  // The v2 API records no wall-clock time, so acquired sessions (ordered by
  // journal position) come first and dated imports follow, newest first.
  const rows = [...(await evidenceRows(acquired)), ...imported.map(toRow)];
  // The footer names the game of the newest session instead of the reference platform.
  const game = rows[0]?.meta[0] ?? "F1";
  return {
    live: true,
    status,
    footer: { ...footer, lines: [game, footer.lines[1] ?? ""] },
    header: acquisition ? connectionHeader(acquisition.state) : { items: ["Local engine connected"] },
    sessions: rows,
  };
}
