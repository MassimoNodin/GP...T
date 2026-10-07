import {
  preservedAppStateQuery,
  type AppScreen,
  type AppSearchParams,
} from "@/lib/navigation";
import { isTestDataScenario } from "@/lib/test-data";
import AppHeader from "./AppHeader";
import TestDataWorkspace from "./TestDataWorkspace";

// Branch before mounting any production controllers, evidence readers or pollers.
export function testDataRoute(screen: AppScreen, params: AppSearchParams) {
  if (params.test_data === undefined) return null;
  const query = preservedAppStateQuery(params);
  const validated = new URLSearchParams(query);
  const scenario =
    typeof params.test_data === "string" &&
    isTestDataScenario(params.test_data) &&
    validated.get("test_data") !== "invalid"
      ? params.test_data
      : "invalid";
  return (
    <div className="app-shell">
      <AppHeader active={screen} preservedQuery={query} />
      <main
        className="page-content reference-page-content"
        id="main-content"
        tabIndex={-1}
      >
        <TestDataWorkspace
          key={scenario}
          screen={screen}
          scenario={scenario}
          initialSelection={{
            session: validated.get("test_session") ?? "",
            target: validated.get("test_target") ?? "",
            reference: validated.get("test_reference") ?? "",
          }}
        />
      </main>
    </div>
  );
}
