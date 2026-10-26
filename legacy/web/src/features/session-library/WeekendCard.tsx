"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { formatLapTime } from "@/features/review-data/format";
import { useLocalTime } from "@/features/review-data/use-local-time";
import { ChevronDownIcon } from "@/features/ui-foundation/icons";
import { Badge } from "@/features/ui-foundation/primitives";
import { TrackPhoto } from "@/features/ui-foundation/TrackPhoto";
import { SessionRow } from "./SessionRow";
import { shortSessionLabel, weekendBestLine, weekendDateLabel, type Weekend } from "./weekends";
import styles from "./SessionLibrary.module.css";

/** One race weekend: a summary row with a chip per session, expanding to the sessions themselves. */
export function WeekendCard({ weekend }: { weekend: Weekend }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const best = weekendBestLine(weekend);
  const local = useLocalTime();
  return (
    <li className={styles.weekend} data-open={open || undefined}>
      <div className={styles.weekendHead}>
        <TrackPhoto src={weekend.photo} alt={`${weekend.title} circuit`} className={styles.thumb} />
        <div className={styles.info}>
          <h2 className={styles.title}>{weekend.title}</h2>
          <div className={styles.badges}>
            <Badge tone="grey">Weekend</Badge>
            <Badge tone="blue">{weekend.rows.length} sessions</Badge>
          </div>
          <div className={styles.date}>{weekendDateLabel(weekend, local)}</div>
          <ul className={styles.sessionChips} aria-label={`${weekend.title} sessions`}>
            {weekend.rows.map((row) => {
              const label = shortSessionLabel(row.type.label);
              const time = typeof row.bestMs === "number" ? formatLapTime(row.bestMs) : null;
              const content = (
                <>
                  <strong>{label}</strong>
                  <span>{time ?? "No laps"}</span>
                </>
              );
              return (
                <li key={row.key}>
                  {row.href ? (
                    <Link
                      href={row.href}
                      className={styles.sessionChip}
                      data-best={weekend.best?.row.key === row.key || undefined}
                      aria-label={`${row.type.label}${time ? `, best ${time}` : ", no timed laps"}`}
                    >
                      {content}
                    </Link>
                  ) : (
                    <span className={styles.sessionChip}>{content}</span>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
        <div className={styles.laps}>
          <strong>{weekend.laps}</strong>
          <span>Laps</span>
        </div>
        <p className={styles.notes}>
          {best && <span className={styles.noteLine}>{best}</span>}
          <span className={styles.noteLine}>{weekend.game}</span>
        </p>
        <div className={styles.side}>
          <button
            type="button"
            className={styles.expand}
            aria-expanded={open}
            aria-controls={panelId}
            onClick={() => setOpen((value) => !value)}
          >
            {open ? "Hide sessions" : "Show sessions"}
            <ChevronDownIcon size={14} className={styles.expandIcon} />
          </button>
        </div>
      </div>
      <ul id={panelId} className={styles.weekendSessions} hidden={!open} aria-label={`${weekend.title} weekend sessions`}>
        {weekend.rows.map((row) => (
          <SessionRow key={row.key} row={row} nested />
        ))}
      </ul>
    </li>
  );
}
