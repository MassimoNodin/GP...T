import type { Metadata } from "next";
import { loadLibrary } from "@/features/session-library/load";
import { SessionLibrary } from "@/features/session-library/SessionLibrary";
import { liveShellProps, loadShellState } from "@/features/live-status/shell-state";
import { AppShell, HeaderContext } from "@/features/ui-foundation/AppShell";

export const metadata: Metadata = { title: "Session Library · Race Engineer" };

export default async function ReviewPage() {
  const [model, state] = await Promise.all([loadLibrary(), loadShellState()]);
  const shell = liveShellProps(state, "telemetry", model.footer);
  return (
    <AppShell
      active="review"
      live="telemetry"
      status={shell.status}
      context={<HeaderContext items={model.header.items} trailing={model.header.trailing} />}
      footer={shell.footer}
    >
      <SessionLibrary model={model} />
    </AppShell>
  );
}
