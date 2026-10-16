"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Card } from "@/features/ui-foundation/primitives";
import { Field } from "./shared";
import { keyStatusLabel, parseKeyStatus, type KeyStatus } from "./speech-settings";
import styles from "./Settings.module.css";
import tabStyles from "./SettingsTabs.module.css";

const endpoint = "/api/secrets/openai-api-key";

export function ApiKeysCard() {
  const router = useRouter();
  const [status, setStatus] = useState<KeyStatus | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void fetch(endpoint, { cache: "no-store", signal: controller.signal }).then(async (response) => {
      const parsed = response.ok ? parseKeyStatus(await response.json()) : null;
      if (!controller.signal.aborted) {
        setStatus(parsed);
        if (!parsed) setMessage("API key status could not be read. Reload to try again.");
      }
    }).catch(() => { if (!controller.signal.aborted) setMessage("API key status could not be read."); });
    return () => controller.abort();
  }, []);

  async function mutate(action: "save" | "test" | "remove") {
    setBusy(true);
    setMessage(null);
    const submitted = key;
    setKey("");
    try {
      const response = await fetch(endpoint, {
        method: action === "remove" ? "DELETE" : "PUT",
        headers: { "content-type": "application/json" },
        body: action === "remove" ? undefined : JSON.stringify(
          action === "test" && !submitted ? { verify: true } : { key: submitted, verify: action === "test" }),
      });
      const parsed = response.ok ? parseKeyStatus(await response.json()) : null;
      if (!parsed) setMessage("The API key could not be updated. Check the key format and engine control access.");
      else {
        setStatus(parsed);
        setMessage(action === "remove" ? "API key removed." : parsed.verified === "ok" ? "API key verified." :
          parsed.verified === "auth_failed" ? "OpenAI rejected this key. Replace it before using cloud speech." :
          parsed.verified === "unreachable" ? "Key saved, but OpenAI could not be reached. Try Test again." : "API key saved.");
        router.refresh();
      }
    } catch { setMessage("The local engine could not be reached."); }
    finally { setBusy(false); }
  }

  return <Card title="API keys" labelledBy="api-keys">
    <p className={styles.cardDesc}>Used by Speech recognition (OpenAI). Keys stay in the engine data folder.</p>
    <p role="status">{status ? keyStatusLabel(status) : "Checking API key status"}</p>
    <Field label="OpenAI API key" help="Enter a replacement key. The saved key is never returned to the dashboard.">
      <input type="password" aria-label="OpenAI API key" autoComplete="off" spellCheck={false}
        value={key} onChange={(event) => setKey(event.target.value)} disabled={busy} maxLength={512} />
    </Field>
    <div className={tabStyles.row}>
      <Button disabled={busy || !key} onClick={() => void mutate("save")}>Save</Button>
      <Button disabled={busy || !key && !status?.configured} onClick={() => void mutate("test")}>Test</Button>
      <Button disabled={busy || !status?.configured} onClick={() => void mutate("remove")}>Remove</Button>
    </div>
    {message && <p role="status" className={styles.footnote}>{message}</p>}
    <p className={styles.footnote}>POSIX key files are owner-only. On Windows, access relies on the data folder ACL.</p>
  </Card>;
}
