"use client";

import { useId, useMemo, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from "react";
import { Bone } from "@/features/route-states/Skeleton";
import { ChevronDownIcon, SortIcon } from "./icons";
import { nextRovingIndex } from "./primitives";
import styles from "./DataTable.module.css";

export type Column<R> = {
  key: string;
  header: string;
  /** Column width: "64px", "20%", "minmax(110px, 1fr)" (min becomes the minimum) or "1fr". */
  width?: string;
  align?: "start" | "end" | "center";
  /** Tabular figures, right aligned unless `align` says otherwise. */
  numeric?: boolean;
  /** Makes the header a sort button; compares two rows ascending. */
  sort?: (a: R, b: R) => number;
  render: (row: R) => ReactNode;
  /** Shown under the table and referenced from the header by aria-describedby. */
  footnote?: string;
};

export type DataTableProps<R> = {
  /** Screen reader caption (visually hidden). */
  caption: string;
  columns: Column<R>[];
  rows: R[];
  rowKey: (row: R) => string;
  selectedKey?: string | null;
  /** Adds a selection button to the first cell of every row. */
  onSelect?: (row: R) => void;
  rowTone?: (row: R) => "muted" | "warning" | undefined;
  defaultSort?: { key: string; dir: "asc" | "desc" };
  loading?: boolean;
  loadingRows?: number;
  /** Rendered in the body when `rows` is empty (and not loading). */
  empty?: ReactNode;
  stickyHeader?: boolean;
  maxHeight?: number;
  density?: "regular" | "compact";
};

type SortState = { key: string; dir: "asc" | "desc" } | null;

/** Sorts a copy of `rows`; equal rows keep their original order. */
export function sortRows<R>(rows: R[], columns: Column<R>[], sort: SortState): R[] {
  const column = sort ? columns.find((candidate) => candidate.key === sort.key) : undefined;
  if (!sort || !column?.sort) return rows;
  const compare = column.sort;
  const sign = sort.dir === "asc" ? 1 : -1;
  return rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => sign * compare(a.row, b.row) || a.index - b.index)
    .map((entry) => entry.row);
}

/** Minimum pixel width implied by a column width string, or 0 when unknown. */
export function columnMinWidth(width: string | undefined): number {
  const match = width?.match(/^\s*(?:minmax\(\s*)?(\d+(?:\.\d+)?)px/);
  return match ? Number(match[1]) : 0;
}

function trackWidth(width: string | undefined): string | undefined {
  if (!width) return undefined;
  if (/^\s*\d+(?:\.\d+)?(px|%)\s*$/.test(width)) return width.trim();
  return undefined; // fr tracks and minmax() share the remaining space
}

export function DataTable<R>({
  caption,
  columns,
  rows,
  rowKey,
  selectedKey,
  onSelect,
  rowTone,
  defaultSort,
  loading,
  loadingRows = 5,
  empty,
  stickyHeader,
  maxHeight,
  density = "regular",
}: DataTableProps<R>) {
  const [sort, setSort] = useState<SortState>(defaultSort ?? null);
  const baseId = useId();
  const sorted = useMemo(() => sortRows(rows, columns, sort), [rows, columns, sort]);
  const footnotes = columns.flatMap((column, index) =>
    column.footnote ? [{ id: `${baseId}-note-${index}`, text: column.footnote, key: column.key }] : [],
  );
  const noteId = (key: string) => footnotes.find((note) => note.key === key)?.id;
  const minWidth = columns.reduce((total, column) => total + (columnMinWidth(column.width) || 80), 0);

  function toggleSort(key: string) {
    setSort((current) => ({ key, dir: current?.key === key && current.dir === "asc" ? "desc" : "asc" }));
  }

  function onBodyKeyDown(event: KeyboardEvent<HTMLTableSectionElement>) {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const buttons = Array.from(
      event.currentTarget.querySelectorAll<HTMLButtonElement>("button[data-row-button]"),
    );
    const current = buttons.indexOf(event.target as HTMLButtonElement);
    if (current < 0) return;
    const next = nextRovingIndex(event.key, current, buttons.length, "vertical", false);
    if (next === null) return;
    event.preventDefault();
    buttons[next]?.focus();
  }

  const showEmpty = !loading && sorted.length === 0 && empty;
  return (
    <div className={styles.wrap}>
      <div
        className={styles.scroll}
        data-sticky={stickyHeader ? "true" : undefined}
        style={maxHeight ? ({ maxHeight } as CSSProperties) : undefined}
      >
        <table
          className={styles.table}
          data-density={density}
          aria-busy={loading ? true : undefined}
          style={{ minWidth }}
        >
          <caption className="sr-only">{caption}</caption>
          <colgroup>
            {columns.map((column) => (
              <col key={column.key} style={{ width: trackWidth(column.width) }} />
            ))}
          </colgroup>
          <thead>
            <tr>
              {columns.map((column) => {
                const active = sort?.key === column.key;
                const ariaSort = column.sort
                  ? active
                    ? sort.dir === "asc"
                      ? "ascending"
                      : "descending"
                    : "none"
                  : undefined;
                const align = column.align ?? (column.numeric ? "end" : "start");
                return (
                  <th
                    key={column.key}
                    scope="col"
                    className={styles.th}
                    data-align={align}
                    aria-sort={ariaSort}
                    aria-describedby={noteId(column.key)}
                  >
                    {column.sort ? (
                      <button type="button" className={styles.sortButton} onClick={() => toggleSort(column.key)}>
                        {column.header}
                        <span className={styles.sortIcon} data-active={active ? "true" : undefined} data-dir={active ? sort.dir : undefined} aria-hidden="true">
                          {active ? <ChevronDownIcon size={12} /> : <SortIcon size={12} />}
                        </span>
                      </button>
                    ) : (
                      column.header
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody onKeyDown={onSelect ? onBodyKeyDown : undefined}>
            {loading
              ? Array.from({ length: loadingRows }, (_, rowIndex) => (
                  <tr key={rowIndex} className={styles.tr} aria-hidden="true">
                    {columns.map((column, index) => (
                      <td key={column.key} className={styles.td}>
                        <Bone width={index === 0 ? 36 : "70%"} height={12} />
                      </td>
                    ))}
                  </tr>
                ))
              : sorted.map((row) => {
                  const key = rowKey(row);
                  const selected = selectedKey === key;
                  return (
                    <tr
                      key={key}
                      className={styles.tr}
                      data-tone={rowTone?.(row)}
                      data-selected={selected ? "true" : undefined}
                      aria-selected={onSelect ? selected : undefined}
                    >
                      {columns.map((column, index) => {
                        const align = column.align ?? (column.numeric ? "end" : "start");
                        return (
                          <td
                            key={column.key}
                            className={styles.td}
                            data-align={align}
                            data-numeric={column.numeric ? "true" : undefined}
                          >
                            {index === 0 && onSelect ? (
                              <button
                                type="button"
                                className={styles.rowButton}
                                data-row-button=""
                                aria-pressed={selected}
                                onClick={() => onSelect(row)}
                              >
                                {column.render(row)}
                              </button>
                            ) : (
                              column.render(row)
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
            {showEmpty && (
              <tr>
                <td colSpan={columns.length} className={styles.emptyCell}>
                  {empty}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {footnotes.map((note) => (
        <p key={note.id} id={note.id} className={styles.footnote}>
          {note.text}
        </p>
      ))}
    </div>
  );
}
