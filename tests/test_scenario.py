"""Menu scenarios, substitution and the profit search.

The central rule: every share and every profit number comes from one
multinomial over the *whole* menu, including "none".  Per-plan forecasts are
never added together.
"""

from __future__ import annotations

import itertools

import pytest

from app.catalog import PLANS
from app.model import option_for
from app.scenario import (ScenarioPlan, compare, default_scenario, displacement,
                          evaluate, inbound_sources, price_sensitivity,
                          search_portfolio, substitution)


@pytest.fixture(scope="module")
def estimator(main_analysis):
    return main_analysis.estimator


@pytest.fixture(scope="module")
def current_menu(estimator):
    return default_scenario(estimator)


def test_shares_include_none_and_sum_to_one(estimator, current_menu):
    metrics = evaluate(estimator, current_menu)
    total = sum(metrics["shares"].values()) + metrics["none"]
    assert total == pytest.approx(1.0, abs=1e-12)
    assert 0 < metrics["none"] < 1


def test_empty_menu_is_all_none(estimator):
    metrics = evaluate(estimator, [])
    assert metrics["none"] == 1.0
    assert metrics["shares"] == {}
    assert metrics["expected_profit"] == 0.0


def test_profit_uses_whole_menu_not_summed_single_plan_forecasts(estimator):
    menu = [p for p in default_scenario(estimator) if p.package_id in
            ("basic", "value", "premium")]
    metrics = evaluate(estimator, menu)
    # naive alternative: treat each plan as its own two-way menu, then add
    naive = 0.0
    for plan in menu:
        solo = evaluate(estimator, [plan])
        naive += solo["shares"][plan.package_id] * plan.margin
    assert metrics["expected_profit"] != pytest.approx(naive, rel=1e-3)
    expected = sum(metrics["shares"][p.package_id] * p.margin for p in menu)
    assert metrics["expected_profit"] == pytest.approx(expected, abs=1e-12)


def test_compare_rows_balance_with_none(estimator, current_menu):
    scenario = [p for p in current_menu if p.package_id != "video"]
    result = compare(current_menu, scenario, estimator)
    base_total = sum(row["share_before"] for row in result["rows"]) + \
        result["baseline"]["none"]
    assert base_total == pytest.approx(1.0, abs=1e-12)
    deltas = sum(row["delta"] for row in result["rows"])
    assert deltas + result["none_delta"] == pytest.approx(0.0, abs=1e-12)


def test_substitution_removal_conserves_share(estimator, current_menu):
    removal = substitution(estimator, current_menu, "gamer", "remove")
    gained = sum(r["gain"] for r in removal["receivers"]) + removal["to_none"]
    assert gained == pytest.approx(removal["released"], abs=1e-12)
    assert removal["released"] > 0


def test_inbound_sources_conserve_share(estimator, current_menu):
    ids = {p.package_id for p in current_menu}
    assert "senior" not in ids
    menu = current_menu + [ScenarioPlan.from_option(option_for("senior"))]
    added = inbound_sources(estimator, menu, "senior")
    taken = sum(s["loss"] for s in added["sources"]) + added["from_none"]
    assert taken == pytest.approx(added["gained"], abs=1e-12)


def test_displacement_balances_exactly(estimator, current_menu):
    scenario = [ScenarioPlan.from_option(option_for(i)) for i in
                ("basic", "value", "family", "senior")]
    disp = displacement(estimator, current_menu, scenario)
    assert disp["balanced"] is True
    assert disp["total_displaced"] == pytest.approx(disp["total_gained"], abs=1e-12)
    assert abs(disp["total_displaced"]) > 0


def test_search_evaluates_whole_multinomials(estimator):
    candidates = [ScenarioPlan.from_option(option_for(i)) for i in
                  ("basic", "lite", "student", "value", "video")]
    result = search_portfolio(estimator, candidates, max_size=3)
    for row in result["results"]:
        shares = row["shares"]
        total = sum(shares.values()) + row["none"]
        assert total == pytest.approx(1.0, abs=1e-12)
        expected_profit = sum(shares[i] * next(p for p in candidates
                                               if p.package_id == i).margin
                              for i in row["ids"])
        assert row["expected_profit"] == pytest.approx(expected_profit, abs=1e-12)
    profits = [r["expected_profit"] for r in result["results"]]
    assert profits == sorted(profits, reverse=True)


def test_search_respects_must_include_entry(estimator):
    candidates = [ScenarioPlan.from_option(option_for(i)) for i in
                  ("basic", "lite", "student", "value", "video", "gamer")]
    result = search_portfolio(estimator, candidates, max_size=4,
                              must_include=["lite"])
    assert result["must_include"] == ["lite"]
    for row in result["results"] + ([result["best"]] if result["best"] else []):
        assert "lite" in row["ids"]
    for row in result["results"]:
        assert row["ids"].count("lite") == 1


def test_search_rejects_unknown_must_include(estimator):
    candidates = [ScenarioPlan.from_option(option_for(i)) for i in
                  ("basic", "value")]
    with pytest.raises(ValueError):
        search_portfolio(estimator, candidates, must_include=["premium"])


def test_search_caps_candidates_at_ten(estimator):
    ids = [p.id for p in PLANS][:12]
    candidates = [ScenarioPlan.from_option(option_for(i)) for i in ids]
    result = search_portfolio(estimator, candidates, max_size=2, max_candidates=10)
    assert len(result["candidates"]) == 10
    # exhaustive over combinations of the capped candidate list
    expected = sum(len(list(itertools.combinations(range(10), k)))
                   for k in (1, 2))
    assert result["searched"] == expected


def test_price_sensitivity_reevaluates_whole_menu(estimator, current_menu):
    rows = price_sensitivity(estimator, current_menu, "gamer")
    assert [row["change"] for row in rows] == [-0.1, -0.05, 0.0, 0.05, 0.1]
    shares = [row["share"] for row in rows]
    # raising the price must not raise its own share
    assert shares == sorted(shares, reverse=True)
    zero = next(row for row in rows if row["change"] == 0.0)
    base = evaluate(estimator, current_menu)
    assert zero["share"] == pytest.approx(base["shares"]["gamer"], abs=1e-12)
    assert zero["expected_profit"] == pytest.approx(base["expected_profit"], abs=1e-12)
    # each row is a full multinomial, not a linear extrapolation of one share
    for row in rows:
        assert 0 < row["none"] < 1
