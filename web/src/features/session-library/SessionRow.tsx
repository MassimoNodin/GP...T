import Link from "next/link";
import { Badge, StatusBadge } from "@/features/ui-foundation/primitives";
import { NotAvailable } from "@/features/ui-foundation/states";
import { TrackPhoto } from "@/features/ui-foundation/TrackPhoto";
import { UnitText } from "@/features/ui-foundation/units";
import type { LibraryRow } from "./load";
import { SessionRowMenu } from "./SessionRowMenu";
import styles from "./SessionLibrary.module.css";

export function SessionRow({ row, nested = false }: { row: LibraryRow; nested?: boolean }) {
  const title = row.href ? (
    <Link href={row.href} className={styles.titleLink}>
      {row.title}
    </Link>
  ) : (
    row.title
  );
  return (
    <li className={styles.row} data-openable={row.href ? "true" : undefined} data-nested={nested || undefined}>
      <TrackPhoto src={row.photo} alt={`${row.title} circuit`} className={styles.thumb} />
      <div className={styles.info}>
        <h2 className={styles.title}>{title}</h2>
        <div className={styles.badges}>
          <Badge tone={row.type.tone}>{row.type.label}</Badge>
          <Badge tone={row.source.tone}>{row.source.label}</Badge>
        </div>
        <div className={row.dateKnown ? styles.date : `${styles.date} ${styles.dateUnknown}`}>{row.date}</div>
        <div className={styles.meta}>
          {row.meta.map((part, index) => (
            <span key={`${part}-${index}`}>
              {index > 0 && <span className={styles.dot}>•</span>}
              <UnitText>{part}</UnitText>
            </span>
          ))}
        </div>
      </div>
      <div className={styles.laps}>
        <strong>{row.detailed ? row.laps : <NotAvailable reason="Lap counts are read when the session is opened" compact />}</strong>
        <span>Laps</span>
      </div>
      <p className={styles.notes}>
        {row.highlights.length > 0
          ? row.highlights.map((line) => (
              <span key={line} className={styles.noteLine}>
                {line}
              </span>
            ))
          : row.notes}
      </p>
      <div className={styles.side}>
        <StatusBadge tone={row.status.tone}>{row.status.label}</StatusBadge>
        <SessionRowMenu row={row} />
      </div>
    </li>
  );
}
