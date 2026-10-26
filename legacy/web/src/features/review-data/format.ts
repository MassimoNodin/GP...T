/** 73420 -> "1:13.420" */
export function formatLapTime(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  const minutes = Math.floor(ms / 60000);
  const seconds = (ms - minutes * 60000) / 1000;
  return `${minutes}:${seconds.toFixed(3).padStart(6, "0")}`;
}

/** 18420 -> "18.420"; values over a minute fall back to lap-time format. */
export function formatSectorTime(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  return ms >= 60000 ? formatLapTime(ms) : (ms / 1000).toFixed(3);
}

/** Signed seconds with three decimals: 578 -> "+0.578", -210 -> "-0.210". */
export function formatDelta(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  const sign = ms > 0 ? "+" : ms < 0 ? "-" : "±";
  return `${sign}${(Math.abs(ms) / 1000).toFixed(3)}`;
}

/** 6138000 -> "1:42:18" */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) return "—";
  const total = Math.round(ms / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const mm = String(minutes).padStart(hours ? 2 : 1, "0");
  const ss = String(seconds).padStart(2, "0");
  return hours ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * ISO timestamp -> "4 Jun 2024". UTC by default so server and first client
 * render agree; pass `local` (after hydration, see `useLocalTime`) for the
 * viewer's time zone.
 */
export function formatDay(iso: string | null | undefined, local = false): string {
  const date = iso ? new Date(iso) : null;
  if (!date || Number.isNaN(date.getTime())) return "—";
  return local
    ? `${date.getDate()} ${MONTHS[date.getMonth()]} ${date.getFullYear()}`
    : `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}`;
}

/** ISO timestamp -> "4 Jun 2024, 15:00" (UTC unless `local`). */
export function formatDayTime(iso: string | null | undefined, local = false): string {
  const date = iso ? new Date(iso) : null;
  if (!date || Number.isNaN(date.getTime())) return "—";
  const hh = String(local ? date.getHours() : date.getUTCHours()).padStart(2, "0");
  const mm = String(local ? date.getMinutes() : date.getUTCMinutes()).padStart(2, "0");
  return `${formatDay(iso, local)}, ${hh}:${mm}`;
}

export function formatGame(packetFormat: number | null | undefined): string {
  if (!packetFormat || packetFormat < 2000) return "F1";
  return `F1 ${String(packetFormat).slice(2)}`;
}

/** "qualifying_3" -> "Qualifying 3", "time_trial" -> "Time Trial" */
export function formatSessionType(value: string | null | undefined): string {
  if (!value) return "Session";
  const words = value.split("_").filter(Boolean);
  return words.map((word) => word[0].toUpperCase() + word.slice(1)).join(" ");
}

export function formatWeather(value: string | null | undefined): string | null {
  if (!value) return null;
  return value
    .split("_")
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(" ");
}

/** Lap time axis label: 68000 -> "1:08" */
export function formatAxisLapTime(ms: number): string {
  const minutes = Math.floor(ms / 60000);
  const seconds = Math.round((ms - minutes * 60000) / 1000);
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}
