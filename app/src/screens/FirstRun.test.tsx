import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FirstRunWizard } from "./FirstRun";
import { ThemeProvider } from "../theme";

vi.mock("../api", () => ({ checkHealth: vi.fn().mockResolvedValue(false) }));

describe("FirstRunWizard", () => {
  it("segments setup and previews comfort preferences immediately", async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <FirstRunWizard onEngineReady={vi.fn()} onComplete={vi.fn()} />
      </ThemeProvider>,
    );

    expect(screen.getByRole("heading", { name: "Your grading stays yours" })).toBeInTheDocument();
    expect(screen.getByText("Step 1 of 4")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(screen.getByRole("heading", { name: "Choose a comfortable view" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Skip appearance choices" })).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "Dark mode" }));
    await user.click(screen.getByRole("radio", { name: "Spacious density" }));
    await user.click(screen.getByRole("checkbox", { name: "Reduce interface motion" }));

    expect(document.documentElement).toHaveAttribute("data-mode", "dark");
    expect(document.documentElement).toHaveAttribute("data-density", "spacious");
    expect(document.documentElement).toHaveAttribute("data-motion", "reduced");
  });
});
