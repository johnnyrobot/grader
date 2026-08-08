"""Which annotated files go back to Canvas with which grade.

`writeback.py` produces annotated copies and feedback reports; `canvas.py` can
upload one as a submission-comment attachment. Nothing joined the two, so PRD
line 110 / FR-3.4 — "annotated files upload as comment attachments" — had a
writer and an uploader and no record of what belonged to whom.

This is that record, and it is deliberately thin: a grade id, a path on this
machine, and when it was attached. The file itself stays on disk. Nothing is
uploaded here — attaching is a local act the teacher can undo, and it is
`canvas_post` that decides an approved grade is going out.

Paths are stored absolute and de-duplicated per grade, so attaching the same
report twice does not post it twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from grader.db import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS grade_attachments (
    id          INTEGER PRIMARY KEY,
    grade_id    TEXT NOT NULL,
    path        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (grade_id, path)
);
CREATE INDEX IF NOT EXISTS idx_ga_grade ON grade_attachments(grade_id);
"""


@dataclass(frozen=True)
class Attachment:
    id: int
    grade_id: str
    path: Path
    created_at: str

    @property
    def exists(self) -> bool:
        return self.path.exists()


class OutsideAttachmentRoot(ValueError):
    """A path that would send a file the app does not own to Canvas."""


class AttachmentStore:
    """The teacher's choice of which files ride along with a posted grade.

    Scoped to `root`. Attaching is the one place where a path chosen by a client
    decides which bytes leave this machine — `canvas_post` uploads whatever is
    attached — so an unconstrained path here is an arbitrary-file-read wired to
    an outbound request, and the first product invariant is that student data
    stays on the device. Confining attachments to the app's own data directory
    keeps the feature (writeback writes its annotated copies there) without
    letting `~/.ssh/id_rsa` or the settings file holding the Canvas token be
    named as feedback.
    """

    def __init__(self, db: Database, root: Path | str) -> None:
        self.db = db
        self.root = Path(root).expanduser().resolve()
        self.db.executescript(SCHEMA)

    def attach(self, grade_id: str, path: Path | str) -> Attachment:
        """Record `path` as going out with `grade_id`.

        Refuses a file that is not there: the failure belongs at the moment the
        teacher chooses it, not in the middle of posting a batch. Refuses one
        outside `root` for the reason in the class docstring.
        """
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_relative_to(self.root):
            raise OutsideAttachmentRoot(
                f"attachments must live under {self.root}, got {resolved}"
            )
        if not resolved.is_file():
            raise FileNotFoundError(f"no such file to attach: {resolved}")
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "INSERT OR IGNORE INTO grade_attachments (grade_id, path, created_at) "
            "VALUES (?,?,?)",
            (grade_id, str(resolved), now),
        )
        self.db.commit()
        row = self.db.execute(
            "SELECT * FROM grade_attachments WHERE grade_id = ? AND path = ?",
            (grade_id, str(resolved)),
        ).fetchone()
        return _to_attachment(row)

    def for_grade(self, grade_id: str) -> list[Attachment]:
        rows = self.db.execute(
            "SELECT * FROM grade_attachments WHERE grade_id = ? ORDER BY id",
            (grade_id,),
        ).fetchall()
        return [_to_attachment(row) for row in rows]

    def detach(self, grade_id: str, path: Path | str) -> bool:
        """Drop one attachment. True if there was one to drop."""
        resolved = Path(path).expanduser().resolve()
        cur = self.db.execute(
            "DELETE FROM grade_attachments WHERE grade_id = ? AND path = ?",
            (grade_id, str(resolved)),
        )
        self.db.commit()
        return cur.rowcount > 0


def _to_attachment(row) -> Attachment:
    return Attachment(
        id=row["id"],
        grade_id=row["grade_id"],
        path=Path(row["path"]),
        created_at=row["created_at"],
    )
