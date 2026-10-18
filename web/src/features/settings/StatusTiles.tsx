import { GamepadIcon, MicrophoneIcon, SpeakerIcon, StorageIcon } from "@/features/ui-foundation/icons";
import { Card } from "@/features/ui-foundation/primitives";
import type { DeviceTile } from "./devices";
import type { SettingsModel } from "./load";
import { RuntimeChip } from "./shared";
import styles from "./Settings.module.css";

export function StatusTiles({
  model,
  microphone,
  audio,
  showRuntimeChip,
}: {
  model: SettingsModel;
  microphone: DeviceTile;
  audio: DeviceTile;
  showRuntimeChip: boolean;
}) {
  return (
    <Card
      title="Current Configuration & Status"
      labelledBy="current-configuration"
      className={styles.statusCard}
      aside={showRuntimeChip ? <RuntimeChip model={model} /> : undefined}
    >
      <ul className={styles.tiles}>
        <li className={styles.tile}>
          <MicrophoneIcon size={30} strokeWidth={1.4} className={styles.tileIcon} />
          <div>
            <div className={styles.tileLabel}>Radio microphone</div>
            <div className={styles.tileName} title={microphone.name}>
              {microphone.name}
            </div>
            <div className={styles.tileState} data-tone={microphone.tone}>
              <i /> {microphone.state}
            </div>
          </div>
        </li>
        <li className={styles.tile}>
          <SpeakerIcon size={30} strokeWidth={1.4} className={styles.tileIcon} />
          <div>
            <div className={styles.tileLabel}>Engineer voice</div>
            <div className={styles.tileName} title={audio.name}>
              {audio.name}
            </div>
            <div className={styles.tileState} data-tone={audio.tone}>
              <i /> {audio.state}
            </div>
          </div>
        </li>
        <li className={styles.tile}>
          <GamepadIcon size={30} strokeWidth={1.4} className={styles.tileIcon} />
          <div>
            <div className={styles.tileLabel}>Game Connection</div>
            <div className={styles.tileName}>{model.game.name}</div>
            <div className={styles.tileState} data-tone={model.game.tone}>
              {model.game.state}
            </div>
          </div>
        </li>
        <li className={styles.tile}>
          <StorageIcon size={30} strokeWidth={1.4} className={styles.tileIcon} />
          <div>
            <div className={styles.tileLabel}>Storage</div>
            <div className={styles.tileName}>{model.status.storage.used}</div>
            <div className={styles.tileState}>{model.status.storage.available}</div>
          </div>
        </li>
      </ul>
    </Card>
  );
}
