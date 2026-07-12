import { createContext, useContext, useEffect, useMemo, useState } from "react";

export type ColorMode = "light" | "dark";
export type Density = "comfortable" | "spacious";
export type MotionPreference = "system" | "reduced";

export interface ThemePreferences {
  mode: ColorMode;
  density: Density;
  motion: MotionPreference;
}

export const DEFAULT_THEME: ThemePreferences = {
  mode: "light",
  density: "comfortable",
  motion: "system",
};

const STORAGE_KEY = "grader_theme_preferences";

interface ThemeContextValue {
  preferences: ThemePreferences;
  updatePreferences: (next: Partial<ThemePreferences>) => void;
  resetPreferences: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function readPreferences(): ThemePreferences {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null") as Partial<ThemePreferences> | null;
    return {
      mode: parsed?.mode === "dark" ? "dark" : "light",
      density: parsed?.density === "spacious" ? "spacious" : "comfortable",
      motion: parsed?.motion === "reduced" ? "reduced" : "system",
    };
  } catch {
    return DEFAULT_THEME;
  }
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [preferences, setPreferences] = useState<ThemePreferences>(readPreferences);

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.mode = preferences.mode;
    root.dataset.density = preferences.density;
    root.dataset.motion = preferences.motion;
    root.style.colorScheme = preferences.mode;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences));
  }, [preferences]);

  const value = useMemo<ThemeContextValue>(() => ({
    preferences,
    updatePreferences(next) {
      setPreferences(current => ({ ...current, ...next }));
    },
    resetPreferences() {
      setPreferences(DEFAULT_THEME);
    },
  }), [preferences]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme must be used within ThemeProvider");
  return value;
}
