import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SplitView } from "./SplitView";
import type { CalibrationHealth, GradeItem } from "../api";
import { approveGrade, fetchCalibration, overrideGrade } from "../api";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    approveGrade: vi.fn().mockResolvedValue(undefined),
    overrideGrade: vi.fn().mockResolvedValue({ ai_total: 7, teacher_total: 8, refinement_proposals: [] }),
    fetchCalibration: vi.fn(),
  };
});

/** The same payload the calibration screen reads — no per-draft figure exists. */
const calibration: CalibrationHealth = {
  draft_bias: -0.066,
  n: 34,
  evidence_floor: 20,
  discloses: true,
  demotes: false,
  override_count: 8,
  override_rate: 8 / 30,
  exemplar_count: 30,
  per_criterion: { Thesis: { bias: -0.136, abs_bias: 0.15, count: 34 } },
  cold_start: { unlocked: true, exemplar_count: 30, remaining: 0, message: "AI drafts unlocked — 30 exemplars calibrated." },
};

const item: GradeItem = {
  id: "grade-1",
  submission_id: "Student 1042",
  assignment_id: "essay-1",
  ai_scores: { Thesis: 4, Evidence: 3 },
  ai_feedback: "Clear claim. Add a second source.",
  confidence: 0.64,
  flags: ["citation_check"],
  lane: "review_first",
  approved: false,
  teacher_scores: null,
  teacher_feedback: null,
};

beforeEach(() => {
  // Nothing to disclose is the ordinary case: an assignment early in its grading.
  vi.mocked(fetchCalibration).mockReset();
  vi.mocked(fetchCalibration).mockResolvedValue({
    ...calibration, draft_bias: null, n: 4, discloses: false, per_criterion: {},
  });
});

describe("SplitView", () => {
  it("presents the AI result as an editable draft with explicit teacher ownership", async () => {
    const user = userEvent.setup();
    render(<SplitView item={item} onBack={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "Review Student 1042" })).toBeInTheDocument();
    expect(screen.getByText("AI suggestion — teacher decision required")).toBeInTheDocument();
    expect(screen.getByText("Citation check (experimental)")).toBeInTheDocument();
    expect(screen.getByText("64% confidence")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "5 points for Thesis" })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("textbox", { name: "Feedback for Student 1042" })).toHaveValue(item.ai_feedback);

    await user.click(screen.getByRole("button", { name: "Approve AI draft" }));
    await waitFor(() => expect(approveGrade).toHaveBeenCalledWith("grade-1"));
    expect(await screen.findByText("Grade approved. It is ready for your next posting step.")).toBeInTheDocument();
  });

  it("saves changed scores and feedback as a teacher override", async () => {
    const user = userEvent.setup();
    render(<SplitView item={item} onBack={vi.fn()} />);

    await user.click(screen.getByRole("radio", { name: "5 points for Thesis" }));
    const feedback = screen.getByRole("textbox", { name: "Feedback for Student 1042" });
    await user.clear(feedback);
    await user.type(feedback, "Strong claim. Add one more cited example.");
    await user.click(screen.getByRole("button", { name: "Save changes and approve" }));

    await waitFor(() => expect(overrideGrade).toHaveBeenCalledWith(
      "grade-1",
      { Thesis: 5, Evidence: 3 },
      "Strong claim. Add one more cited example.",
    ));
    expect(await screen.findByText("Your changes are approved. Score updated from 7 to 8.")).toBeInTheDocument();
    expect(screen.queryByText("Teacher changes not saved yet.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve AI draft" })).toHaveTextContent("Approved");
  });

  it("saves feedback-only edits without leaving a stale unsaved warning", async () => {
    vi.mocked(overrideGrade).mockResolvedValueOnce({
      ai_total: 7,
      teacher_total: 7,
      refinement_proposals: [],
    });
    const user = userEvent.setup();
    render(<SplitView item={item} onBack={vi.fn()} />);

    const feedback = screen.getByRole("textbox", { name: "Feedback for Student 1042" });
    await user.clear(feedback);
    await user.type(feedback, "Keep the claim. Add one concrete citation connection.");
    expect(screen.getByText("Teacher changes not saved yet.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save changes and approve" }));

    await waitFor(() => expect(overrideGrade).toHaveBeenCalledWith(
      "grade-1",
      { Thesis: 4, Evidence: 3 },
      "Keep the claim. Add one concrete citation connection.",
    ));
    expect(await screen.findByText("Your changes are approved. Feedback updated; score remains 7.")).toBeInTheDocument();
    expect(screen.queryByText("Teacher changes not saved yet.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve AI draft" })).toHaveTextContent("Approved");
  });

  it("treats numeric string totals as unchanged after a feedback-only approval", async () => {
    vi.mocked(overrideGrade).mockResolvedValueOnce({
      ai_total: "7",
      teacher_total: "7",
      refinement_proposals: [],
    });
    const user = userEvent.setup();
    render(<SplitView item={item} onBack={vi.fn()} />);

    const feedback = screen.getByRole("textbox", { name: "Feedback for Student 1042" });
    await user.clear(feedback);
    await user.type(feedback, "Feedback-only edit that should not report a score change.");
    await user.click(screen.getByRole("button", { name: "Save changes and approve" }));

    expect(await screen.findByText("Your changes are approved. Feedback updated; score remains 7.")).toBeInTheDocument();
    expect(screen.queryByText("Score updated from 7 to 7")).not.toBeInTheDocument();
    expect(screen.queryByText("Teacher changes not saved yet.")).not.toBeInTheDocument();
  });
});

describe("SplitView draft bias disclosure", () => {
  it("states the assignment's Draft bias where the teacher reads the score", async () => {
    vi.mocked(fetchCalibration).mockResolvedValue(calibration);
    render(<SplitView item={item} onBack={vi.fn()} />);

    const note = await screen.findByText(/Across 34 approvals on this assignment/);
    // The calibration screen's payload, for the assignment this draft belongs to.
    expect(fetchCalibration).toHaveBeenCalledWith("essay-1");

    // Direction, rough size, and how many approvals it rests on.
    expect(note).toHaveTextContent("about 7% of a criterion's maximum below your grading");

    // Assignment-level only: Grader has no per-draft bias figure and claims none.
    expect(note).toHaveTextContent("not a claim about this draft");

    // Announced when it arrives, like every other message this app renders after
    // its screen — a disclosure nobody is told about has not been disclosed.
    expect(note).toHaveAttribute("role", "status");
    expect(note).toHaveAttribute("aria-live", "polite");
  });

  it("says nothing when the gap is measured but too small to disclose", async () => {
    vi.mocked(fetchCalibration).mockResolvedValue({
      ...calibration, draft_bias: -0.012, discloses: false,
    });
    render(<SplitView item={item} onBack={vi.fn()} />);

    await waitFor(() => expect(fetchCalibration).toHaveBeenCalledWith("essay-1"));
    expect(screen.queryByText(/on this assignment/)).toBeNull();
    expect(screen.queryByText(/your grading/i)).toBeNull();
  });

  it("says nothing below the evidence floor, where there is no direction to state", async () => {
    // 12 approvals: the engine reports counts and withholds the direction, and no
    // consumer may infer one from the counts.
    vi.mocked(fetchCalibration).mockResolvedValue({
      ...calibration, draft_bias: null, n: 12, discloses: false, per_criterion: {},
    });
    render(<SplitView item={item} onBack={vi.fn()} />);

    await waitFor(() => expect(fetchCalibration).toHaveBeenCalledWith("essay-1"));
    expect(screen.queryByText(/on this assignment/)).toBeNull();
    expect(screen.queryByText(/12 approvals/)).toBeNull();
  });

  it("leaves the teacher's decision intact when calibration cannot be read", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchCalibration).mockRejectedValue(new Error("calibration: 500"));
    render(<SplitView item={item} onBack={vi.fn()} />);

    await waitFor(() => expect(fetchCalibration).toHaveBeenCalledWith("essay-1"));
    expect(screen.queryByText(/on this assignment/)).toBeNull();
    // A calibration read that failed is not the teacher's problem mid-review.
    expect(screen.queryByText(/calibration: 500/)).toBeNull();

    await user.click(screen.getByRole("button", { name: "Approve AI draft" }));
    expect(await screen.findByText("Grade approved. It is ready for your next posting step.")).toBeInTheDocument();
  });
});
