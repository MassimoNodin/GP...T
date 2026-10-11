import type { Metadata } from "next";
import { DriveDiagnostics } from "@/features/drive-diagnostics/DriveDiagnostics";
import { loadDrive } from "@/features/drive-diagnostics/load";
import { AppShell, HeaderContext } from "@/features/ui-foundation/AppShell";

export const metadata: Metadata = { title: "Drive Diagnostics · Race Engineer" };

export default async function DrivePage() {
  const model = await loadDrive();
  return (
    <AppShell
      active="drive"
      live="engineer"
      status={model.status}
      context={<HeaderContext items={model.header.items} trailing={model.header.trailing} />}
      footer={model.footer}
    >
      <DriveDiagnostics model={model} />
    </AppShell>
  );
}
