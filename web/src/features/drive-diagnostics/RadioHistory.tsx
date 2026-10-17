import { FilterChips } from "@/features/ui-foundation/FilterChips";
import { MicrophoneIcon, StatusGlyph } from "@/features/ui-foundation/icons";
import { Badge, Card, Toggle } from "@/features/ui-foundation/primitives";
import { EmptyState, InlineAlert } from "@/features/ui-foundation/states";
import {
  FILTER_EMPTY,
  FILTER_ITEMS,
  KIND_LABEL,
  type RadioFilter,
  type RadioKind,
  type RadioRow,
} from "./radio";
import type { FeedStatus } from "./radio-feed";
import styles from "./DriveDiagnostics.module.css";

function KindIcon({ kind }: { kind: RadioKind }) {
  if (kind === "proactive") return <StatusGlyph tone="green" glyph="square" size={13} />;
  if (kind === "question") return <StatusGlyph tone="blue" glyph="user" size={13} />;
  if (kind === "response") return <StatusGlyph tone="dark" glyph="flag" size={13} />;
  return <StatusGlyph tone="red" glyph="alert" size={13} />;
}

export type RadioHistoryProps = {
  /** Newest first. */
  rows: RadioRow[];
  counts: Record<RadioFilter, number>;
  filter: RadioFilter;
  onFilter: (filter: RadioFilter) => void;
  selectedId: string | null;
  onSelect: (row: RadioRow) => void;
  /** Row ids that are unread. */
  unread: ReadonlySet<string>;
  /** Row ids that arrived with the last poll (brief highlight). */
  fresh: ReadonlySet<string>;
  onMarkAllRead: () => void;
  /** Feed state; only meaningful on the live path. */
  feed: { status: FeedStatus; reference: boolean };
  readAloud: {
    enabled: boolean;
    onChange: (enabled: boolean) => void;
    /** Why calls cannot be spoken even though the setting is on. */
    note: string | null;
  };
};

export function RadioHistory({
  rows,
  counts,
  filter,
  onFilter,
  selectedId,
  onSelect,
  unread,
  fresh,
  onMarkAllRead,
  feed,
  readAloud,
}: RadioHistoryProps) {
  const visible = filter === "all" ? rows : rows.filter((row) => filter === "radio" ? row.source === "radio" : row.kind === filter);
  const empty = rows.length === 0;
  const items = FILTER_ITEMS.map((item) => ({ ...item, count: counts[item.key] }));

  return (
    <Card
      title={
        <>
          Radio History
          {unread.size > 0 && (
            <span className={styles.newBadge}>
              <Badge tone="blue">{unread.size} new</Badge>
            </span>
          )}
        </>
      }
      aside={
        feed.reference ? undefined : (
          <div className={styles.radioTools}>
            {unread.size > 0 && (
              <button type="button" className={styles.textButton} onClick={onMarkAllRead}>
                Mark all read
              </button>
            )}
            <span className={styles.toggleField}>
              <span className={styles.toggleText} aria-hidden="true">
                Read calls aloud
              </span>
              <Toggle label="Read calls aloud" checked={readAloud.enabled} onChange={readAloud.onChange} />
            </span>
          </div>
        )
      }
      labelledBy="radio-history"
      className={styles.radioCard}
    >
      {readAloud.enabled && readAloud.note && <p className={styles.radioNote}>{readAloud.note}</p>}
      {!feed.reference && feed.status !== "ok" && !empty && (
        <div className={styles.radioAlert}>
          <InlineAlert tone="warning">Radio history is not updating. Showing the last events received; retrying.</InlineAlert>
        </div>
      )}
      {empty ? (
        <div className={styles.radioEmpty}>
          {feed.status === "ok" ? (
            <EmptyState
              icon={<MicrophoneIcon size={20} />}
              title="No radio yet"
              body="Engineer calls and your questions appear here as they happen."
            />
          ) : (
            <EmptyState
              tone="warning"
              title="Radio history is unavailable"
              body="The engineer activity log could not be read."
            />
          )}
        </div>
      ) : (
        <>
          <div className={styles.filters}>
            <FilterChips items={items} value={filter} onChange={onFilter} label="Filter radio history" />
          </div>
          {visible.length === 0 && filter !== "all" ? (
            <p className={styles.filterEmpty}>{FILTER_EMPTY[filter]}</p>
          ) : (
            <ul className={styles.radioList} aria-label="Radio events">
              {visible.map((row) => (
                <li key={row.id}>
                  <button
                    type="button"
                    className={styles.radioRow}
                    aria-pressed={selectedId === row.id}
                    data-unread={unread.has(row.id) ? "true" : undefined}
                    data-fresh={fresh.has(row.id) ? "true" : undefined}
                    onClick={() => onSelect(row)}
                  >
                    <span className={styles.radioTime}>
                      {unread.has(row.id) && <span className="sr-only">Unread. </span>}
                      {row.time}
                    </span>
                    <span className={styles.radioKind} data-kind={row.kind}>
                      <KindIcon kind={row.kind} />
                      {row.source === "radio" ? "Radio" : KIND_LABEL[row.kind]}
                    </span>
                    <span className={styles.radioText}>{row.text}</span>
                    <span className={styles.radioOutcome}>{row.outcome}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Card>
  );
}
