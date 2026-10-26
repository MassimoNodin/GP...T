import Link from "next/link";
import { formatDelta, formatLapTime } from "@/features/review-data/format";
import { Badge, Card, DeltaPill, PageHeader } from "@/features/ui-foundation/primitives";
import { EmptyState } from "@/features/ui-foundation/states";
import { TrackMap } from "@/features/ui-foundation/TrackMap";
import { TrendChart } from "./TrendChart";
import type { LibraryRow } from "./library-rows";
import { personalBests, runningBest, trendPoints, type TrackBests } from "./personal-bests";
import styles from "./PersonalBests.module.css";

/** Fastest laps per circuit: overall, per kind of session, and how they moved session to session. */
export function PersonalBests({ sessions, reference }: { sessions: LibraryRow[]; reference: boolean }) {
  const tracks = reference ? [] : personalBests(sessions);
  return (
    <>
      <PageHeader
        title="Personal Bests"
        subtitle="Your fastest laps at each circuit."
        actions={
          <Link href="/review" className={styles.back}>
            Session Library
          </Link>
        }
      />
      {tracks.length === 0 ? (
        <EmptyState
          size="page"
          title={reference ? "Personal bests need the local engine" : "No timed laps yet"}
          body={
            reference
              ? "Start the local engine to see your own laps."
              : "Personal bests appear once a session has a valid timed lap."
          }
        />
      ) : (
        <ul className={styles.list} aria-label="Circuits">
          {tracks.map((track) => (
            <TrackCard key={track.key} track={track} />
          ))}
        </ul>
      )}
    </>
  );
}

function TrackCard({ track }: { track: TrackBests }) {
  const { best } = track;
  return (
    <li>
      <Card className={styles.card}>
        <div className={styles.head}>
          <TrackMap track={track.track} className={styles.map} strokeWidth={2.2} title={`${track.title} circuit`} />
          <div className={styles.identity}>
            <h2 className={styles.title}>{track.title}</h2>
            <div className={styles.meta}>
              {track.game} · {track.sessions} {track.sessions === 1 ? "session" : "sessions"} with timed laps
            </div>
          </div>
          <div className={styles.bestBlock}>
            <div className={styles.bestLabel}>Personal best</div>
            <div className={styles.bestTime}>{formatLapTime(best.ms)}</div>
            <div className={styles.bestSub}>
              <Badge tone={best.row.type.tone}>{best.row.type.label}</Badge>
              {best.href && (
                <Link href={best.href} className={styles.link}>
                  Analyse lap
                </Link>
              )}
            </div>
          </div>
        </div>
        <div className={styles.body}>
          <table className={styles.table}>
            <caption className="sr-only">{track.title} best lap by session type</caption>
            <thead>
              <tr>
                <th scope="col">Session type</th>
                <th scope="col">Best</th>
                <th scope="col">To personal best</th>
                <th scope="col">Sessions</th>
                <th scope="col">
                  <span className="sr-only">Open</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {track.byFamily.map(({ family, best: familyBest, sessions }) => (
                <tr key={family}>
                  <th scope="row">{family}</th>
                  <td className="tabular">{formatLapTime(familyBest.ms)}</td>
                  <td>
                    {familyBest.ms === best.ms ? (
                      <span className={styles.pb}>Personal best</span>
                    ) : (
                      <DeltaPill ms={familyBest.ms - best.ms}>{formatDelta(familyBest.ms - best.ms)}</DeltaPill>
                    )}
                  </td>
                  <td className="tabular">{sessions}</td>
                  <td className={styles.open}>
                    {familyBest.href && (
                      <Link href={familyBest.href} className={styles.link} aria-label={`Analyse best ${family.toLowerCase()} lap`}>
                        Analyse
                      </Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Trend track={track} />
        </div>
      </Card>
    </li>
  );
}


/**
 * Personal best progression, oldest session on the left; lower is faster.
 * The line is the best so far; dots are each session's own best, so mixed
 * practice, qualifying and race paces do not read as a trend.
 */
function Trend({ track }: { track: TrackBests }) {
  const times = track.trend.map((ref) => ref.ms);
  if (times.length < 2) {
    return <p className={styles.trendEmpty}>Progress appears after a second session with timed laps.</p>;
  }
  const running = runningBest(track.trend);
  const line = trendPoints(running, times);
  const dots = trendPoints(times).map(([x, y], index) => ({
    key: track.trend[index].row.key,
    title: `${track.trend[index].row.type.label}: ${formatLapTime(track.trend[index].ms)}`,
    x,
    y,
    best: track.trend[index].ms === track.best.ms,
  }));
  const first = track.trend[0];
  const change = running[running.length - 1] - first.ms;
  return (
    <figure className={styles.trend}>
      <figcaption className={styles.trendLabel}>
        Personal best since the first session
        <DeltaPill ms={change}>{formatDelta(change)}</DeltaPill>
      </figcaption>
      <TrendChart
        label={`Personal best over ${track.trend.length} sessions, from ${formatLapTime(first.ms)} to ${formatLapTime(track.best.ms)}.`}
        line={line}
        dots={dots}
      />
    </figure>
  );
}
