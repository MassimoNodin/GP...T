import type { AppSearchParams } from "@/lib/navigation";
import { preservedAppStateQuery } from "@/lib/navigation";
import AppHeader from "../AppHeader";
import SettingsVoicePreferences from "../SettingsVoicePreferences";
import SettingsStorageUsage from "../SettingsStorageUsage";

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
      <main className="page-content settings-page-content" id="main-content" tabIndex={-1}>
        <section className="compare-page-intro settings-page-intro">
          <div className="eyebrow">SETTINGS / LOCAL PREFERENCES</div>
          <h1>
            Tune your
            <br />
            <span>engineer setup.</span>
          </h1>
          <p>
            Voice read-aloud preferences are available in this browser. Service and data settings will appear here only when their controls are ready.
          </p>
        </section>

        <nav className="settings-section-nav panel" aria-label="Settings sections">
          {sections.map((section) => (
            <a key={section.id} href={`#${section.id}`}>
              {section.label}
            </a>
          ))}
        </nav>

        <div className="settings-layout">
          <aside className="settings-overview panel" aria-label="Settings availability">
            <span className="eyebrow">AVAILABILITY</span>
            <h2>One active group</h2>
            <p>Voice &amp; Audio changes only the browser’s local read-aloud presentation.</p>
            <ul>
              <li><span className="settings-availability-dot is-ready" />Voice &amp; Audio <b>Available</b></li>
              <li><span className="settings-availability-dot" />General <b>Planned</b></li>
              <li><span className="settings-availability-dot" />AI Runtime <b>Not configured</b></li>
              <li><span className="settings-availability-dot" />Telemetry <b>Planned</b></li>
              <li><span className="settings-availability-dot" />Devices <b>Planned</b></li>
              <li><span className="settings-availability-dot" />HUD <b>Planned</b></li>
              <li><span className="settings-availability-dot is-ready" />Storage <b>Measured</b></li>
            </ul>
          </aside>

          <div className="settings-content-column">
            <section className="settings-section settings-planned-section panel" id="general">
              <div className="settings-section-heading">
                <div><span className="eyebrow">GENERAL</span><h2>Browser preferences</h2></div>
                <span className="settings-state">PLANNED</span>
              </div>
              <p className="settings-copy">Theme, units, and display density are not configurable yet.</p>
            </section>
            <section className="settings-section settings-planned-section panel" id="ai">
              <div className="settings-section-heading">
                <div><span className="eyebrow">AI RUNTIME</span><h2>Model and privacy</h2></div>
                <span className="settings-state">NOT CONFIGURED</span>
              </div>
              <p className="settings-copy">There is no AI model runtime configured. Deterministic reports remain separate, and this section makes no provider or offline-inference claims.</p>
            </section>

            <SettingsVoicePreferences />

            <section className="settings-section settings-planned-section panel" id="telemetry">
              <div className="settings-section-heading">
                <div><span className="eyebrow">TELEMETRY</span><h2>Local service</h2></div>
                <span className="settings-state">PLANNED</span>
              </div>
              <p className="settings-copy">UDP host, port, and service state are not changed from this page.</p>
            </section>
            <section className="settings-section settings-planned-section panel" id="devices">
              <div className="settings-section-heading">
                <div><span className="eyebrow">DEVICES</span><h2>Input and output</h2></div>
                <span className="settings-state">PLANNED</span>
              </div>
              <p className="settings-copy">Voice output uses the browser-discovered local voice. Device routing, microphone access, and speech input are not available.</p>
            </section>
            <section className="settings-section settings-planned-section panel" id="hud">
              <div className="settings-section-heading">
                <div><span className="eyebrow">HUD</span><h2>Browser display</h2></div>
                <span className="settings-state">PLANNED</span>
              </div>
              <p className="settings-copy">The app has no always-on-top or in-game overlay. A browser HUD surface is planned.</p>
            </section>
            <SettingsStorageUsage />
          </div>
        </div>
      </main>
      <footer className="footer-bar">
        <span>GP...T <b>·</b> LOCAL FIRST</span>
        <span>Browser speech preferences · Read-only storage measurement</span>
      </footer>
    </div>
  );
}
