"use client";

import { useEffect, useState } from "react";
import {
  createAcquisitionPoller,
  type AcquisitionStatus,
} from "@/lib/acquisition-status";
import { AnalysisPanel } from "./AnalysisWidgets";

export default function AcquisitionStatusPanel() {
  const [status, setStatus] = useState<AcquisitionStatus | null>(null);
  useEffect(() => {
    const poller = createAcquisitionPoller({ onStatus: setStatus });
    const visibility = () =>
      poller.setHidden(document.visibilityState === "hidden");
    document.addEventListener("visibilitychange", visibility);
    visibility();
    return () => {
      document.removeEventListener("visibilitychange", visibility);
      poller.stop();
    };
  }, []);
  const count = (value: number | null | undefined) =>
    value == null ? "Unknown" : value.toLocaleString();
  const hasLoss = [
    status?.dropped,
    status?.kernel_dropped,
    status?.socket_errors,
  ].some((value) => value != null && value > 0);
  const rows = [
    [
      "Received / journaled / processed",
      [status?.received, status?.journaled, status?.processed]
        .map(count)
        .join(" / "),
    ],
    ["Application queue drops", count(status?.dropped)],
    ["Kernel socket drops (sampled)", count(status?.kernel_dropped)],
    ["Socket errors", count(status?.socket_errors)],
    [
      "Queue depth / limit",
      [status?.queue_depth, status?.queue_limit].map(count).join(" / "),
    ],
    ["Pending publication", count(status?.pending_publication)],
    [
      "Last publication backlog",
      status?.last_publication_delay_s == null
        ? "Unknown"
        : (status.last_publication_delay_s * 1000).toFixed(1) + " ms",
    ],
  ];
  return (
    <AnalysisPanel
      className="acquisition-status-panel"
      title="Automatic UDP acquisition"
      action={
        <span className="ref-badge">{status?.state ?? "Unavailable"}</span>
      }
    >
      <div
        className="ref-facts-box"
        aria-label="Automatic acquisition diagnostics"
      >
        {rows.map(([label, value]) => (
          <div className="ref-fact" key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
      {hasLoss ? (
        <p role="status">
          This listener has observed packet losses or socket errors. Inspect
          session gap evidence before accepting laps.
        </p>
      ) : null}
      <p className="live-monitor-unavailable">
        Read-only v2 receiver diagnostics, independent of the pinned
        recording/replay below. Listening means the socket is open, not that
        game packets have arrived. Listener-lifetime counters and the last
        backlog sample do not establish lossless laps or comparison eligibility.
      </p>
    </AnalysisPanel>
  );
}
