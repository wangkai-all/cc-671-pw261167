"""Model fitting and diagnostic gates.

The fitter must fit one whole record per choice occasion, and the diagnostic
layer must block operational scenarios when the parameters are not trustworthy.
"""

from __future__ import annotations

import pytest

from app.model import fit_model
from tests.conftest import simple_choice_records


def codes(est):
    return {d["code"] for d in est.diagnostics}


def test_fit_uses_one_likelihood_term_per_record():
    records = simple_choice_records()
    est = fit_model(records)
    assert est.converged is True
    assert est.blocked is False
    assert est.n_records == len(records)
    # one plan row per shown option plus the "none" row per record
    assert est.n_alternatives == sum(len(r.options) + 1 for r in records)


def test_price_coefficient_is_negative_and_significant():
    est = fit_model(simple_choice_records())
    assert est.beta["price"] < 0
    assert est.se["price"] is not None
    # the fixture is generated with a price effect, so it must be detectable
    assert est.beta["price"] / est.se["price"] < -1.645


def test_wtp_only_defined_for_negative_price():
    est = fit_model(simple_choice_records())
    assert est.wtp("data_gb") == pytest.approx(-est.beta["data_gb"] / est.beta["price"])
    est.beta["price"] = 0.01
    assert est.wtp("data_gb") is None


def test_duplicate_records_are_removed_before_fitting(tmp_path):
    import json

    from app.history import load_dataset

    records = simple_choice_records()
    payload = {"records": [r.to_dict() for r in records] +
                          [r.to_dict() for r in records]}
    path = tmp_path / "dup.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    dataset = load_dataset(str(path))
    assert dataset.raw_count == 2 * len(records)
    assert dataset.duplicates_ignored == len(records)
    baseline = fit_model(records)
    deduped = fit_model(dataset.records)
    assert deduped.n_records == baseline.n_records
    assert deduped.loglik == pytest.approx(baseline.loglik, rel=1e-9)


def test_identifiability_collinearity_is_reported():
    est = fit_model(simple_choice_records())
    pairs = [tuple(d["pair"]) for d in est.diagnostics if d["code"] == "collinear"]
    # the fictional ladder moves price, data and voice together, so at least
    # one pair must be flagged and its WTP is not separately interpretable
    assert pairs
    for d in est.diagnostics:
        if d["code"] == "collinear":
            assert "无法分开估计" in d["message"]
            assert d["severity"] == "warning"


def test_price_wrong_sample_is_blocked_with_diagnostic():
    from app.analysis import load_analysis

    analysis = load_analysis("price_wrong")
    est = analysis.estimator
    assert est.blocked is True
    assert "price_direction" in codes(est)
    assert est.diagnostics  # diagnostics kept, not swallowed


def test_separation_sample_is_blocked_with_diagnostic():
    from app.analysis import load_analysis

    analysis = load_analysis("separation")
    est = analysis.estimator
    assert est.blocked is True
    assert "separation" in codes(est)


def test_collinear_sample_is_blocked_or_warned():
    from app.analysis import load_analysis

    analysis = load_analysis("collinear")
    est = analysis.estimator
    assert est.blocked is True
    assert "collinear" in codes(est) or "information_singular" in codes(est)


def test_negative_price_but_insignificant_is_not_usable():
    """A tiny, noisy sample should trip the significance gate, not give advice."""
    est = fit_model(simple_choice_records(n=40, seed=123))
    price_ok = est.beta["price"] < 0 and est.se["price"] and \
        est.beta["price"] / est.se["price"] < -1.645
    if not price_ok:
        assert est.blocked is True
        assert "price_not_significant" in codes(est) or "price_direction" in codes(est)
