import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { QueueScreen } from "./Queue";
import type { QueueData } from "../api";
import { checkDesktopUpdate, installPendingUpdate } from "../updater";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    checkUpdates: vi.fn().mockResolvedValue({ update_available: false }),
    gradeBatch: vi.fn().mockResolvedValue({ job_id: "job-1", total: 3 }),
  };
});

vi.mock("../updater", () => ({
  checkDesktopUpdate: vi.fn().mockResolvedValue(null),
  installPendingUpdate: vi.fn().mockResolvedValue(undefined),
}));

const queue: QueueData = {
  stats: {
    total: 3,
    review_first: 1,
    standard: 1,
    high_confidence: 1,
    summary: "1 needs review, 1 standard, 1 high confidence",
  },
  items: [
    {
      id: "grade-1",
      submission_id: "Student 1042",
      assignment_id: "essay-1",
      ai_scores: { thesis: 4, evidence: 3 },
      ai_feedback: "Clear claim; add a second source.",
      confidence: 0.64,
      flags: ["citation_check"],
      lane: "review_first",
      approved: false,
      teacher_scores: null,
      teacher_feedback: null,
    },
    {
      id: "grade-2",
      submission_id: "Student 1177",
      assignment_id: "essay-1",
      ai_scores: { thesis: 5, evidence: 5 },
      ai_feedback: "Ready for review.",
      confidence: 0.91,
      flags: [],
      lane: "high_confidence",
      approved: true,
      teacher_scores: null,
      teacher_feedback: null,
    },
  ],
};

function renderQueue(data: QueueData | null = queue) {
  const props = {
    data,
    onSelectItem: vi.fn(),
    assignmentId: "essay-1",
    setAssignmentId: vi.fn(),
    selectedLane: undefined,
    setSelectedLane: vi.fn(),
    onRefresh: vi.fn(),
    engineIssue: "",
    recoveringEngine: false,
    onRecoverEngine: vi.fn(),
    onOpenModels: vi.fn(),
    onOpenSettings: vi.fn(),
  };
  render(<QueueScreen {...props} />);
  return props;
}

describe("QueueScreen", () => {
  it("makes triage state and the next grading action explicit", async () => {
    const user = userEvent.setup();
    const props = renderQueue();

    expect(screen.getByRole("heading", { name: "Grading queue" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Grade submissions" })).toBeInTheDocument();
    expect(screen.getAllByText("Needs teacher review").length).toBeGreaterThan(0);
    expect(screen.getAllByText("High confidence").length).toBeGreaterThan(0);
    expect(screen.getByText("Citation check (experimental)")).toBeInTheDocument();
    expect(screen.getByText("Approved")).toBeInTheDocument();

    const all = screen.getByRole("button", { name: "All submissions" });
    expect(all).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Needs review" }));
    expect(props.setSelectedLane).toHaveBeenCalledWith("review_first");
  });

  it("explains the first useful action when no submissions are available", () => {
    renderQueue({
      items: [],
      stats: { total: 0, review_first: 0, standard: 0, high_confidence: 0, summary: "No submissions" },
    });

    expect(screen.getByRole("heading", { name: "No submissions to review yet" })).toBeInTheDocument();
    expect(screen.getByText(/Import submissions, then choose Grade submissions/i)).toBeInTheDocument();
  });

  it("surfaces local engine recovery instead of hiding connection failures", async () => {
    const user = userEvent.setup();
    const props = renderQueue();
    props.onRecoverEngine.mockClear();

    render(
      <QueueScreen
        {...props}
        engineIssue="Local engine connection lost."
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("Local engine needs attention");
    await user.click(screen.getByRole("button", { name: "Restart local engine" }));
    expect(props.onRecoverEngine).toHaveBeenCalled();
  });

  it("offers a keyboard reachable install action when the signed updater finds a new version", async () => {
    vi.mocked(checkDesktopUpdate).mockResolvedValueOnce({
      version: "0.1.1",
      currentVersion: "0.1.0",
      notes: "Patch",
    });
    const user = userEvent.setup();
    renderQueue();

    const install = await screen.findByRole("button", { name: "Install update and restart" });
    await user.click(install);

    expect(installPendingUpdate).toHaveBeenCalled();
    expect(screen.getByText(/Preparing the signed update/i)).toBeInTheDocument();
  });
});
