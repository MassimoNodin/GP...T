import Dashboard from "./screens/DashboardScreen";
import Live from "./screens/LiveScreen";
import Engineer from "./screens/EngineerScreen";
import Compare from "./screens/CompareScreen";
import Settings from "./screens/SettingsScreen";
import Recordings from "./screens/RecordingsScreen";
import Track from "./screens/TrackScreen";
import Sessions from "./screens/SessionsScreen";

export type ReferenceScreenId =
  | "dashboard"
  | "live"
  | "engineer"
  | "compare"
  | "settings"
  | "recordings"
  | "track"
  | "sessions";

export default function ReferenceScreen({
  screen,
  preservedQuery,
}: {
  screen: ReferenceScreenId;
  preservedQuery: string;
}) {
  const content = {
    dashboard: <Dashboard preservedQuery={preservedQuery} />,
    live: <Live preservedQuery={preservedQuery} />,
    engineer: <Engineer preservedQuery={preservedQuery} />,
    compare: <Compare preservedQuery={preservedQuery} />,
    settings: <Settings preservedQuery={preservedQuery} />,
    recordings: <Recordings preservedQuery={preservedQuery} />,
    track: <Track preservedQuery={preservedQuery} />,
    sessions: <Sessions preservedQuery={preservedQuery} />,
  }[screen];

  return (
    <section className={`reference-screen reference-${screen}`} aria-label={`${screen} screen`}>
      {content}
    </section>
  );
}
