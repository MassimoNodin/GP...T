"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

// Display units chosen in Settings (browser-only). Values stay metric
// everywhere; text and labels are converted where they are drawn.

export type Units = {
  speed: "kmh" | "mph";
  temperature: "c" | "f";
  /** How a lap-time delta is signed: "+" for slower (racing convention) or for faster. */
  delta: "slower-positive" | "faster-positive";
};

export const DEFAULT_UNITS: Units = { speed: "kmh", temperature: "c", delta: "slower-positive" };

export const UNITS_KEY = "f1-engineer:display-units";
const UNITS_EVENT = "f1-engineer:display-units";
export const MPH_PER_KMH = 0.621371;

export function parseUnits(raw: string | null): Units {
  try {
    const value = raw ? (JSON.parse(raw) as Partial<Units>) : {};
    return {
      speed: value.speed === "mph" ? "mph" : "kmh",
      temperature: value.temperature === "f" ? "f" : "c",
      delta: value.delta === "faster-positive" ? "faster-positive" : "slower-positive",
    };
  } catch {
    return DEFAULT_UNITS;
  }
}

export function readUnits(): Units {
  try {
    return parseUnits(window.localStorage.getItem(UNITS_KEY));
  } catch {
    return DEFAULT_UNITS;
  }
}

/** Saves the choice and tells every mounted UnitsProvider in this tab. */
export function saveUnits(units: Units): boolean {
  try {
    window.localStorage.setItem(UNITS_KEY, JSON.stringify(units));
  } catch {
    return false;
  }
  window.dispatchEvent(new Event(UNITS_EVENT));
  return true;
}

export function speedUnitLabel(units: Units): string {
  return units.speed === "mph" ? "mph" : "km/h";
}

export function convertSpeed(kmh: number, units: Units): number {
  return units.speed === "mph" ? kmh * MPH_PER_KMH : kmh;
}

/**
 * Rewrites temperatures ("26°C") and speeds ("318 km/h", "-12 km/h") in a
 * display string to the chosen units. Anything else is left untouched.
 */
export function convertText(text: string, units: Units): string {
  let result = text;
  if (units.temperature === "f") {
    result = result.replace(/(-?\d+(?:\.\d+)?)\s?°C/g, (_match, value: string) => `${Math.round((Number(value) * 9) / 5 + 32)}°F`);
  }
  if (units.speed === "mph") {
    result = result.replace(/([+-]?)(\d+(?:\.\d+)?)\s?km\/h/g, (_match, sign: string, value: string) => `${sign}${Math.round(Number(value) * MPH_PER_KMH)} mph`);
  }
  return result;
}

/** Flips the sign of a formatted delta ("+0.578" <-> "-0.578") for the "faster is positive" convention. */
export function displayDelta(text: string, units: Units): string {
  if (units.delta === "slower-positive") return text;
  const match = /^([+\-−])(.*)$/.exec(text.trim());
  if (!match) return text;
  return `${match[1] === "+" ? "-" : "+"}${match[2]}`;
}

const UnitsContext = createContext<Units>(DEFAULT_UNITS);

/**
 * Provides the chosen units. The first render uses the defaults so server and
 * client markup match; the saved choice applies right after hydration.
 */
export function UnitsProvider({ children }: { children: ReactNode }) {
  const [units, setUnits] = useState<Units>(DEFAULT_UNITS);
  useEffect(() => {
    const sync = () => setUnits(readUnits());
    sync();
    window.addEventListener(UNITS_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(UNITS_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  return <UnitsContext.Provider value={units}>{children}</UnitsContext.Provider>;
}

export function useUnits(): Units {
  return useContext(UnitsContext);
}

/** A display string with its temperatures and speeds in the chosen units. */
export function UnitText({ children }: { children: string }) {
  return <>{convertText(children, useUnits())}</>;
}
