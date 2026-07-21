"""Anchor-exemplar store: the mechanism that makes Grader grade like *this* teacher.

This is the product's core bet, and it is evidence-backed rather than intuitive. Human
raters calibrate by reading "anchor papers" — exemplars that operationalize each score
point. The AES literature ("Anchor is the key", ScienceDirect S1075293526000413) finds that
putting anchors in the prompt substantially reduces the gap between LLM and human scores,
and that *which* exemplars you choose measurably changes agreement and bias.

So calibration is retrieval, not fine-tuning. At 20-200 examples, a LoRA mostly memorizes a
teacher's quirks on those items (RESEARCH.md §8); retrieval instead:
  - works from the very first graded item,
  - updates the instant the teacher grades one more paper,
  - is explainable ("scored 4 because it resembles these three you gave 4s"),
  - and leaves zero per-teacher model artifacts to ship.

Two retrieval rules that matter:
  * similarity  — nearest neighbours beat random exemplars,
  * verdict balance — spread across score levels, or the model inherits a base-rate prior
                      and drifts toward whatever grade dominates the store.
"""

from __future__ import annotations

import copy
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import httpx

EMBED_MODEL = "nomic-embed-text"
EMBED_URL = "http://localhost:11434/api/embeddings"

SCHEMA = """
CREATE TABLE IF NOT EXISTS exemplars (
    id            INTEGER PRIMARY KEY,
    assignment_id TEXT NOT NULL,
    student_ref   TEXT,              -- local pseudonym; never a real name
    text          TEXT NOT NULL,
    total         REAL NOT NULL,
    max_total     REAL NOT NULL,
    scores_json   TEXT NOT NULL,     -- {criterion_id: points}
    comment       TEXT DEFAULT '',
    source        TEXT NOT NULL,     -- imported | graded_in_app | override
    embedding     BLOB,
    embedding_model TEXT,            -- which model produced `embedding`; vectors are
                                     -- only comparable within one model
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ex_assignment ON exemplars(assignment_id);
"""


@dataclass
class Exemplar:
    id: int | None
    assignment_id: str
    text: str
    total: float
    max_total: float
    scores: dict[str, float]
    comment: str = ""
    source: str = "graded_in_app"
    student_ref: str | None = None
    embedding: list[float] | None = None
    embedding_model: str | None = None

    @property
    def pct(self) -> float:
        return self.total / self.max_total if self.max_total else 0.0

    def band(self, n_bands: int = 4) -> int:
        """Coarse score band, used to balance retrieval across the range."""
        return min(n_bands - 1, int(self.pct * n_bands))


def embed(texts: list[str], *, url: str = EMBED_URL, model: str = EMBED_MODEL) -> list[list[float]]:
    """Local embeddings via Ollama. Nothing leaves the machine."""
    out: list[list[float]] = []
    with httpx.Client(timeout=120) as client:
        for t in texts:
            r = client.post(url, json={"model": model, "prompt": t})
            r.raise_for_status()
            out.append(r.json()["embedding"])
    return out


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        # zip() would truncate to the shorter vector and return a plausible-looking
        # score. Vectors of different lengths come from different embedding models,
        # which is never a meaningful comparison — fail loudly instead.
        raise ValueError(
            f"cannot compare embeddings of different dimensions ({len(a)} vs {len(b)}); "
            "they came from different embedding models"
        )
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class ExemplarStore:
    """SQLite-backed. Vectors are small enough to brute-force at desktop scale.

    (A semester is O(1k) exemplars; a linear scan is microseconds. sqlite-vec only earns
    its keep well past that, and this keeps the dependency surface honest.)
    """

    def __init__(
        self,
        path: Path | str = "fixtures/exemplars.db",
        *,
        embedding_model: str = EMBED_MODEL,
    ) -> None:
        self.path = Path(path)
        self.embedding_model = embedding_model
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate_embedding_model()

    def _migrate_embedding_model(self) -> None:
        """Add and backfill `embedding_model` on stores written before it existed.

        Any vector already on disk was produced while the model was the hardcoded
        EMBED_MODEL constant, so backfilling that value is accurate rather than a guess
        — and it keeps a teacher's existing exemplars retrievable across the upgrade.
        """
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(exemplars)")}
        if "embedding_model" not in columns:
            self.db.execute("ALTER TABLE exemplars ADD COLUMN embedding_model TEXT")
        self.db.execute(
            "UPDATE exemplars SET embedding_model = ? "
            "WHERE embedding_model IS NULL AND embedding IS NOT NULL",
            (EMBED_MODEL,),
        )
        self.db.commit()

    def for_model(self, embedding_model: str) -> ExemplarStore:
        """A view of this store bound to one embedding model, sharing its connection.

        Request handlers must never reassign a shared store's `embedding_model`: the
        store is a singleton that grading worker threads read from, so a mid-flight
        change silently retargets a running batch's retrieval. Bind a view instead.
        """
        view = copy.copy(self)  # shallow: deliberately shares self.db
        view.embedding_model = embedding_model
        return view

    def add(self, ex: Exemplar, *, embed_now: bool = True) -> int:
        vec = ex.embedding
        if vec is None and embed_now:
            vec = embed([ex.text], model=self.embedding_model)[0]
        elif vec is not None and ex.embedding_model not in (None, self.embedding_model):
            # Storing a foreign vector under this store's model label would reintroduce
            # exactly the cross-model comparison the embedding_model column prevents.
            raise ValueError(
                f"embedding was produced by {ex.embedding_model!r}, but this store is "
                f"bound to {self.embedding_model!r}; re-embed before adding"
            )
        cur = self.db.execute(
            "INSERT INTO exemplars (assignment_id, student_ref, text, total, max_total, "
            "scores_json, comment, source, embedding, embedding_model, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                ex.assignment_id, ex.student_ref, ex.text, ex.total, ex.max_total,
                json.dumps(ex.scores), ex.comment, ex.source,
                json.dumps(vec) if vec else None,
                self.embedding_model if vec else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def add_many(self, exs: Iterable[Exemplar]) -> int:
        items = list(exs)
        if not items:
            return 0
        vecs = embed(
            [e.text for e in items],
            model=self.embedding_model,
        )  # one batch; embedding dominates the cost
        for e, v in zip(items, vecs):
            e.embedding = v
            e.embedding_model = self.embedding_model
            self.add(e, embed_now=False)
        return len(items)

    def _all(self, assignment_id: str) -> list[Exemplar]:
        rows = self.db.execute(
            "SELECT * FROM exemplars WHERE assignment_id = ?", (assignment_id,)
        ).fetchall()
        return [
            Exemplar(
                id=r["id"], assignment_id=r["assignment_id"], student_ref=r["student_ref"],
                text=r["text"], total=r["total"], max_total=r["max_total"],
                scores=json.loads(r["scores_json"]), comment=r["comment"] or "",
                source=r["source"],
                embedding=json.loads(r["embedding"]) if r["embedding"] else None,
                embedding_model=r["embedding_model"],
            )
            for r in rows
        ]

    def count(self, assignment_id: str) -> int:
        """Exemplars retrieval can actually use under the current embedding model.

        This gates cold-start and anchor use, so counting rows that `retrieve()` will
        skip would report a calibrated store while grading with no anchors at all.
        """
        return self.db.execute(
            "SELECT COUNT(*) FROM exemplars "
            "WHERE assignment_id = ? AND embedding IS NOT NULL AND embedding_model = ?",
            (assignment_id, self.embedding_model),
        ).fetchone()[0]

    # Override exemplars get a similarity boost so they outrank imports at the same
    # distance. The teacher's correction is the most recent signal about their standard,
    # and the PRD (FR-5.2) requires overrides weighted above pre-import exemplars.
    OVERRIDE_BOOST = 0.15

    def retrieve(
        self,
        assignment_id: str,
        query_text: str,
        *,
        k: int = 6,
        balance: bool = True,
        exclude_ids: set[int] | None = None,
    ) -> list[tuple[Exemplar, float]]:
        """Top-k by similarity, spread across score bands.

        Pure nearest-neighbour retrieval will happily return six A-grade papers, and the
        model then anchors on "everything here is an A". Balancing across bands keeps the
        full range of the teacher's judgment in view. Diminishing returns past ~8-12
        exemplars, so k defaults small.

        Override exemplars (source="override") get a similarity boost so the teacher's
        most recent corrections are preferentially retrieved — this is the "weighted
        above pre-import exemplars" requirement (PRD FR-5.2).
        """
        # Vectors from another embedding model live in a different space; comparing them
        # yields a confident-looking number that means nothing. Skip them rather than
        # quietly degrading retrieval when the teacher changes model_embed in Settings.
        pool = [e for e in self._all(assignment_id)
                if e.embedding
                and e.embedding_model == self.embedding_model
                and (not exclude_ids or e.id not in exclude_ids)]
        if not pool:
            return []

        qv = embed([query_text], model=self.embedding_model)[0]
        scored = sorted(
            ((e, cosine(qv, e.embedding) + (self.OVERRIDE_BOOST if e.source == "override" else 0.0))
             for e in pool),  # type: ignore[arg-type]
            key=lambda p: -p[1],
        )
        if not balance or len(scored) <= k:
            return scored[:k]

        by_band: dict[int, list[tuple[Exemplar, float]]] = {}
        for e, s in scored:
            by_band.setdefault(e.band(), []).append((e, s))

        picked: list[tuple[Exemplar, float]] = []
        bands = sorted(by_band)
        # Round-robin the bands, taking the most similar remaining item from each.
        while len(picked) < k and any(by_band[b] for b in bands):
            for b in bands:
                if by_band[b] and len(picked) < k:
                    picked.append(by_band[b].pop(0))
        return sorted(picked, key=lambda p: -p[1])


def render_anchors(anchors: list[tuple[Exemplar, float]], rubric_ids: list[str]) -> str:
    """Format retrieved exemplars for the prompt.

    The teacher's actual scores and comments are the payload — this is what "the instructor
    graded work like this at this level" looks like to the model.
    """
    if not anchors:
        return ""
    out: list[str] = []
    for i, (e, sim) in enumerate(anchors, 1):
        detail = ", ".join(f"{cid}={e.scores.get(cid, '?')}" for cid in rubric_ids if cid in e.scores)
        out.append(f"## Example {i} (similarity {sim:.2f}) — instructor scored {e.total:g}/{e.max_total:g}")
        if detail:
            out.append(f"Per criterion: {detail}")
        if e.comment:
            out.append(f'Instructor comment: "{e.comment}"')
        excerpt = e.text if len(e.text) <= 900 else e.text[:900] + " ..."
        out.append(f"Submission:\n{excerpt}\n")
    return "\n".join(out)
