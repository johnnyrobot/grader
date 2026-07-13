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
