from grader.citations import _crossref_record_has_retraction
from verify_citations import decide_citation_mode


def test_citation_mode_enabled_when_all_thresholds_pass():
    summary = {
        "accuracy": 0.91,
        "false_positives": 0,
        "retraction_check": {"total": 3, "caught": 3},
    }
    mode = decide_citation_mode(summary)
    assert mode["mode"] == "enabled"
    assert mode["release_status"] == "PASS"
    assert mode["experimental_required"] is False


def test_citation_mode_experimental_when_retractions_fail():
    summary = {
        "accuracy": 0.95,
        "false_positives": 0,
        "retraction_check": {"total": 3, "caught": 2},
    }
    mode = decide_citation_mode(summary)
    assert mode["mode"] == "experimental"
    assert mode["release_status"] == "FAIL"
    assert "retraction" in " ".join(mode["failures"])


def test_crossref_retraction_detects_list_shaped_updated_by():
    message = {
        "updated-by": [
            {
                "DOI": "10.18632/oncotarget.28797",
                "type": "retraction",
                "label": "Retraction",
                "source": "retraction-watch",
            }
        ]
    }

    assert _crossref_record_has_retraction(message) is True


def test_crossref_retraction_detects_list_shaped_update_to():
    message = {
        "update-to": [
            {
                "DOI": "10.1145/3395352.3402901",
                "type": "retraction",
                "label": "Retraction",
                "source": "retraction-watch",
            }
        ]
    }

    assert _crossref_record_has_retraction(message) is True


def test_crossref_retraction_ignores_non_retraction_updates():
    message = {
        "updated-by": [
            {
                "DOI": "10.1000/example-correction",
                "type": "correction",
                "label": "Correction",
            }
        ]
    }

    assert _crossref_record_has_retraction(message) is False
