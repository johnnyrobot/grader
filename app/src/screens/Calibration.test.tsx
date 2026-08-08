import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CalibrationScreen } from "./Calibration";
import { fetchCalibration } from "../api";

vi.mock("../api", () => ({
  fetchCalibration: vi.fn(),
}));

const measured = {
  draft_bias: -0.066,
  n: 34,
  evidence_floor: 20,
  discloses: true,
  demotes: false,
  // Exemplars, not approvals, are the override rate's denominator — they differ
  // here so the screen cannot pass by dividing by the wrong population.
  override_count: 8,
  override_rate: 8 / 30,
  exemplar_count: 30,
  per_criterion: {
    thesis: { bias: -0.136, abs_bias: 0.15, count: 34 },
    evidence: { bias: 0.02, abs_bias: 0.06, count: 30 },
  },
  cold_start: { unlocked: true, exemplar_count: 34, remaining: 0, message: "AI drafts unlocked — 34 exemplars calibrated." },
};

const belowFloor = {
  draft_bias: null,
  n: 12,
  evidence_floor: 20,
  discloses: false,
  demotes: false,
  override_count: 3,
  override_rate: 0.25,
  exemplar_count: 12,
  per_criterion: {},
  cold_start: { unlocked: true, exemplar_count: 12, remaining: 0, message: "AI drafts unlocked — 12 exemplars calibrated." },
};

beforeEach(() => {
  vi.mocked(fetchCalibration).mockReset();
});

describe("CalibrationScreen", () => {
  it("names the direction and size of Draft bias, and the disagreement behind it", async () => {
    vi.mocked(fetchCalibration).mockResolvedValue(measured);
    render(<CalibrationScreen assignmentId="essay-1" onClose={vi.fn()} />);

    expect(await screen.findByRole("heading", { name: "Calibration health" })).toBeInTheDocument();
    expect(fetchCalibration).toHaveBeenCalledWith("essay-1");

    // Direction and size, in the signed convention the engine reports.
    expect(screen.getByText(/drafts run below your grading/i)).toBeInTheDocument();
    expect(screen.getByText("−6.6%")).toBeInTheDocument();
    expect(screen.getByText(/never changes a score/i)).toBeInTheDocument();

    // Per-criterion disagreement, same population.
    const thesis = screen.getByRole("row", { name: /Thesis/ });
    expect(thesis).toHaveTextContent("−13.6%");
    expect(thesis).toHaveTextContent("34");
    expect(screen.getByRole("row", { name: /Evidence/ })).toHaveTextContent("+2.0%");

    // Override rate, Exemplar count and n.
    expect(screen.getByText("27%")).toBeInTheDocument();
    expect(screen.getByText("8 of 30 graded items were corrected")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Exemplars kept" })).toHaveTextContent("30");
    expect(screen.getByRole("group", { name: "Approvals measured" })).toHaveTextContent("34");

    // QWK is a release-gate metric and is not reported here.
    expect(screen.queryByText(/QWK/i)).toBeNull();
  });

  it("reports counts and no direction below the evidence floor, without reading as an error", async () => {
    vi.mocked(fetchCalibration).mockResolvedValue(belowFloor);
    render(<CalibrationScreen assignmentId="essay-1" onClose={vi.fn()} />);

    expect(await screen.findByText("12 of 20 approvals")).toBeInTheDocument();
    expect(screen.getByText(/8 more approvals/i)).toBeInTheDocument();

    // No direction is stated, and nothing here is an error or an empty state.
    expect(screen.queryByText(/runs? (below|above) your grading/i)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();

    // The counts are still reported.
    expect(screen.getByRole("group", { name: "Override rate" })).toHaveTextContent("25%");
    expect(screen.getByRole("group", { name: "Exemplars kept" })).toHaveTextContent("12");
  });

  it("says when a gap is large enough to restructure the queue, and refreshes on request", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchCalibration).mockResolvedValue({ ...measured, draft_bias: -0.136, demotes: true });
    render(<CalibrationScreen assignmentId="essay-1" onClose={vi.fn()} />);

    expect(await screen.findByText(/routed for standard review/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Refresh calibration" }));
    await waitFor(() => expect(fetchCalibration).toHaveBeenCalledTimes(2));
  });

  it("keeps a failed read out of the calibration figures", async () => {
    vi.mocked(fetchCalibration).mockRejectedValue(new Error("calibration: 500"));
    render(<CalibrationScreen assignmentId="essay-1" onClose={vi.fn()} />);

    expect(await screen.findByRole("status")).toHaveTextContent("calibration: 500");
    expect(screen.queryByText(/of a criterion's maximum/i)).toBeNull();
  });
});
