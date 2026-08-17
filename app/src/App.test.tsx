import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { ThemeProvider } from "./theme";

/** The tree main.tsx mounts. */
const app = (
  <ThemeProvider>
    <App />
  </ThemeProvider>
);

const checkHealth = vi.fn();
const invoke = vi.fn();

vi.mock("./api", () => ({
  checkHealth: (...args: unknown[]) => checkHealth(...args),
  setBaseUrl: vi.fn(),
  setToken: vi.fn(),
  fetchQueue: vi.fn().mockResolvedValue({ items: [], stats: { total: 0, review_first: 0, standard: 0, high_confidence: 0, summary: "" } }),
  connectWebSocket: vi.fn(() => ({ close: vi.fn() })),
}));

vi.mock("@tauri-apps/api/core", () => ({ invoke: (...args: unknown[]) => invoke(...args) }));

const BUNDLE_MISSING = {
  port: "8756",
  running: "false",
  log_path: "/Users/someone/Library/Logs/com.grader.app/engine.log",
  error_kind: "engine_missing",
  error_message: "This copy of Grader is missing its grading engine.",
  error_detail: "Expected /Applications/Grader.app/Contents/Resources/engine/api.py inside the app bundle.",
};

describe("App engine startup", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    localStorage.clear();
    checkHealth.mockReset();
    invoke.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /** Run the startup budget out without waiting in real time. */
  async function drainHealthPolling() {
    await vi.advanceTimersByTimeAsync(130_000);
  }

  it("shows why the engine failed instead of waiting forever", async () => {
    checkHealth.mockResolvedValue(false);
    invoke.mockResolvedValue(BUNDLE_MISSING);

    render(app);
    await drainHealthPolling();

    expect(await screen.findByRole("heading", { name: "The grading engine did not start" })).toBeInTheDocument();
    expect(screen.getByText(BUNDLE_MISSING.error_message)).toBeInTheDocument();
    expect(screen.getByText(BUNDLE_MISSING.error_detail)).toBeInTheDocument();
    expect(screen.getByText(BUNDLE_MISSING.log_path)).toBeInTheDocument();
  });

  it("reports a silent timeout as a failure rather than leaving the wizard spinning", async () => {
    checkHealth.mockResolvedValue(false);
    // An engine that is running but never answers reports no error of its own.
    invoke.mockResolvedValue({ port: "8756", running: "true", log_path: "/tmp/engine.log" });

    render(app);
    await drainHealthPolling();

    expect(await screen.findByRole("heading", { name: "The grading engine did not start" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/did not become ready/i);
  });

  it("does not report a failure while the engine is still starting", async () => {
    checkHealth.mockResolvedValue(false);
    invoke.mockResolvedValue({ port: "8756", running: "true" });

    render(app);
    // A first launch on an unfamiliar Mac pays for Gatekeeper and a cold dependency
    // import. Half a minute of silence is a slow start, not a failure.
    await vi.advanceTimersByTimeAsync(30_000);

    expect(screen.queryByRole("heading", { name: "The grading engine did not start" })).not.toBeInTheDocument();
  });

  it("reports a bundle that cannot start without waiting out the budget", async () => {
    checkHealth.mockResolvedValue(false);
    invoke.mockResolvedValue(BUNDLE_MISSING);

    render(app);
    // A missing engine will not appear on its own, so there is nothing to wait for.
    await vi.advanceTimersByTimeAsync(3_000);

    expect(await screen.findByRole("heading", { name: "The grading engine did not start" })).toBeInTheDocument();
  });

  it("takes up an engine that was only slow, with nothing for the teacher to press", async () => {
    checkHealth.mockResolvedValue(false);
    invoke.mockResolvedValue({ port: "8756", running: "true" });

    render(app);
    await drainHealthPolling();
    await screen.findByRole("heading", { name: "The grading engine did not start" });

    // The engine finally answers.
    checkHealth.mockResolvedValue(true);
    invoke.mockResolvedValue({ token: "late-token", port: "8756", running: "true" });
    await vi.advanceTimersByTimeAsync(6_000);

    await waitFor(() => {
      expect(screen.queryByRole("heading", { name: "The grading engine did not start" })).not.toBeInTheDocument();
    });
    expect(invoke).not.toHaveBeenCalledWith("restart_engine");
  });

  it("does not restart an engine that is already answering", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    checkHealth.mockResolvedValue(false);
    invoke.mockResolvedValue(BUNDLE_MISSING);

    render(app);
    await drainHealthPolling();
    await screen.findByRole("heading", { name: "The grading engine did not start" });

    // Health comes back between the report and the retry: restarting would kill it.
    checkHealth.mockResolvedValue(true);
    invoke.mockResolvedValue({ token: "live-token", port: "8756", running: "true" });

    await user.click(screen.getByRole("button", { name: "Try starting it again" }));

    await waitFor(() => {
      expect(screen.queryByRole("heading", { name: "The grading engine did not start" })).not.toBeInTheDocument();
    });
    expect(invoke).not.toHaveBeenCalledWith("restart_engine");
  });

  it("clears the failure when a retry brings the engine up", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    checkHealth.mockResolvedValue(false);
    invoke.mockResolvedValue(BUNDLE_MISSING);

    render(app);
    await drainHealthPolling();
    await screen.findByRole("heading", { name: "The grading engine did not start" });

    // Still down when retry is pressed, up once the restart lands.
    checkHealth.mockResolvedValueOnce(false).mockResolvedValue(true);
    invoke.mockResolvedValue({ token: "live-token", port: "8791", running: "true" });

    await user.click(screen.getByRole("button", { name: "Try starting it again" }));

    await waitFor(() => {
      expect(screen.queryByRole("heading", { name: "The grading engine did not start" })).not.toBeInTheDocument();
    });
    expect(invoke).toHaveBeenCalledWith("restart_engine");
  });

  it("keeps the failure visible when a retry throws", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    checkHealth.mockResolvedValue(false);
    invoke.mockResolvedValue(BUNDLE_MISSING);

    render(app);
    await drainHealthPolling();
    await screen.findByRole("heading", { name: "The grading engine did not start" });

    invoke.mockRejectedValue("This copy of Grader is missing its grading engine. Reinstall it.");
    await user.click(screen.getByRole("button", { name: "Try starting it again" }));

    expect(await screen.findByText(/Reinstall it\./)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "The grading engine did not start" })).toBeInTheDocument();
  });

  it("goes to onboarding, not to a failure, when the engine comes up", async () => {
    checkHealth.mockResolvedValue(true);
    invoke.mockResolvedValue({ token: "t", port: "8756", running: "true" });

    render(app);

    expect(await screen.findByRole("heading", { name: "Your grading stays yours" })).toBeInTheDocument();
  });
});
