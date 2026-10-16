"use client";

import { CaptureFileIcon, CloseIcon } from "@/features/ui-foundation/icons";
import { Badge, Button, IconButton, StatusBadge } from "@/features/ui-foundation/primitives";
import { NotAvailable } from "@/features/ui-foundation/states";
import { ProgressBar } from "@/features/ui-foundation/ProgressBar";
import styles from "../SessionLibrary.module.css";
import { useImports } from "./ImportProvider";
import { badgeOf, canCancel, canRetry, formatBytes, isActive, noteOf, progressOf, type ImportItem } from "./import-state";
import local from "./imports.module.css";

/** An import in flight (or failed), in the same row grid as the sessions. */
export function ImportRow({ item }: { item: ImportItem }) {
  const imports = useImports();
  const badge = badgeOf(item);
  const progress = progressOf(item);
  const failed = item.phase.kind === "failed";
  const glyph = badge.tone === "green" ? "check" : badge.tone === "red" ? "alert" : "dot";
  return (
    <li
      className={`${styles.row} ${local.importRow}`}
      data-phase={item.phase.kind}
      data-import-row="true"
      aria-busy={isActive(item) ? true : undefined}
    >
      <div className={`${styles.thumb} ${local.captureThumb}`} aria-hidden="true">
        <CaptureFileIcon size={30} />
      </div>
      <div className={styles.info}>
        <h2 className={styles.title}>{item.fileName}</h2>
        <div className={styles.badges}>
          <Badge tone="grey">Import</Badge>
        </div>
        <div className={styles.date}>{formatBytes(item.bytes)}</div>
      </div>
      <div className={styles.laps}>
        <strong>
          <NotAvailable reason="Laps are counted once the import finishes" />
        </strong>
        <span>Laps</span>
      </div>
      <div className={`${styles.notes} ${local.noteStack}`}>
        <span className={local.noteText} data-tone={failed ? "error" : undefined}>
          {noteOf(item)}
        </span>
        {progress !== undefined && <ProgressBar value={progress} label={`${badge.label}: ${item.fileName}`} size="sm" />}
      </div>
      <div className={styles.side}>
        <StatusBadge tone={badge.tone} glyph={glyph}>
          {badge.label}
        </StatusBadge>
        <div className={local.rowActions}>
          {canCancel(item) && (
            <Button variant="muted" onClick={() => imports.cancel(item.key)} aria-label={`Cancel import of ${item.fileName}`}>
              Cancel
            </Button>
          )}
          {canRetry(item) && (
            <Button variant="outline-blue" onClick={() => imports.retry(item.key)} aria-label={`Retry import of ${item.fileName}`}>
              Retry
            </Button>
          )}
          {(failed || item.phase.kind === "cancelled") && (
            <IconButton label={`Dismiss ${item.fileName}`} onClick={() => imports.dismiss(item.key)}>
              <CloseIcon size={14} />
            </IconButton>
          )}
        </div>
      </div>
    </li>
  );
}
