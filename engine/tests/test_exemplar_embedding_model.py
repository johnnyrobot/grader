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


def test_legacy_rows_without_a_model_are_backfilled_to_the_default(tmp_path, monkeypatch):
    """Pre-migration vectors provably came from the hardcoded EMBED_MODEL constant."""
    db_path = tmp_path / "ex.db"
    store = ExemplarStore(db_path, embedding_model=EMBED_MODEL)
    monkeypatch.setattr("grader.exemplars.embed", _fixed_embed([1.0, 0.0]))
    store.add(_exemplar("legacy row"))
    # Simulate a database written before the embedding_model column existed.
    store.db.execute("UPDATE exemplars SET embedding_model = NULL")
    store.db.commit()
    store.db.close()

    reopened = ExemplarStore(db_path, embedding_model=EMBED_MODEL)
    results = reopened.retrieve("a1", "query")

    assert [e.text for e, _ in results] == ["legacy row"]


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
