// Shared by the server root layout and the client theme switch, so it must not
// be a client module (the layout needs the script text, not a client reference).

export type Theme = "light" | "dark" | "system";

export const THEME_KEY = "f1-engineer:theme";

export function parseTheme(value: string | null): Theme {
  return value === "dark" || value === "system" ? value : "light";
}

/** Runs in <head> before paint; keep it dependency-free. */
export const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem(${JSON.stringify(THEME_KEY)});if(t==="dark"||t==="system")document.documentElement.setAttribute("data-theme",t)}catch(e){}})()`;
