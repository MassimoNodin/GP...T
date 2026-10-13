"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { ReviewIcon, SearchIcon, SortIcon } from "@/features/ui-foundation/icons";
import { Dropzone } from "@/features/ui-foundation/Dropzone";
import { Badge, Button, IconButton, PageHeader, Select, StatusBadge } from "@/features/ui-foundation/primitives";
import { EmptyState, InlineAlert, NotAvailable } from "@/features/ui-foundation/states";
import { TrackPhoto } from "@/features/ui-foundation/TrackPhoto";
import { ImportActivityDrawer } from "./imports/ImportActivityDrawer";
import { ImportProvider, useImports } from "./imports/ImportProvider";
import { ImportRow } from "./imports/ImportRow";
import { listItems } from "./imports/import-state";
import { ImportSessionButton } from "./ImportSessionButton";
import { NO_FILTERS, PAGE_SIZE, filterRows, filtersActive, paginate, type RowFilters } from "./library-rows";
import type { LibraryModel, LibraryRow } from "./load";
import { SessionRowMenu } from "./SessionRowMenu";
import styles from "./SessionLibrary.module.css";

export const REFERENCE_BANNER =
  "The local engine is not reachable, so example sessions from the reference design are shown. Start the engine to see your own data.";
export const EMPTY_TITLE = "No sessions yet";
export const EMPTY_BODY = "Drive with telemetry enabled, or import a .f1ecap capture.";

function unique(values: string[]) {
  return [...new Set(values)].sort((a, b) => a.localeCompare(b));
}

export function SessionLibrary({ model }: { model: LibraryModel }) {
  return (
    <ImportProvider
      enabled={model.live}
      restored={model.imports.restored}
      folder={model.imports.folder}
      folderTotal={model.imports.folderTotal}
    >
      <LibraryBody model={model} />
      <ImportActivityDrawer />
    </ImportProvider>
  );
}

function isTypingTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

function LibraryBody({ model }: { model: LibraryModel }) {
  const imports = useImports();
  const [filters, setFilters] = useState<RowFilters>(NO_FILTERS);
  const [shown, setShown] = useState(PAGE_SIZE);
  const searchRef = useRef<HTMLInputElement>(null);
  const countRef = useRef<HTMLParagraphElement>(null);

  function change(patch: Partial<RowFilters>) {
    setFilters((current) => ({ ...current, ...patch }));
    setShown(PAGE_SIZE);
  }

  // "/" focuses the search box unless the user is typing somewhere.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) return;
      if (isTypingTarget(event.target) || !searchRef.current) return;
      event.preventDefault();
      searchRef.current.focus();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  const options = useMemo(
    () => ({
      tracks: unique(model.sessions.map((row) => row.title)),
      types: unique(model.sessions.map((row) => row.type.label)),
      sources: unique(model.sessions.map((row) => row.source.label)),
      statuses: unique(model.sessions.map((row) => row.status.label.replace(/\s+\d+%$/, ""))),
    }),
    [model.sessions],
  );

  const rows = useMemo(() => filterRows(model.sessions, filters), [model.sessions, filters]);
  const page = paginate(rows, shown);
  const inFlight = listItems(imports.items);
  const hasSessions = model.sessions.length > 0;

  function showMore() {
    setShown((current) => current + PAGE_SIZE);
    if (page.remaining <= PAGE_SIZE) requestAnimationFrame(() => countRef.current?.focus());
  }

  return (
    <>
      {model.mode === "reference" && (
        <div className={styles.banners}>
          <InlineAlert tone="info" title="Reference data">
            {REFERENCE_BANNER}
          </InlineAlert>
        </div>
      )}
      {model.warnings.length > 0 && (
        <div className={styles.banners}>
          {model.warnings.map((warning) => (
            <InlineAlert key={warning} tone="warning">
              {warning}
            </InlineAlert>
          ))}
        </div>
      )}
      <PageHeader
        title="Session Library"
        subtitle="Browse, search and manage your recorded sessions."
        actions={<ImportSessionButton />}
      />
      <Dropzone
        accept={[".f1ecap"]}
        maxFiles={5}
        onFiles={imports.startFiles}
        onReject={imports.reject}
        overlayLabel="Drop .f1ecap captures to import"
        disabled={!model.live}
      >
        {hasSessions && (
          <>
            <label className={styles.search}>
              <SearchIcon size={15} className={styles.searchIcon} />
              <span className="sr-only">Search sessions</span>
              <input
                ref={searchRef}
                type="search"
                placeholder="Search sessions..."
                value={filters.query}
                onChange={(event) => change({ query: event.target.value })}
              />
            </label>
            <div className={styles.filters}>
              <Select aria-label="Track" value={filters.track} onChange={(event) => change({ track: event.target.value })}>
                <option value="">All Tracks</option>
                {options.tracks.map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </Select>
              <Select aria-label="Session type" value={filters.type} onChange={(event) => change({ type: event.target.value })}>
                <option value="">All Types</option>
                {options.types.map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </Select>
              <Select aria-label="Source" value={filters.source} onChange={(event) => change({ source: event.target.value })}>
                <option value="">All Sources</option>
                {options.sources.map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </Select>
              <Select aria-label="Status" value={filters.status} onChange={(event) => change({ status: event.target.value })}>
                <option value="">All Status</option>
                {options.statuses.map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </Select>
              <IconButton
                label={filters.oldestFirst ? "Sort newest first" : "Sort oldest first"}
                className={styles.sort}
                aria-pressed={filters.oldestFirst}
                onClick={() => change({ oldestFirst: !filters.oldestFirst })}
              >
                <SortIcon size={16} />
              </IconButton>
            </div>
          </>
        )}

        {!hasSessions && inFlight.length === 0 ? (
          model.sessionsUnreadable ? (
            <EmptyState
              size="page"
              tone="warning"
              title="Sessions could not be read"
              body="The engine is running but did not return its session list. Try again in a moment."
              action={
                <Button onClick={imports.refreshLibrary}>Refresh library</Button>
              }
            />
          ) : (
            <EmptyState
              size="page"
              icon={<ReviewIcon size={22} />}
              title={EMPTY_TITLE}
              body={EMPTY_BODY}
              action={<ImportSessionButton />}
            />
          )
        ) : (
          <>
            {(inFlight.length > 0 || rows.length > 0) && (
              <ul className={styles.list} aria-label="Sessions">
                {inFlight.map((item) => (
                  <ImportRow key={item.key} item={item} />
                ))}
                {page.visible.map((row) => (
                  <SessionRow key={row.key} row={row} />
                ))}
              </ul>
            )}
            {rows.length === 0 && hasSessions && (
              <EmptyState
                size="card"
                title="No sessions match these filters."
                action={
                  filtersActive(filters) ? (
                    <Button variant="muted" onClick={() => change({ ...NO_FILTERS, oldestFirst: filters.oldestFirst })}>
                      Clear filters
                    </Button>
                  ) : undefined
                }
              />
            )}
            {rows.length > PAGE_SIZE && (
              <div className={styles.pager}>
                {page.remaining > 0 && <Button onClick={showMore}>{page.more}</Button>}
                <p ref={countRef} className={styles.count} tabIndex={-1} aria-live="polite">
                  {page.countLine}
                </p>
              </div>
            )}
          </>
        )}
      </Dropzone>
    </>
  );
}

function SessionRow({ row }: { row: LibraryRow }) {
  const title = row.href ? (
    <Link href={row.href} className={styles.titleLink}>
      {row.title}
    </Link>
  ) : (
    row.title
  );
  return (
    <li className={styles.row} data-openable={row.href ? "true" : undefined}>
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
              {part}
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
