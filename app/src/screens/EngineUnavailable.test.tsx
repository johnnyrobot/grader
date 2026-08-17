import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { EngineUnavailable } from "./EngineUnavailable";
import { ThemeProvider } from "../theme";

const bundleMissing = {
  kind: "engine_missing",
  message: "This copy of Grader is missing its grading engine.",
  detail: "Expected /Applications/Grader.app/Contents/Resources/engine/api.py inside the app bundle. Reinstall Grader from the signed release.",
  logPath: "/Users/someone/Library/Logs/com.grader.app/engine.log",
};

function renderScreen(props: Partial<Parameters<typeof EngineUnavailable>[0]> = {}) {
  return render(
    <ThemeProvider>
      <EngineUnavailable error={bundleMissing} retrying={false} onRetry={vi.fn()} {...props} />
    </ThemeProvider>,
  );
}

describe("EngineUnavailable", () => {
  it("states what went wrong rather than spinning", () => {
    renderScreen();

    expect(screen.getByRole("heading", { name: "The grading engine did not start" })).toBeInTheDocument();
    expect(screen.getByText(bundleMissing.message)).toBeInTheDocument();
    expect(screen.getByText(bundleMissing.detail)).toBeInTheDocument();
    expect(screen.queryByText(/Waiting for the local engine/)).not.toBeInTheDocument();
  });

  it("announces the failure to assistive technology", () => {
    renderScreen();

    expect(screen.getByRole("alert")).toHaveTextContent(bundleMissing.message);
  });

  it("names the log so a report can carry evidence", () => {
    renderScreen();

    expect(screen.getByText(bundleMissing.logPath)).toBeInTheDocument();
  });

  it("omits the log line when no log was ever opened", () => {
    renderScreen({ error: { ...bundleMissing, logPath: "" } });

    expect(screen.queryByText(/engine\.log/)).not.toBeInTheDocument();
  });

  it("offers a retry and reports while it is running", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    const { rerender } = renderScreen({ onRetry });

    await user.click(screen.getByRole("button", { name: "Try starting it again" }));
    expect(onRetry).toHaveBeenCalledOnce();

    rerender(
      <ThemeProvider>
        <EngineUnavailable error={bundleMissing} retrying onRetry={onRetry} />
      </ThemeProvider>,
    );
    expect(screen.getByRole("button", { name: "Starting…" })).toBeDisabled();
  });

  it("falls back to a general statement when the failure carries no message", () => {
    renderScreen({ error: { kind: "", message: "", detail: "", logPath: "" } });

    expect(screen.getByRole("alert")).toHaveTextContent(/did not become ready/i);
  });
});
