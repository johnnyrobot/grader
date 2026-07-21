"""Exemplar vectors must never be compared across embedding models.

Saved settings let the teacher change `model_embed` at runtime (api.py rebinds
`_exemplar_store.embedding_model`). Vectors already in the store came from whatever
model was configured when they were written, so retrieval has to know which model
produced each row — otherwise a settings change silently turns anchor retrieval into
noise with no error and no UI signal.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

pytest = __import__("pytest")

from grader.exemplars import EMBED_MODEL, Exemplar, ExemplarStore, cosine


def _exemplar(text: str, total: float = 4.0) -> Exemplar:
    return Exemplar(
        id=None,
        assignment_id="a1",
        student_ref="pseudo-1",
        text=text,
        total=total,
        max_total=5.0,
        scores={"c1": total},
        comment="",
        source="imported",
    )


def _fixed_embed(vector: list[float]):
    """Stand-in for the Ollama call; returns one fixed vector per text."""
    def _embed(texts, *, url=None, model=None):
        return [list(vector) for _ in texts]
    return _embed


def test_cosine_rejects_dimension_mismatch():
    """zip() truncates silently, turning a model mismatch into a plausible number."""
    with pytest.raises(ValueError):
        cosine([1.0, 0.0, 0.0], [1.0, 0.0])


def test_retrieve_excludes_exemplars_embedded_by_a_different_model(tmp_path, monkeypatch):
    store = ExemplarStore(tmp_path / "ex.db", embedding_model="model-a")
    monkeypatch.setattr("grader.exemplars.embed", _fixed_embed([1.0, 0.0]))
    store.add(_exemplar("written under model-a"))

    # Teacher switches the embedding model in Settings.
    store.embedding_model = "model-b"
    monkeypatch.setattr("grader.exemplars.embed", _fixed_embed([1.0, 0.0, 0.0]))

    assert store.retrieve("a1", "query") == []


def test_retrieve_returns_exemplars_embedded_by_the_current_model(tmp_path, monkeypatch):
    store = ExemplarStore(tmp_path / "ex.db", embedding_model="model-a")
    monkeypatch.setattr("grader.exemplars.embed", _fixed_embed([1.0, 0.0]))
    store.add(_exemplar("written under model-a"))

    results = store.retrieve("a1", "query")

    assert [e.text for e, _ in results] == ["written under model-a"]


LEGACY_SCHEMA = """
CREATE TABLE exemplars (
    id            INTEGER PRIMARY KEY,
    assignment_id TEXT NOT NULL,
    student_ref   TEXT,
    text          TEXT NOT NULL,
    total         REAL NOT NULL,
    max_total     REAL NOT NULL,
    scores_json   TEXT NOT NULL,
    comment       TEXT DEFAULT '',
    source        TEXT NOT NULL,
    embedding     BLOB,
    created_at    TEXT NOT NULL
);
"""


def test_legacy_database_without_the_column_migrates_and_stays_retrievable(tmp_path, monkeypatch):
    """Exercises the real ALTER TABLE path against a pre-migration teacher database.

    Vectors written before this column existed provably came from the hardcoded
    EMBED_MODEL constant, so backfilling that value preserves the exemplar library
    instead of stranding it.
    """
    import sqlite3

    db_path = tmp_path / "ex.db"
    legacy = sqlite3.connect(db_path)
    legacy.executescript(LEGACY_SCHEMA)
    legacy.execute(
        "INSERT INTO exemplars (assignment_id, student_ref, text, total, max_total, "
        "scores_json, comment, source, embedding, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("a1", "pseudo-1", "legacy row", 4.0, 5.0, json.dumps({"c1": 4.0}), "",
         "imported", json.dumps([1.0, 0.0]), "2026-01-01T00:00:00+00:00"),
    )
    legacy.commit()
    columns = {r[1] for r in legacy.execute("PRAGMA table_info(exemplars)")}
    legacy.close()
    assert "embedding_model" not in columns, "fixture must start without the column"

    monkeypatch.setattr("grader.exemplars.embed", _fixed_embed([1.0, 0.0]))
    migrated = ExemplarStore(db_path, embedding_model=EMBED_MODEL)

    assert migrated.count("a1") == 1
    assert [e.text for e, _ in migrated.retrieve("a1", "query")] == ["legacy row"]


def test_add_records_the_model_that_produced_the_vector(tmp_path, monkeypatch):
    store = ExemplarStore(tmp_path / "ex.db", embedding_model="model-a")
    monkeypatch.setattr("grader.exemplars.embed", _fixed_embed([1.0, 0.0]))
    store.add(_exemplar("row"))

    row = store.db.execute("SELECT embedding_model, embedding FROM exemplars").fetchone()

    assert row["embedding_model"] == "model-a"
    assert json.loads(row["embedding"]) == [1.0, 0.0]


def test_count_reflects_only_exemplars_usable_by_the_current_model(tmp_path, monkeypatch):
    """count() gates cold-start, anchor use, and the health panel.

    If it counted rows retrieval cannot use, the app would report a calibrated store
    while grading with zero anchors.
    """
    store = ExemplarStore(tmp_path / "ex.db", embedding_model="model-a")
    monkeypatch.setattr("grader.exemplars.embed", _fixed_embed([1.0, 0.0]))
    store.add(_exemplar("row"))
    assert store.count("a1") == 1

    store.embedding_model = "model-b"

    assert store.count("a1") == 0


def test_for_model_view_does_not_mutate_the_shared_store(tmp_path):
    """Request handlers must bind a model per call, never reassign the shared store's.

    The store is a module-level singleton shared with grading worker threads; mutating
    it mid-request retargets a running batch's retrieval.
    """
    store = ExemplarStore(tmp_path / "ex.db", embedding_model="model-a")

    view = store.for_model("model-b")

    assert view.embedding_model == "model-b"
    assert store.embedding_model == "model-a"
    assert view.db is store.db


def test_add_rejects_a_precomputed_vector_from_another_model(tmp_path):
    store = ExemplarStore(tmp_path / "ex.db", embedding_model="model-a")
    foreign = _exemplar("from another model")
    foreign.embedding = [1.0, 0.0]
    foreign.embedding_model = "model-b"

    with pytest.raises(ValueError):
        store.add(foreign)


def test_add_accepts_a_precomputed_vector_from_the_same_model(tmp_path):
    store = ExemplarStore(tmp_path / "ex.db", embedding_model="model-a")
    native = _exemplar("same model")
    native.embedding = [1.0, 0.0]
    native.embedding_model = "model-a"

    store.add(native)

    assert store.count("a1") == 1
