"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  describeStorageReason,
  formatStorageBytes,
  parseStorageUsageResponse,
  STORAGE_SCOPE_KEYS,
  STORAGE_VOLUME_KEYS,
  type StorageScopeKey,
  type StorageUsage,
  type StorageVolumeKey,
} from "@/lib/storage-usage.mjs";

const SCOPE_LABELS: Record<StorageScopeKey, string> = {
  database: "Database and SQLite sidecars",
  finalized_captures: "Finalized captures",
  recorder_staging: "Recorder staging files",
  imported_traces: "Imported traces and observations",
};

const VOLUME_LABELS: Record<StorageVolumeKey, string> = {
  database_location: "Database location",
  recordings_location: "Recordings location",
};

function measuredAt(value: string) {
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toLocaleString() : "Time unavailable";
}

export default function SettingsStorageUsage() {
  const [usage, setUsage] = useState<StorageUsage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);

  const refresh = useCallback(async () => {
    const currentRequest = ++requestId.current;
    setUsage(null);
    setError(null);
    setLoading(true);
    try {
      const response = await fetch("/api/storage/usage", { cache: "no-store" });
      const body: unknown = await response.json();
      const parsed = parseStorageUsageResponse(body);
      if (!response.ok || !parsed) {
        throw new Error("The storage measurement could not be validated.");
      }
      if (requestId.current === currentRequest) setUsage(parsed);
    } catch {
      if (requestId.current === currentRequest) {
        setError("Storage usage is unavailable. Check that the local service is running, then refresh.");
      }
    } finally {
      if (requestId.current === currentRequest) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => {
      requestId.current += 1;
    };
  }, [refresh]);

  const hasUnavailableMeasurement = usage
    ? STORAGE_SCOPE_KEYS.some((key) => usage.scopes[key].status !== "available") ||
      STORAGE_VOLUME_KEYS.some((key) => usage.volumes[key].status !== "available")
    : false;

  return (
    <section className="settings-section settings-storage-section panel" id="storage" aria-labelledby="settings-storage-title">
      <div className="settings-section-heading">
        <div>
          <span className="eyebrow">STORAGE</span>
          <h2 id="settings-storage-title">Managed data usage</h2>
        </div>
        <span className={`settings-state ${loading ? "" : error || hasUnavailableMeasurement ? "settings-state-unavailable" : "settings-state-available"}`}>
          {loading ? "MEASURING" : error ? "UNAVAILABLE" : hasUnavailableMeasurement ? "PARTIAL" : "MEASURED"}
        </span>
      </div>
      <p className="settings-copy settings-storage-intro">
        Read-only estimates for files managed by the local service. These scopes can overlap, so do not add them into one application total. They report logical file sizes, not reclaimable space.
      </p>

      <div className="settings-storage-actions">
        <button className="button-tertiary" type="button" onClick={() => void refresh()} disabled={loading}>
          {loading ? "Measuring…" : "Refresh usage"}
        </button>
        {usage ? <span>Measured {measuredAt(usage.measurement_completed_at_utc)}</span> : null}
      </div>

      {error ? <p className="settings-storage-error" role="status">{error}</p> : null}
      {!error && loading && !usage ? <p className="settings-copy" role="status">Reading file metadata from configured locations…</p> : null}

      {usage ? (
        <>
          <div className="settings-storage-grid" aria-label="Managed file usage">
            {STORAGE_SCOPE_KEYS.map((key) => {
              const scope = usage.scopes[key];
              return (
                <article className="settings-storage-card" key={key}>
                  <span className="eyebrow">{SCOPE_LABELS[key]}</span>
                  {scope.status === "available" ? (
                    <>
                      <strong>{formatStorageBytes(scope.logical_bytes ?? 0)}</strong>
                      <span>{scope.regular_file_count} regular {scope.regular_file_count === 1 ? "file" : "files"}</span>
                      {scope.excluded_entry_count ? <small>{scope.excluded_entry_count} other entries excluded</small> : null}
                    </>
                  ) : (
                    <span className="settings-storage-card-unavailable">{describeStorageReason(scope.reason)}</span>
                  )}
                </article>
              );
            })}
          </div>

          <div className="settings-storage-volumes" aria-label="Filesystem capacity">
            <span className="eyebrow">FILESYSTEM CAPACITY</span>
            {STORAGE_VOLUME_KEYS.map((key) => {
              const volume = usage.volumes[key];
              return (
                <div className="settings-storage-volume" key={key}>
                  <span>{VOLUME_LABELS[key]}</span>
                  {volume.status === "available" ? (
                    <b>{formatStorageBytes(volume.free_bytes ?? 0)} free of {formatStorageBytes(volume.total_bytes ?? 0)}</b>
                  ) : (
                    <b>{describeStorageReason(volume.reason)}</b>
                  )}
                </div>
              );
            })}
          </div>
          <p className="settings-copy settings-storage-note">{usage.measurement_note} Refresh does not change capture, import, replay, or retention state.</p>
        </>
      ) : null}
    </section>
  );
}
