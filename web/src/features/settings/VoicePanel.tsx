"use client";

import { RadioCard } from "./RadioCard";
import { SpeechRecognitionCard } from "./SpeechRecognitionCard";
import { UnitsCard } from "./UnitsCard";
import styles from "./Settings.module.css";

export function VoicePanel() {
  return (
    <div className={styles.grid}>
      <RadioCard />
      <SpeechRecognitionCard />
      <UnitsCard />
    </div>
  );
}
