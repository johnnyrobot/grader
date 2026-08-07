"""What the teacher configured: models, Canvas credentials, review thresholds.

Settings used to live in the transport layer while `grader/*` consumed them,
which is backwards — and it showed. Eight route handlers each re-read and
re-validated the file, then indexed a raw dict and coerced the value themselves.
Commit f5aa945 had to touch six files to make "consult settings" work, because
there was no seam to change; commit 56f5844 fixed two handlers that had each
read settings independently and then written to the same shared store.

So: one value, resolved once, typed. `SettingsStore.current()` returns a frozen
`Settings`, not a dict — there is nothing to re-coerce and nothing to mutate.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)

# Hosts where plaintext HTTP cannot be intercepted off-device.
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

# Where Canvas attachments land before a teacher has configured anything. The engine
# reads the same variable for its own data directory; the default belongs here because
# `data_dir` is a setting, and a setting's default is this module's to know.
DEFAULT_DATA_DIR = str(Path(os.environ.get("GRADER_DATA_DIR", "fixtures")))


@dataclass(frozen=True)
class CanvasConnection:
    """Where Canvas is and how to authenticate to it.

    Shaped to match `grader.canvas`'s existing `**connection` convention, so
    that adapter's interface does not have to change.
    """

    base_url: str | None
    token: str | None

    def as_kwargs(self) -> dict[str, str | None]:
        return {"base_url": self.base_url, "token": self.token}

    def __repr__(self) -> str:
        return f"CanvasConnection(base_url={self.base_url!r}, token=<redacted>)"


@dataclass(frozen=True)
class Thresholds:
    """Where the triage lanes fall."""

    review_first: float
    auto_approve: float


class Settings(BaseModel):
    """A snapshot of the saved settings. Frozen: a resolved value cannot drift.

    That immutability is the point. A handler holding this cannot be retargeted
    by a concurrent write, and cannot retarget anything else by assigning to it.
    """

    model_config = {"frozen": True}

    # repr=False so the token cannot reach a log line via an interpolated
    # Settings; `masked()` is the only shape allowed out of the process.
    canvas_token: str = Field(default="", repr=False)
    canvas_url: str = ""
    model_text: str = "grader-text:8b"
    model_vision: str = "grader-vision:1.3b"
    model_embed: str = "nomic-embed-text:latest"
    data_dir: str = DEFAULT_DATA_DIR
    auto_approve_threshold: float = Field(default=0.95, ge=0.5, le=1.0)
    review_first_threshold: float = Field(default=0.70, ge=0.0, le=1.0)

    @field_validator("canvas_url")
    @classmethod
    def validate_canvas_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            return value
        parsed = urlsplit(value)
        if parsed.scheme == "https":
            return value
        # A Canvas token over plaintext HTTP is a credential in the clear. Loopback is
        # the one place that cannot be intercepted, and local Canvas dev instances
        # legitimately run without TLS. Compare the parsed host, never a prefix —
        # "localhost.attacker.example" starts with "localhost".
        if parsed.scheme == "http" and (parsed.hostname or "").lower() in LOOPBACK_HOSTS:
            return value
        raise ValueError(
            "Canvas URL must use https:// (plain http:// is allowed only for localhost)"
        )

    @field_validator("model_text")
    @classmethod
    def validate_text_model(cls, value: str) -> str:
        if not value.startswith("grader-text:"):
            raise ValueError("text model must use a capped grader-text tag")
        return value

    @field_validator("model_vision")
    @classmethod
    def validate_vision_model(cls, value: str) -> str:
        if not value.startswith("grader-vision:"):
            raise ValueError("vision model must use a capped grader-vision tag")
        return value

    @field_validator("model_embed")
    @classmethod
    def validate_embed_model(cls, value: str) -> str:
        if not value.startswith("nomic-embed-text:"):
            raise ValueError("embedding model must use a nomic-embed-text tag")
        return value

    @field_validator("data_dir")
    @classmethod
    def validate_data_dir(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Canvas attachment directory cannot be blank")
        return str(Path(value).expanduser())

    @model_validator(mode="after")
    def validate_threshold_order(self) -> Settings:
        if self.review_first_threshold >= self.auto_approve_threshold:
            raise ValueError("needs-review threshold must be lower than high-confidence threshold")
        return self

    # -- derived views --------------------------------------------------------

    @property
    def canvas(self) -> CanvasConnection:
        return CanvasConnection(
            base_url=self.canvas_url or None,
            token=self.canvas_token or None,
        )

    @property
    def thresholds(self) -> Thresholds:
        return Thresholds(
            review_first=self.review_first_threshold,
            auto_approve=self.auto_approve_threshold,
        )

    def attachment_root(self, *, ensure: bool = True) -> Path:
        """Where Canvas attachments are downloaded. Created by default."""
        root = Path(self.data_dir or DEFAULT_DATA_DIR).expanduser()
        if ensure:
            root.mkdir(parents=True, exist_ok=True)
        return root

    def masked(self) -> dict:
        """The only shape these settings may leave the process in.

        Anywhere else could forget to mask and leak a teacher's Canvas token.
        """
        response = {k: v for k, v in self.model_dump().items() if k != "canvas_token"}
        if self.canvas_token:
            response["canvas_token_masked"] = (
                "••••••••" if len(self.canvas_token) <= 8
                else self.canvas_token[:4] + "..." + self.canvas_token[-4:]
            )
        return response


class SettingsPatch(BaseModel):
    """A partial update. Unset fields are left alone."""

    canvas_token: str | None = None
    canvas_url: str | None = None
    model_text: str | None = None
    model_vision: str | None = None
    model_embed: str | None = None
    data_dir: str | None = None
    auto_approve_threshold: float | None = None
    review_first_threshold: float | None = None


class SettingsStore:
    """The settings file, and the rules for reading and replacing it."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def current(self) -> Settings:
        """Load what is saved, discarding as little as possible. Never raises.

        Falling back silently is indistinguishable from "never configured", and
        discarding the whole file over one bad field loses settings the teacher
        did set — which is how a stricter validator turns into surprise data
        loss on upgrade. So: drop the fields that fail, keep the rest, say so.
        """
        if not self.path.exists():
            return Settings()

        try:
            saved = json.loads(self.path.read_text())
        except Exception:
            logger.warning(
                "Ignoring unreadable settings file %s; falling back to defaults",
                self.path, exc_info=True,
            )
            return Settings()

        try:
            return Settings.model_validate(saved)
        except ValidationError as first_error:
            # Cross-field errors (e.g. threshold ordering) carry no field location,
            # so they cannot be attributed to one setting — those fall back wholesale.
            invalid = {str(e["loc"][0]) for e in first_error.errors() if e.get("loc")}
            if invalid:
                kept = {k: v for k, v in saved.items() if k not in invalid}
                try:
                    settings = Settings.model_validate(kept)
                except ValidationError:
                    pass
                else:
                    logger.warning(
                        "Ignoring invalid settings (%s) in %s; keeping the rest",
                        ", ".join(sorted(invalid)), self.path,
                    )
                    return settings
            logger.warning(
                "Ignoring unusable settings file %s; falling back to defaults",
                self.path, exc_info=True,
            )
            return Settings()

    def update(self, patch: SettingsPatch) -> Settings:
        """Merge a patch onto what is saved, validate the whole, then persist.

        All or nothing: a rejected patch leaves the file untouched. Reads fresh
        from disk rather than trusting a Settings the caller may be holding, so
        two concurrent updates cannot each write a stale merge.

        An explicit empty `canvas_token` means "leave it alone", not "clear it" —
        the settings form sends a blank field when the teacher does not retype a
        saved token.
        """
        merged = self.current().model_dump()
        for key, value in patch.model_dump(exclude_unset=True).items():
            if value is None or (key == "canvas_token" and value == ""):
                continue
            merged[key] = value

        settings = Settings.model_validate(merged)
        root = settings.attachment_root(ensure=False)
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"Canvas attachment directory is not writable: {exc}") from exc
        if not root.is_dir():
            raise ValueError("Canvas attachment directory is not a directory")

        self._write(settings)
        return settings

    def _write(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(settings.model_dump(), indent=2))
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
