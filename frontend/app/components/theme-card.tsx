"use client";

import { useEffect, useState } from "react";
import { applyTheme, readTheme, saveTheme } from "@/lib/theme";
import type { ThemePreference } from "@/lib/theme";

const OPTIONS: { value: ThemePreference; label: string }[] = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

/** Settings -> Appearance. "System" follows the device; the choice is remembered in this browser only. */
export default function ThemeCard() {
  const [theme, setTheme] = useState<ThemePreference>("system");

  useEffect(() => {
    const timer = setTimeout(() => { const saved = readTheme(); setTheme(saved); applyTheme(saved); }, 0);
    return () => clearTimeout(timer);
  }, []);

  function choose(value: ThemePreference) {
    setTheme(value);
    saveTheme(value);
  }

  return <section className="appearance-card" aria-labelledby="appearance-title">
    <h2 id="appearance-title" className="section-title">Appearance</h2>
    <p className="assistant-hint">Choose a light or dark theme. System follows your device&apos;s setting. This is remembered in this browser only.</p>
    <fieldset>
      <legend>Theme</legend>
      <div className="segmented">
        {OPTIONS.map(({ value, label }) => (
          <label key={value}>
            <input type="radio" name="theme" value={value} checked={theme === value} onChange={() => choose(value)} />
            {label}
          </label>
        ))}
      </div>
    </fieldset>
  </section>;
}
