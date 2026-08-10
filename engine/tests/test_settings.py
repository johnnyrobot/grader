"""What the teacher configured — read, validated, and recovered from.

These exercise `grader.settings` directly. They used to reach through `api` for a
Pydantic model that had nothing to do with HTTP; the rules live in the settings
module now, so the tests ask the settings module.
"""

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parents[1]))

from grader.settings import (  # noqa: E402  (needs the path insert above)
    Settings,
    SettingsPatch,
    SettingsStore,
)


# ---------------------------------------------------------------------------
# Canvas URL: a token over plaintext HTTP is a credential in the clear
# ---------------------------------------------------------------------------

def test_canvas_url_accepts_https():
    assert Settings(canvas_url="https://canvas.example.edu/").canvas_url == (
        "https://canvas.example.edu"
    )


def test_canvas_url_rejects_plaintext_http_for_remote_hosts():
    with pytest.raises(ValidationError) as exc:
        Settings(canvas_url="http://canvas.example.edu")
    assert "https" in str(exc.value).lower()


def test_canvas_url_allows_plaintext_http_on_loopback():
    """Local Canvas dev instances legitimately run without TLS."""
    for url in ("http://localhost:3000", "http://127.0.0.1:8900"):
        assert Settings(canvas_url=url).canvas_url == url


def test_canvas_url_rejects_hostnames_that_merely_start_with_localhost():
    """The loopback exemption must match the parsed host, not a string prefix."""
    with pytest.raises(ValidationError) as exc:
        Settings(canvas_url="http://localhost.attacker.example")
    assert "https" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Reading a saved file: discard as little as possible, and say what was dropped
# ---------------------------------------------------------------------------

def test_missing_file_reads_as_defaults(tmp_path):
    assert SettingsStore(tmp_path / "settings.json").current() == Settings()


def test_invalid_settings_file_is_logged_before_falling_back(tmp_path, caplog):
    """A silent reset to defaults looks identical to 'settings were never saved'."""
    path = tmp_path / "settings.json"
    path.write_text("{not valid json")

    with caplog.at_level("WARNING"):
        result = SettingsStore(path).current()

    assert result == Settings()
    assert any("settings" in r.message.lower() for r in caplog.records)


def test_one_invalid_setting_does_not_discard_the_others(tmp_path, caplog):
    """Resetting every setting because one field is bad is silent data loss.

    A teacher upgrading into stricter Canvas URL validation should lose the URL, not
    their thresholds and data directory too.
    """
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "canvas_url": "http://canvas.example.edu",   # newly invalid: remote + plaintext
        "high_confidence_threshold": 0.88,
        "review_first_threshold": 0.42,
        "model_text": "grader-text:8b",
    }))

    with caplog.at_level("WARNING"):
        result = SettingsStore(path).current()

    assert result.canvas_url == ""                 # dropped back to default
    assert result.high_confidence_threshold == 0.88   # preserved
    assert result.review_first_threshold == 0.42   # preserved
    assert any("canvas_url" in r.message for r in caplog.records)


def test_a_saved_file_using_the_old_key_keeps_its_value(tmp_path):
    """An install saved before the rename holds `auto_approve_threshold` on disk.

    Ignoring an unknown key would silently reset the teacher's lane boundary.
    """
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"auto_approve_threshold": 0.88}))

    assert SettingsStore(path).current().high_confidence_threshold == 0.88


def test_the_next_write_migrates_the_old_key(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"auto_approve_threshold": 0.88}))
    store = SettingsStore(path)

    store.update(SettingsPatch(data_dir=str(tmp_path / "d")))
    saved = json.loads(path.read_text())

    assert saved["high_confidence_threshold"] == 0.88
    assert "auto_approve_threshold" not in saved


def test_unattributable_validation_error_falls_back_to_defaults(tmp_path, caplog):
    """Cross-field errors name no single field, so the whole file is untrustworthy."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "high_confidence_threshold": 0.60,
        "review_first_threshold": 0.90,  # must be lower than high-confidence
    }))

    with caplog.at_level("WARNING"):
        result = SettingsStore(path).current()

    assert result == Settings()
    assert caplog.records


# ---------------------------------------------------------------------------
# Writing: all or nothing, and never a stale merge
# ---------------------------------------------------------------------------

def test_update_merges_onto_what_is_saved(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.update(SettingsPatch(
        canvas_url="https://canvas.example.edu",
        canvas_token="secret-token",
        data_dir=str(tmp_path / "attachments"),
    ))

    after = store.update(SettingsPatch(review_first_threshold=0.5))

    assert after.review_first_threshold == 0.5
    assert after.canvas_url == "https://canvas.example.edu"
    assert after.canvas_token == "secret-token"


def test_blank_token_means_leave_it_alone(tmp_path):
    """The settings form sends an empty field when the teacher does not retype it."""
    store = SettingsStore(tmp_path / "settings.json")
    store.update(SettingsPatch(canvas_token="secret-token",
                               data_dir=str(tmp_path / "d")))

    after = store.update(SettingsPatch(canvas_token="", canvas_url="https://c.example"))

    assert after.canvas_token == "secret-token"


def test_a_rejected_patch_leaves_the_file_untouched(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.update(SettingsPatch(high_confidence_threshold=0.9,
                               review_first_threshold=0.4,
                               data_dir=str(tmp_path / "d")))
    before = path.read_text()

    with pytest.raises(ValidationError):
        store.update(SettingsPatch(review_first_threshold=0.95))

    assert path.read_text() == before
    assert store.current().review_first_threshold == 0.4


def test_update_rejects_an_unusable_attachment_directory(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("I am a file")
    store = SettingsStore(tmp_path / "settings.json")

    with pytest.raises(ValueError, match="directory"):
        store.update(SettingsPatch(data_dir=str(blocker)))


def test_saved_file_is_owner_only(tmp_path):
    """It holds a Canvas token; another account on the machine must not read it."""
    path = tmp_path / "settings.json"
    SettingsStore(path).update(SettingsPatch(canvas_token="secret",
                                             data_dir=str(tmp_path / "d")))

    assert path.stat().st_mode & 0o777 == 0o600


# ---------------------------------------------------------------------------
# The token must not leak
# ---------------------------------------------------------------------------

def test_masked_never_carries_the_raw_token():
    settings = Settings(canvas_token="1234567890abcdef")
    masked = settings.masked()

    assert "canvas_token" not in masked
    assert masked["canvas_token_masked"] == "1234...cdef"
    assert "1234567890abcdef" not in json.dumps(masked)


def test_short_token_is_masked_entirely():
    assert Settings(canvas_token="abc").masked()["canvas_token_masked"] == "••••••••"


def test_no_token_means_no_masked_field():
    assert "canvas_token_masked" not in Settings().masked()


def test_repr_does_not_leak_the_token():
    """An interpolated Settings must not put a teacher's token in a log line."""
    settings = Settings(canvas_token="1234567890abcdef",
                        canvas_url="https://canvas.example.edu")

    assert "1234567890abcdef" not in repr(settings)
    assert "1234567890abcdef" not in repr(settings.canvas)
    assert "canvas.example.edu" in repr(settings.canvas)


# ---------------------------------------------------------------------------
# Derived views
# ---------------------------------------------------------------------------

def test_canvas_connection_reports_unset_credentials_as_none():
    """`grader.canvas` distinguishes "not configured" from "configured as empty"."""
    assert Settings().canvas.as_kwargs() == {"base_url": None, "token": None}


def test_settings_are_frozen():
    """A handler holding a resolved value cannot be retargeted by a concurrent write."""
    settings = Settings()
    with pytest.raises(ValidationError):
        settings.model_text = "grader-text:other"
