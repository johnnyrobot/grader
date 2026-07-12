import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ModelManager } from "./ModelManager";
import { fetchModels, installModel } from "../api";

vi.mock("../api", () => ({
  fetchModels: vi.fn().mockResolvedValue({
    ollama_running: true,
    available: [{ name: "grader-text:8b", size: 8_100_000_000 }],
  }),
  installModel: vi.fn().mockResolvedValue({ success: true }),
}));

describe("ModelManager", () => {
  it("explains local model roles and gives install progress a textual status", async () => {
    const user = userEvent.setup();
    render(<ModelManager onClose={vi.fn()} />);

    expect(await screen.findByRole("heading", { name: "Local models" })).toBeInTheDocument();
    expect(screen.getByText("Ollama is running")).toBeInTheDocument();
    expect(screen.getByText("Grades text submissions")).toBeInTheDocument();
    expect(screen.getByText("Reads handwriting and scans")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Install handwriting model" }));
    await waitFor(() => expect(installModel).toHaveBeenCalledWith("minicpm-v4.6:latest"));
    expect(await screen.findByRole("status")).toHaveTextContent("minicpm-v4.6:latest downloaded");
    expect(fetchModels).toHaveBeenCalled();
  });
});
