import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { DEFAULT_THEME, ThemeProvider, useTheme } from "./theme";

function PreferenceProbe() {
  const { preferences, updatePreferences, resetPreferences } = useTheme();
  return (
    <div>
      <output aria-label="current preferences">{JSON.stringify(preferences)}</output>
      <button onClick={() => updatePreferences({ mode: "dark", density: "spacious", motion: "reduced" })}>
        Make calm
      </button>
      <button onClick={resetPreferences}>Reset</button>
    </div>
  );
}

describe("ThemeProvider", () => {
  beforeEach(() => localStorage.clear());

  it("starts in a comfortable light mode and applies semantic root attributes", () => {
    render(<ThemeProvider><PreferenceProbe /></ThemeProvider>);

    expect(screen.getByLabelText("current preferences")).toHaveTextContent(JSON.stringify(DEFAULT_THEME));
    expect(document.documentElement).toHaveAttribute("data-mode", "light");
    expect(document.documentElement).toHaveAttribute("data-density", "comfortable");
    expect(document.documentElement).toHaveAttribute("data-motion", "system");
  });

  it("persists user choices and restores them on the next render", async () => {
    const user = userEvent.setup();
    const first = render(<ThemeProvider><PreferenceProbe /></ThemeProvider>);
    await user.click(screen.getByRole("button", { name: "Make calm" }));

    expect(document.documentElement).toHaveAttribute("data-mode", "dark");
    expect(JSON.parse(localStorage.getItem("grader_theme_preferences") || "{}")).toEqual({
      mode: "dark",
      density: "spacious",
      motion: "reduced",
    });

    first.unmount();
    render(<ThemeProvider><PreferenceProbe /></ThemeProvider>);
    expect(screen.getByLabelText("current preferences")).toHaveTextContent('"mode":"dark"');
  });

  it("resets every preference to the light-first defaults", async () => {
    const user = userEvent.setup();
    render(<ThemeProvider><PreferenceProbe /></ThemeProvider>);
    await user.click(screen.getByRole("button", { name: "Make calm" }));
    await user.click(screen.getByRole("button", { name: "Reset" }));

    expect(screen.getByLabelText("current preferences")).toHaveTextContent(JSON.stringify(DEFAULT_THEME));
    expect(document.documentElement).toHaveAttribute("data-mode", "light");
  });
});
