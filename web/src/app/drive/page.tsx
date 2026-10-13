import type { Metadata } from "next";
import { DriveDiagnostics } from "@/features/drive-diagnostics/DriveDiagnostics";
import { loadDrive } from "@/features/drive-diagnostics/load";
import { liveShellProps, loadShellState } from "@/features/live-status/shell-state";
import { AppShell, HeaderContext } from "@/features/ui-foundation/AppShell";

export const metadata: Metadata = { title: "Drive Diagnostics · Race Engineer" };

export default async function DrivePage() {
  const [model, state] = await Promise.all([loadDrive(), loadShellState()]);
  const shell = liveShellProps(state, "engineer", model.footer);
  return (
    <AppShell
      active="drive"
      live="engineer"
      status={shell.status}
      context={<HeaderContext items={model.header.items} trailing={model.header.trailing} />}
      footer={shell.footer}
    >
      <DriveDiagnostics model={model} />
    </AppShell>
  );
}
