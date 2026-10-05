"use client";

import { useState, useSyncExternalStore, type CSSProperties } from "react";
import {
  applyLocalHudPreferences,
  DEFAULT_HUD_PREFERENCES,
  getLocalHudPreferenceSnapshot,
  getServerLocalHudPreferenceSnapshot,
  subscribeToLocalHudPreferences,
  type HudAlignment,
  type HudPreferences,
  type HudTheme,
} from "@/lib/local-hud-preferences";

const alignments: { value: HudAlignment; label: string }[] = [
  { value: "top-left", label: "Top left" },
  { value: "top-right", label: "Top right" },
  { value: "bottom-left", label: "Bottom left" },
  { value: "bottom-right", label: "Bottom right" },
];

export default function SettingsHudPreferences() {
  const preferenceState = useSyncExternalStore(
    subscribeToLocalHudPreferences,
    getLocalHudPreferenceSnapshot,
    getServerLocalHudPreferenceSnapshot,
  );
  const [draftOverride, setDraftOverride] = useState<HudPreferences | null>(
    null,
  );
  const draft = draftOverride ?? preferenceState.effective;
  const pending =
    draftOverride !== null &&
    !samePreferences(draft, preferenceState.effective);

  function updateDraft(next: HudPreferences) {
    setDraftOverride(
      samePreferences(next, preferenceState.effective) ? null : next,
    );
  }

  function applyDraft() {
    if (applyLocalHudPreferences(draft).ok) setDraftOverride(null);
  }

  function cancelDraft() {
    setDraftOverride(null);
  }

  function resetToDefaults() {
    if (applyLocalHudPreferences(DEFAULT_HUD_PREFERENCES).ok)
      setDraftOverride(null);
  }

  const statusText =
    preferenceState.message ??
    (preferenceState.status === "stored"
      ? "Loaded from this browser."
      : preferenceState.status === "available"
        ? "No saved browser preference record. Defaults are active."
        : "Checking browser storage.");

  return (
    <section
      className="settings-section settings-hud-section panel"
      id="hud"
      aria-labelledby="settings-hud-title"
    >
      <div className="settings-section-heading">
        <div>
          <span className="eyebrow">HUD · BROWSER ONLY</span>
          <h2 id="settings-hud-title">Browser display</h2>
        </div>
        <span
          className={"settings-state settings-state-" + preferenceState.status}
        >
          {preferenceState.status === "stored"
            ? "SAVED HERE"
            : preferenceState.status.replaceAll("_", " ").toUpperCase()}
        </span>
      </div>
      <p className="settings-copy">
        These settings affect the compact HUD inside a browser tab. They do not
        position a window over the game or change telemetry collection.
      </p>
      <fieldset className="hud-alignment-control">
        <legend>Panel corner inside the browser</legend>
        <div className="hud-alignment-grid">
          {alignments.map(({ value, label }) => (
            <label key={value}>
              <input
                type="radio"
                name="hud-alignment"
                value={value}
                checked={draft.alignment === value}
                onChange={() => updateDraft({ ...draft, alignment: value })}
              />
              <span>{label}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="settings-voice-grid hud-range-grid">
        <label className="settings-range-control">
          <span>
            PANEL OPACITY{" "}
            <output>{Math.round(draft.background_opacity * 100)}%</output>
          </span>
          <input
            type="range"
            min="0.6"
            max="1"
            step="0.05"
            value={draft.background_opacity}
            onChange={(event) =>
              updateDraft({
                ...draft,
                background_opacity: Number(event.currentTarget.value),
              })
            }
            aria-label="HUD panel background opacity"
          />
          <small>Text stays fully opaque for readability.</small>
        </label>
        <label className="settings-range-control">
          <span>
            TEXT SIZE <output>{draft.text_scale.toFixed(1)}×</output>
          </span>
          <input
            type="range"
            min="0.8"
            max="1.5"
            step="0.1"
            value={draft.text_scale}
            onChange={(event) =>
              updateDraft({
                ...draft,
                text_scale: Number(event.currentTarget.value),
              })
            }
            aria-label="HUD text size"
          />
          <small>0.8× compact · 1.5× larger</small>
        </label>
      </div>
      <fieldset className="hud-theme-control">
        <legend>Theme</legend>
        {(["dark", "light"] as HudTheme[]).map((theme) => (
          <label key={theme}>
            <input
              type="radio"
              name="hud-theme"
              value={theme}
              checked={draft.theme === theme}
              onChange={() => updateDraft({ ...draft, theme })}
            />
            <span>{theme === "dark" ? "Dark" : "Light"}</span>
          </label>
        ))}
      </fieldset>
      <div
        className={"browser-hud-preview hud-theme-" + draft.theme}
        style={
          {
            "--hud-opacity": String(draft.background_opacity),
            "--hud-base-font-size": `${16 * draft.text_scale}px`,
          } as CSSProperties
        }
      >
        <span className="eyebrow">PREVIEW · BROWSER HUD</span>
        <strong>
          238 km/h <span>·</span> 7th gear
        </strong>
        <small>FRESH · age 0.1 s</small>
      </div>
      <div className="settings-voice-actions">
        <button className="import-button" type="button" onClick={applyDraft}>
          Apply
        </button>
        <button
          className="import-button secondary-import-button"
          type="button"
          onClick={cancelDraft}
          disabled={!pending}
        >
          Cancel
        </button>
        <button
          className="import-button secondary-import-button"
          type="button"
          onClick={resetToDefaults}
        >
          Reset
        </button>
        <span role="status" aria-live="polite">
          {statusText}
        </span>
      </div>
    </section>
  );
}

function samePreferences(first: HudPreferences, second: HudPreferences) {
  return (
    first.alignment === second.alignment &&
    first.background_opacity === second.background_opacity &&
    first.text_scale === second.text_scale &&
    first.theme === second.theme
  );
}
