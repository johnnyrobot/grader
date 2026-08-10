"""FastAPI engine — the single interface the Tauri shell talks to.

Loopback only, per-launch bearer token. The shell starts this as a sidecar and talks
to it over localhost HTTP + WebSocket. All AI/document/Canvas work lives in the engine;
the shell is a thin client.

Run:
  uv run uvicorn engine.api:app --host 127.0.0.1 --port 8756 --reload

The engine reuses grader.agent.grade, grader.triage.triage, grader.approvals.ApprovalStore,
grader.calibration.compute_health, grader.calibration.can_draft as-is — no logic is
duplicated from the package.

Approval is not this module's to grant. grade_records.approved_at is written only
by grader.approvals, and canvas_post asks postable() rather than reading it.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from grader.approvals import AlreadyApproved, ApprovalStore, GradeNotFound
from grader.attachments import AttachmentStore, OutsideAttachmentRoot
from grader.calibration import (
    EVIDENCE_FLOOR,
    ApprovedComparison,
    cold_start_status,
    compute_health,
)
from grader.canvas import OfflinePostQueue
from grader.db import Database
from grader.exemplars import ExemplarStore
from grader.gradingjob import DraftBiasLookup, GradeOutcome, GradingJob, SubmissionText
from grader.overrides import OverrideStore, detect_patterns
from grader.ports import Judge
from grader.rubrics import RubricStore, parse_pasted_rubric
from grader.schema import GradeDraft, Rubric
from grader.settings import Settings, SettingsPatch, SettingsStore

# ---------------------------------------------------------------------------
# Per-launch auth: the shell gets the token from the engine's stdout on startup.
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

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
# Settings — what the teacher configured. `grader.settings` owns the rules; this
# is only the resolution seam. It sits above the routes because a handler names
# `Depends(current_settings)` in its signature, which is evaluated at def time.
# ---------------------------------------------------------------------------

SETTINGS_FILE = DATA_DIR / "settings.json"


def _settings_store() -> SettingsStore:
    """The settings file, resolved at call time rather than at import.

    SETTINGS_FILE is module state that tests rebind after import, so a store built
    at import would keep pointing at the real data directory. A SettingsStore is a
    path plus the rules for reading it — constructing one per call costs nothing.
    """
    return SettingsStore(SETTINGS_FILE)


def current_settings() -> Settings:
    """Resolve the saved settings once for this request.

    Routes take this as a dependency rather than calling the store, so FastAPI's
    per-request cache guarantees one read per request. Eight handlers used to
    each load and re-coerce their own copy, which is how two of them ended up
    writing conflicting values onto a shared store.
    """
    return _settings_store().current()


# ---------------------------------------------------------------------------
# Persistence: SQLite WAL for the job queue + grade records + audit log.
# ---------------------------------------------------------------------------

DB_PATH = DATA_DIR / "grader_engine.db"

# submissions.source_kind values. OCR text is a transcription of an image, so it
# carries a reading error the grade cannot see; handwriting.py has always said
# scanned work defaults to REVIEW_FIRST, and this is what lets triage do it.
#
# Only image attachments are recognised today: `_parse_image` is the OCR path and
# reports source_type='image'. A scanned *PDF* goes through Docling, comes back
# as 'pdf', and `_parse_with_docling` hardcodes confidence=1.0 — so nothing marks
# it. Photos of handwritten work are covered; scanned PDFs are not, until
# Docling's own OCR confidence is surfaced through ParsedDoc.
SCANNED = "scanned"
TYPED = "text"

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    id            TEXT PRIMARY KEY,
    assignment_id TEXT NOT NULL,
    student_ref   TEXT,
    text          TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'imported',
    -- How the text was obtained. 'scanned' means it came out of OCR, which is
    -- the signal triage needs to insist a human reads it first.
    source_kind   TEXT NOT NULL DEFAULT 'text',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sub_assignment ON submissions(assignment_id);
CREATE INDEX IF NOT EXISTS idx_sub_state ON submissions(state);

CREATE TABLE IF NOT EXISTS grade_records (
    id              TEXT PRIMARY KEY,
    submission_id   TEXT NOT NULL,
    assignment_id   TEXT NOT NULL,
    rubric_version  INTEGER NOT NULL DEFAULT 1,
    rubric_title    TEXT,
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


def _migrate_submissions(db: Database) -> None:
    """Add source_kind to submissions made before OCR provenance was recorded.

    Defaults to 'text': an existing row is one we cannot retroactively know was
    scanned, and claiming otherwise would send every old submission to review.
    """
    columns = {r["name"] for r in db.execute("PRAGMA table_info(submissions)")}
    if "source_kind" not in columns:
        db.execute(
            "ALTER TABLE submissions ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'text'"
        )
        db.commit()


def _migrate_grade_records(db: Database) -> None:
    """Add rubric_title to grade_records on databases made before it existed.

    CREATE TABLE IF NOT EXISTS leaves an existing table alone, so a teacher
    upgrading from an earlier build needs the ALTER.
    """
    columns = {r["name"] for r in db.execute("PRAGMA table_info(grade_records)")}
    if "rubric_title" not in columns:
        db.execute("ALTER TABLE grade_records ADD COLUMN rubric_title TEXT")
        db.commit()


_engine_db: Database | None = None
_engine_db_path: Path | None = None
_engine_db_lock = threading.Lock()


def _db() -> Database:
    """The engine's own tables, on one connection held for the process lifetime.

    This used to open a connection and re-run executescript(DB_SCHEMA) on every
    one of nineteen call sites. Now it matches the policy grader.db already sets
    for the four other stores: one connection, WAL, a busy timeout, and
    check_same_thread off, because the request threadpool reads while the
    background grading thread writes.

    Callers must not close it — closing a shared connection breaks the next
    request, not the current one.

    Opened lazily and keyed on DB_PATH. Tests rebind DB_PATH after import, so
    connecting at import would bind the real data directory, and caching without
    the key would keep serving the previous test's database.
    """
    global _engine_db, _engine_db_path
    with _engine_db_lock:
        if _engine_db is None or _engine_db_path != DB_PATH:
            if _engine_db is not None:
                _engine_db.close()
            db = Database.at(DB_PATH)
            db.executescript(DB_SCHEMA)
            _migrate_submissions(db)
            _migrate_grade_records(db)
            db.commit()
            _engine_db, _engine_db_path = db, DB_PATH
        return _engine_db


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


# ---------------------------------------------------------------------------
# Stores (shared across requests)
# ---------------------------------------------------------------------------

_exemplar_store = ExemplarStore(Database.at(DATA_DIR / "exemplars.db"))
_override_store = OverrideStore(Database.at(DATA_DIR / "overrides.db"))
_rubric_store = RubricStore(Database.at(DATA_DIR / "rubrics.db"))
_post_queue = OfflinePostQueue(Database.at(DATA_DIR / "post_queue.db"))
_attachments = AttachmentStore(Database.at(DATA_DIR / "attachments.db"), DATA_DIR)

# Sole owner of grade_records.approved_at and posted_at. Takes _db itself rather
# than a connection so that redirecting DB_PATH after import still reaches it.
_approvals = ApprovalStore(_db, _audit)


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


class _SqliteGradeStore:
    """The engine's own tables, behind the GradeStore port.

    Runs on the background grading thread, against the same connection the
    request threadpool uses — which is what WAL and the busy timeout in
    grader.db are for.
    """

    def fetch_submission(self, submission_id: str) -> SubmissionText | None:
        row = _db().execute(
            "SELECT * FROM submissions WHERE id = ?", (submission_id,)
        ).fetchone()
        if row is None:
            return None
        return SubmissionText(
            id=row["id"],
            assignment_id=row["assignment_id"],
            text=row["text"],
            scanned=row["source_kind"] == SCANNED,
        )

    def save(self, outcome: GradeOutcome, *, rubric_title: str) -> None:
        record = outcome.record
        db = _db()
        db.execute(
            "INSERT OR REPLACE INTO grade_records "
            "(id, submission_id, assignment_id, rubric_version, rubric_title, "
            "ai_scores_json, ai_feedback, ai_confidence, ai_flags_json, lane, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (outcome.record_id, outcome.submission_id, record["assignment_id"], 1,
             rubric_title,
             json.dumps(record.get("ai_scores")) if record.get("ai_scores") else None,
             record.get("ai_feedback"),
             record.get("confidence"),
             json.dumps(record.get("flags")) if record.get("flags") else None,
             record.get("lane"), _now()),
        )
        db.execute(
            "UPDATE submissions SET state = ? WHERE id = ?",
            (outcome.state, outcome.submission_id),
        )
        db.commit()

    def audit(self, entity: str, actor: str, event: str, payload: dict) -> None:
        _audit(entity, actor, event, payload)


_grade_store = _SqliteGradeStore()


def _rubric_of_record(rubric_title: str | None) -> Rubric | None:
    """The Rubric a grade was produced against — not whichever is saved now.

    By title, and deliberately not by `grade_records.rubric_version`: the writer
    above stamps that column with a literal 1 on every record, while grading
    resolves the Rubric by title and gets the *latest* version. Honouring the
    recorded version would therefore scale a grade drafted against version 2 by
    version 1's criterion maxima — a wrong number rather than a missing one.
    Resolve by it once the writer records the version it actually graded against.

    The fallback is for records written before rubric_title existed: inferring is
    only safe when there is exactly one Rubric to infer from, since the wrong
    criterion max silently misscales everything measured against it.
    """
    if rubric_title:
        rubric = _rubric_store.get(str(rubric_title))
        if rubric is not None:
            return rubric
    saved = _rubric_store.list()
    if len(saved) == 1:
        return _rubric_store.get(str(saved[0]["title"]))
    return None


class _SqliteApprovedComparisons:
    """Approved grade records for one Assignment, as AI-vs-teacher comparisons.

    The read seam ADR-0001 asks for. grader.calibration receives comparisons whose
    Rubric is already resolved and never learns that they were rows, or that a
    title has a fallback. Every approval is here, including the ones the teacher
    changed nothing on — that is the whole point of the population.

    A record with no AI scores, or whose Rubric cannot be resolved, is left out:
    there is no gap to measure in the first case and nothing to scale it by in
    the second.
    """

    def list(self, assignment_id: str, *, limit: int = 200) -> list[ApprovedComparison]:
        rows = _db().execute(
            "SELECT ai_scores_json, teacher_scores_json, rubric_title "
            "FROM grade_records WHERE assignment_id = ? AND approved_at IS NOT NULL "
            "AND approved_at != '' ORDER BY approved_at DESC LIMIT ?",
            (assignment_id, limit),
        ).fetchall()

        resolved: dict[str | None, Rubric | None] = {}
        comparisons: list[ApprovedComparison] = []
        for row in rows:
            ai_scores = json.loads(row["ai_scores_json"] or "{}")
            if not ai_scores:
                continue
            title = row["rubric_title"]
            if title not in resolved:
                resolved[title] = _rubric_of_record(title)
            rubric = resolved[title]
            if rubric is None:
                continue
            teacher_scores = json.loads(row["teacher_scores_json"] or "null")
            comparisons.append(
                ApprovedComparison(
                    ai_scores=ai_scores,
                    teacher_scores=teacher_scores or None,
                    rubric=rubric,
                )
            )
        return comparisons


_approved_comparisons = _SqliteApprovedComparisons()


def _draft_bias_lookup(embedding_model: str) -> DraftBiasLookup:
    """How a running batch asks what this Assignment's Draft bias is.

    The same calibration the panel reports, so the lane a teacher sees and the
    figure on the screen can never tell them different stories. `draft_bias` is
    None below the evidence floor — that is how the floor gates the demotion,
    without this layer knowing what the floor is.
    """

    def resolve(assignment_id: str) -> float | None:
        health = compute_health(
            _approved_comparisons,
            _override_store,
            _exemplar_store.for_model(embedding_model),
            assignment_id,
        )
        return health.draft_bias

    return resolve


def _run_grading_job(
    job_id: str,
    assignment_id: str,
    submission_ids: list[str],
    rubric: Rubric,
    model: str,
    materials: str,
    low_confidence: float,
    high_confidence: float,
    embedding_model: str,
    judge: Judge | None = None,
) -> None:
    """Transport around GradingJob: job bookkeeping and websocket progress."""
    if judge is None:
        # Imported here rather than at module scope: this is the only place the
        # engine needs pydantic_ai, and importing it up top made every route test
        # stub four fake submodules before it could load the engine at all.
        from grader.agent import PydanticAIJudge

        judge = PydanticAIJudge(model_name=model)

    with _job_lock:
        _jobs[job_id]["status"] = "running"

    job = GradingJob(
        rubric,
        judge=judge,
        # Bound once for the life of the job: a concurrent settings change must
        # not retarget retrieval half-way through a batch.
        exemplars=_exemplar_store.for_model(embedding_model),
        store=_grade_store,
        low_confidence=low_confidence,
        high_confidence=high_confidence,
        materials=materials,
        # Resolved once inside run(), for the Assignment the batch is grading.
        draft_bias=_draft_bias_lookup(embedding_model),
    )

    total = len(submission_ids)

    def on_progress(done: int, _total: int, outcome: GradeOutcome) -> None:
        with _job_lock:
            _jobs[job_id]["done"] = done
        _broadcast({"type": "job_progress", "job_id": job_id, "done": done,
                     "total": total, "latest": outcome.record})

    outcomes = job.run(assignment_id, submission_ids, on_progress=on_progress)
    results = [o.record for o in outcomes]

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
    rubric_title: str | None = None
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
    _audit(req.assignment_id, "teacher", "import", {"count": count})
    return {"imported": count}


@app.post("/api/rubrics", dependencies=[Depends(_auth)])
def create_rubric(req: CreateRubric) -> dict:
    if req.rubric_text:
        try:
            rubric = parse_pasted_rubric(req.rubric_text, title=req.title)
        except ValueError as exc:
            # Unreadable paste is the teacher's input, not an engine failure.
            raise HTTPException(400, str(exc)) from exc
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


def _resolve_rubric(title: str | None) -> Rubric:
    """Pick the Rubric to grade against, refusing to guess between several.

    Grading against the wrong Rubric is silent: the scores look plausible, and the
    Exemplars derived from the teacher's Overrides then carry the wrong ceiling into
    every later batch. So when more than one Rubric exists and the caller named none,
    fail loudly rather than taking whichever sorts first.
    """
    saved = _rubric_store.list()
    if not saved:
        raise HTTPException(400, "no rubric — create one first")
    if title is None:
        if len(saved) > 1:
            raise HTTPException(
                400,
                "more than one rubric exists — name the one to grade against: "
                + ", ".join(r["title"] for r in saved),
            )
        title = str(saved[0]["title"])
    rubric = _rubric_store.get(title)
    if not rubric:
        raise HTTPException(400, f"rubric not found: {title}")
    return rubric


@app.post("/api/grade/batch", dependencies=[Depends(_auth)])
def grade_batch(req: GradeBatch, settings: Settings = Depends(current_settings)) -> dict:
    rubric = _resolve_rubric(req.rubric_title)

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
    sub_ids = [r["id"] for r in rows]
    if not sub_ids:
        raise HTTPException(400, "no submissions to grade for this assignment")

    model = req.model or settings.model_text
    low_confidence = settings.thresholds.review_first
    high_confidence = settings.thresholds.high_confidence
    embedding_model = settings.model_embed

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
            embedding_model,
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
    try:
        approval = _approvals.approve(grade_id)
    except GradeNotFound:
        raise HTTPException(404, "grade not found")
    except AlreadyApproved:
        raise HTTPException(409, "already approved")
    return {"approved": True, "grade_id": approval.grade_id}


@app.post("/api/grades/{grade_id}/override", dependencies=[Depends(_auth)])
def override_grade(grade_id: str, req: OverrideRequest,
                    settings: Settings = Depends(current_settings)) -> dict:
    db = _db()
    row = db.execute("SELECT * FROM grade_records WHERE id = ?", (grade_id,)).fetchone()
    if not row:
        raise HTTPException(404, "grade not found")
    sub_row = db.execute("SELECT * FROM submissions WHERE id = ?", (row["submission_id"],)).fetchone()

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

    # The Override is scaled against the Rubric this grade was actually produced
    # against — not whichever one happens to be saved now. Same resolution the
    # calibration reader uses, so both scale one grade by the same criterion max.
    rubric = _rubric_of_record(row["rubric_title"])
    if rubric is None:
        raise HTTPException(
            400,
            "cannot tell which rubric this grade was produced against — "
            "re-grade this submission before overriding it",
        )
    max_total = rubric.total_points

    exemplars = _exemplar_store.for_model(settings.model_embed)
    ov = _approvals.override(
        grade_id,
        override_store=_override_store,
        exemplar_store=exemplars,
        assignment_id=row["assignment_id"],
        submission_text=sub_row["text"] if sub_row else "",
        ai_draft=draft,
        teacher_scores=req.teacher_scores,
        teacher_feedback=req.teacher_feedback,
        max_total=max_total,
    ).override

    proposals = detect_patterns(_override_store.list(row["assignment_id"]), min_pattern=3)
    return {
        "overridden": True, "grade_id": grade_id,
        "ai_total": ov.ai_total, "teacher_total": ov.teacher_total,
        "exemplar_count": exemplars.count(row["assignment_id"]),
        "refinement_proposals": [
            {"criterion_id": p.criterion_id, "direction": p.direction,
             "count": p.pattern_count, "avg_delta": p.avg_delta,
             "suggested_text": p.suggested_text}
            for p in proposals
        ],
    }


@app.get("/api/calibration/{assignment_id}", dependencies=[Depends(_auth)])
def calibration_health(assignment_id: str,
                        settings: Settings = Depends(current_settings)) -> dict:
    exemplars = _exemplar_store.for_model(settings.model_embed)
    health = compute_health(
        _approved_comparisons, _override_store, exemplars, assignment_id
    )
    cold = cold_start_status(exemplars, assignment_id)
    return {
        # Signed and already scaled as a fraction of criterion max; null below the
        # evidence floor, where `n` is all there is to report. `discloses` and
        # `demotes` are the thresholds' verdicts — a consumer reads them rather
        # than re-deriving 0.03 and 0.10 for itself.
        "draft_bias": health.draft_bias,
        "n": health.n,
        "evidence_floor": EVIDENCE_FLOOR,
        "discloses": health.discloses,
        "demotes": health.demotes,
        "override_count": health.override_count,
        "override_rate": health.override_rate,
        "exemplar_count": health.exemplar_count,
        "per_criterion": health.per_criterion,
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
# Settings routes — the store and the resolution seam are defined near the top,
# because a handler's `Depends(current_settings)` is evaluated at def time.
# ---------------------------------------------------------------------------

@app.get("/api/settings", dependencies=[Depends(_auth)])
def get_settings(settings: Settings = Depends(current_settings)) -> dict:
    return settings.masked()


@app.put("/api/settings", dependencies=[Depends(_auth)])
def update_settings(req: SettingsPatch) -> dict:
    """Apply a patch. Rejecting it is a 400 — the teacher typed something invalid.

    The route does not read, merge or validate: `SettingsStore.update` owns that,
    so a partial write cannot happen here. All this layer decides is which HTTP
    status a rejection deserves.
    """
    try:
        settings = _settings_store().update(req)
    except ValueError as exc:  # includes pydantic's ValidationError
        raise HTTPException(400, str(exc)) from exc

    _audit("settings", "user", "update",
           {"keys": sorted(req.model_dump(exclude_unset=True))})
    return {"success": True, "settings": settings.masked()}


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
def canvas_import(req: CanvasImportReq,
                   settings: Settings = Depends(current_settings)) -> dict:
    """Import submissions from Canvas into the local queue.

    Downloads attachments, parses with Docling, enqueues as submissions.
    Per-student partial failures are reported, not fatal.
    """
    from grader.canvas import list_ungraded, download_submission_attachments
    from grader.parse import parse

    canvas_connection = settings.canvas.as_kwargs()
    dest_dir = (
        settings.attachment_root()
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
    skipped: list[dict] = []

    for sub in submissions:
        user_id = sub.get("user_id")
        if not user_id:
            continue

        # Canvas sends `"body": null` — the key is there — for a student who
        # uploaded a file instead of typing, so a default cannot stand in.
        body = (sub.get("body") or "").strip()

        # Canvas returns a row for every enrolled student, submitted or not.
        # Importing one produces an AI draft scored from nothing, sitting in
        # the queue looking exactly like a draft of real work — and a teacher
        # working down the lane approves it. Note this is emptiness at the
        # source, not a file Grader could not read: that student did submit,
        # and dropping them would hide real work behind a line in `failures`.
        if not body and not sub.get("attachments"):
            skipped.append({"user_id": user_id, "reason": "nothing submitted"})
            continue

        texts: list[str] = [body] if body else []

        scanned = False
        if req.download_attachments:
            downloads = download_submission_attachments(
                sub,
                dest_dir,
                token=canvas_connection["token"],
            )
            for download in downloads:
                if not download.ok:
                    failures.append({
                        "user_id": user_id,
                        "error": f"download failed: {download.path.name}"
                        + (f" ({download.error})" if download.error else ""),
                    })
                    continue
                p = download.path
                try:
                    parsed = parse(p, vision_model=settings.model_vision)
                    if parsed.markdown.strip():
                        texts.append(parsed.markdown)
                        if parsed.source_type == "image":
                            scanned = True
                    else:
                        # A file that produced no text is a file the teacher has
                        # to look at themselves. Importing it silently leaves a
                        # student holding a grade derived from nothing.
                        failures.append({
                            "user_id": user_id,
                            "error": f"no readable text extracted from {p.name}",
                        })
                except Exception as e:
                    failures.append({"user_id": user_id, "error": f"parse failed: {str(e)[:100]}"})

        submission_text = "\n\n".join(t for t in texts if t.strip())
        if not submission_text:
            submission_text = "[No extractable text]"

        sub_id = f"sub_{secrets.token_hex(8)}"
        assignment_key = f"canvas_{req.course_id}_{req.assignment_id}"
        db.execute(
            "INSERT INTO submissions (id, assignment_id, student_ref, text, state, "
            "source_kind, created_at) VALUES (?,?,?,?,?,?,?)",
            (sub_id, assignment_key, str(user_id), submission_text, "imported",
             SCANNED if scanned else TYPED, _now()),
        )
        imported += 1
        imported_submissions.append({"id": sub_id, "canvas_user_id": str(user_id)})

    db.commit()
    _audit(f"canvas_{req.course_id}_{req.assignment_id}", "canvas", "import",
           {"imported": imported, "failures": len(failures), "skipped": len(skipped)})
    return {
        "imported": imported,
        "submissions": imported_submissions,
        "failures": failures,
        "skipped": skipped,
    }


@app.post("/api/canvas/post", dependencies=[Depends(_auth)])
def canvas_post(req: CanvasPostReq, settings: Settings = Depends(current_settings)) -> dict:
    """Post approved grades back to Canvas.

    This route cannot read approved_at — it asks _approvals.postable(), which is
    the only way the question can be asked. Bulk post polls Progress to
    completion (gotcha #1) before reporting success.

    Do not send local rubric criterion slugs as Canvas rubric IDs. Canvas bulk
    updates accept rubric_assessment only for assignments with real Canvas
    criterion identifiers; invalid local keys can leave the async Progress
    object stuck even when the score writes.
    """
    from grader.canvas import post_grades_batch

    batch = _approvals.postable(req.grade_ids)
    if not batch.ready:
        raise HTTPException(400, f"no approved grades to post. Skipped: {batch.skip_details}")

    grade_data = batch.grade_data()

    # Attachments upload before the grades, and inside the try: an outage during
    # the upload is the same outage FR-3.5 already covers. Ids that did land are
    # kept so a queued post carries them and the file is not uploaded twice.
    uploaded: dict[str, list[int]] = {}
    pending: dict[str, list[str]] = {}
    upload_error = ""
    try:
        for grade in batch.ready:
            upload = _upload_attachments_for(
                grade, req.course_id, req.assignment_id, settings
            )
            if upload.file_ids:
                uploaded[grade.canvas_user_id] = upload.file_ids
                grade_data[grade.canvas_user_id]["file_ids"] = upload.file_ids
            if upload.pending:
                pending[grade.canvas_user_id] = upload.pending
                upload_error = upload_error or upload.error
        if pending:
            # Posting now would deliver the grade without the feedback the
            # teacher attached to it, and nothing would be left to send it.
            raise RuntimeError(f"attachment upload failed: {upload_error}")

        result = post_grades_batch(
            req.course_id,
            req.assignment_id,
            grade_data,
            **settings.canvas.as_kwargs(),
        )
    except Exception as e:
        # PRD FR-3.5: posts queue until connectivity. A 502 here used to throw
        # the teacher's approval away — they approved the grade, Canvas was
        # merely unreachable, and nothing had ever put an entry in the queue
        # this engine exposes at /api/canvas/post-queue.
        #
        # Queueing cannot post an unapproved grade: every entry below came from
        # batch.ready, which is postable()'s output. mark_posted stays uncalled,
        # because queued is not posted.
        for grade in batch.ready:
            ids = uploaded.get(grade.canvas_user_id)
            paths = pending.get(grade.canvas_user_id)
            _post_queue.enqueue(
                str(req.course_id),
                str(req.assignment_id),
                grade.canvas_user_id,
                str(grade.posted_grade),
                comment=grade.text_comment,
                file_ids=json.dumps(ids) if ids else None,
                pending_files=json.dumps(paths) if paths else None,
                approved_at=grade.approved_at,
            )
        _audit(f"canvas_{req.course_id}_{req.assignment_id}", "canvas", "post_queued",
               {"error": str(e)[:200], "queued": len(batch.ready),
                "pending_attachments": sum(len(p) for p in pending.values())})
        return {
            "posted": 0,
            "queued": len(batch.ready),
            "queued_reason": str(e)[:200],
            "skipped": batch.skip_details,
            "posted_scores": {},
            "progress_state": "queued",
            "completion": 0,
        }

    # Only once Canvas has accepted them: a failed post must stay retryable.
    _approvals.mark_posted(batch.grade_ids)

    _audit(f"canvas_{req.course_id}_{req.assignment_id}", "canvas", "post",
           {"posted": len(grade_data), "skipped": len(batch.skipped),
            "progress": result.get("workflow_state", "unknown")})

    return {
        "posted": len(grade_data),
        "queued": 0,
        "skipped": batch.skip_details,
        "posted_scores": {
            user_id: payload["posted_grade"]
            for user_id, payload in grade_data.items()
        },
        "progress_state": result.get("workflow_state", "completed"),
        "completion": result.get("completion", 100),
    }


@dataclass
class AttachmentUploads:
    """What became of one grade's attachments on the way to Canvas.

    A path in `pending` is a file this machine may still send that Canvas did
    not take. It is kept apart from the ids so the queue can finish the job:
    the alternative is a grade posted without the feedback attached to it.
    """

    file_ids: list[int]
    pending: list[str]
    error: str = ""


def _sendable(entity_ref: str, path: Path) -> bool:
    """May these bytes leave this machine, and are they still there?

    Re-checked at every send, not only at attach time: a path can be replaced
    with a symlink out of the data directory between choosing and posting, and
    a file the teacher moved or deleted is no longer feedback to send.

    `entity_ref` is whatever the caller can name the send by — a grade id while
    posting, an assignment key while flushing, because a queue entry is
    deliberately keyed by student rather than by grade so it survives without
    one. Both are audit entity refs; neither is assumed to be the other.
    """
    if not path.resolve().is_relative_to(_attachments.root):
        _audit(entity_ref, "canvas", "attachment_refused", {"path": str(path)})
        return False
    if not path.is_file():
        _audit(entity_ref, "canvas", "attachment_missing", {"path": str(path)})
        return False
    return True


def _upload_attachments_for(
    grade, course_id: int | str, assignment_id: int | str, settings: Settings
) -> AttachmentUploads:
    """Upload this grade's attachments, newest last.

    A file the teacher attached and then moved or deleted is skipped rather
    than failing the post: the grade is the thing that must reach the student,
    and a missing feedback copy is recorded in the audit log for them to see. A
    file Canvas would not *take*, though, is a different case — the file is
    still there, so it is reported as pending and FR-3.5 queues it.
    """
    from grader.canvas import upload_comment_attachment

    file_ids: list[int] = []
    pending: list[str] = []
    error = ""
    for attachment in _attachments.for_grade(grade.grade_id):
        if not _sendable(grade.grade_id, attachment.path):
            continue
        try:
            file_ids.append(
                upload_comment_attachment(
                    course_id,
                    assignment_id,
                    grade.canvas_user_id,
                    attachment.path,
                    **settings.canvas.as_kwargs(),
                )
            )
        except Exception as exc:
            pending.append(str(attachment.path))
            error = error or str(exc)[:200]
    return AttachmentUploads(file_ids=file_ids, pending=pending, error=error)


def _queued_uploader(settings: Settings):
    """How the queue finishes an upload an outage stopped.

    The bound on which bytes may leave this machine belongs to this layer, not
    to the queue, so it is applied again here rather than trusted from the row.
    Returning None tells the queue to drop a path that no longer qualifies.
    """
    from grader.canvas import upload_comment_attachment

    def upload(course_id: str, assignment_id: str, user_id: str, path: Path) -> int | None:
        if not _sendable(f"canvas_{course_id}_{assignment_id}", path):
            return None
        return upload_comment_attachment(
            course_id, assignment_id, user_id, path, **settings.canvas.as_kwargs()
        )

    return upload


class AttachmentReq(BaseModel):
    path: str


@app.post("/api/grades/{grade_id}/attachments", dependencies=[Depends(_auth)])
def attach_to_grade(grade_id: str, req: AttachmentReq) -> dict:
    """Mark a local file to go out as a comment attachment with this grade.

    Nothing uploads here. The teacher can attach, look, and detach; only
    /api/canvas/post sends anything, and only for a grade postable() cleared.
    """
    row = _db().execute(
        "SELECT id FROM grade_records WHERE id = ?", (grade_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"no such grade: {grade_id}")
    try:
        attachment = _attachments.attach(grade_id, req.path)
    except OutsideAttachmentRoot as e:
        # Not 404: the file may well exist. It is not one this app may send.
        raise HTTPException(400, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    _audit(grade_id, "teacher", "attach", {"path": str(attachment.path)})
    return {"grade_id": grade_id, "path": str(attachment.path)}


@app.get("/api/grades/{grade_id}/attachments", dependencies=[Depends(_auth)])
def list_grade_attachments(grade_id: str) -> dict:
    """What would be posted with this grade — the preview PRD line 131 asks for."""
    items = _attachments.for_grade(grade_id)
    return {
        "grade_id": grade_id,
        "attachments": [
            {"path": str(a.path), "exists": a.exists, "created_at": a.created_at}
            for a in items
        ],
    }


@app.delete("/api/grades/{grade_id}/attachments", dependencies=[Depends(_auth)])
def detach_from_grade(grade_id: str, req: AttachmentReq) -> dict:
    removed = _attachments.detach(grade_id, req.path)
    if not removed:
        raise HTTPException(404, "not attached to this grade")
    _audit(grade_id, "teacher", "detach", {"path": req.path})
    return {"grade_id": grade_id, "detached": req.path}


@app.get("/api/canvas/post-queue", dependencies=[Depends(_auth)])
def canvas_post_queue_status() -> dict:
    """Check the offline post queue status."""
    pending = _post_queue.pending()
    return {"pending": len(pending), "items": pending}


@app.post("/api/canvas/flush", dependencies=[Depends(_auth)])
def canvas_flush_queue(settings: Settings = Depends(current_settings)) -> dict:
    """Flush the offline post queue — attempt to post all pending grades."""
    result = _post_queue.flush(
        upload=_queued_uploader(settings), **settings.canvas.as_kwargs()
    )
    _audit("canvas_queue", "engine", "flush", result)
    return result


# ---------------------------------------------------------------------------
# P4: Document parsing
# ---------------------------------------------------------------------------

class ParseReq(BaseModel):
    file_path: str


@app.post("/api/parse", dependencies=[Depends(_auth)])
def parse_document(req: ParseReq, settings: Settings = Depends(current_settings)) -> dict:
    """Parse a document file to markdown. Returns ParsedDoc fields."""
    from grader.parse import parse
    p = Path(req.file_path)
    if not p.exists():
        raise HTTPException(404, f"file not found: {req.file_path}")
    try:
        result = parse(p, vision_model=settings.model_vision)
        return json.loads(result.model_dump_json())
    except Exception as e:
        raise HTTPException(500, f"parse error: {str(e)[:200]}")


# ---------------------------------------------------------------------------
# P4: Handwriting transcription
# ---------------------------------------------------------------------------

class TranscribeReq(BaseModel):
    file_paths: list[str]


@app.post("/api/transcribe", dependencies=[Depends(_auth)])
def transcribe_images(req: TranscribeReq, settings: Settings = Depends(current_settings)) -> dict:
    """Transcribe handwritten images to text. Returns combined text + confidence + review flags."""
    from grader.handwriting import transcribe_pages
    paths = [Path(p) for p in req.file_paths if Path(p).exists()]
    if not paths:
        raise HTTPException(404, "no valid image paths")
    result = transcribe_pages(paths, model=settings.model_vision)
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
