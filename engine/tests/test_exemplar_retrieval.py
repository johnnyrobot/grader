"""Retrieval is the calibration bet, so what it selects is the product.

`ExemplarStore.retrieve` decides which of the teacher's previously graded papers
become anchors in the grading prompt. Two rules ride on it that the PRD states
outright:

  * band balancing — pure nearest-neighbour will happily return six A papers,
    and the model then anchors on "everything here is an A";
  * FR-5.2 — a teacher's override outranks an imported exemplar at the same
    distance, because a correction is the most recent word on their standard.

Neither was tested. These use injected vectors rather than the embedding
service, so they assert the selection logic and never touch Ollama.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from grader.db import Database  # noqa: E402
from grader.exemplars import (  # noqa: E402
    Exemplar,
    ExemplarStore,
    render_anchors,
)

MODEL = "nomic-embed-text:latest"


@pytest.fixture
def store():
    return ExemplarStore(Database.memory(), embedding_model=MODEL)


def _add(store, *, text, total, vec, source="graded_in_app", max_total=10.0, comment=""):
    """Add an exemplar with a hand-chosen vector, so similarity is decided here."""
    return store.add(
        Exemplar(
            id=None,
            assignment_id="a1",
            text=text,
            total=total,
            max_total=max_total,
            scores={"overall": total},
            comment=comment,
            source=source,
            embedding=vec,
            embedding_model=MODEL,
        ),
        embed_now=False,
    )


@pytest.fixture
def no_embedding_service(monkeypatch):
    """The query vector, without calling Ollama."""
    monkeypatch.setattr("grader.exemplars.embed", lambda texts, **kw: [[1.0, 0.0]])


# -- Band balancing ----------------------------------------------------------


def test_retrieval_spreads_across_score_bands_rather_than_returning_six_a_papers(
    store, no_embedding_service
):
    # Five near-identical top-scoring papers, one weak one further away.
    for i in range(5):
        _add(store, text=f"strong {i}", total=10.0, vec=[1.0, 0.01 * i])
    _add(store, text="weak", total=1.0, vec=[0.7, 0.7])

    hits = store.retrieve("a1", "query", k=3, balance=True)

    bands = {e.band() for e, _ in hits}
    assert len(bands) > 1, "all three anchors came from one score band"
    assert any(e.total == 1.0 for e, _ in hits)


def test_without_balancing_the_nearest_neighbours_win_outright(
    store, no_embedding_service
):
    """The control: balancing is doing the work, not the ordering."""
    for i in range(5):
        _add(store, text=f"strong {i}", total=10.0, vec=[1.0, 0.01 * i])
    _add(store, text="weak", total=1.0, vec=[0.7, 0.7])

    hits = store.retrieve("a1", "query", k=3, balance=False)

    assert all(e.total == 10.0 for e, _ in hits)


def test_retrieval_returns_at_most_k(store, no_embedding_service):
    for i in range(10):
        _add(store, text=f"paper {i}", total=float(i), vec=[1.0, 0.01 * i])

    assert len(store.retrieve("a1", "query", k=4)) == 4


def test_a_pool_smaller_than_k_returns_everything_it_has(store, no_embedding_service):
    _add(store, text="only one", total=5.0, vec=[1.0, 0.0])

    assert len(store.retrieve("a1", "query", k=6)) == 1


# -- FR-5.2: the teacher's correction outranks an import ---------------------


def test_an_override_outranks_an_import_at_the_same_distance(
    store, no_embedding_service
):
    _add(store, text="imported", total=5.0, vec=[1.0, 0.0], source="import")
    _add(store, text="corrected", total=5.0, vec=[1.0, 0.0], source="override")

    hits = store.retrieve("a1", "query", k=2, balance=False)

    assert hits[0][0].text == "corrected"


def test_the_boost_is_not_large_enough_to_beat_a_much_closer_import(
    store, no_embedding_service
):
    """A correction is weighted, not decisive — it must still be relevant."""
    _add(store, text="near import", total=5.0, vec=[1.0, 0.0], source="import")
    _add(store, text="far override", total=5.0, vec=[-1.0, 0.0], source="override")

    hits = store.retrieve("a1", "query", k=2, balance=False)

    assert hits[0][0].text == "near import"


# -- Isolation ---------------------------------------------------------------


def test_retrieval_never_crosses_assignments(store, no_embedding_service):
    _add(store, text="other assignment", total=5.0, vec=[1.0, 0.0])
    store.add(
        Exemplar(id=None, assignment_id="a2", text="elsewhere", total=5.0,
                 max_total=10.0, scores={}, embedding=[1.0, 0.0], embedding_model=MODEL),
        embed_now=False,
    )

    hits = store.retrieve("a2", "query", k=6)

    assert [e.text for e, _ in hits] == ["elsewhere"]


def test_an_excluded_id_is_not_retrieved_as_its_own_anchor(store, no_embedding_service):
    """Grading a paper must not anchor on that same paper."""
    first = _add(store, text="the one being graded", total=5.0, vec=[1.0, 0.0])
    _add(store, text="another", total=5.0, vec=[0.9, 0.1])

    hits = store.retrieve("a1", "query", k=6, exclude_ids={first})

    assert [e.text for e, _ in hits] == ["another"]


def test_vectors_from_another_embedding_model_are_skipped(store, no_embedding_service):
    """Comparing across embedding spaces yields a confident number meaning nothing."""
    _add(store, text="current model", total=5.0, vec=[1.0, 0.0])
    store.db.execute(
        "UPDATE exemplars SET embedding_model = 'some-old-model' WHERE text = ?",
        ("current model",),
    )
    store.db.commit()

    assert store.retrieve("a1", "query", k=6) == []


# -- What the model actually sees --------------------------------------------


def test_rendered_anchors_carry_the_teachers_score_and_comment():
    ex = Exemplar(id=1, assignment_id="a1", text="the essay body", total=8.0,
                  max_total=10.0, scores={"thesis": 4.0}, comment="clear argument")

    rendered = render_anchors([(ex, 0.91)], ["thesis"])

    assert "8/10" in rendered
    assert "thesis=4.0" in rendered
    assert "clear argument" in rendered
    assert "the essay body" in rendered


def test_no_anchors_renders_to_nothing_rather_than_an_empty_heading():
    """An empty section would tell the model it has seen the teacher's standard."""
    assert render_anchors([], ["thesis"]) == ""


def test_a_long_exemplar_is_excerpted_so_anchors_cannot_crowd_out_the_submission():
    ex = Exemplar(id=1, assignment_id="a1", text="x" * 5000, total=8.0,
                  max_total=10.0, scores={})

    rendered = render_anchors([(ex, 0.9)], [])

    assert len(rendered) < 1200
    assert "..." in rendered
