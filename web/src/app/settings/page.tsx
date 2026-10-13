import type { Metadata } from "next";
import { liveShellProps, loadShellState } from "@/features/live-status/shell-state";
import { loadSettings } from "@/features/settings/load";
import { parseSection } from "@/features/settings/sections";
import { Settings } from "@/features/settings/Settings";
import { AppShell, HeaderContext } from "@/features/ui-foundation/AppShell";

export const metadata: Metadata = { title: "Settings · Race Engineer" };

export default async function SettingsPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const [model, query, state] = await Promise.all([loadSettings(), searchParams, loadShellState()]);
  const shell = liveShellProps(state, "engineer", { tone: model.pill.tone, label: model.pill.label, lines: model.footerLines });
  return (
    <AppShell
      active="settings"
      live="engineer"
      status={shell.status}
      context={<HeaderContext items={model.header.items} />}
      footer={shell.footer}
    >
      <Settings model={model} initialSection={parseSection(query.section)} />
    </AppShell>
  );
}
