import { afterEach, describe, expect, it, vi } from "vitest";
import {
  attachToGrade,
  canvasImport,
  canvasPost,
  fetchPostQueue,
  flushPostQueue,
  gradeBatch,
} from "./api";

describe("gradeBatch", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lets the engine apply the saved text-model default", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ job_id: "job-1", total: 2 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await gradeBatch("essay-1");

    const request = fetchMock.mock.calls[0][1];
    expect(JSON.parse(request.body)).toEqual({ assignment_id: "essay-1" });
  });

  it("still permits an explicit validated model override", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ job_id: "job-2", total: 1 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await gradeBatch("essay-2", "grader-text:8b");

    const request = fetchMock.mock.calls[0][1];
    expect(JSON.parse(request.body)).toEqual({
      assignment_id: "essay-2",
      model: "grader-text:8b",
    });
  });
});

describe("the Canvas round-trip client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubJson(payload: unknown, ok = true) {
    const fetchMock = vi.fn().mockResolvedValue({
      ok,
      status: ok ? 200 : 500,
      json: vi.fn().mockResolvedValue(payload),
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("asks the engine to download attachments by default", async () => {
    const fetchMock = stubJson({ imported: 3, submissions: [], failures: [] });

    await canvasImport(106, 2841);

    const [url, request] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/canvas/import");
    expect(JSON.parse(request.body)).toEqual({
      course_id: 106,
      assignment_id: 2841,
      download_attachments: true,
    });
  });

  it("reports per-student failures rather than hiding them", async () => {
    stubJson({
      imported: 2,
      submissions: [{ id: "sub_1", canvas_user_id: "98765" }],
      failures: [{ user_id: "1042", error: "no readable text extracted from essay.pdf" }],
    });

    const result = await canvasImport(106, 2841);

    expect(result.imported).toBe(2);
    expect(result.failures).toHaveLength(1);
    expect(result.failures[0].user_id).toBe("1042");
  });

  it("posts only the grade ids it is given", async () => {
    const fetchMock = stubJson({ posted: 1, queued: 0, skipped: [] });

    await canvasPost(106, 2841, ["gr_1"]);

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      course_id: 106,
      assignment_id: 2841,
      grade_ids: ["gr_1"],
    });
  });

  it("surfaces a queued post as success-with-queue, not as an error", async () => {
    // FR-3.5: Canvas being unreachable is an outage, not a lost approval. The
    // engine answers 200 with queued>0, so a UI must not read posted===0 as
    // failure.
    stubJson({
      posted: 0,
      queued: 2,
      queued_reason: "connection refused",
      skipped: [],
      posted_scores: {},
      progress_state: "queued",
      completion: 0,
    });

    const result = await canvasPost(106, 2841, ["gr_1", "gr_2"]);

    expect(result.queued).toBe(2);
    expect(result.progress_state).toBe("queued");
    expect(result.queued_reason).toContain("connection refused");
  });

  it("reads the pending queue", async () => {
    stubJson({ pending: 2, items: [{ user_id: "98765" }, { user_id: "1042" }] });

    const status = await fetchPostQueue();

    expect(status.pending).toBe(2);
  });

  it("reports entries the flush gave up on", async () => {
    stubJson({ posted: 1, failed: 0, abandoned: 1, remaining: 1 });

    const result = await flushPostQueue();

    expect(result.abandoned).toBe(1);
  });

  it("raises when the engine refuses an attachment outside its data directory", async () => {
    stubJson({ detail: "attachments must live under ..." }, false);

    await expect(attachToGrade("gr_1", "/Users/x/.ssh/id_rsa")).rejects.toThrow(/attach/);
  });
});
