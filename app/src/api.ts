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
  const r = await fetch(`${base}/api/grade/batch`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify({ assignment_id: assignmentId, model: model || "grader-text:8b" }),
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
