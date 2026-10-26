import { Card } from "@/features/ui-foundation/primitives";
import styles from "./Settings.module.css";
import tabStyles from "./SettingsTabs.module.css";

export function AdvancedPanel() {
  return (
    <div className={styles.grid}>
      <div className={tabStyles.span2}>
        <Card title="Diagnostic Tools" labelledBy="diagnostic-tools">
          <p className={styles.cardDesc}>Replay and capture tools run from the command line so they never compete with the live receiver.</p>
          <dl className={styles.kv}>
            <div>
              <dt>Check a capture</dt>
              <dd>
                <code>f1-engineer inspect &lt;capture.f1ecap&gt;</code>
              </dd>
            </div>
            <div>
              <dt>Validate a replay</dt>
              <dd>
                <code>f1-engineer replay-udp &lt;capture.f1ecap&gt; --host &lt;receiver-ip&gt; --dry-run</code>
                <span className={styles.cmdNote}>
                  Sends nothing. Use <code>--confirm-idle</code> instead of <code>--dry-run</code> to send, with F1 off.
                </span>
              </dd>
            </div>
            <div>
              <dt>Manual acquisition</dt>
              <dd>
                <code>f1-engineer api --manual-acquisition</code>
                <span className={styles.cmdNote}>
                  For legacy manual capture diagnostics only. It turns off automatic acquisition, so sessions are not
                  recorded from the game.
                </span>
              </dd>
            </div>
            <div>
              <dt>Receiver address</dt>
              <dd>
                <code>f1-engineer api --udp-host &lt;ip&gt; --udp-port &lt;port&gt;</code>
              </dd>
            </div>
          </dl>
          <p className={styles.footnote}>
            Run these with <code>uv run --extra app</code> or from the project&apos;s virtual environment. See README.md for details.
          </p>
        </Card>
      </div>
    </div>
  );
}
