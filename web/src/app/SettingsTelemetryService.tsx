"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  parseTelemetryServiceResponse,
  telemetryReservationLabel,
} from "@/lib/telemetry-service.mjs";

const TELEMETRY_STATUS_TIMEOUT_MS = 10_000;

function checkedAt(value: string) {
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toLocaleString() : "Time unavailable";
}

function serviceBadge(
  loading: boolean,
  hasError: boolean,
  service: ReturnType<typeof parseTelemetryServiceResponse>,
) {
  if (loading) return "CHECKING";
  if (hasError || !service?.controller_ready) return "UNAVAILABLE";
  if (!service.configuration_available) return "CONFIG LIMIT";
  return service.operation_reservation === "idle" ? "READY" : "IN USE";
}

export default function SettingsTelemetryService() {
  const [service, setService] = useState<ReturnType<typeof parseTelemetryServiceResponse>>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copyMessage, setCopyMessage] = useState<string | null>(null);
  const requestId = useRef(0);
  const activeRequest = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    const currentRequest = ++requestId.current;
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    const deadline = window.setTimeout(
      () => controller.abort(),
      TELEMETRY_STATUS_TIMEOUT_MS,
    );
    setService(null);
    setError(null);
    setCopyMessage(null);
    setLoading(true);
    try {
      const response = await fetch("/api/telemetry/service", {
        cache: "no-store",
        signal: controller.signal,
      });
      const body: unknown = await response.json();
      const parsed = parseTelemetryServiceResponse(body);
      if (!response.ok || !parsed) {
        throw new Error("Telemetry status could not be validated.");
      }
      if (requestId.current === currentRequest) setService(parsed);
    } catch {
      if (requestId.current === currentRequest) {
        setError(
          controller.signal.aborted
            ? "The telemetry status check timed out or was cancelled. Refresh to try again."
            : "Telemetry service status is unavailable. Check the local service, then refresh.",
        );
      }
    } finally {
      window.clearTimeout(deadline);
      if (activeRequest.current === controller) activeRequest.current = null;
      if (requestId.current === currentRequest) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => {
      requestId.current += 1;
      activeRequest.current?.abort();
      activeRequest.current = null;
    };
  }, [refresh]);

  const copyPort = useCallback(async () => {
    if (!service || service.udp_port === null) return;
    setCopyMessage(null);
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(String(service.udp_port));
      setCopyMessage("UDP port copied.");
    } catch {
      setCopyMessage("Could not copy the port. Select the value to copy it manually.");
    }
  }, [service]);

  const badge = serviceBadge(loading, error !== null, service);
  const wildcardBind = service?.udp_bind_host === "0.0.0.0" || service?.udp_bind_host === "::";

  return (
    <section className="settings-section settings-telemetry-section panel" id="telemetry" aria-labelledby="settings-telemetry-title">
      <div className="settings-section-heading">
        <div>
          <span className="eyebrow">TELEMETRY</span>
          <h2 id="settings-telemetry-title">Local service</h2>
        </div>
        <span className={`settings-state ${badge === "READY" ? "settings-state-available" : badge === "UNAVAILABLE" || badge === "CONFIG LIMIT" ? "settings-state-unavailable" : ""}`}>
          {badge}
        </span>
      </div>
      <p className="settings-copy settings-telemetry-intro">
        Read-only status for the configured UDP receiver and the local operation using it.
      </p>

      <div className="settings-storage-actions settings-telemetry-actions">
        <button className="button-tertiary" type="button" onClick={() => void refresh()} aria-label={loading ? "Cancel and refresh telemetry status" : "Refresh telemetry status"}>
          {loading ? "Check again" : "Refresh status"}
        </button>
        {service ? <span>Last checked {checkedAt(service.observed_at_utc)}</span> : null}
      </div>

      {error ? <p className="settings-storage-error" role="status">{error}</p> : null}
      {!error && loading && !service ? <p className="settings-copy" role="status">Reading local service status…</p> : null}

      {service ? (
        <>
          {!service.configuration_available ? (
            <p className="settings-telemetry-warning" role="status">
              The configured values exceed this panel’s safe display limits, so they are hidden.
            </p>
          ) : null}
          {service.configuration_available ? (
            <dl className="settings-telemetry-grid" aria-label="Configured UDP receiver">
              <div>
                <dt>UDP bind host</dt>
                <dd><code>{service.udp_bind_host}</code></dd>
              </div>
              <div>
                <dt>UDP port</dt>
                <dd><code>{service.udp_port}</code></dd>
              </div>
              <div>
                <dt>Receive queue</dt>
                <dd><code>{service.receive_queue_size?.toLocaleString()}</code> packets</dd>
              </div>
              <div>
                <dt>Local operation</dt>
                <dd>{telemetryReservationLabel(service.operation_reservation)}</dd>
              </div>
            </dl>
          ) : (
            <p className="settings-copy settings-telemetry-reservation">
              Local operation: {telemetryReservationLabel(service.operation_reservation)}
            </p>
          )}

          {service.configuration_available && service.udp_port !== null ? (
            <div className="settings-telemetry-copy-row">
              <button className="button-tertiary" type="button" onClick={() => void copyPort()}>
                Copy UDP port
              </button>
              {copyMessage ? <span role="status">{copyMessage}</span> : null}
            </div>
          ) : null}

          {wildcardBind ? (
            <p className="settings-telemetry-note">
              This wildcard address is where GP...T listens; it is not the destination to enter in F1. Use an address for this PC that the game can reach.
            </p>
          ) : null}
          {!service.controller_ready ? (
            <p className="settings-telemetry-warning" role="status">
              The local recording controller is unavailable. Configured receiver values are shown for reference only.
            </p>
          ) : null}
          {service.controller_ready && service.operation_reservation !== "idle" ? (
            <p className="settings-telemetry-note" role="status">
              {telemetryReservationLabel(service.operation_reservation)}. Refresh reports ownership only; it does not interrupt that operation.
            </p>
          ) : null}
          <p className="settings-telemetry-note">
            Ready means the local controller is available. It does not confirm a bound UDP socket, game connection, or incoming packets.
          </p>
        </>
      ) : null}
    </section>
  );
}
