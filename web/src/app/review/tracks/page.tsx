import type { Metadata } from "next";
import { liveShellProps, loadShellState } from "@/features/live-status/shell-state";
import { loadLibrary } from "@/features/session-library/load";
import { PersonalBests } from "@/features/session-library/PersonalBests";
import { AppShell, HeaderContext } from "@/features/ui-foundation/AppShell";

export const metadata: Metadata = { title: "Personal Bests · Race Engineer" };

export default async function PersonalBestsPage() {
  const [model, state] = await Promise.all([loadLibrary(), loadShellState()]);
  const shell = liveShellProps(state, "telemetry", model.footer);
  return (
    <AppShell
      active="review"
      live="telemetry"
      status={shell.status}
      context={<HeaderContext items={["Personal bests"]} />}
      footer={shell.footer}
    >
      <PersonalBests sessions={model.sessions} reference={model.mode === "reference"} />
    </AppShell>
  );
}
