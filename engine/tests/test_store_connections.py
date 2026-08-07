"""Connection policy for the SQLite-backed stores.

The engine holds these stores for the life of the process and touches them from
both the request threadpool and the background grading thread. Rollback-journal
mode serialises a writer against every reader, which is where "database is
locked" comes from under exactly that access pattern.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from grader.canvas import OfflinePostQueue
from grader.exemplars import ExemplarStore
from grader.overrides import OverrideStore
from grader.rubrics import RubricStore


STORES = [ExemplarStore, OverrideStore, RubricStore, OfflinePostQueue]


@pytest.mark.parametrize("store_cls", STORES, ids=lambda c: c.__name__)
def test_store_opens_its_database_in_wal_mode(tmp_path, store_cls):
    store = store_cls(tmp_path / "store.db")
    mode = store.db.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


@pytest.mark.parametrize("store_cls", STORES, ids=lambda c: c.__name__)
def test_wal_survives_reconnection(tmp_path, store_cls):
    """engine/api.py replaces each store's connection after construction.

    journal_mode is written into the database header, so the replacement
    connection inherits it — the store constructor is the only place that has
    to set it.
    """
    path = tmp_path / "store.db"
    store_cls(path)

    reconnected = sqlite3.connect(str(path), check_same_thread=False)
    mode = reconnected.execute("PRAGMA journal_mode").fetchone()[0]
    reconnected.close()
    assert mode.lower() == "wal"
