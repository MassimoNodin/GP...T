"use client";

import { useLayoutEffect } from "react";
import { parseTheme, THEME_KEY, type Theme } from "./theme-script";

// Light, dark or system appearance, saved in this browser. The root layout's
// inline script applies it before the first paint; this module changes it and
// re-applies it after React's development remount resets <html> attributes.

export type { Theme };

export function readTheme(): Theme {
  try {
    return parseTheme(window.localStorage.getItem(THEME_KEY));
  } catch {
    return "light";
  }
}

export function applyTheme(theme: Theme): boolean {
  document.documentElement.setAttribute("data-theme", theme);
  try {
    window.localStorage.setItem(THEME_KEY, theme);
    return true;
  } catch {
    return false;
  }
}

/** Mounted in the app shell: re-applies the saved theme before paint (a no-op in production). */
export function ThemeSync() {
  useLayoutEffect(() => {
    document.documentElement.setAttribute("data-theme", readTheme());
  }, []);
  return null;
}
