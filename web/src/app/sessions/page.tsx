import type { AppSearchParams } from "@/lib/navigation";
import { preservedAppStateQuery } from "@/lib/navigation";
import AppHeader from "../AppHeader";
import { testDataRoute } from "../TestDataRoute";
import SessionsRunEvidence from "../SessionsRunEvidence";
import ReferenceScreen from "../ReferenceScreens";

export default async function SessionsPage({
  searchParams,
}: {
  searchParams: Promise<AppSearchParams>;
}) {
  const params = await searchParams;
  const syntheticPage = testDataRoute("sessions", params);
  if (syntheticPage) return syntheticPage;
  const preservedQuery = preservedAppStateQuery(params);

  return (
    <div className="app-shell">
      <AppHeader active="sessions" preservedQuery={preservedQuery} />
      <main className="page-content reference-page-content" id="main-content" tabIndex={-1}>
        <ReferenceScreen screen="sessions" preservedQuery={preservedQuery} />
        <details className="ref-workflow-details" id="sessions-workflow">
          <summary id="sessions-workflow-trigger">Open the imported-session evidence workflow</summary>
          <div className="ref-workflow-content">
        <section className="sessions-page-intro">
          <div className="eyebrow">SESSIONS / IMPORTED RUN EVIDENCE</div>
          <h1>
            Every run.
            <br />
            <span>Evidence intact.</span>
          </h1>
          <p>
            Browse imported captures, inspect their stored sessions and
            attempts, and follow the exact evidence behind each run.
          </p>
        </section>
        <SessionsRunEvidence
          searchParams={Promise.resolve(params)}
          screen="sessions"
        />
          </div>
        </details>
      </main>
      <footer className="footer-bar">
        <span>
          GP...T <b>·</b> LOCAL FIRST
        </span>
        <span>Imported session evidence · Read only</span>
      </footer>
    </div>
  );
}
