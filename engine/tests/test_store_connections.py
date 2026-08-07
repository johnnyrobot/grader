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
def test_store_built_on_a_file_gets_the_app_connection_policy(tmp_path, store_cls):
    """End to end: a store handed a file-backed Database inherits WAL from it.

    Connection policy lives in Database.at() rather than in four constructors,
    so this asserts the wiring rather than re-testing the pragma.
    """
    from grader.db import Database

    store = store_cls(Database.at(tmp_path / "store.db"))
    assert store.db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


# ---------------------------------------------------------------------------
# The Database seam
# ---------------------------------------------------------------------------

def test_database_at_applies_the_app_connection_policy(tmp_path):
    from grader.db import Database

    db = Database.at(tmp_path / "nested" / "store.db")

    assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert db.execute("PRAGMA busy_timeout").fetchone()[0] > 0
    assert db.path.parent.exists()  # parent directories created


def test_database_memory_is_usable_without_a_file(tmp_path):
    from grader.db import Database

    db = Database.memory()
    db.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT);")
    db.execute("INSERT INTO t (name) VALUES ('x')")
    db.commit()

    assert db.path is None
    assert db.execute("SELECT name FROM t").fetchone()["name"] == "x"


def test_migrate_applies_each_step_exactly_once(tmp_path):
    from grader.db import Database

    db = Database.at(tmp_path / "store.db")
    db.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY);")

    applied = []

    def add_column(conn):
        applied.append("callable")
        conn.execute("ALTER TABLE t ADD COLUMN label TEXT")

    steps = ["CREATE TABLE IF NOT EXISTS other (id INTEGER PRIMARY KEY);", add_column]
    db.migrate("t", steps)
    db.migrate("t", steps)  # second construction of the same store

    assert applied == ["callable"]
    columns = {r["name"] for r in db.execute("PRAGMA table_info(t)")}
    assert "label" in columns


def test_migrate_retries_a_step_that_failed(tmp_path):
    """A step that raises leaves no ledger row, so the next open tries again."""
    from grader.db import Database

    db = Database.at(tmp_path / "store.db")
    db.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY);")

    attempts = []

    def flaky(conn):
        attempts.append(len(attempts))
        if len(attempts) == 1:
            raise sqlite3.OperationalError("transient")
        conn.execute("ALTER TABLE t ADD COLUMN label TEXT")

    with pytest.raises(sqlite3.OperationalError):
        db.migrate("t", [flaky])
    db.migrate("t", [flaky])

    assert len(attempts) == 2
    assert "label" in {r["name"] for r in db.execute("PRAGMA table_info(t)")}


@pytest.mark.parametrize("store_cls", STORES, ids=lambda c: c.__name__)
def test_store_accepts_a_database(store_cls):
    """Stores take a connection, so a caller never reaches past them to swap one."""
    from grader.db import Database

    store = store_cls(Database.memory())
    assert store.db.execute("SELECT 1").fetchone()[0] == 1
