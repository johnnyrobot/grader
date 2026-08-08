import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
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

describe("posting approved grades to Canvas", () => {
  const postable = {
    submission_id: "Student 98765",
    assignment_id: "canvas_106_2841",
    ai_scores: { thesis: 4 },
    ai_feedback: "Good work.",
    confidence: 0.9,
    flags: [],
    lane: "standard",
    teacher_scores: null,
    teacher_feedback: null,
  };

  const canvasQueue: QueueData = {
    items: [
      { ...postable, id: "gr_1", approved: true },
      { ...postable, id: "gr_2", approved: false },
    ],
    stats: { total: 2, review_first: 0, standard: 2, high_confidence: 0, summary: "2 to review" },
  };

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  function renderCanvasQueue(onRefresh = vi.fn()) {
    return render(
      <QueueScreen
        data={canvasQueue}
        onSelectItem={vi.fn()}
        assignmentId="canvas_106_2841"
        setAssignmentId={vi.fn()}
        selectedLane={undefined}
        setSelectedLane={vi.fn()}
        onRefresh={onRefresh}
        onOpenModels={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );
  }

  it("offers no post button for a batch that did not come from Canvas", () => {
    render(
      <QueueScreen
        data={canvasQueue}
        onSelectItem={vi.fn()}
        assignmentId="essay-1"
        setAssignmentId={vi.fn()}
        selectedLane={undefined}
        setSelectedLane={vi.fn()}
        onRefresh={vi.fn()}
        onOpenModels={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: /Post .* to Canvas/ })).toBeNull();
  });

  it("posts only the grades the teacher has approved", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        posted: 1, queued: 0, skipped: [], posted_scores: { "98765": 7 },
        progress_state: "completed", completion: 100,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    renderCanvasQueue();

    fireEvent.click(screen.getByRole("button", { name: /Post 1 approved to Canvas/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.grade_ids).toEqual(["gr_1"]);
    expect(body.course_id).toBe(106);
    expect(body.assignment_id).toBe(2841);
  });

  it("shows a per-student receipt after posting", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        posted: 1, queued: 0, skipped: [], posted_scores: { "98765": 7 },
        progress_state: "completed", completion: 100,
      }),
    }));
    renderCanvasQueue();

    fireEvent.click(screen.getByRole("button", { name: /Post 1 approved/ }));

    expect(await screen.findByText(/Posted 1 grade to Canvas/)).toBeInTheDocument();
    expect(screen.getByText(/Student 98765: 7/)).toBeInTheDocument();
  });

  it("tells the teacher a queued post is kept, not lost", async () => {
    // FR-3.5. posted === 0 here, and reading that as failure would be wrong.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        posted: 0, queued: 1, queued_reason: "connection refused", skipped: [],
        posted_scores: {}, progress_state: "queued", completion: 0,
      }),
    }));
    renderCanvasQueue();

    fireEvent.click(screen.getByRole("button", { name: /Post 1 approved/ }));

    const receipt = await screen.findByText(/Canvas was unreachable/);
    expect(receipt).toBeInTheDocument();
    expect(receipt.textContent).toMatch(/queued and will post/);
  });

  it("surfaces what the engine refused to post", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        posted: 0, queued: 0, skipped: ["gr_9: not approved"],
        posted_scores: {}, progress_state: "completed", completion: 100,
      }),
    }));
    renderCanvasQueue();

    fireEvent.click(screen.getByRole("button", { name: /Post 1 approved/ }));

    expect(await screen.findByText(/Skipped: gr_9: not approved/)).toBeInTheDocument();
  });
});

describe("importing a batch from Canvas", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  function renderQueue(setAssignmentId = vi.fn(), onRefresh = vi.fn()) {
    render(
      <QueueScreen
        data={queue}
        onSelectItem={vi.fn()}
        assignmentId=""
        setAssignmentId={setAssignmentId}
        selectedLane={undefined}
        setSelectedLane={vi.fn()}
        onRefresh={onRefresh}
        onOpenModels={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );
  }

  function typeIds() {
    fireEvent.change(screen.getByLabelText("Canvas course"), { target: { value: "106" } });
    fireEvent.change(screen.getByLabelText("Canvas assignment"), { target: { value: "2841" } });
  }

  it("asks the engine for that course and assignment", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ imported: 3, submissions: [], failures: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);
    renderQueue();
    typeIds();

    fireEvent.click(screen.getByRole("button", { name: "Import from Canvas" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body).toEqual({ course_id: 106, assignment_id: 2841, download_attachments: true });
  });

  it("points the queue at what it just imported", async () => {
    const setAssignmentId = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ imported: 3, submissions: [], failures: [] }),
    }));
    renderQueue(setAssignmentId);
    typeIds();

    fireEvent.click(screen.getByRole("button", { name: "Import from Canvas" }));

    await waitFor(() => expect(setAssignmentId).toHaveBeenCalledWith("canvas_106_2841"));
  });

  it("names the students whose files could not be read", async () => {
    // The engine reports these per student now; burying them would leave a
    // teacher believing a submission was graded when nothing was extracted.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        imported: 2,
        submissions: [],
        failures: [{ user_id: "1042", error: "no readable text extracted from essay.pdf" }],
      }),
    }));
    renderQueue();
    typeIds();

    fireEvent.click(screen.getByRole("button", { name: "Import from Canvas" }));

    expect(await screen.findByText(/Student 1042: no readable text/)).toBeInTheDocument();
  });

  it("refuses to call the engine without numeric ids", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderQueue();
    fireEvent.change(screen.getByLabelText("Canvas course"), { target: { value: "not a course" } });

    fireEvent.click(screen.getByRole("button", { name: "Import from Canvas" }));

    expect(await screen.findByText(/numeric Canvas course and assignment ids/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
