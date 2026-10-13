"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";
import type { ShellFooter, ShellStatus } from "@/features/ui-foundation/AppShell";
import { useAcquisitionFeed, useRuntimeFeed } from "./hooks";
import { combinedStatus, sessionFooter, telemetryDetail } from "./map";
import { ShellFooterBlock, StatusPill } from "./ShellParts";

export type LiveMode = "telemetry" | "engineer";

type ShellState = { status: ShellStatus; footer: ShellFooter };

const ShellContext = createContext<ShellState | null>(null);

/**
 * Provides the header pill and sidebar footer for one shell. It starts from the
 * server-rendered props (so SSR and hydration match) and replaces them once the
 * browser has polled both the receiver and the engineer runtime. Every screen
 * shows the same combined pill; `mode` only decides the footer's second line:
 * the receiver detail on telemetry screens, the page's session lines otherwise.
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
  const acquisition = useAcquisitionFeed(undefined);
  const runtime = useRuntimeFeed(undefined);
  const state = useMemo<ShellState>(() => {
    // Both halves are needed before the pill can say anything new.
    if (acquisition === undefined || runtime === undefined) return { status, footer };
    const lines = mode === "telemetry" ? [footer.lines[0] ?? "", telemetryDetail(acquisition)] : footer.lines;
    return {
      status: { ...combinedStatus(acquisition, runtime), menu: status.menu },
      footer: sessionFooter(acquisition, runtime, lines),
    };
  }, [acquisition, runtime, mode, status, footer]);
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
