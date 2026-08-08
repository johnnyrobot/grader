let base = "http://127.0.0.1:8756";

let token: string | null = null;

export function setToken(t: string) {
  token = t;
}

export function setBaseUrl(url: string) {
  base = url.replace(/\/$/, "");
}

function headers(): HeadersInit {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export interface GradeItem {
  id: string;
  submission_id: string;
  assignment_id: string;
  ai_scores: Record<string, number> | null;
  ai_feedback: string | null;
  confidence: number | null;
  flags: string[];
  lane: string | null;
  approved: boolean;
  teacher_scores: Record<string, number> | null;
  teacher_feedback: string | null;
}

export interface QueueData {
  items: GradeItem[];
  stats: {
    total: number;
    review_first: number;
    standard: number;
    high_confidence: number;
    summary: string;
  };
}

export async function checkHealth(): Promise<boolean> {
  try {
    const r = await fetch(`${base}/health`);
    return r.ok;
  } catch {
    return false;
  }
}

export async function fetchQueue(assignmentId?: string, lane?: string): Promise<QueueData> {
  const params = new URLSearchParams();
  if (assignmentId) params.set("assignment_id", assignmentId);
  if (lane) params.set("lane", lane);
  const r = await fetch(`${base}/api/queue?${params}`, { headers: headers() });
  if (!r.ok) throw new Error(`queue: ${r.status}`);
  return r.json();
}

export async function approveGrade(gradeId: string): Promise<void> {
  const r = await fetch(`${base}/api/grades/${gradeId}/approve`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
  });
  if (!r.ok) throw new Error(`approve: ${r.status}`);
}

export async function overrideGrade(
  gradeId: string,
  teacherScores: Record<string, number>,
  teacherFeedback: string
): Promise<any> {
  const r = await fetch(`${base}/api/grades/${gradeId}/override`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify({ teacher_scores: teacherScores, teacher_feedback: teacherFeedback }),
  });
  if (!r.ok) throw new Error(`override: ${r.status}`);
  return r.json();
}

export async function importSubmissions(
  assignmentId: string,
  submissions: { student_ref: string; text: string }[]
): Promise<number> {
  const r = await fetch(`${base}/api/import`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify({ assignment_id: assignmentId, submissions }),
  });
  if (!r.ok) throw new Error(`import: ${r.status}`);
  return r.json().then((j) => j.imported);
}

export async function createRubric(rubricText: string, title?: string): Promise<any> {
  const r = await fetch(`${base}/api/rubrics`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify({ rubric_text: rubricText, title }),
  });
  if (!r.ok) throw new Error(`rubric: ${r.status}`);
  return r.json();
}

export async function getRubric(title: string): Promise<any> {
  const r = await fetch(`${base}/api/rubrics/${encodeURIComponent(title)}`, { headers: headers() });
  if (!r.ok) throw new Error(`get rubric: ${r.status}`);
  return r.json();
}

export async function listRubrics(): Promise<any[]> {
  const r = await fetch(`${base}/api/rubrics`, { headers: headers() });
  if (!r.ok) throw new Error(`list rubrics: ${r.status}`);
  return r.json();
}

export async function gradeBatch(assignmentId: string, model?: string): Promise<{ job_id: string; total: number }> {
  const body: { assignment_id: string; model?: string } = { assignment_id: assignmentId };
  if (model) body.model = model;
  const r = await fetch(`${base}/api/grade/batch`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`grade: ${r.status}`);
  return r.json();
}

export async function fetchCalibration(assignmentId: string): Promise<any> {
  const r = await fetch(`${base}/api/calibration/${assignmentId}`, { headers: headers() });
  if (!r.ok) throw new Error(`calibration: ${r.status}`);
  return r.json();
}

export async function fetchModels(): Promise<{ available: any[]; ollama_running: boolean }> {
  const r = await fetch(`${base}/api/models`, { headers: headers() });
  if (!r.ok) throw new Error(`models: ${r.status}`);
  return r.json();
}

export async function installModel(modelName: string): Promise<{ success: boolean; error?: string }> {
  const r = await fetch(`${base}/api/models/install`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify({ model_name: modelName }),
  });
  if (!r.ok) throw new Error(`install: ${r.status}`);
  return r.json();
}

export function connectWebSocket(onMessage: (msg: any) => void): WebSocket {
  const ws = new WebSocket(`${base.replace("http", "ws")}/ws/jobs`);
  ws.onmessage = (e) => onMessage(JSON.parse(e.data));
  return ws;
}

export async function checkUpdates(): Promise<{
  update_available: boolean;
  current_version?: string;
  latest_version?: string;
  notes?: string;
  error?: string;
}> {
  const r = await fetch(`${base}/api/updates/check`, { headers: headers() });
  if (!r.ok) throw new Error(`updates: ${r.status}`);
  return r.json();
}

export async function getSettings(): Promise<Record<string, string | number>> {
  const r = await fetch(`${base}/api/settings`, { headers: headers() });
  if (!r.ok) throw new Error(`settings: ${r.status}`);
  return r.json();
}

export async function updateSettings(settings: Record<string, string | number>): Promise<{ success: boolean }> {
  const r = await fetch(`${base}/api/settings`, {
    method: "PUT",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!r.ok) throw new Error(`settings: ${r.status}`);
  return r.json();
}

export async function saveRubric(rubricData: any): Promise<{ id: number }> {
  const r = await fetch(`${base}/api/rubrics`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify(rubricData),
  });
  if (!r.ok) throw new Error(`save rubric: ${r.status}`);
  return r.json();
}

// ---------------------------------------------------------------------------
// Canvas round-trip.
//
// The engine has implemented import, post, the offline queue and comment
// attachments for some time; this file had no way to reach any of it, so no
// screen could either. These are the client functions a Canvas UI needs. They
// commit to no particular screen layout.
// ---------------------------------------------------------------------------

export interface CanvasImportResult {
  imported: number;
  submissions: { id: string; canvas_user_id: string }[];
  failures: { user_id: string; error: string }[];
}

export interface CanvasPostResult {
  posted: number;
  /** Non-zero when Canvas was unreachable and the posts were queued (FR-3.5). */
  queued: number;
  queued_reason?: string;
  skipped: string[];
  posted_scores: Record<string, number>;
  progress_state: string;
  completion: number;
}

export interface PostQueueStatus {
  pending: number;
  items: Record<string, unknown>[];
}

export interface FlushResult {
  posted: number;
  failed: number;
  /** Entries that burned their retries and stopped being attempted. */
  abandoned: number;
  remaining: number;
}

export async function canvasImport(
  courseId: number,
  assignmentId: number,
  downloadAttachments = true,
): Promise<CanvasImportResult> {
  const r = await fetch(`${base}/api/canvas/import`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify({
      course_id: courseId,
      assignment_id: assignmentId,
      download_attachments: downloadAttachments,
    }),
  });
  if (!r.ok) throw new Error(`canvas import: ${r.status}`);
  return r.json();
}

export async function canvasPost(
  courseId: number,
  assignmentId: number,
  gradeIds: string[],
): Promise<CanvasPostResult> {
  const r = await fetch(`${base}/api/canvas/post`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify({
      course_id: courseId,
      assignment_id: assignmentId,
      grade_ids: gradeIds,
    }),
  });
  if (!r.ok) throw new Error(`canvas post: ${r.status}`);
  return r.json();
}

export async function fetchPostQueue(): Promise<PostQueueStatus> {
  const r = await fetch(`${base}/api/canvas/post-queue`, { headers: headers() });
  if (!r.ok) throw new Error(`post queue: ${r.status}`);
  return r.json();
}

export async function flushPostQueue(): Promise<FlushResult> {
  const r = await fetch(`${base}/api/canvas/flush`, {
    method: "POST",
    headers: headers(),
  });
  if (!r.ok) throw new Error(`flush queue: ${r.status}`);
  return r.json();
}

export async function listGradeAttachments(
  gradeId: string,
): Promise<{ grade_id: string; attachments: { path: string; exists: boolean }[] }> {
  const r = await fetch(`${base}/api/grades/${gradeId}/attachments`, {
    headers: headers(),
  });
  if (!r.ok) throw new Error(`attachments: ${r.status}`);
  return r.json();
}

export async function attachToGrade(gradeId: string, path: string): Promise<{ path: string }> {
  const r = await fetch(`${base}/api/grades/${gradeId}/attachments`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!r.ok) throw new Error(`attach: ${r.status}`);
  return r.json();
}
