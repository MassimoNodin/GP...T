// Pure mapping from local API payloads to the strings the Settings screen shows.
// Kept free of framework and alias imports so it can be tested directly.

export type DisplayTone = "green" | "amber" | "red" | "grey";

/** Engineer runtime fields from GET /api/v1/engineer/runtime (v1 envelope data). */
export type RuntimeInfo = {
  runtime: string;
  modelName: string;
  billing: string | null;
  cloudRouting: string | null;
  requestedPlacement: string | null;
  endpoint: string | null;
  status: string;
  reason: string | null;
  lastInferencePlacement: string | null;
  lastInferenceAtUtc: string | null;
};

/** Telemetry receiver fields from GET /api/v2/session-evidence/status. */
export type ReceiverInfo = {
  state: string;
  host: string | null;
  port: number | null;
  receiverMode: string | null;
  received: number | null;
  dropped: number | null;
  kernelDropped: number | null;
  socketErrors: number | null;
  publicationDelayS: number | null;
};

export type DisplayRow = { label: string; value: string; tone?: DisplayTone };

export type RuntimeView = {
  available: boolean;
  tone: DisplayTone;
  statusLabel: string;
  summary: string;
  rows: DisplayRow[];
};

export type ReceiverView = {
  available: boolean;
  tone: DisplayTone;
  stateLabel: string;
  detail: string;
  rows: DisplayRow[];
};

const UNAVAILABLE = "Unavailable";

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown, maximum = 96): string | null {
  return typeof value === "string" && value.length > 0 && value.length <= maximum ? value : null;
}

function count(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

/** Tolerant reader: shows what the backend reports without enforcing a pinned runtime. */
export function parseRuntimeInfo(value: unknown): RuntimeInfo | null {
  const envelope = record(value);
  const data = record(envelope?.data);
  if (!envelope || envelope.api_version !== "v1" || !data) return null;
  const runtime = text(data.runtime, 32);
  const modelName = text(data.model_name, 64);
  const status = text(data.status, 32);
  if (!runtime || !modelName || !status) return null;
  return {
    runtime,
    modelName,
    billing: text(data.billing, 48),
    cloudRouting: text(data.cloud_routing, 48),
    requestedPlacement: text(data.requested_placement, 32),
    endpoint: text(data.endpoint, 64),
    status,
    reason: text(data.reason),
    lastInferencePlacement: text(data.last_inference_placement, 32),
    lastInferenceAtUtc: text(data.last_inference_at_utc, 40),
  };
}

export function parseReceiverInfo(value: unknown): ReceiverInfo | null {
  const data = record(value);
  const state = text(data?.state, 24);
  if (!data || !state) return null;
  const port = count(data.port);
  const delay = data.last_publication_delay_s;
  return {
    state,
    host: text(data.host, 64),
    port: port !== null && port > 0 && port < 65536 ? port : null,
    receiverMode: text(data.receiver_mode, 48),
    received: count(data.received),
    dropped: count(data.dropped),
    kernelDropped: count(data.kernel_dropped),
    socketErrors: count(data.socket_errors),
    publicationDelayS: typeof delay === "number" && Number.isFinite(delay) && delay >= 0 ? delay : null,
  };
}

function words(code: string): string {
  const spaced = code.replaceAll("_", " ").replaceAll(":", " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function formatCount(value: number | null): string {
  return value === null ? "Not reported" : new Intl.NumberFormat("en-US").format(value);
}

export function formatDelay(seconds: number | null): string {
  if (seconds === null) return "Not reported";
  const ms = seconds * 1000;
  return ms > 0 && ms < 1 ? "< 1 ms" : `${Math.round(ms)} ms`;
}

/** UTC timestamp as a readable, locale-independent string. */
export function formatUtc(iso: string | null): string | null {
  if (!iso) return null;
  const time = Date.parse(iso);
  if (!Number.isFinite(time)) return null;
  return `${new Date(time).toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

const RUNTIME_STATUS: Record<string, { label: string; tone: DisplayTone }> = {
  ready: { label: "Ready", tone: "green" },
  not_configured: { label: "Not configured", tone: "amber" },
  model_unavailable: { label: "Model unavailable", tone: "amber" },
  model_changed: { label: "Model changed", tone: "amber" },
  model_rejected: { label: "Model rejected", tone: "red" },
  unavailable: { label: "Unavailable", tone: "red" },
};

function describePlacement(info: RuntimeInfo): string {
  const placement = info.requestedPlacement;
  if (!placement) return "Not reported";
  const name = placement.toLowerCase() === "cloud" ? "Cloud" : placement;
  if (info.cloudRouting === "subscription") return `${name} (Codex subscription)`;
  if (info.cloudRouting === "unverified") return `${name} (cloud routing not verified)`;
  return info.cloudRouting ? `${name} (${words(info.cloudRouting).toLowerCase()})` : name;
}

export function describeRuntime(info: RuntimeInfo | null): RuntimeView {
  if (!info) {
    return {
      available: false,
      tone: "red",
      statusLabel: UNAVAILABLE,
      summary: "Engineer runtime could not be reached.",
      rows: [
        { label: "Runtime", value: UNAVAILABLE },
        { label: "Model", value: UNAVAILABLE },
        { label: "Placement", value: UNAVAILABLE },
        { label: "Status", value: "Start the local engine to read the engineer runtime.", tone: "red" },
        { label: "Last inference", value: UNAVAILABLE },
      ],
    };
  }
  const known = RUNTIME_STATUS[info.status] ?? { label: words(info.status), tone: "amber" as DisplayTone };
  const lastWhen = formatUtc(info.lastInferenceAtUtc);
  const lastWhere = info.lastInferencePlacement;
  const lastInference = lastWhen
    ? `${lastWhen}${lastWhere ? ` (${lastWhere})` : ""}`
    : "None recorded";
  const rows: DisplayRow[] = [
    { label: "Runtime", value: info.runtime },
    { label: "Model", value: info.modelName },
    { label: "Placement", value: describePlacement(info) },
    { label: "Status", value: known.label, tone: known.tone },
  ];
  if (info.reason) rows.push({ label: "Reason", value: words(info.reason) });
  rows.push({ label: "Last inference", value: lastInference });
  return {
    available: true,
    tone: known.tone,
    statusLabel: known.label,
    summary: `${info.runtime} · ${info.modelName}`,
    rows,
  };
}

const RECEIVER_STATE: Record<string, { label: string; tone: DisplayTone; detail: string }> = {
  receiving: { label: "Receiving", tone: "green", detail: "Telemetry packets are arriving" },
  listening: { label: "Listening", tone: "amber", detail: "Waiting for the game to send telemetry" },
  starting: { label: "Starting", tone: "amber", detail: "The receiver is starting" },
  stale: { label: "Stale", tone: "amber", detail: "No recent packets" },
  stopped: { label: "Stopped", tone: "red", detail: "The receiver is stopped" },
  failed: { label: "Failed", tone: "red", detail: "The receiver reported a failure" },
  disabled: { label: "Disabled", tone: "grey", detail: "Automatic acquisition is disabled" },
};

export function formatEndpoint(host: string | null, port: number | null): string {
  if (!host && port === null) return "Not reported";
  const address = port === null ? (host ?? "Unknown host") : `${host ?? "Unknown host"}:${port}`;
  return host === "0.0.0.0" ? `${address} (all interfaces)` : address;
}

export function describeReceiver(info: ReceiverInfo | null): ReceiverView {
  if (!info) {
    return {
      available: false,
      tone: "red",
      stateLabel: UNAVAILABLE,
      detail: "Start the local engine to receive telemetry.",
      rows: [
        { label: "Receiver", value: "Local engine unavailable", tone: "red" },
        { label: "Address", value: UNAVAILABLE },
        { label: "Receiver mode", value: UNAVAILABLE },
        { label: "Packets received", value: UNAVAILABLE },
        { label: "Packets dropped", value: UNAVAILABLE },
        { label: "Publication delay", value: UNAVAILABLE },
      ],
    };
  }
  const known = RECEIVER_STATE[info.state] ?? { label: words(info.state), tone: "amber" as DisplayTone, detail: "" };
  const disabled = info.state === "disabled";
  const dropped =
    disabled || info.dropped === null
      ? "Not reported"
      : `${formatCount(info.dropped)} (kernel ${info.kernelDropped === null ? "n/a" : formatCount(info.kernelDropped)}, socket errors ${info.socketErrors === null ? "n/a" : formatCount(info.socketErrors)})`;
  return {
    available: true,
    tone: known.tone,
    stateLabel: known.label,
    detail: known.detail,
    rows: [
      { label: "Receiver", value: known.label, tone: known.tone },
      { label: "Address", value: formatEndpoint(info.host, info.port) },
      { label: "Receiver mode", value: info.receiverMode ? words(info.receiverMode) : "Not reported" },
      { label: "Packets received", value: disabled ? "Not reported" : formatCount(info.received) },
      { label: "Packets dropped", value: dropped },
      { label: "Publication delay", value: disabled ? "Not reported" : formatDelay(info.publicationDelayS) },
    ],
  };
}

/** Game Connection tile text. Connected only while packets are arriving. */
export function describeGameTile(
  info: ReceiverInfo | null,
  reference: { name: string; state: string },
): { name: string; state: string; tone: "green-text" | "amber-text" | "grey-text" } {
  if (!info) return { name: reference.name, state: "Engine unavailable", tone: "grey-text" };
  if (info.state === "receiving") {
    return {
      name: reference.name,
      state: info.publicationDelayS === null ? "Connected" : `Connected (${formatDelay(info.publicationDelayS)})`,
      tone: "green-text",
    };
  }
  const known = RECEIVER_STATE[info.state];
  return {
    name: reference.name,
    state: info.state === "disabled" ? "Disabled" : "Not connected",
    tone: known?.tone === "grey" ? "grey-text" : "amber-text",
  };
}
