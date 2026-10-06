import type { AppSearchParams } from "@/lib/navigation";
import { preservedAppStateQuery } from "@/lib/navigation";
import AppHeader from "../AppHeader";
import SettingsVoicePreferences from "../SettingsVoicePreferences";
import SettingsStorageUsage from "../SettingsStorageUsage";
import SettingsHudPreferences from "../SettingsHudPreferences";
import SettingsTelemetryService from "../SettingsTelemetryService";
import SettingsAIRuntime from "./SettingsAIRuntime";

const sections = [
  { id: "general", label: "General" },
  { id: "ai", label: "AI Runtime" },
  { id: "voice-audio", label: "Voice & Audio" },
  { id: "telemetry", label: "Telemetry" },
  { id: "devices", label: "Devices" },
  { id: "hud", label: "HUD" },
  { id: "storage", label: "Storage" },
];

export default async function SettingsPage({
  searchParams,
}: {
  searchParams: Promise<AppSearchParams>;
}) {
  const params = await searchParams;
  const preservedQuery = preservedAppStateQuery(params);

  return (
    <div className="app-shell">
      <AppHeader active="settings" preservedQuery={preservedQuery} />
      <main
        className="page-content settings-page-content"
        id="main-content"
        tabIndex={-1}
      >
        <section className="compare-page-intro settings-page-intro">
          <div className="eyebrow">SETTINGS / LOCAL PREFERENCES</div>
          <h1>
            Tune your
            <br />
            <span>engineer setup.</span>
          </h1>
          <p>
            AI runtime status, voice preferences, telemetry service status, and
            managed storage are available here. Other sections remain clearly
            marked until their controls are ready.
          </p>
        </section>

        <nav
          className="settings-section-nav panel"
          aria-label="Settings sections"
        >
          {sections.map((section) => (
            <a key={section.id} href={`#${section.id}`}>
              {section.label}
            </a>
          ))}
        </nav>

        <div className="settings-layout">
          <aside
            className="settings-overview panel"
            aria-label="Settings availability"
          >
            <span className="eyebrow">AVAILABILITY</span>
            <h2>Local status</h2>
            <p>
              Voice preferences stay in this browser. Runtime, telemetry, and
              storage status are observed from local services.
            </p>
            <ul>
              <li>
                <span className="settings-availability-dot is-ready" />
                Voice &amp; Audio <b>Available</b>
              </li>
              <li>
                <span className="settings-availability-dot" />
                General <b>Planned</b>
              </li>
              <li>
                <span className="settings-availability-dot" />
                AI Runtime <b>Live status</b>
              </li>
              <li>
                <span className="settings-availability-dot is-ready" />
                Telemetry <b>Read-only</b>
              </li>
              <li>
                <span className="settings-availability-dot" />
                Devices <b>Planned</b>
              </li>
              <li>
                <span className="settings-availability-dot is-ready" />
                HUD <b>Available</b>
              </li>
              <li>
                <span className="settings-availability-dot is-ready" />
                Storage <b>Measured</b>
              </li>
            </ul>
          </aside>

          <div className="settings-content-column">
            <section
              className="settings-section settings-planned-section panel"
              id="general"
            >
              <div className="settings-section-heading">
                <div>
                  <span className="eyebrow">GENERAL</span>
                  <h2>Browser preferences</h2>
                </div>
                <span className="settings-state">PLANNED</span>
              </div>
              <p className="settings-copy">
                Theme, units, and display density are not configurable yet.
              </p>
            </section>
            <SettingsAIRuntime />

            <SettingsVoicePreferences />

            <SettingsTelemetryService />
            <section
              className="settings-section settings-planned-section panel"
              id="devices"
            >
              <div className="settings-section-heading">
                <div>
                  <span className="eyebrow">DEVICES</span>
                  <h2>Input and output</h2>
                </div>
                <span className="settings-state">PLANNED</span>
              </div>
              <p className="settings-copy">
                Voice output uses the browser-discovered local voice. Device
                routing, microphone access, and speech input are not available.
              </p>
            </section>
            <SettingsHudPreferences />
            <SettingsStorageUsage />
          </div>
        </div>
      </main>
      <footer className="footer-bar">
        <span>
          GP...T <b>·</b> LOCAL FIRST
        </span>
        <span>
          Browser speech preferences · Read-only telemetry and storage
        </span>
      </footer>
    </div>
  );
}
