"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { EngineerRuntimeStatus } from "@/lib/api";
import { parseEngineerRuntimeResponse } from "@/lib/engineer-ask";

export default function SettingsAIRuntime() {
  const [status, setStatus] = useState<EngineerRuntimeStatus | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const activeRequest = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    setRefreshing(true);
    setError(null);
    setStatus(null);
    const deadline = window.setTimeout(() => controller.abort(), 5_000);
    const isCurrent = () => activeRequest.current === controller;
    try {
      const response = await fetch("/api/engineer/runtime", {
        method: "GET",
        cache: "no-store",
        signal: controller.signal,
      });
      const envelope: unknown = await response.json();
      const parsed = parseEngineerRuntimeResponse(envelope);
      if (!isCurrent()) return;
      if (!response.ok || !parsed) {
        setError("The local AI runtime status could not be verified.");
        return;
      }
      setStatus(parsed);
    } catch {
      if (isCurrent()) {
        setError(
          controller.signal.aborted
            ? "The local AI runtime status check timed out."
            : "The local AI runtime service could not be reached.",
        );
      }
    } finally {
      window.clearTimeout(deadline);
      if (isCurrent()) {
        activeRequest.current = null;
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => {
      const controller = activeRequest.current;
      activeRequest.current = null;
      controller?.abort();
    };
  }, [refresh]);

  const stateLabel = status
    ? status.status === "ready"
      ? "PINNED MODEL READY"
      : status.status.replaceAll("_", " ").toUpperCase()
    : error
      ? "STATUS UNAVAILABLE"
      : "CHECKING";

  return (
    <section className="settings-section settings-ai-runtime panel" id="ai">
      <div className="settings-section-heading">
        <div>
          <span className="eyebrow">AI RUNTIME</span>
          <h2>Ask GP...T · local model</h2>
        </div>
        <span
          className={`settings-state${status?.status === "ready" ? " settings-state-available" : " settings-state-unavailable"}`}
          aria-live="polite"
        >
          {stateLabel}
        </span>
      </div>

      <p className="settings-copy">
        GP...T uses a pinned Qwen3 4B model on the backend's private Ollama profile at
        127.0.0.1:11435 on that backend host, not necessarily this Windows PC.
        It routes your current question to a supported report;
        stored measurements and report text remain deterministic.
      </p>

      {error ? (
        <p className="settings-ai-error" role="status">
          {error}
        </p>
      ) : null}
      {status ? (
        <dl className="settings-telemetry-grid settings-ai-grid">
          <div>
            <dt>Model</dt>
            <dd>
              {status.model_name} ·{" "}
              {status.model_parameter_size ?? "size unreported"}
              {status.model_quantization
                ? ` · ${status.model_quantization}`
                : ""}
            </dd>
          </div>
          <div>
            <dt>Pinned digest</dt>
            <dd>
              <code>{status.model_digest ?? "No model pin"}</code>
            </dd>
          </div>
          <div>
            <dt>Runtime</dt>
            <dd>
              {status.runtime} {status.runtime_version ?? "version unavailable"}{" "}
              · {status.endpoint}
            </dd>
          </div>
          <div>
            <dt>Latest observed placement</dt>
            <dd>
              {status.last_inference_placement
                ? `${status.last_inference_placement}${status.last_inference_at_utc ? ` · ${new Date(status.last_inference_at_utc).toLocaleString()}` : ""}`
                : "No successful inference observed in this API process"}
            </dd>
          </div>
          <div>
            <dt>Cloud routing</dt>
            <dd>
              Unverified · app transport is fixed to loopback; remote-model
              metadata is rejected.
            </dd>
          </div>
          <div>
            <dt>Model status</dt>
            <dd>
              {status.reason === "runtime_version_unsupported"
                ? "Update Ollama to 0.9.0 or newer to use Ask GP...T."
                : (status.reason ?? "Exact pinned digest found")}
            </dd>
          </div>
        </dl>
      ) : null}

      <p className="settings-ai-note">
        The app requests CPU inference and reports CPU only after Ollama
        confirms zero VRAM for the exact pinned model. “Cloud routing” remains
        unverified: this status check cannot attest every Ollama daemon setting.
      </p>

      <div className="settings-ai-setup">
        <h3>Ubuntu backend setup</h3>
        <p>Run in the Ubuntu repository. This installs a checksum-pinned user-local runtime and private user service.</p>
        <code className="settings-ai-command">
          ~/.local/bin/uv run --frozen --extra app python -m scripts.setup_ubuntu_ollama
        </code>
        <p>
          Later starts use <code>systemctl --user start f1-engineer-ollama</code>.
          Stop it with <code>systemctl --user stop f1-engineer-ollama</code>.
          Ubuntu also owns transcription; Windows owns microphone capture and browser playback.
        </p>
        <h3>Windows standalone setup</h3>
        <ol>
          <li>Install Ollama if it is not already installed.</li>
          <li>
            From the project folder, run this once to start the isolated profile
            and download the pinned model:
          </li>
        </ol>
        <code className="settings-ai-command">
          .\scripts\start-private-ollama.ps1 -InstallModel
        </code>
        <p>
          Later starts use <code>.\scripts\start-private-ollama.ps1</code>. Stop
          the app-managed process with{" "}
          <code>.\scripts\stop-private-ollama.ps1</code>; the downloaded model
          and pin stay on disk.
        </p>
      </div>

      <div className="settings-ai-actions">
        <button
          className="button-tertiary"
          type="button"
          onClick={() => void refresh()}
          disabled={refreshing}
        >
          {refreshing ? "Checking…" : "Refresh status"}
        </button>
        {status?.reason ? <span>Detail: {status.reason}</span> : null}
      </div>
    </section>
  );
}
