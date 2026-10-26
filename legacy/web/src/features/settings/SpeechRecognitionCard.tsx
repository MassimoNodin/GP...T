"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Button, Card, Select } from "@/features/ui-foundation/primitives";
import { Segmented } from "@/features/ui-foundation/Segmented";
import { Field } from "./shared";
import { parseSpeechDocument, selectionReady, setupCommand, speechSaveFailure,
  type SpeechDocument, type SpeechSettings } from "./speech-settings";
import styles from "./Settings.module.css";
import tabStyles from "./SettingsTabs.module.css";

export function SpeechRecognitionCard() {
  const router = useRouter();
  const [document, setDocument] = useState<SpeechDocument | null>(null);
  const [draft, setDraft] = useState<SpeechSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function reload(signal?: AbortSignal) {
    setBusy(true);
    try {
      const response = await fetch("/api/engineer/speech", { cache: "no-store", signal });
      const parsed = response.ok ? parseSpeechDocument(await response.json()) : null;
      if (signal?.aborted) return;
      if (!parsed) setMessage("Speech settings could not be read. Reload to try again.");
      else { setDocument(parsed); setDraft(parsed.settings); setConflict(false); setMessage(null); }
    } catch { if (!signal?.aborted) setMessage("The local engine could not be reached."); }
    finally { if (!signal?.aborted) setBusy(false); }
  }

  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, []);

  async function save() {
    if (!draft || !document) return;
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch("/api/engineer/speech", {
        method: "PUT", headers: { "content-type": "application/json", "if-match": String(document.revision) },
        body: JSON.stringify(draft),
      });
      const parsed = response.ok ? parseSpeechDocument(await response.json()) : null;
      if (!parsed) { setConflict(response.status === 409); setMessage(speechSaveFailure(response.status)); }
      else { setDocument(parsed); setDraft(parsed.settings); setMessage("Speech settings saved. New clips use this provider."); router.refresh(); }
    } catch { setMessage("The local engine could not be reached."); }
    finally { setBusy(false); }
  }

  const cloudReady = document?.options.some((option) => option.provider === "openai" && option.status === "ready");
  const localReady = (model: string, device?: string) => document?.options.some((option) =>
    option.provider === "local" && option.model === model && (!device || option.device === device) && option.status === "ready");

  return <Card title="Speech recognition" labelledBy="voice-speech-recognition">
    <p className={styles.cardDesc}>Choose how new microphone clips become text. No restart is needed.</p>
    {draft && document && <fieldset className={styles.fieldset} disabled={busy}>
      <legend className={styles.label}>Provider</legend>
      <Segmented label="Speech provider" value={draft.provider} disabled={busy}
        items={[{ key: "local", label: "Local Whisper" }, { key: "openai", label: "OpenAI", disabled: !cloudReady }]}
        onChange={(provider) => setDraft({ ...draft, provider })} />
      {!cloudReady && <p className={styles.help}>OpenAI needs an API key. <Link href="/settings?section=connections">Configure API keys in Connections</Link>.</p>}
      {draft.provider === "local" ? <>
        <Field label="Model" help="Only installed, verified variants can be selected."><Select aria-label="Speech model" value={draft.local.model}
          onChange={(event) => {
            const model = event.target.value as SpeechSettings["local"]["model"];
            const device = localReady(model, draft.local.device) ? draft.local.device :
              localReady(model, "cpu") ? "cpu" : "cuda";
            setDraft({ ...draft, local: { model, device } });
          }}>
          <option value="tiny.en" disabled={!localReady("tiny.en")}>tiny.en</option>
          <option value="large-v3" disabled={!localReady("large-v3")}>large-v3</option>
        </Select></Field>
        <Field label="Device" help="GPU requires a pinned CUDA runtime."><Select aria-label="Speech device" value={draft.local.device}
          onChange={(event) => setDraft({ ...draft, local: { ...draft.local, device: event.target.value as SpeechSettings["local"]["device"] } })}>
          <option value="cpu" disabled={!localReady(draft.local.model, "cpu")}>CPU</option>
          <option value="cuda" disabled={!localReady(draft.local.model, "cuda")}>GPU (CUDA)</option>
        </Select></Field>
        <p className={styles.help}>Recognition runs on this computer. Nothing is sent to a cloud service.</p>
      </> : <p className={styles.help}>Audio is sent to OpenAI. OpenAI API billing applies.</p>}
      {document.options.filter((option) => option.provider === "local" && option.status !== "ready").map((option) =>
        <div key={String(option.model) + option.device} className={styles.footnote}>
          <p>{option.model} / {option.device === "cuda" ? "GPU (CUDA)" : "CPU"}: {option.reason ?? option.status}</p>
          <code>{setupCommand(option.model!, option.device!)}</code>
        </div>)}
    </fieldset>}
    <div className={tabStyles.row}>
      <Button onClick={() => void save()} disabled={busy || conflict || !draft || !document || !selectionReady(draft, document.options)}>Save</Button>
      <Button onClick={() => void reload()} disabled={busy}>Reload settings</Button>
    </div>
    {message && <p role="status" className={styles.footnote}>{message}</p>}
  </Card>;
}
