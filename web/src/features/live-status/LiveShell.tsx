"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";
import type { ShellFooter, ShellStatus } from "@/features/ui-foundation/AppShell";
import { useAcquisitionFeed, useRuntimeFeed } from "./hooks";
import { ENGINE_OFFLINE_FOOTER, engineerFooter, engineerStatus, telemetryFooter, telemetryStatus } from "./map";
import { ShellFooterBlock, StatusPill } from "./ShellParts";

export type LiveMode = "telemetry" | "engineer";

type ShellState = { status: ShellStatus; footer: ShellFooter };

const ShellContext = createContext<ShellState | null>(null);

/**
 * Provides the header pill and sidebar footer for one shell. It starts from the
 * server-rendered props (so SSR and hydration match) and replaces them once the
 * browser has polled live state.
 */
export function LiveShellProvider({
  mode,
  status,
  footer,
  children,
}: {
  mode: LiveMode;
  status: ShellStatus;
  footer: ShellFooter;
  children: ReactNode;
}) {
  return mode === "engineer" ? (
    <EngineerProvider status={status} footer={footer}>
      {children}
    </EngineerProvider>
  ) : (
    <TelemetryProvider status={status} footer={footer}>
      {children}
    </TelemetryProvider>
  );
}

type ProviderProps = { status: ShellStatus; footer: ShellFooter; children: ReactNode };

function TelemetryProvider({ status, footer, children }: ProviderProps) {
  const acquisition = useAcquisitionFeed(undefined);
  const firstLine = footer.lines[0] ?? "";
  const state = useMemo<ShellState>(
    () =>
      acquisition === undefined
        ? { status, footer }
        : {
            status: { ...telemetryStatus(acquisition), menu: status.menu },
            footer: telemetryFooter(acquisition, firstLine),
          },
    [acquisition, status, footer, firstLine],
  );
  return <ShellContext.Provider value={state}>{children}</ShellContext.Provider>;
}

function EngineerProvider({ status, footer, children }: ProviderProps) {
  const runtime = useRuntimeFeed(undefined);
  const state = useMemo<ShellState>(
    () =>
      runtime === undefined
        ? { status, footer }
        : {
            status: { ...engineerStatus(runtime), menu: status.menu },
            // Unreachable: the session lines are stale, so say what to do instead.
            footer: runtime === null ? ENGINE_OFFLINE_FOOTER : engineerFooter(runtime, footer.lines),
          },
    [runtime, status, footer],
  );
  return <ShellContext.Provider value={state}>{children}</ShellContext.Provider>;
}

export function LiveStatusPill({ fallback }: { fallback: ShellStatus }) {
  const state = useContext(ShellContext);
  return <StatusPill status={state?.status ?? fallback} />;
}

export function LiveShellFooter({ fallback }: { fallback: ShellFooter }) {
  const state = useContext(ShellContext);
  return <ShellFooterBlock footer={state?.footer ?? fallback} />;
}
