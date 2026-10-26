"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { ReviewIcon, SearchIcon, SortIcon } from "@/features/ui-foundation/icons";
import { Dropzone } from "@/features/ui-foundation/Dropzone";
import { Button, Checkbox, IconButton, PageHeader, Select } from "@/features/ui-foundation/primitives";
import { EmptyState, InlineAlert } from "@/features/ui-foundation/states";
import { ImportActivityDrawer } from "./imports/ImportActivityDrawer";
import { ImportProvider, useImports } from "./imports/ImportProvider";
import { ImportRow } from "./imports/ImportRow";
import { listItems } from "./imports/import-state";
import { ImportSessionButton } from "./ImportSessionButton";
import {
  NO_FILTERS,
  PAGE_SIZE,
  filterRows,
  filtersActive,
  isEmptySession,
  paginate,
  type RowFilters,
} from "./library-rows";
import type { LibraryModel } from "./load";
import { SessionRow } from "./SessionRow";
import { WeekendCard } from "./WeekendCard";
import { groupWeekends } from "./weekends";
import styles from "./SessionLibrary.module.css";

const GROUP_KEY = "f1-engineer:library-group-weekends";

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
  // Grouping is on by default; a saved choice is restored after hydration.
  const [grouped, setGrouped] = useState(true);
  useEffect(() => {
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      if (window.localStorage.getItem(GROUP_KEY) === "0") setGrouped(false);
    } catch {
      // Keep the default.
    }
  }, []);
  function changeGrouped(value: boolean) {
    setGrouped(value);
    setShown(PAGE_SIZE);
    try {
      window.localStorage.setItem(GROUP_KEY, value ? "1" : "0");
    } catch {
      // The choice still applies to this page.
    }
  }
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
  const emptyCount = useMemo(() => model.sessions.filter(isEmptySession).length, [model.sessions]);
  const entries = useMemo(
    () => (grouped ? groupWeekends(rows) : rows.map((row) => ({ kind: "row" as const, row }))),
    [grouped, rows],
  );
  const weekendCount = useMemo(
    () => groupWeekends(model.sessions).filter((entry) => entry.kind === "weekend").length,
    [model.sessions],
  );
  const page = paginate(entries, shown);
  const countLine = grouped && entries.length !== rows.length
    ? `Showing ${page.visible.length} of ${entries.length} weekends and sessions (${rows.length} sessions)`
    : page.countLine;
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
        actions={
          <span className={styles.headerActions}>
            <Link href="/review/tracks" className={styles.bestsLink}>
              Personal bests
            </Link>
            <ImportSessionButton />
          </span>
        }
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
            {(emptyCount > 0 || weekendCount > 0) && (
              <div className={styles.listTools}>
                {weekendCount > 0 && (
                  <Checkbox label="Group by weekend" checked={grouped} onChange={changeGrouped} />
                )}
                {emptyCount > 0 && (
                  <Checkbox
                    label={`Show empty sessions (${emptyCount})`}
                    checked={filters.showEmpty}
                    onChange={(showEmpty) => change({ showEmpty })}
                  />
                )}
              </div>
            )}
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
                {page.visible.map((entry) =>
                  entry.kind === "weekend" ? (
                    <WeekendCard key={entry.weekend.key} weekend={entry.weekend} />
                  ) : (
                    <SessionRow key={entry.row.key} row={entry.row} />
                  ),
                )}
              </ul>
            )}
            {rows.length === 0 && hasSessions && (
              <EmptyState
                size="card"
                title={
                  !filtersActive(filters) && !filters.showEmpty && emptyCount > 0
                    ? "Every session so far has no timed laps."
                    : "No sessions match these filters."
                }
                action={
                  filtersActive(filters) ? (
                    <Button
                      variant="muted"
                      onClick={() => change({ ...NO_FILTERS, oldestFirst: filters.oldestFirst, showEmpty: filters.showEmpty })}
                    >
                      Clear filters
                    </Button>
                  ) : !filters.showEmpty && emptyCount > 0 ? (
                    <Button variant="muted" onClick={() => change({ showEmpty: true })}>
                      Show empty sessions
                    </Button>
                  ) : undefined
                }
              />
            )}
            {entries.length > PAGE_SIZE && (
              <div className={styles.pager}>
                {page.remaining > 0 && <Button onClick={showMore}>{page.more}</Button>}
                <p ref={countRef} className={styles.count} tabIndex={-1} aria-live="polite">
                  {countLine}
                </p>
              </div>
            )}
          </>
        )}
      </Dropzone>
    </>
  );
}

