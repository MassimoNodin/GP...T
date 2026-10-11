import type { Metadata } from "next";
import { DriveDiagnostics } from "@/features/drive-diagnostics/DriveDiagnostics";
import { loadDrive } from "@/features/drive-diagnostics/load";
import { shellFixture } from "@/features/review-data/fixtures";
import { AppShell, HeaderContext } from "@/features/ui-foundation/AppShell";

export const metadata: Metadata = { title: "Drive Diagnostics · Race Engineer" };

export default async function DrivePage() {
  const model = await loadDrive();
  return (
    <AppShell
      active="drive"
      status={shellFixture.engineerRunning}
      context={<HeaderContext items={model.header.items} trailing={model.header.trailing} />}
      footer={shellFixture.sessionFooter}
    >
      <DriveDiagnostics model={model} />
    </AppShell>
  );
}
