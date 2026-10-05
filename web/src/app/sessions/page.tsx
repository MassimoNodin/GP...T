import type { AppSearchParams } from "@/lib/navigation";
import { preservedAppStateQuery } from "@/lib/navigation";
import AppHeader from "../AppHeader";
import SessionsRunEvidence from "../SessionsRunEvidence";

export default async function SessionsPage({
  searchParams,
}: {
  searchParams: Promise<AppSearchParams>;
}) {
  const params = await searchParams;
  const preservedQuery = preservedAppStateQuery(params);

  return (
    <div className="app-shell">
      <AppHeader active="sessions" preservedQuery={preservedQuery} />
      <main className="page-content" id="main-content" tabIndex={-1}>
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
