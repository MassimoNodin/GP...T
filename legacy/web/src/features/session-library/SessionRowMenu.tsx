"use client";

import Link from "next/link";
import { MoreIcon } from "@/features/ui-foundation/icons";
import { Menu, MenuItem } from "@/features/ui-foundation/primitives";
import { useToast } from "@/features/ui-foundation/Toast";
import type { LibraryRow } from "./load";
import local from "./imports/imports.module.css";
import styles from "./SessionLibrary.module.css";

/** `/api/recording-sources/{id}/download?version=…` for a capture the catalog knows. */
export function captureDownloadHref(download: { captureId: string; version: string }): string {
  return `/api/recording-sources/${encodeURIComponent(download.captureId)}/download?version=${encodeURIComponent(download.version)}`;
}

/** `/review/{id}/laps?target=&reference=` for the two fastest valid laps. */
export function compareHref(base: string, pair: { target: string; reference: string }): string {
  const query = new URLSearchParams({ target: pair.target, reference: pair.reference });
  return `${base}/laps?${query}`;
}

/**
 * Per-row actions. Only what the backend supports: open, compare, copy the
 * session id, download the capture. There is no delete, rename or notes.
 */
export function SessionRowMenu({ row }: { row: LibraryRow }) {
  const toast = useToast();

  async function copyId() {
    try {
      await navigator.clipboard.writeText(row.key);
      toast.push({ tone: "success", title: "Session ID copied" });
    } catch {
      toast.push({ tone: "error", title: "Could not copy the session ID" });
    }
  }

  return (
    <Menu
      label={`More actions for ${row.title}`}
      trigger={<MoreIcon size={16} />}
      className={styles.rowMenu}
      triggerClassName={styles.more}
      panelClassName={styles.rowMenuPanel}
    >
      {row.href ? (
        <>
          <Link href={row.href}>Open overview</Link>
          <Link href={`${row.href}/laps`}>Open lap analysis</Link>
          {row.compare && <Link href={compareHref(row.href, row.compare)}>Compare best laps</Link>}
        </>
      ) : (
        <span className={styles.menuNote}>
          {row.status.label === "Unavailable" ? "Session data is unavailable" : "Available once processing finishes"}
        </span>
      )}
      {row.kind !== "reference" && (
        <>
          <hr className={local.menuSeparator} />
          <MenuItem onSelect={() => void copyId()}>Copy session ID</MenuItem>
          {row.download && (
            <a href={captureDownloadHref(row.download)} download>
              Download capture
            </a>
          )}
        </>
      )}
    </Menu>
  );
}
