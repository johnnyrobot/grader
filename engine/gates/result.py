"""One answer shape for "did this check pass, and what did it see?"

Five gates each declared their own result dataclass — same three fields, same
exit_code, same print loop, differing only in the banner they print. Six copies
of fourteen lines. The evidence scripts under scripts/ then invented a second
vocabulary for the same idea, which is why release_readiness.py has to normalise
every artifact it reads before it can compare any two of them.

So: one CheckResult. A gate returns one, a script serialises one, and
`CheckResult.load` reads one back without the reader needing to know which check
wrote it.

Two rules travel with the type:

**Absent evidence is MISSING, never PASS and never FAIL.** A check that did not
run has not passed, and reporting it as failed sends someone looking for a
defect that isn't there. It gets its own status so a reader can tell "we looked
and it was broken" from "we never looked".

**A check's result is its own.** `load` reads exactly one artifact and consults
nothing else. Verdicts that combine checks are computed by the caller, from
results that were each produced independently — otherwise one flaky check
silently rewrites the status of its neighbours, and the report stops being
evidence of anything.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
MISSING = "MISSING"

# Unknown statuses print with a neutral marker rather than raising. A gate that
# has already done its work must not die formatting the answer.
_ICONS = {PASS: "✓", FAIL: "✗", SKIP: "⊘", MISSING: "?"}
_UNKNOWN_ICON = "•"


def artifact_ref(path: Path | None) -> dict[str, Any]:
    """Provenance for a file a check read or wrote.

    The hash is what makes an evidence bundle auditable after the fact: it says
    which bytes produced this verdict, not merely which filename.
    """
    if path is None:
        return {"path": None, "exists": False}
    ref: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return ref
    stat = path.stat()
    ref.update({
        "size_bytes": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
    return ref


@dataclass
class CheckResult:
    """What one check concluded, and the numbers behind it.

    Subclasses set `label` and nothing else — the banner is the only thing that
    legitimately differs between a quality gate and a privacy gate.
    """

    status: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    label: ClassVar[str] = "GATE"

    # -- verdict --------------------------------------------------------------

    def exit_code(self) -> int:
        """Only FAIL blocks.

        SKIP and MISSING exit 0 deliberately: gates degrade rather than block CI
        when the golden set or Ollama isn't there, and a missing optional
        artifact is a gap in the report, not a broken build. Whether a gap may
        ship is the release verdict's call, not one gate's.
        """
        return 1 if self.status == FAIL else 0

    def print(self) -> None:
        icon = _ICONS.get(self.status, _UNKNOWN_ICON)
        print(f"\n{icon} {self.label}: {self.status}")
        print(f"  {self.summary}")
        for key, value in self.details.items():
            print(f"  {key}: {value}")

    # -- serialisation --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The artifact shape: status, summary, and everything the check saw.

        `details` wins over `summary`, because a result read back off disk keeps
        the artifact's own `summary` in details — several checks use that name
        for a structured block rather than a sentence, and round-tripping one
        must not flatten it.
        """
        out: dict[str, Any] = {"status": self.status}
        if self.summary and "summary" not in self.details:
            out["summary"] = self.summary
        out.update(self.details)
        return out

    def write(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))
        return path

    @classmethod
    def load(cls, path: Path | str | None, *, missing: str = MISSING) -> CheckResult:
        """Read one check's evidence off disk. Never raises.

        `missing` is the status for evidence that isn't there — callers that
        treat an absent artifact as an acceptable skip pass SKIP; the default
        says the report has a hole in it.

        Older artifacts spell the status `release_status`, and the citation
        artifact nests it one level down. Reading those here is what keeps the
        variation from spreading into every consumer.
        """
        path = Path(path) if path is not None else None
        ref = artifact_ref(path)

        if path is None or not path.exists():
            return cls(status=missing, summary="no evidence artifact",
                       details={"artifact": ref})

        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            # An unreadable artifact is not a passing check, and it is not a
            # traceback either — the report has to be able to say so.
            return cls(status=missing, summary=f"unreadable evidence artifact: {exc}",
                       details={"artifact": ref})

        if not isinstance(data, dict):
            return cls(status=missing, summary="evidence artifact is not an object",
                       details={"artifact": ref})

        data = dict(data)
        status = data.pop("status", None) or _legacy_status(data) or missing

        # `details` keeps everything the artifact held, `summary` is only a view
        # onto it. Nothing is moved out: several checks use `summary` for a
        # structured block rather than a sentence, and a reader that expects one
        # must still find it exactly where its writer put it.
        raw_summary = data.get("summary")
        summary = raw_summary if isinstance(raw_summary, str) else ""

        return cls(status=str(status), summary=summary,
                   details={**data, "artifact": ref})


def _legacy_status(data: dict[str, Any]) -> str | None:
    """Where a status hides in artifacts written before `status` was the name."""
    if data.get("release_status"):
        return str(data["release_status"])
    nested = data.get("citation_mode")
    if isinstance(nested, dict) and nested.get("release_status"):
        return str(nested["release_status"])
    return None
