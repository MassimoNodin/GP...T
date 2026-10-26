"use client";

import { useEffect, useState } from "react";
import { applyTheme, readTheme, type Theme } from "@/features/ui-foundation/theme";
import { Card } from "@/features/ui-foundation/primitives";
import { Segmented } from "@/features/ui-foundation/Segmented";
import { DEFAULT_UNITS, saveUnits, useUnits, type Units } from "@/features/ui-foundation/units";
import styles from "./Settings.module.css";

const THEME = [
  { key: "light", label: "Light" },
  { key: "dark", label: "Dark" },
  { key: "system", label: "System" },
] as const;

const SPEED = [
  { key: "kmh", label: "km/h" },
  { key: "mph", label: "mph" },
] as const;

const TEMPERATURE = [
  { key: "c", label: "°C" },
  { key: "f", label: "°F" },
] as const;

const DELTA = [
  { key: "slower-positive", label: "+ is slower" },
  { key: "faster-positive", label: "+ is faster" },
] as const;

/** Display units for speeds, temperatures and lap-time deltas; saved in this browser and applied at once. */
export function UnitsCard() {
  const units = useUnits();
  const [failed, setFailed] = useState(false);
  // The server renders Light; the saved appearance is read after hydration.
  const [theme, setTheme] = useState<Theme>("light");
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTheme(readTheme());
  }, []);
  const changeTheme = (next: Theme) => {
    setTheme(next);
    setFailed(!applyTheme(next));
  };
  const change = (patch: Partial<Units>) => setFailed(!saveUnits({ ...units, ...patch }));
  const isDefault =
    units.speed === DEFAULT_UNITS.speed && units.temperature === DEFAULT_UNITS.temperature && units.delta === DEFAULT_UNITS.delta;

  return (
    <Card title="Display" labelledBy="units-format">
      <p className={styles.cardDesc}>Appearance, and how speeds, temperatures and time gaps are shown across the app.</p>
      <div className={styles.field}>
        <div className={styles.label}>Appearance</div>
        <Segmented items={[...THEME]} value={theme} onChange={changeTheme} label="Appearance" />
        <p className={styles.help}>System follows your computer&apos;s light or dark setting.</p>
      </div>
      <div className={styles.field}>
        <div className={styles.label}>Speed</div>
        <Segmented items={[...SPEED]} value={units.speed} onChange={(speed) => change({ speed })} label="Speed unit" />
      </div>
      <div className={styles.field}>
        <div className={styles.label}>Temperature</div>
        <Segmented
          items={[...TEMPERATURE]}
          value={units.temperature}
          onChange={(temperature) => change({ temperature })}
          label="Temperature unit"
        />
      </div>
      <div className={styles.field}>
        <div className={styles.label}>Time gaps</div>
        <Segmented items={[...DELTA]} value={units.delta} onChange={(delta) => change({ delta })} label="Delta sign" />
        <p className={styles.help}>
          {units.delta === "slower-positive"
            ? "A lap 0.3 s slower than your best reads +0.300, as on the pit wall."
            : "A lap 0.3 s slower than your best reads -0.300. Colours still show red for slower."}
        </p>
      </div>
      <p className={styles.footnote} role="status">
        {failed
          ? "These settings could not be saved in this browser."
          : isDefault
            ? "Saved in this browser."
            : "Saved in this browser. Recorded data stays metric; only the display changes."}
      </p>
    </Card>
  );
}
