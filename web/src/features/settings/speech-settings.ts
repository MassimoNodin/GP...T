export type SpeechSettings = {
  provider: "local" | "openai";
  local: { model: "tiny.en" | "large-v3"; device: "cpu" | "cuda" };
};

export type SpeechOption = {
  provider: "local" | "openai";
  model?: "tiny.en" | "large-v3";
  device?: "cpu" | "cuda";
  status: "ready" | "not_installed" | "invalid" | "not_configured";
  reason: string | null;
};

export type SpeechDocument = {
  settings: SpeechSettings;
  revision: number;
  options: SpeechOption[];
};

export type KeyStatus = {
  configured: boolean;
  last4: string | null;
  updated_at_utc: string | null;
  verified: "ok" | "auth_failed" | "unreachable" | null;
};

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function parseSpeechDocument(body: unknown): SpeechDocument | null {
  if (!record(body) || body.api_version !== "v1" || body.status !== "ok" || !record(body.data)) return null;
  const data = body.data;
  const settings = data.settings;
  if (!record(settings) || (settings.provider !== "local" && settings.provider !== "openai") ||
      !record(settings.local) || !["tiny.en", "large-v3"].includes(String(settings.local.model)) ||
      !["cpu", "cuda"].includes(String(settings.local.device)) ||
      !Number.isSafeInteger(data.revision) || (data.revision as number) < 0 || !Array.isArray(data.options)) return null;
  const options: SpeechOption[] = [];
  for (const option of data.options) {
    if (!record(option) || (option.provider !== "local" && option.provider !== "openai") ||
        !["ready", "not_installed", "invalid", "not_configured"].includes(String(option.status)) ||
        !(option.reason === null || typeof option.reason === "string" && option.reason.length <= 128)) return null;
    if (option.provider === "local" && (!["tiny.en", "large-v3"].includes(String(option.model)) ||
        !["cpu", "cuda"].includes(String(option.device)))) return null;
    options.push(option as SpeechOption);
  }
  if (options.length !== 5 || new Set(options.map((option) =>
    [option.provider, option.model ?? "", option.device ?? ""].join(":"))).size !== 5) return null;
  return { settings: settings as SpeechSettings, revision: data.revision as number, options };
}

export function parseKeyStatus(body: unknown): KeyStatus | null {
  if (!record(body) || body.api_version !== "v1" || body.status !== "ok" || !record(body.data)) return null;
  const data = body.data;
  if (typeof data.configured !== "boolean" ||
      !(data.configured ? typeof data.last4 === "string" && data.last4.length === 4 : data.last4 === null) ||
      !(data.updated_at_utc === null || typeof data.updated_at_utc === "string") ||
      ![null, "ok", "auth_failed", "unreachable"].includes(data.verified as string | null)) return null;
  return { configured: data.configured, last4: data.last4 as string | null,
    updated_at_utc: data.updated_at_utc as string | null, verified: data.verified as KeyStatus["verified"] };
}

export function keyStatusLabel(status: KeyStatus): string {
  return status.configured ? "Configured · ends in " + status.last4 : "Not set";
}

export function setupCommand(model: SpeechSettings["local"]["model"], device: SpeechSettings["local"]["device"]): string {
  return "uv run python scripts/setup_ubuntu_speech_runtime.py --model " + model + " --device " + device + " --database <database-path>";
}

export function selectionReady(settings: SpeechSettings, options: SpeechOption[]): boolean {
  return options.some((option) => option.provider === settings.provider && option.status === "ready" &&
    (settings.provider === "openai" || option.model === settings.local.model && option.device === settings.local.device));
}

export function speechSaveFailure(status: number): string {
  return status === 409 ? "Speech settings changed elsewhere. Reload settings before saving again." :
    status === 403 ? "This dashboard is not authorised to change speech settings." :
    "Speech settings could not be saved. Check that the selected provider is ready, then reload.";
}
