import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsScreen } from "./Settings";
import { ThemeProvider } from "../theme";

vi.mock("../api", () => ({
  getSettings: vi.fn().mockResolvedValue({ high_confidence_threshold: 0.95, review_first_threshold: 0.7 }),
  updateSettings: vi.fn().mockResolvedValue({ success: true }),
  listRubrics: vi.fn().mockResolvedValue([]),
  getRubric: vi.fn(),
  saveRubric: vi.fn(),
}));

describe("SettingsScreen", () => {
  // Each test states the settings it needs outright, and none may use the `...Once`
  // variants: the screen reloads settings after a save, so a one-shot answer leaves the
  // second call falling through to the bare mock, whose reply carries no data_dir.
  // That is a real coupling, though not what broke CI — see the wait below.
  beforeEach(async () => {
    const api = await import("../api");
    vi.mocked(api.getSettings).mockReset().mockResolvedValue({
      high_confidence_threshold: 0.95,
      review_first_threshold: 0.7,
    });
    vi.mocked(api.updateSettings).mockReset().mockResolvedValue({ success: true });
    vi.mocked(api.listRubrics).mockReset().mockResolvedValue([]);
  });

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
    vi.mocked(api.getSettings).mockRejectedValue(new Error("engine unavailable"));
    render(<ThemeProvider><SettingsScreen onClose={vi.fn()} /></ThemeProvider>);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("engine unavailable"));
  });

  it("labels the attachment directory accurately and saves runtime grading settings", async () => {
    const api = await import("../api");
    vi.mocked(api.getSettings).mockResolvedValue({
      canvas_url: "https://canvas.example.test",
      canvas_token_masked: "1234...cdef",
      data_dir: "/tmp/grader-canvas",
      high_confidence_threshold: 0.95,
      review_first_threshold: 0.7,
    });
    const user = userEvent.setup();
    render(<ThemeProvider><SettingsScreen onClose={vi.fn()} /></ThemeProvider>);

    // The input renders before the settings arrive, so findByRole returns it while it is
    // still empty. Wait for the value rather than the element: locally the load resolves
    // fast enough to hide the gap, and on CI it did not.
    const directory = await screen.findByRole("textbox", { name: /Canvas attachment directory/i });
    await waitFor(() => expect(directory).toHaveValue("/tmp/grader-canvas"));
    expect(screen.getByText(/Core databases and the durable post queue are selected when the local engine starts/i)).toBeInTheDocument();

    await user.clear(screen.getByRole("spinbutton", { name: /High-confidence threshold/i }));
    await user.type(screen.getByRole("spinbutton", { name: /High-confidence threshold/i }), "0.9");
    await user.click(screen.getByRole("button", { name: "Save general settings" }));

    expect(api.updateSettings).toHaveBeenCalledWith(expect.objectContaining({
      canvas_url: "https://canvas.example.test",
      data_dir: "/tmp/grader-canvas",
      high_confidence_threshold: 0.9,
      review_first_threshold: 0.7,
    }));
  });
});
