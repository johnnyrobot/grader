import { afterEach, describe, expect, it, vi } from "vitest";
import { gradeBatch } from "./api";

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
