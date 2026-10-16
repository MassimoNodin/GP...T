"use client";
import { useEffect, useState } from "react";
import { Button, Card, Select } from "@/features/ui-foundation/primitives";
import { Field } from "./shared";
import { radioStatus, type RadioSettings } from "./radio-settings";
import { useRadioPresence } from "./useRadioPresence";
import styles from "./Settings.module.css";

export function RadioCard() {
  const presence = useRadioPresence();
  const [form, setForm] = useState<RadioSettings | null>(null);
  const [revision, setRevision] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  async function load(signal?: AbortSignal) {
    try {
      const response = await fetch("/api/engineer/radio/settings", { cache: "no-store", signal });
      if (!response.ok) throw new Error("unavailable");
      const { data } = await response.json();
      const { revision: loadedRevision, schema_version: schema, ...settings } = data;
      if (schema !== 1 || !Number.isInteger(loadedRevision)) throw new Error("invalid");
      setForm(settings); setRevision(loadedRevision); setConflict(false); setMessage("");
    } catch { if (!signal?.aborted) setMessage("Radio settings could not be loaded. Try again."); }
  }
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, []);
  function edit(key: keyof RadioSettings, value: string | number | null) {
    setForm((current) => current ? { ...current, [key]: value,
      ...(key === "answer_mode" && value === "grounded" ? { intent_mode: "luna" } : {}),
    } : current);
    if (!conflict) setMessage("");
  }
  async function save() {
    if (!form || revision === null) return;
    setSaving(true);
    try {
      const response = await fetch("/api/engineer/radio/settings", {
        method: "PUT", headers: { "Content-Type": "application/json", "If-Match": `"${revision}"` },
        body: JSON.stringify(form), signal: AbortSignal.timeout(10000),
      });
      if (response.status === 409) { setConflict(true); setMessage("Settings changed elsewhere. Load latest before saving."); return; }
      if (!response.ok) { setMessage(response.status === 403 ? "Saving is not authorized." : "Settings were rejected or the engine is unavailable."); return; }
      const { data } = await response.json();
      setRevision(data.revision); setMessage("Saved. The running radio applies changes at its next natural boundary.");
    } catch { setMessage("The engine could not be reached. Your edits have been kept."); }
    finally { setSaving(false); }
  }
  return <Card title="Radio" labelledBy="radio-settings">
    <p className={styles.cardDesc} role="status">{radioStatus(presence, revision)}</p>
    <p className={styles.help}>Saved values override radio command-line defaults. Active speech and captures continue unchanged.</p>
    <Button onClick={() => { void fetch("/api/engineer/radio/presence/refresh", { method: "PUT", headers: { "Content-Type": "application/json" }, body: "{}" }).then((response) => setMessage(response.ok ? "Device refresh requested." : "Device refresh was not authorized or the engine is unavailable.")).catch(() => setMessage("Device refresh could not reach the engine.")); }}>Refresh radio devices</Button>
    {form && <fieldset disabled={saving} style={{ border: 0, padding: 0, margin: 0 }}>
      {([
        ["proactive_mode", "Proactive mode", ["off", "rules", "model"]],
        ["answer_mode", "Answer mode", ["intent", "grounded"]],
        ["tts_engine", "Voice engine", ["edge", "windows"]],
      ] as const).map(([key, label, choices]) => <Field key={key} label={label} help="">
        <Select aria-label={label} value={form[key]} onChange={(event) => edit(key, event.target.value)}>
          {choices.map((value) => <option key={value} value={value}>{value}</option>)}
        </Select>
      </Field>)}
      <Field label="Radio voice" help=""><Select aria-label="Radio voice" value={form.voice ?? ""} onChange={(event) => edit("voice", event.target.value || null)}>
        <option value="">Engine default</option>
        {Array.from(new Set([...(presence.tts_engine === form.tts_engine ? presence.voices ?? [] : []), ...(form.voice ? [form.voice] : [])])).map((value) => <option key={value} value={value}>{value}</option>)}
      </Select></Field>
      {([ ["microphone", "Radio microphone", "input"], ["cue_output", "Cue output", "output"] ] as const).map(([key, label, direction]) => <Field key={key} label={label} help="">
        <Select aria-label={label} value={form[key] ?? ""} onChange={(event) => edit(key, event.target.value === "" ? null : Number(event.target.value))}>
          <option value="">System default</option>
          {form[key] !== null && !presence.devices?.some((device) => device[direction] && device.index === form[key]) && <option value={form[key]!}>Device {form[key]} (not currently available)</option>}
          {presence.devices?.filter((device) => device[direction]).map((device) => <option key={device.index} value={device.index}>{device.name}</option>)}
        </Select>
      </Field>)}
      {([ ["rate", "Radio rate", -10, 10, 1], ["volume", "Radio volume", 0, 100, 1],
        ["cue_volume", "Cue volume", 0.01, 0.3, 0.01], ["capture_seconds", "Capture length (seconds)", 1, 12, 0.5] ] as const).map(([key, label, min, max, step]) => <Field key={key} label={label} help="">
        <input aria-label={label} type="range" min={min} max={max} step={step} value={form[key]} onChange={(event) => edit(key, Number(event.target.value))} /> <output>{form[key]}</output>
      </Field>)}
      <Button disabled={conflict || saving} onClick={() => void save()}>{saving ? "Saving..." : "Save radio settings"}</Button>
    </fieldset>}
    {(!form || conflict) && <Button disabled={saving} onClick={() => void load()}>Load latest</Button>}
    {message && <p role="status" className={styles.footnote}>{message}</p>}
  </Card>;
}
