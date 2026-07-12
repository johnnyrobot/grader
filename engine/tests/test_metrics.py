from grader.metrics import Agreement, agreement, human_ceiling, quadratic_weighted_kappa


def test_qwk_perfect_agreement():
    assert quadratic_weighted_kappa([1, 2, 3], [1, 2, 3]) == 1.0


def test_qwk_identical_scores():
    a = [3, 3, 3, 3]
    b = [3, 3, 3, 3]
    assert quadratic_weighted_kappa(a, b) == 1.0


def test_qwk_chance_level():
    a = [0, 1, 2, 3, 4]
    b = [4, 3, 2, 1, 0]
    qwk = quadratic_weighted_kappa(a, b)
    assert qwk < 0.0


def test_qwk_partial_agreement():
    a = [0, 1, 2, 3, 4]
    b = [0, 1, 2, 3, 3]
    qwk = quadratic_weighted_kappa(a, b)
    assert 0.5 < qwk < 1.0


def test_agreement_basic():
    ai = [3.0, 4.0, 2.0, 5.0]
    human = [3.0, 4.0, 2.0, 5.0]
    agr = agreement(ai, human)
    assert agr.qwk == 1.0
    assert agr.exact == 1.0
    assert agr.mean_error == 0.0


def test_agreement_signed_bias():
    ai = [4.0, 4.0, 4.0]
    human = [3.0, 3.0, 3.0]
    agr = agreement(ai, human)
    assert agr.mean_error > 0.0
    assert "lenient" in str(agr)


def test_agreement_negative_bias():
    ai = [2.0, 2.0]
    human = [4.0, 4.0]
    agr = agreement(ai, human)
    assert agr.mean_error < 0.0
    assert "harsh" in str(agr)


def test_agreement_adjacent():
    ai = [3.0, 4.0, 2.0]
    human = [3.0, 3.0, 2.0]
    agr = agreement(ai, human)
    assert agr.adjacent == 1.0
    assert agr.exact < 1.0


def test_human_ceiling():
    rater_a = [1, 2, 3, 4, 5]
    rater_b = [1, 2, 3, 4, 5]
    ceiling = human_ceiling(rater_a, rater_b)
    assert ceiling.qwk == 1.0


def test_human_ceiling_imperfect():
    rater_a = [1, 2, 3, 4, 5]
    rater_b = [1, 2, 3, 5, 4]
    ceiling = human_ceiling(rater_a, rater_b)
    assert 0.7 < ceiling.qwk < 1.0


def test_agreement_string():
    agr = Agreement(qwk=0.75, exact=0.6, adjacent=0.9,
                   mean_error=0.2, mean_abs_error=0.3, n=10)
    s = str(agr)
    assert "QWK" in s
    assert "lenient" in s
    assert "n=10" in s