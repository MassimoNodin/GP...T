"use client";

import { useEffect, useRef, useState } from "react";
import { InlineAlert } from "@/features/ui-foundation/states";
import { ProgressBar } from "@/features/ui-foundation/ProgressBar";
import { Badge, Button, Card } from "@/features/ui-foundation/primitives";
import {
  CONNECTION_TEST_SECONDS,
  connectionTestOutcome,
  type ConnectionTestResult,
} from "./connections";
import { parseReceiverInfo } from "./display";
import type { SettingsModel } from "./load";
import { KeyValues, RuntimeChip } from "./shared";
import styles from "./Settings.module.css";
import tabStyles from "./SettingsTabs.module.css";

type Phase = { kind: "idle" } | { kind: "running"; elapsed: number } | { kind: "done"; result: ConnectionTestResult };

async function readReceiver(signal: AbortSignal) {
  try {
    const response = await fetch("/api/session-evidence/status", { cache: "no-store", signal });
    if (!response.ok) return null;
    return parseReceiverInfo(await response.json());
  } catch {
    return null;
  }
}

/** Watches the packet counter for ten seconds and reports whether the game is sending. */
function useConnectionTest() {
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const controller = useRef<AbortController | null>(null);

  useEffect(() => () => controller.current?.abort(), []);

  async function start() {
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    setPhase({ kind: "running", elapsed: 0 });
    const first = await readReceiver(abort.signal);
    if (abort.signal.aborted) return;
    let latest = first;
    const began = performance.now();
    while (!abort.signal.aborted) {
      const elapsed = (performance.now() - began) / 1000;
      if (elapsed >= CONNECTION_TEST_SECONDS) break;
      setPhase({ kind: "running", elapsed });
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
      if (abort.signal.aborted) return;
      const next = await readReceiver(abort.signal);
      if (abort.signal.aborted) return;
      if (next) latest = next;
    }
    setPhase({
      kind: "done",
      result: connectionTestOutcome({
        baseline: first?.received ?? null,
        latest: latest?.received ?? null,
        state: latest?.state ?? null,
        host: latest?.host ?? first?.host ?? null,
        port: latest?.port ?? first?.port ?? null,
      }),
    });
  }

  return { phase, start };
}

export function ConnectionsPanel({ model }: { model: SettingsModel }) {
  const { runtime, receiver, speech } = model;
  const test = useConnectionTest();
  const running = test.phase.kind === "running";
  return (
    <div className={styles.grid}>
      <Card title="Game Telemetry" labelledBy="game-telemetry">
        <p className={styles.cardDesc}>Set the game&apos;s UDP telemetry destination to this computer.</p>
        <KeyValues rows={receiver.rows} />
        <div className={tabStyles.row}>
          <Button onClick={() => void test.start()} disabled={running} aria-busy={running || undefined}>
            {running ? "Testing…" : "Test connection"}
          </Button>
          <span className={tabStyles.hint}>Watches for packets for {CONNECTION_TEST_SECONDS} s</span>
        </div>
        {test.phase.kind === "running" && (
          <div className={tabStyles.mt}>
            <ProgressBar
              value={(Math.min(CONNECTION_TEST_SECONDS, test.phase.elapsed) / CONNECTION_TEST_SECONDS) * 100}
              label="Connection test progress"
            />
          </div>
        )}
        {test.phase.kind === "done" && (
          <div className={tabStyles.mt}>
            <InlineAlert tone={test.phase.result.tone}>{test.phase.result.text}</InlineAlert>
          </div>
        )}
        <p className={styles.footnote}>
          {receiver.available
            ? receiver.detail
            : "Telemetry details could not be read because the local engine did not answer."}
        </p>
      </Card>
      <div className={styles.rightCol}>
        <Card title="Engineer Runtime" labelledBy="engineer-runtime" aside={<RuntimeChip model={model} />}>
          <p className={styles.cardDesc}>The runtime that answers engineer questions.</p>
          <KeyValues rows={runtime.rows} />
        </Card>
        <Card
          title="Speech Recognition"
          labelledBy="speech-recognition"
          aside={
            <span className={styles.chip}>
              <Badge tone={speech.tone}>{speech.statusLabel}</Badge>
            </span>
          }
        >
          <p className={styles.cardDesc}>Turns a spoken question into text on this computer.</p>
          <KeyValues rows={speech.rows} />
          <p className={styles.footnote}>{speech.footnote}</p>
        </Card>
        <Card title="Local Engine" labelledBy="local-engine">
          <p className={styles.cardDesc}>The processing service this dashboard reads from.</p>
          <KeyValues
            rows={[
              {
                label: "Connection",
                value: model.engineReachable ? "Connected" : "Unavailable",
                tone: model.engineReachable ? "green" : "red",
              },
              {
                label: "Changes",
                value: "The receiver address and port are set when starting the engine and require a restart.",
              },
            ]}
          />
        </Card>
      </div>
    </div>
  );
}
