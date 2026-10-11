"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { MoreIcon, SearchIcon, SortIcon } from "@/features/ui-foundation/icons";
import { Badge, IconButton, PageHeader, Select, StatusBadge } from "@/features/ui-foundation/primitives";
import { TrackPhoto } from "@/features/ui-foundation/TrackPhoto";
import { ImportSessionButton } from "./ImportSessionButton";
import type { LibraryModel, LibraryRow } from "./load";
import styles from "./SessionLibrary.module.css";

function unique(values: string[]) {
  return [...new Set(values)].sort((a, b) => a.localeCompare(b));
}

export function SessionLibrary({ model }: { model: LibraryModel }) {
  const [query, setQuery] = useState("");
  const [track, setTrack] = useState("");
  const [type, setType] = useState("");
  const [source, setSource] = useState("");
  const [status, setStatus] = useState("");
  const [oldestFirst, setOldestFirst] = useState(false);

  const options = useMemo(
    () => ({
      tracks: unique(model.sessions.map((row) => row.title)),
      types: unique(model.sessions.map((row) => row.type.label)),
      sources: unique(model.sessions.map((row) => row.source.label)),
      statuses: unique(model.sessions.map((row) => row.status.label.replace(/\s+\d+%$/, ""))),
    }),
    [model.sessions],
  );

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = model.sessions.filter((row) => {
      if (track && row.title !== track) return false;
      if (type && row.type.label !== type) return false;
      if (source && row.source.label !== source) return false;
      if (status && !row.status.label.startsWith(status)) return false;
      if (!needle) return true;
      return [row.title, row.type.label, row.source.label, row.date, row.notes, ...row.meta]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
    return oldestFirst ? [...filtered].reverse() : filtered;
  }, [model.sessions, query, track, type, source, status, oldestFirst]);

  return (
    <>
      <PageHeader
        title="Session Library"
        subtitle="Browse, search and manage your recorded sessions."
        actions={<ImportSessionButton enabled={model.live} />}
      />
      <label className={styles.search}>
        <SearchIcon size={15} className={styles.searchIcon} />
        <span className="sr-only">Search sessions</span>
        <input
          type="search"
          placeholder="Search sessions..."
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      <div className={styles.filters}>
        <Select aria-label="Track" value={track} onChange={(event) => setTrack(event.target.value)}>
          <option value="">All Tracks</option>
          {options.tracks.map((value) => (
            <option key={value}>{value}</option>
          ))}
        </Select>
        <Select aria-label="Session type" value={type} onChange={(event) => setType(event.target.value)}>
          <option value="">All Types</option>
          {options.types.map((value) => (
            <option key={value}>{value}</option>
          ))}
        </Select>
        <Select aria-label="Source" value={source} onChange={(event) => setSource(event.target.value)}>
          <option value="">All Sources</option>
          {options.sources.map((value) => (
            <option key={value}>{value}</option>
          ))}
        </Select>
        <Select aria-label="Status" value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="">All Status</option>
          {options.statuses.map((value) => (
            <option key={value}>{value}</option>
          ))}
        </Select>
        <IconButton
          label={oldestFirst ? "Sort newest first" : "Sort oldest first"}
          className={styles.sort}
          aria-pressed={oldestFirst}
          onClick={() => setOldestFirst((value) => !value)}
        >
          <SortIcon size={16} />
        </IconButton>
      </div>

      {rows.length === 0 ? (
        <div className={styles.empty}>
          {model.sessions.length === 0
            ? "No sessions recorded yet. Drive with telemetry enabled or import a capture."
            : "No sessions match these filters."}
        </div>
      ) : (
        <ul className={styles.list} aria-label="Sessions">
          {rows.map((row) => (
            <SessionRow key={row.key} row={row} />
          ))}
        </ul>
      )}
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
        <div className={styles.date}>{row.date}</div>
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
        <strong>{row.laps}</strong>
        <span>Laps</span>
      </div>
      <p className={styles.notes}>{row.notes}</p>
      <div className={styles.side}>
        <StatusBadge tone={row.status.tone}>{row.status.label}</StatusBadge>
        <details className={styles.rowMenu}>
          <summary className={styles.more} aria-label={`More actions for ${row.title}`} title="More actions">
            <MoreIcon size={16} />
          </summary>
          <div className={styles.menu}>
            {row.href ? (
              <>
                <Link href={row.href}>Open overview</Link>
                <Link href={`${row.href}/laps`}>Open lap analysis</Link>
              </>
            ) : (
              <span className={styles.menuNote}>
                {row.status.label === "Unavailable" ? "Session data is unavailable" : "Available once processing finishes"}
              </span>
            )}
          </div>
        </details>
      </div>
    </li>
  );
}
