import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SplitView } from "./SplitView";
import type { GradeItem } from "../api";
import { approveGrade, overrideGrade } from "../api";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    approveGrade: vi.fn().mockResolvedValue(undefined),
    overrideGrade: vi.fn().mockResolvedValue({ ai_total: 7, teacher_total: 8, refinement_proposals: [] }),
  };
});

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
  });
});
