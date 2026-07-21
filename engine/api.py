"""FastAPI engine — the single interface the Tauri shell talks to.

Loopback only, per-launch bearer token. The shell starts this as a sidecar and talks
to it over localhost HTTP + WebSocket. All AI/document/Canvas work lives in the engine;
the shell is a thin client.

Run:
  uv run uvicorn engine.api:app --host 127.0.0.1 --port 8756 --reload

The engine reuses grader.agent.grade, grader.triage.triage, grader.overrides.apply_override,
grader.calibration.compute_health, grader.calibration.can_draft as-is — no logic is
duplicated from the package.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator, model_validator

from grader.agent import grade
from grader.calibration import cold_start_status, compute_health
from grader.exemplars import ExemplarStore, render_anchors
from grader.overrides import OverrideStore, apply_override, detect_patterns
from grader.rubrics import RubricStore, parse_pasted_rubric
from grader.schema import GradeDraft, Rubric
from grader.triage import triage

# ---------------------------------------------------------------------------
# Per-launch auth: the shell gets the token from the engine's stdout on startup.
# ---------------------------------------------------------------------------

ENGINE_TOKEN = os.environ.get("GRADER_ENGINE_TOKEN") or secrets.token_urlsafe(32)
DATA_DIR = Path(os.environ.get("GRADER_DATA_DIR", "fixtures"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Grader Engine", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "tauri://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _auth(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    if authorization.removeprefix("Bearer ") != ENGINE_TOKEN:
        raise HTTPException(403, "invalid token")


# ---------------------------------------------------------------------------
# Persistence: SQLite WAL for the job queue + grade records + audit log.
# ---------------------------------------------------------------------------

DB_PATH = DATA_DIR / "grader_engine.db"

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    id            TEXT PRIMARY KEY,
    assignment_id TEXT NOT NULL,
    student_ref   TEXT,
    text          TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'imported',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sub_assignment ON submissions(assignment_id);
CREATE INDEX IF NOT EXISTS idx_sub_state ON submissions(state);

CREATE TABLE IF NOT EXISTS grade_records (
    id              TEXT PRIMARY KEY,
    submission_id   TEXT NOT NULL,
    assignment_id   TEXT NOT NULL,
    rubric_version  INTEGER NOT NULL DEFAULT 1,
    ai_scores_json  TEXT,
    ai_feedback     TEXT,
    ai_confidence   REAL,
    ai_flags_json   TEXT,
    teacher_scores_json  TEXT,
    teacher_feedback     TEXT,
    lane            TEXT,
    approved_at     TEXT,
    posted_at       TEXT,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (submission_id) REFERENCES submissions(id)
);
CREATE INDEX IF NOT EXISTS idx_gr_assignment ON grade_records(assignment_id);
CREATE INDEX IF NOT EXISTS idx_gr_lane ON grade_records(lane);

CREATE TABLE IF NOT EXISTS audit_events (
    id          INTEGER PRIMARY KEY,
    entity_ref  TEXT NOT NULL,
    actor        TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_events(entity_ref);
"""


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.executescript(DB_SCHEMA)
    db.execute("PRAGMA journal_mode=WAL")
    return db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _audit(entity: str, actor: str, event: str, payload: dict) -> None:
    db = _db()
    db.execute(
        "INSERT INTO audit_events (entity_ref, actor, event_type, payload, created_at) "
        "VALUES (?,?,?,?,?)",
        (entity, actor, event, json.dumps(payload), _now()),
    )
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Stores (shared across requests)
# ---------------------------------------------------------------------------

_exemplar_store = ExemplarStore(DATA_DIR / "exemplars.db")
_exemplar_store.db.close()
_exemplar_store.db = sqlite3.connect(str(DATA_DIR / "exemplars.db"), check_same_thread=False)
_exemplar_store.db.row_factory = sqlite3.Row

_override_store = OverrideStore(DATA_DIR / "overrides.db")
_override_store.db.close()
_override_store.db = sqlite3.connect(str(DATA_DIR / "overrides.db"), check_same_thread=False)
_override_store.db.row_factory = sqlite3.Row

_rubric_store = RubricStore(DATA_DIR / "rubrics.db")
_rubric_store.db.close()
_rubric_store.db = sqlite3.connect(str(DATA_DIR / "rubrics.db"), check_same_thread=False)
_rubric_store.db.row_factory = sqlite3.Row


# ---------------------------------------------------------------------------
# Job queue: background grading with WebSocket progress.
# ---------------------------------------------------------------------------

_jobs: dict[str, dict] = {}
_job_lock = threading.Lock()
_ws_clients: list[WebSocket] = []


def _broadcast(msg: dict) -> None:
    for ws in list(_ws_clients):
        try:
            ws.send_json(msg)
        except Exception:
            _ws_clients.remove(ws)


def _run_grading_job(
    job_id: str,
    assignment_id: str,
    submission_ids: list[str],
    rubric: Rubric,
    model: str,
    materials: str,
    low_confidence: float,
    high_confidence: float,
) -> None:
    with _job_lock:
        _jobs[job_id]["status"] = "running"

    total = len(submission_ids)
    results: list[dict] = []

    for i, sub_id in enumerate(submission_ids):
        db = _db()
        row = db.execute("SELECT * FROM submissions WHERE id = ?", (sub_id,)).fetchone()
        db.close()
        if not row:
            continue

        submission_text = row["text"]
        record_id = f"gr_{sub_id}"

        anchors_txt = ""
        if _exemplar_store.count(assignment_id) >= 3:
            hits = _exemplar_store.retrieve(assignment_id, submission_text, k=6, balance=True)
            anchor_ids = [c.id for c in rubric.criteria]
            anchors_txt = render_anchors(hits, anchor_ids)

        run = grade(rubric, submission_text, model_name=model, mode="tool",
                    anchors=anchors_txt, materials=materials)

        if not run.ok:
            state = "error"
            record = {
                "id": record_id, "submission_id": sub_id, "assignment_id": assignment_id,
                "error": run.error, "lane": None, "ai_scores": None,
                "ai_feedback": None, "confidence": None, "flags": None,
            }
        else:
            t = triage(
                run.draft,
                rubric,
                submission_text,
                model_name=model,
                anchors=anchors_txt,
                materials=materials,
                low_confidence=low_confidence,
                high_confidence=high_confidence,
            )
            state = "drafted"
            ai_scores = {cs.criterion_id: cs.score for cs in run.draft.criterion_scores}
            record = {
                "id": record_id, "submission_id": sub_id, "assignment_id": assignment_id,
                "ai_scores": ai_scores, "ai_feedback": run.draft.feedback,
                "confidence": run.draft.confidence,
                "flags": [f.value for f in run.draft.flags],
                "lane": t.lane.value, "confidence_level": t.confidence,
                "triage_reasons": t.reasons, "triage_consistency": t.consistency,
                "seconds": run.seconds, "retries": run.retries,
            }

        db = _db()
        db.execute(
            "INSERT OR REPLACE INTO grade_records "
            "(id, submission_id, assignment_id, rubric_version, ai_scores_json, ai_feedback, "
            "ai_confidence, ai_flags_json, lane, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (record_id, sub_id, assignment_id, 1,
             json.dumps(record.get("ai_scores")) if record.get("ai_scores") else None,
             record.get("ai_feedback"),
             record.get("confidence"),
             json.dumps(record.get("flags")) if record.get("flags") else None,
             record.get("lane"), _now()),
        )
        db.execute("UPDATE submissions SET state = ? WHERE id = ?", (state, sub_id))
        db.commit()
        db.close()

        _audit(record_id, "engine", "graded", record)
        results.append(record)

        with _job_lock:
            _jobs[job_id]["done"] = i + 1
        _broadcast({"type": "job_progress", "job_id": job_id, "done": i + 1,
                     "total": total, "latest": record})

    with _job_lock:
        _jobs[job_id]["status"] = "completed"
        _jobs[job_id]["results"] = results
    _broadcast({"type": "job_done", "job_id": job_id, "results": results})


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------

class ImportSubmissions(BaseModel):
    assignment_id: str
    submissions: list[dict] = Field(description="[{student_ref, text}] — student_ref is a local pseudonym")


class CreateRubric(BaseModel):
    rubric_text: str | None = None
    title: str | None = None


class GradeBatch(BaseModel):
    assignment_id: str
    model: str | None = None
    materials: str = ""
    submission_ids: list[str] | None = None

    @field_validator("model")
    @classmethod
    def validate_model_override(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("grader-text:"):
            raise ValueError("grading model must use a capped grader-text tag")
        return value


class OverrideRequest(BaseModel):
    teacher_scores: dict[str, float]
    teacher_feedback: str = ""


class ApproveRequest(BaseModel):
    pass


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "token": ENGINE_TOKEN[:8] + "..."}


@app.post("/api/import", dependencies=[Depends(_auth)])
def import_submissions(req: ImportSubmissions) -> dict:
    db = _db()
    count = 0
    for s in req.submissions:
        sub_id = f"sub_{secrets.token_hex(8)}"
        db.execute(
            "INSERT INTO submissions (id, assignment_id, student_ref, text, state, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (sub_id, req.assignment_id, s.get("student_ref"), s["text"],
             "imported", _now()),
        )
        count += 1
    db.commit()
    db.close()
    _audit(req.assignment_id, "teacher", "import", {"count": count})
    return {"imported": count}


@app.post("/api/rubrics", dependencies=[Depends(_auth)])
def create_rubric(req: CreateRubric) -> dict:
    if req.rubric_text:
        rubric = parse_pasted_rubric(req.rubric_text, title=req.title)
    elif req.title:
        rubric = Rubric(title=req.title, criteria=[])
    else:
        raise HTTPException(400, "provide rubric_text or title")
    rid = _rubric_store.save(rubric)
    _audit(str(rid), "teacher", "rubric_create", {"title": rubric.title})
    return {"id": rid, "title": rubric.title, "criteria_count": len(rubric.criteria)}


@app.get("/api/rubrics", dependencies=[Depends(_auth)])
def list_rubrics() -> list[dict]:
    return _rubric_store.list()


@app.get("/api/rubrics/{title}", dependencies=[Depends(_auth)])
def get_rubric(title: str, version: int | None = None) -> dict:
    r = _rubric_store.get(title, version=version)
    if not r:
        raise HTTPException(404, "rubric not found")
    return json.loads(r.model_dump_json())


@app.post("/api/grade/batch", dependencies=[Depends(_auth)])
def grade_batch(req: GradeBatch) -> dict:
    rubrics = _rubric_store.list()
    if not rubrics:
        raise HTTPException(400, "no rubric — create one first")
    rubric = _rubric_store.get(rubrics[0]["title"])
    if not rubric:
        raise HTTPException(400, "rubric not found")

    db = _db()
    if req.submission_ids:
        placeholders = ",".join("?" for _ in req.submission_ids)
        rows = db.execute(
            f"SELECT id FROM submissions WHERE assignment_id = ? AND state = 'imported' "
            f"AND id IN ({placeholders})",
            (req.assignment_id, *req.submission_ids),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id FROM submissions WHERE assignment_id = ? AND state = 'imported'",
            (req.assignment_id,),
        ).fetchall()
    db.close()
    sub_ids = [r["id"] for r in rows]
    if not sub_ids:
        raise HTTPException(400, "no submissions to grade for this assignment")

    settings = _load_settings()
    model = req.model or str(settings["model_text"])
    low_confidence = float(settings["review_first_threshold"])
    high_confidence = float(settings["auto_approve_threshold"])
    _exemplar_store.embedding_model = str(settings["model_embed"])

    job_id = f"job_{secrets.token_hex(8)}"
    with _job_lock:
        _jobs[job_id] = {"status": "queued", "total": len(sub_ids), "done": 0, "assignment_id": req.assignment_id}

    thread = threading.Thread(
        target=_run_grading_job,
        args=(
            job_id,
            req.assignment_id,
            sub_ids,
            rubric,
            model,
            req.materials,
            low_confidence,
            high_confidence,
        ),
        daemon=True,
    )
    thread.start()
    return {"job_id": job_id, "total": len(sub_ids)}


@app.get("/api/queue", dependencies=[Depends(_auth)])
def get_queue(assignment_id: str | None = None, lane: str | None = None) -> dict:
    db = _db()
    query = "SELECT * FROM grade_records"
    params: list[Any] = []
    conditions: list[str] = []
    if assignment_id:
        conditions.append("assignment_id = ?")
        params.append(assignment_id)
    if lane:
        conditions.append("lane = ?")
        params.append(lane)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at DESC"
    rows = db.execute(query, params).fetchall()
    db.close()

    items = []
    lane_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        item = {
            "id": r["id"], "submission_id": r["submission_id"],
            "assignment_id": r["assignment_id"],
            "ai_scores": json.loads(r["ai_scores_json"]) if r["ai_scores_json"] else None,
            "ai_feedback": r["ai_feedback"],
            "confidence": r["ai_confidence"],
            "flags": json.loads(r["ai_flags_json"]) if r["ai_flags_json"] else [],
            "lane": r["lane"],
            "approved": r["approved_at"] is not None,
            "teacher_scores": json.loads(r["teacher_scores_json"]) if r["teacher_scores_json"] else None,
            "teacher_feedback": r["teacher_feedback"],
        }
        items.append(item)
        if r["lane"]:
            lane_counts[r["lane"]] += 1

    total = len(items)
    review_first = lane_counts.get("review_first", 0)
    standard = lane_counts.get("standard", 0)
    high_conf = lane_counts.get("high_confidence", 0)
    must_read = review_first + standard
    spot = round(high_conf * 0.2)
    effective = must_read + spot
    saved = 1 - effective / total if total else 0

    return {
        "items": items,
        "stats": {
            "total": total, "review_first": review_first,
            "standard": standard, "high_confidence": high_conf,
            "summary": (f"{total} submissions → read {must_read} + spot-check {spot} "
                        f"of {high_conf} = {effective} ({saved:.0%} less reading)")
                if total else "empty queue",
        },
    }


@app.post("/api/grades/{grade_id}/approve", dependencies=[Depends(_auth)])
def approve_grade(grade_id: str) -> dict:
    db = _db()
    row = db.execute("SELECT * FROM grade_records WHERE id = ?", (grade_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "grade not found")
    if row["approved_at"]:
        db.close()
        raise HTTPException(409, "already approved")
    db.execute("UPDATE grade_records SET approved_at = ? WHERE id = ?", (_now(), grade_id))
    db.commit()
    db.close()
    _audit(grade_id, "teacher", "approve", {})
    return {"approved": True, "grade_id": grade_id}


@app.post("/api/grades/{grade_id}/override", dependencies=[Depends(_auth)])
def override_grade(grade_id: str, req: OverrideRequest) -> dict:
    db = _db()
    row = db.execute("SELECT * FROM grade_records WHERE id = ?", (grade_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "grade not found")
    sub_row = db.execute("SELECT * FROM submissions WHERE id = ?", (row["submission_id"],)).fetchone()
    db.close()

    ai_scores = json.loads(row["ai_scores_json"]) if row["ai_scores_json"] else {}
    ai_flags = json.loads(row["ai_flags_json"]) if row["ai_flags_json"] else []
    from grader.schema import CriterionScore, Flag
    draft = GradeDraft(
        criterion_scores=[CriterionScore(criterion_id=cid, score=s, rationale="AI draft")
                          for cid, s in ai_scores.items()],
        feedback=row["ai_feedback"] or "",
        confidence=row["ai_confidence"] or 0.5,
        flags=[Flag(f) for f in ai_flags],
    )

    rubric_row = _rubric_store.list()
    rubric = _rubric_store.get(rubric_row[0]["title"]) if rubric_row else None
    max_total = rubric.total_points if rubric else 5.0

    _exemplar_store.embedding_model = str(_load_settings()["model_embed"])
    ov = apply_override(
        _override_store, _exemplar_store,
        assignment_id=row["assignment_id"],
        submission_text=sub_row["text"] if sub_row else "",
        ai_draft=draft,
        teacher_scores=req.teacher_scores,
        teacher_feedback=req.teacher_feedback,
        max_total=max_total,
    )

    db = _db()
    db.execute(
        "UPDATE grade_records SET teacher_scores_json = ?, teacher_feedback = ?, "
        "approved_at = ? WHERE id = ?",
        (json.dumps(req.teacher_scores), req.teacher_feedback, _now(), grade_id),
    )
    db.commit()
    db.close()

    _audit(grade_id, "teacher", "override",
           {"ai_total": ov.ai_total, "teacher_total": ov.teacher_total, "delta": ov.criterion_delta})

    proposals = detect_patterns(_override_store.list(row["assignment_id"]), min_pattern=3)
    return {
        "overridden": True, "grade_id": grade_id,
        "ai_total": ov.ai_total, "teacher_total": ov.teacher_total,
        "exemplar_count": _exemplar_store.count(row["assignment_id"]),
        "refinement_proposals": [
            {"criterion_id": p.criterion_id, "direction": p.direction,
             "count": p.pattern_count, "avg_delta": p.avg_delta,
             "suggested_text": p.suggested_text}
            for p in proposals
        ],
    }


@app.get("/api/calibration/{assignment_id}", dependencies=[Depends(_auth)])
def calibration_health(assignment_id: str) -> dict:
    health = compute_health(_override_store, _exemplar_store, assignment_id)
    cold = cold_start_status(_exemplar_store, assignment_id)
    return {
        "qwk": health.qwk, "exact": health.exact, "adjacent": health.adjacent,
        "mean_error": health.mean_error, "override_count": health.override_count,
        "override_rate": health.override_rate, "exemplar_count": health.exemplar_count,
        "per_criterion": health.per_criterion,
        "qwk_trend": health.qwk_trend,
        "cold_start": cold,
    }


@app.get("/api/models", dependencies=[Depends(_auth)])
def list_models() -> dict:
    import httpx
    try:
        r = httpx.get("http://localhost:11434/api/tags", timeout=5)
        models = r.json().get("models", [])
        grader_models = [m for m in models if m["name"].startswith("grader-") or m["name"].startswith("nomic-embed")]
        return {
            "available": [{"name": m["name"], "size": m.get("size", 0)} for m in grader_models],
            "ollama_running": True,
        }
    except Exception:
        return {"available": [], "ollama_running": False}


class InstallModelRequest(BaseModel):
    model_name: str = Field(description="e.g. 'qwen3:8b' — the base model to pull")


@app.post("/api/models/install", dependencies=[Depends(_auth)])
def install_model(req: InstallModelRequest) -> dict:
    import subprocess
    try:
        result = subprocess.run(
            ["ollama", "pull", req.model_name],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            return {"success": False, "error": result.stderr[:200]}
        return {"success": True, "model": req.model_name}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "pull timed out (10 min)"}
    except Exception as e:
        return {"success": False, "error": str(e)[:200]}


# ---------------------------------------------------------------------------
# Settings — Canvas token, data dir, model preferences
# ---------------------------------------------------------------------------

SETTINGS_FILE = DATA_DIR / "settings.json"


class SettingsState(BaseModel):
    canvas_token: str = ""
    canvas_url: str = ""
    model_text: str = "grader-text:8b"
    model_vision: str = "grader-vision:1.3b"
    model_embed: str = "nomic-embed-text:latest"
    data_dir: str = str(DATA_DIR)
    auto_approve_threshold: float = Field(default=0.95, ge=0.5, le=1.0)
    review_first_threshold: float = Field(default=0.70, ge=0.0, le=1.0)

    @field_validator("canvas_url")
    @classmethod
    def validate_canvas_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not value.startswith(("https://", "http://")):
            raise ValueError("Canvas URL must start with https:// or http://")
        return value

    @field_validator("model_text")
    @classmethod
    def validate_text_model(cls, value: str) -> str:
        if not value.startswith("grader-text:"):
            raise ValueError("text model must use a capped grader-text tag")
        return value

    @field_validator("model_vision")
    @classmethod
    def validate_vision_model(cls, value: str) -> str:
        if not value.startswith("grader-vision:"):
            raise ValueError("vision model must use a capped grader-vision tag")
        return value

    @field_validator("model_embed")
    @classmethod
    def validate_embed_model(cls, value: str) -> str:
        if not value.startswith("nomic-embed-text:"):
            raise ValueError("embedding model must use a nomic-embed-text tag")
        return value

    @field_validator("data_dir")
    @classmethod
    def validate_data_dir(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Canvas attachment directory cannot be blank")
        return str(Path(value).expanduser())

    @model_validator(mode="after")
    def validate_threshold_order(self) -> SettingsState:
        if self.review_first_threshold >= self.auto_approve_threshold:
            raise ValueError("needs-review threshold must be lower than high-confidence threshold")
        return self


class SettingsUpdate(BaseModel):
    canvas_token: str | None = None
    canvas_url: str | None = None
    model_text: str | None = None
    model_vision: str | None = None
    model_embed: str | None = None
    data_dir: str | None = None
    auto_approve_threshold: float | None = None
    review_first_threshold: float | None = None


DEFAULT_SETTINGS = SettingsState().model_dump()


def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            s = json.loads(SETTINGS_FILE.read_text())
            return SettingsState.model_validate({**DEFAULT_SETTINGS, **s}).model_dump()
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()


def _save_settings(s: dict) -> None:
    validated = SettingsState.model_validate(s).model_dump()
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(validated, indent=2))
    os.chmod(temporary, 0o600)
    temporary.replace(SETTINGS_FILE)


def _settings_response(settings: dict) -> dict:
    response = {key: value for key, value in settings.items() if key != "canvas_token"}
    token = str(settings.get("canvas_token") or "")
    if token:
        response["canvas_token_masked"] = (
            "••••••••" if len(token) <= 8 else token[:4] + "..." + token[-4:]
        )
    return response


def _canvas_connection(settings: dict | None = None) -> dict[str, str | None]:
    current = settings or _load_settings()
    return {
        "base_url": str(current.get("canvas_url") or "") or None,
        "token": str(current.get("canvas_token") or "") or None,
    }


def _canvas_attachment_root(settings: dict | None = None) -> Path:
    current = settings or _load_settings()
    root = Path(str(current.get("data_dir") or DATA_DIR)).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


@app.get("/api/settings", dependencies=[Depends(_auth)])
def get_settings() -> dict:
    return _settings_response(_load_settings())


@app.put("/api/settings", dependencies=[Depends(_auth)])
def update_settings(req: SettingsUpdate) -> dict:
    current = _load_settings()
    updates = req.model_dump(exclude_unset=True)
    for key, value in updates.items():
        if value is None or (key == "canvas_token" and value == ""):
            continue
        current[key] = value

    validated = SettingsState.model_validate(current).model_dump()
    attachment_root = Path(str(validated["data_dir"])).expanduser()
    try:
        attachment_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(400, f"Canvas attachment directory is not writable: {exc}") from exc
    if not attachment_root.is_dir():
        raise HTTPException(400, "Canvas attachment directory is not a directory")

    _save_settings(validated)
    _audit("settings", "user", "update", {"keys": sorted(updates)})
    return {"success": True, "settings": _settings_response(validated)}


@app.get("/api/audit", dependencies=[Depends(_auth)])
def get_audit(entity: str | None = None, limit: int = 50) -> list[dict]:
    db = _db()
    if entity:
        rows = db.execute(
            "SELECT * FROM audit_events WHERE entity_ref = ? ORDER BY id DESC LIMIT ?",
            (entity, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
    db.close()
    return [{"id": r["id"], "entity": r["entity_ref"], "actor": r["actor"],
             "event": r["event_type"], "payload": json.loads(r["payload"]),
             "created_at": r["created_at"]} for r in rows]


# ---------------------------------------------------------------------------
# WebSocket: job progress
# ---------------------------------------------------------------------------

@app.websocket("/ws/jobs")
async def ws_jobs(ws: WebSocket) -> None:
    await ws.accept()
    _ws_clients.append(ws)
    try:
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        _ws_clients.remove(ws)


# ---------------------------------------------------------------------------
# P3: Canvas integration
# ---------------------------------------------------------------------------

class CanvasImportReq(BaseModel):
    course_id: int
    assignment_id: int
    download_attachments: bool = True


class CanvasPostReq(BaseModel):
    course_id: int
    assignment_id: int
    grade_ids: list[str] = Field(description="Grade record IDs to post back")


@app.post("/api/canvas/import", dependencies=[Depends(_auth)])
def canvas_import(req: CanvasImportReq) -> dict:
    """Import submissions from Canvas into the local queue.

    Downloads attachments, parses with Docling, enqueues as submissions.
    Per-student partial failures are reported, not fatal.
    """
    from grader.canvas import list_ungraded, download_submission_attachments
    from grader.parse import parse

    settings = _load_settings()
    canvas_connection = _canvas_connection(settings)
    dest_dir = (
        _canvas_attachment_root(settings)
        / "attachments"
        / f"course_{req.course_id}"
        / f"assign_{req.assignment_id}"
    )
    dest_dir.mkdir(parents=True, exist_ok=True)

    try:
        submissions = list_ungraded(req.course_id, req.assignment_id, **canvas_connection)
    except Exception as e:
        raise HTTPException(502, f"Canvas API error: {str(e)[:200]}")

    db = _db()
    imported = 0
    imported_submissions: list[dict] = []
    failures: list[dict] = []

    for sub in submissions:
        user_id = sub.get("user_id")
        if not user_id:
            continue

        body = sub.get("body", "").strip()
        texts: list[str] = [body] if body else []

        if req.download_attachments:
            att_paths = download_submission_attachments(
                sub,
                dest_dir,
                token=canvas_connection["token"],
            )
            for p in att_paths:
                if not p.exists():
                    failures.append({"user_id": user_id, "error": f"download failed: {p.name}"})
                    continue
                try:
                    parsed = parse(p, vision_model=str(settings["model_vision"]))
                    if parsed.markdown.strip():
                        texts.append(parsed.markdown)
                except Exception as e:
                    failures.append({"user_id": user_id, "error": f"parse failed: {str(e)[:100]}"})

        submission_text = "\n\n".join(t for t in texts if t.strip())
        if not submission_text:
            submission_text = "[No extractable text]"

        sub_id = f"sub_{secrets.token_hex(8)}"
        assignment_key = f"canvas_{req.course_id}_{req.assignment_id}"
        db.execute(
            "INSERT INTO submissions (id, assignment_id, student_ref, text, state, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (sub_id, assignment_key, str(user_id), submission_text, "imported", _now()),
        )
        imported += 1
        imported_submissions.append({"id": sub_id, "canvas_user_id": str(user_id)})

    db.commit()
    db.close()
    _audit(f"canvas_{req.course_id}_{req.assignment_id}", "canvas", "import",
           {"imported": imported, "failures": len(failures)})
    return {"imported": imported, "submissions": imported_submissions, "failures": failures}


@app.post("/api/canvas/post", dependencies=[Depends(_auth)])
def canvas_post(req: CanvasPostReq) -> dict:
    """Post approved grades back to Canvas.

    Enforces the invariant: NOTHING posts without teacher_final.approved_at.
    Bulk post polls Progress to completion (gotcha #1) before reporting success.
    """
    from grader.canvas import post_grades_batch

    db = _db()
    grade_data: dict[str, dict] = {}
    skipped: list[str] = []

    for gid in req.grade_ids:
        row = db.execute(
            "SELECT gr.*, s.student_ref AS canvas_user_id "
            "FROM grade_records gr "
            "LEFT JOIN submissions s ON s.id = gr.submission_id "
            "WHERE gr.id = ?",
            (gid,),
        ).fetchone()
        if not row:
            skipped.append(f"{gid}: not found")
            continue
        if not row["approved_at"]:
            skipped.append(f"{gid}: not approved — WILL NOT POST")
            continue
        if not row["canvas_user_id"]:
            skipped.append(f"{gid}: missing Canvas user id — WILL NOT POST")
            continue

        teacher_scores = json.loads(row["teacher_scores_json"]) if row["teacher_scores_json"] else {}
        ai_scores = json.loads(row["ai_scores_json"]) if row["ai_scores_json"] else {}
        scores = teacher_scores or ai_scores
        total = sum(scores.values()) if scores else 0.0

        grade_data[str(row["canvas_user_id"])] = {
            "posted_grade": total,
            # Do not send local rubric criterion slugs as Canvas rubric IDs.
            # Canvas bulk updates accept rubric_assessment only for assignments
            # with real Canvas criterion identifiers; invalid local keys can
            # leave the async Progress object stuck even when the score writes.
            "text_comment": row["teacher_feedback"] or row["ai_feedback"] or "Posted by Grader after teacher approval.",
        }

    db.close()

    if not grade_data:
        raise HTTPException(400, f"no approved grades to post. Skipped: {skipped}")

    try:
        result = post_grades_batch(
            req.course_id,
            req.assignment_id,
            grade_data,
            **_canvas_connection(),
        )
    except Exception as e:
        _audit(f"canvas_{req.course_id}_{req.assignment_id}", "canvas", "post_failed",
               {"error": str(e)[:200], "count": len(grade_data)})
        raise HTTPException(502, f"Canvas post failed: {str(e)[:200]}")

    _audit(f"canvas_{req.course_id}_{req.assignment_id}", "canvas", "post",
           {"posted": len(grade_data), "skipped": len(skipped),
            "progress": result.get("workflow_state", "unknown")})

    return {
        "posted": len(grade_data),
        "skipped": skipped,
        "posted_scores": {
            user_id: payload["posted_grade"]
            for user_id, payload in grade_data.items()
        },
        "progress_state": result.get("workflow_state", "completed"),
        "completion": result.get("completion", 100),
    }


@app.get("/api/canvas/post-queue", dependencies=[Depends(_auth)])
def canvas_post_queue_status() -> dict:
    """Check the offline post queue status."""
    from grader.canvas import OfflinePostQueue
    queue = OfflinePostQueue(DATA_DIR / "post_queue.db")
    pending = queue.pending()
    return {"pending": len(pending), "items": pending}


@app.post("/api/canvas/flush", dependencies=[Depends(_auth)])
def canvas_flush_queue() -> dict:
    """Flush the offline post queue — attempt to post all pending grades."""
    from grader.canvas import OfflinePostQueue
    queue = OfflinePostQueue(DATA_DIR / "post_queue.db")
    result = queue.flush(**_canvas_connection())
    _audit("canvas_queue", "engine", "flush", result)
    return result


# ---------------------------------------------------------------------------
# P4: Document parsing
# ---------------------------------------------------------------------------

class ParseReq(BaseModel):
    file_path: str


@app.post("/api/parse", dependencies=[Depends(_auth)])
def parse_document(req: ParseReq) -> dict:
    """Parse a document file to markdown. Returns ParsedDoc fields."""
    from grader.parse import parse
    p = Path(req.file_path)
    if not p.exists():
        raise HTTPException(404, f"file not found: {req.file_path}")
    try:
        result = parse(p, vision_model=str(_load_settings()["model_vision"]))
        return json.loads(result.model_dump_json())
    except Exception as e:
        raise HTTPException(500, f"parse error: {str(e)[:200]}")


# ---------------------------------------------------------------------------
# P4: Handwriting transcription
# ---------------------------------------------------------------------------

class TranscribeReq(BaseModel):
    file_paths: list[str]


@app.post("/api/transcribe", dependencies=[Depends(_auth)])
def transcribe_images(req: TranscribeReq) -> dict:
    """Transcribe handwritten images to text. Returns combined text + confidence + review flags."""
    from grader.handwriting import transcribe_pages
    paths = [Path(p) for p in req.file_paths if Path(p).exists()]
    if not paths:
        raise HTTPException(404, "no valid image paths")
    result = transcribe_pages(paths, model=str(_load_settings()["model_vision"]))
    _audit("transcription", "engine", "transcribe", {
        "pages": len(paths),
        "avg_confidence": result["avg_confidence"],
        "needs_review": result["needs_review"],
    })
    return result


# ---------------------------------------------------------------------------
# P4: Citation verification
# ---------------------------------------------------------------------------

class CitationReq(BaseModel):
    reference: str
    claim: str = ""
    check_claim: bool = True


class CitationBatchReq(BaseModel):
    references: list[dict] = Field(description="[{'reference': '...', 'claim': '...'}]")
    check_claim: bool = True


@app.post("/api/citations/verify", dependencies=[Depends(_auth)])
def verify_citation(req: CitationReq) -> dict:
    """Verify a single citation reference."""
    from grader.citations import verify
    result = verify(req.reference, req.claim, check_claim=req.check_claim)
    _audit("citation", "engine", "verify",
           {"verdict": result.verdict, "layer": result.resolution_layer})
    return json.loads(result.model_dump_json())


@app.post("/api/citations/verify-batch", dependencies=[Depends(_auth)])
def verify_citations_batch(req: CitationBatchReq) -> dict:
    """Verify multiple citations with rate limiting."""
    from grader.citations import verify_batch
    results = verify_batch(req.references, check_claim=req.check_claim)
    return {
        "total": len(results),
        "results": [json.loads(r.model_dump_json()) for r in results],
        "summary": {
            "verified": sum(1 for r in results if r.verdict == "verified"),
            "metadata_mismatch": sum(1 for r in results if r.verdict == "metadata_mismatch"),
            "not_found": sum(1 for r in results if r.verdict == "not_found"),
            "claim_unsupported": sum(1 for r in results if r.verdict == "claim_unsupported"),
            "unreachable": sum(1 for r in results if r.verdict == "unreachable"),
        },
    }


# ---------------------------------------------------------------------------
# P4: Annotated feedback write-back
# ---------------------------------------------------------------------------

class WritebackReq(BaseModel):
    src_path: str
    dest_path: str
    comments: list[dict] = Field(description="[{'text': '...', 'anchor_text': '...', 'page': 1, 'cell_ref': 'B3'}]")


@app.post("/api/writeback/annotate", dependencies=[Depends(_auth)])
def annotate_document(req: WritebackReq) -> dict:
    """Add comments to a document copy. NEVER mutates the original."""
    from grader.writeback import annotate, Comment
    src = Path(req.src_path)
    if not src.exists():
        raise HTTPException(404, f"source not found: {req.src_path}")
    dest = Path(req.dest_path)
    comments = [Comment(**c) for c in req.comments]
    try:
        result = annotate(src, dest, comments)
        _audit(str(dest), "engine", "annotate",
               {"comments": result.comments_added})
        return json.loads(result.model_dump_json())
    except Exception as e:
        raise HTTPException(500, f"annotation error: {str(e)[:200]}")


class FeedbackReportReq(BaseModel):
    dest_path: str
    rubric_title: str
    scores: dict[str, float]
    feedback: str
    total: float
    max_total: float


@app.post("/api/writeback/report", dependencies=[Depends(_auth)])
def feedback_report_endpoint(req: FeedbackReportReq) -> dict:
    """Generate a standalone feedback report as .docx."""
    from grader.writeback import feedback_report
    result = feedback_report(
        req.dest_path,
        draft_text="",
        rubric_title=req.rubric_title,
        scores=req.scores,
        feedback=req.feedback,
        total=req.total,
        max_total=req.max_total,
    )
    _audit(str(req.dest_path), "engine", "feedback_report", {})
    return json.loads(result.model_dump_json())


# ---------------------------------------------------------------------------
# App updates — check GitHub releases for newer versions
# ---------------------------------------------------------------------------

@app.get("/api/updates/check", dependencies=[Depends(_auth)])
def check_updates() -> dict:
    """Check for app updates by fetching the Tauri updater manifest from GitHub."""
    import httpx
    try:
        r = httpx.get(
            "https://github.com/johnnyrobot/grader-releases/releases/latest/download/latest.json",
            timeout=10,
        )
        if r.status_code != 200:
            return {"update_available": False, "error": f"manifest returned {r.status_code}"}
        manifest = r.json()
        current = os.environ.get("GRADER_APP_VERSION", "0.0.0")
        latest = manifest.get("version", "0.0.0")
        return {
            "update_available": _version_gt(latest, current),
            "current_version": current,
            "latest_version": latest,
            "notes": manifest.get("notes", ""),
            "pub_date": manifest.get("pub_date", ""),
        }
    except Exception as e:
        return {"update_available": False, "error": str(e)[:200]}


def _version_gt(a: str, b: str) -> bool:
    """Return True if version a > version b (semantic versioning)."""
    pa = [int(x) for x in a.split(".") if x.isdigit()]
    pb = [int(x) for x in b.split(".") if x.isdigit()]
    while len(pa) < len(pb):
        pa.append(0)
    while len(pb) < len(pa):
        pb.append(0)
    return pa > pb
