/** Light / dark / system appearance. Stored only in this browser (a per-device preference), never sent anywhere. */
export type ThemePreference = "system" | "light" | "dark";

export const THEME_KEY = "easycanvas-theme";

/** Anything unexpected (missing, corrupt, from an older version) means "follow the system". */
export function normalizeTheme(value: unknown): ThemePreference {
  return value === "light" || value === "dark" ? value : "system";
}

export function readTheme(): ThemePreference {
  try { return normalizeTheme(localStorage.getItem(THEME_KEY)); } catch { return "system"; }
}

/** "system" removes the attribute so the stylesheet's prefers-color-scheme rule decides. */
export function applyTheme(preference: ThemePreference) {
  const root = document.documentElement;
  if (preference === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", preference);
}

export function saveTheme(preference: ThemePreference) {
  try {
    if (preference === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, preference);
  } catch { /* storage can be blocked; the choice still applies until the page is closed */ }
  applyTheme(preference);
}

/** Runs in <head> before first paint so a saved choice never flashes the wrong theme. */
export const THEME_INIT_SCRIPT = `try{var t=localStorage.getItem(${JSON.stringify(THEME_KEY)});if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}`;
