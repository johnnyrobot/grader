from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from gates.bias import perturb


def test_verbose_perturbation_keeps_short_answers_bounded():
    text = "Question: How does the compiler handle inline functions?\n\nStudent answer: The compiler can ignore the inline qualifier and typically does so for all but the smallest functions."

    verbose = perturb(text, "verbose")

    assert len(verbose) < len(text) * 2
    assert "the aforementioned" not in verbose
    assert "can, in many cases," in verbose
