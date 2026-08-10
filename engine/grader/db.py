"""Connection policy for the engine's SQLite stores.

One Database is one open connection, held for its caller's whole lifetime — the
app process, or a single test. Nothing here opens or closes a connection per
call.

The engine reads these stores from the request threadpool while the background
grading thread writes to them, so every connection needs the same treatment:
WAL, a busy timeout, and check_same_thread off. Before this module each store
picked its own, and engine/api.py reached past three of them afterwards to
replace the connection they had already opened.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# A migration step is either SQL to run, or a callable for the cases SQL can't
# express on its own — an ALTER that has to check whether the column is already
# there, for instance, on a database written before migrations were tracked.
Step = str | Callable[[sqlite3.Connection], None]

_LEDGER = """
CREATE TABLE IF NOT EXISTS _schema_migrations (
    schema     TEXT    NOT NULL,
    step       INTEGER NOT NULL,
    applied_at TEXT    NOT NULL,
    PRIMARY KEY (schema, step)
);
"""

# Long enough to ride out another writer finishing, short enough that a genuine
# deadlock still surfaces as an error rather than a hang.
_BUSY_TIMEOUT_MS = 5000


class Database:
    """One SQLite connection, plus the migrations applied to it.

    Construct with `Database.at(path)` in the app and `Database.memory()` in
    tests. The two differ only in where the bytes live: a store cannot tell
    which one it was handed, which is the point.
    """

    def __init__(self, conn: sqlite3.Connection, *, path: Path | None = None) -> None:
        self.conn = conn
        self.path = path

    @classmethod
    def at(cls, path: Path | str) -> Database:
        """A file-backed database, configured for the engine's threading model."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Persistent: journal_mode lives in the file header, so this survives
        # reconnection. busy_timeout does not — it is per-connection.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        return cls(conn, path=path)

    @classmethod
    def memory(cls) -> Database:
        """An in-memory database for tests.

        No WAL: it is meaningless without a file, and there is only ever one
        connection to an in-memory database anyway.
        """
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        return cls(conn)

    def migrate(self, schema: str, steps: Sequence[Step]) -> None:
        """Apply `steps` in order, each at most once, recorded per schema.

        Steps are identified by position, so they are append-only: inserting or
        reordering one silently re-runs the wrong migration against databases
        that already have it. A step that raises records nothing, so the next
        open retries it.
        """
        self.conn.executescript(_LEDGER)
        done = {
            row["step"]
            for row in self.conn.execute(
                "SELECT step FROM _schema_migrations WHERE schema = ?", (schema,)
            )
        }
        for index, step in enumerate(steps):
            if index in done:
                continue
            if callable(step):
                step(self.conn)
            else:
                self.conn.executescript(step)
            self.conn.execute(
                "INSERT INTO _schema_migrations (schema, step, applied_at) VALUES (?,?,?)",
                (schema, index, datetime.now(timezone.utc).isoformat()),
            )
            self.conn.commit()

    # -- Connection passthrough, so a store's body reads the same as before ----

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, seq: Sequence[Sequence[Any]]) -> sqlite3.Cursor:
        return self.conn.executemany(sql, seq)

    def executescript(self, sql: str) -> sqlite3.Cursor:
        return self.conn.executescript(sql)

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
