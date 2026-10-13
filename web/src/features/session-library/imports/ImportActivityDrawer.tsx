"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";
import { formatDayTime } from "@/features/review-data/format";
import { CloseIcon } from "@/features/ui-foundation/icons";
import { Drawer } from "@/features/ui-foundation/Drawer";
import { Button, IconButton, StatusBadge } from "@/features/ui-foundation/primitives";
import { ProgressBar } from "@/features/ui-foundation/ProgressBar";
import { EmptyState } from "@/features/ui-foundation/states";
import { useImports } from "./ImportProvider";
import {
  badgeOf,
  canCancel,
  canRetry,
  formatBytes,
  noteOf,
  progressOf,
  type FolderSource,
  type ImportItem,
} from "./import-state";
import local from "./imports.module.css";

function clock(at: number): string {
  return new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function RecentEntry({ item }: { item: ImportItem }) {
  const imports = useImports();
  const badge = badgeOf(item);
  const progress = progressOf(item);
  const failed = item.phase.kind === "failed";
  const finished = item.phase.kind === "complete" || failed || item.phase.kind === "cancelled";
  const glyph = badge.tone === "green" ? "check" : badge.tone === "red" ? "alert" : "dot";
  return (
    <li className={local.entry}>
      <span className={local.entryName} title={item.fileName}>
        {item.fileName}
      </span>
      <span className={local.entryAside}>
        <StatusBadge tone={badge.tone} glyph={glyph}>
          {badge.label}
        </StatusBadge>
      </span>
      <span className={local.entryMeta} data-tone={failed ? "error" : undefined}>
        {formatBytes(item.bytes)} &middot; {clock(item.updatedAt)} &middot; {noteOf(item)}
      </span>
      {progress !== undefined && (
        <span className={local.entryProgress}>
          <ProgressBar value={progress} label={`${badge.label}: ${item.fileName}`} size="sm" />
        </span>
      )}
      <span className={local.rowActions}>
        {item.phase.kind === "complete" && item.phase.sessionHref && (
          <Link href={item.phase.sessionHref} className={local.entryLink}>
            Open session
          </Link>
        )}
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
        {finished && (
          <IconButton label={`Dismiss ${item.fileName}`} onClick={() => imports.dismiss(item.key)}>
            <CloseIcon size={14} />
          </IconButton>
        )}
      </span>
    </li>
  );
}

function FolderEntry({ source }: { source: FolderSource }) {
  const imports = useImports();
  return (
    <li className={local.entry}>
      <span className={local.entryName} title={source.name}>
        {source.name}
      </span>
      <span className={local.entryAside}>
        <Button variant="outline-blue" onClick={() => imports.importFromFolder(source)} aria-label={`Import ${source.name}`}>
          Import
        </Button>
      </span>
      <span className={local.entryMeta}>
        {formatBytes(source.bytes)} &middot; {formatDayTime(source.modifiedAtUtc)}
      </span>
    </li>
  );
}

/** Right-hand panel: this tab's imports and the captures waiting in the recordings folder. */
export function ImportActivityDrawer() {
  const imports = useImports();
  const open = imports.drawer !== null;
  const folderSection = useRef<HTMLElement>(null);
  const focusFolder = imports.drawer === "folder";

  useEffect(() => {
    if (open && focusFolder) folderSection.current?.scrollIntoView({ block: "start" });
  }, [open, focusFolder]);

  // Captures already handed to the importer in this tab are no longer "waiting".
  const handled = new Set(imports.items.map((item) => item.captureId).filter((id): id is string => id !== null));
  const waiting = imports.folder?.filter((source) => !handled.has(source.captureId)) ?? null;

  return (
    <Drawer open={open} onClose={imports.closeDrawer} title="Import activity">
      <section className={local.section} aria-labelledby="import-recent-title">
        <h3 className={local.sectionTitle} id="import-recent-title">
          Recent imports
        </h3>
        {imports.items.length === 0 ? (
          <EmptyState size="inline" title="No imports yet in this session." />
        ) : (
          <ul className={local.list} aria-label="Recent imports">
            {imports.items.map((item) => (
              <RecentEntry key={item.key} item={item} />
            ))}
          </ul>
        )}
      </section>
      <section ref={folderSection} className={local.section} aria-labelledby="import-folder-title">
        <h3 className={local.sectionTitle} id="import-folder-title">
          Recordings folder
        </h3>
        {waiting === null ? (
          <EmptyState size="inline" tone="warning" title="The recordings folder could not be read." />
        ) : waiting.length === 0 ? (
          <EmptyState size="inline" title="No captures waiting in the recordings folder." />
        ) : (
          <>
            <ul className={local.list} aria-label="Captures waiting in the recordings folder">
              {waiting.map((source) => (
                <FolderEntry key={source.captureId} source={source} />
              ))}
            </ul>
            {imports.folderTotal > imports.folder!.length && (
              <p className={local.sectionHint}>
                Showing {imports.folder!.length} of {imports.folderTotal} captures.
              </p>
            )}
          </>
        )}
      </section>
    </Drawer>
  );
}
