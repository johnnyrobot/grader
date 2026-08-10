"""The perf gate's retry arithmetic.

The measurement itself needs a live model; the reporting does not, and the
reporting is where a happy-path number quietly stands in for a worst case.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from gates.perf import _retry_worst_case


def test_the_worst_case_is_a_capped_attempt_plus_a_whole_second_one():
    """A runaway does not fail fast — it generates to the token cap, and only
    then is the second attempt started. The item pays for both."""
    details = _retry_worst_case(single_seconds=30.0, cap_seconds=55.0, max_seconds=90)

    assert details["runaway_first_attempt_seconds"] == "55.0"
    assert details["retry_worst_case_seconds"] == "85.0"


def test_a_worst_case_inside_the_budget_says_so():
    details = _retry_worst_case(single_seconds=30.0, cap_seconds=55.0, max_seconds=90)

    assert details["retry_worst_case_budget"] == "within ≤90s"


def test_a_worst_case_over_the_budget_names_the_overage():
    """The 3% of items that retry are the ones the budget has never covered."""
    details = _retry_worst_case(single_seconds=77.0, cap_seconds=55.0, max_seconds=90)

    assert "over" in details["retry_worst_case_budget"].lower()
    assert "42.0s" in details["retry_worst_case_budget"]


def test_an_unmeasured_cap_reports_nothing_rather_than_guessing():
    """No throughput measurement means no worst case. Inventing one from the
    happy-path time would understate it — a runaway attempt runs to the cap,
    which is longer than a grade that stopped on its own."""
    details = _retry_worst_case(single_seconds=30.0, cap_seconds=None, max_seconds=90)

    assert details["retry_worst_case_seconds"] == "not measured"
    assert "retry_worst_case_budget" not in details
