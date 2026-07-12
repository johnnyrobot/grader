import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SettingsScreen } from "./Settings";
import { ThemeProvider } from "../theme";

vi.mock("../api", () => ({
  getSettings: vi.fn().mockResolvedValue({ auto_approve_threshold: 0.95, review_first_threshold: 0.7 }),
  updateSettings: vi.fn().mockResolvedValue({ success: true }),
  listRubrics: vi.fn().mockResolvedValue([]),
  getRubric: vi.fn(),
  saveRubric: vi.fn(),
}));

describe("SettingsScreen", () => {
  it("keeps appearance preferences editable, previewable, and resettable", async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <SettingsScreen onClose={vi.fn()} />
      </ThemeProvider>,
    );

    expect(screen.getByRole("heading", { name: "Settings" })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Appearance" }));
    expect(screen.getByRole("heading", { name: "A workspace that fits how you read" })).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: "Dark mode" }));
    expect(document.documentElement).toHaveAttribute("data-mode", "dark");
    await user.click(screen.getByRole("button", { name: "Restore appearance defaults" }));
    expect(document.documentElement).toHaveAttribute("data-mode", "light");
  });

  it("shows recoverable loading errors as status text", async () => {
    const api = await import("../api");
    vi.mocked(api.getSettings).mockRejectedValueOnce(new Error("engine unavailable"));
    render(<ThemeProvider><SettingsScreen onClose={vi.fn()} /></ThemeProvider>);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("engine unavailable"));
  });
});
