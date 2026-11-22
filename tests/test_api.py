"""End-to-end API behaviour of the local stdlib server.

All requests hit the fictional sample shipped with the app.
"""

from __future__ import annotations

from tests.conftest import get_json, post_json


def test_health_marks_data_as_fictional(api_base_url):
    status, body = get_json(api_base_url + "/api/health")
    assert status == 200
    assert body["ok"] is True
    assert body["fictional"] is True


def test_model_payload_exposes_diagnostics_and_validation_notes(api_base_url):
    status, body = get_json(api_base_url + "/api/model")
    assert status == 200
    assert body["n_records"] == 1200
    assert body["converged"] is True
    assert body["blocked"] is False
    assert any("整条记录" in note for note in body["notes"])
    assert any("零次成交" in note for note in body["notes"])
    codes = {d["code"] for d in body["diagnostics"]}
    assert "collinear" in codes  # the fictional ladder moves attributes together
    wtp = {row["key"]: row["wtp_per_unit"] for row in body["coefficients"]}
    assert wtp["price"] is None


def test_menu_metrics_include_none(api_base_url):
    status, body = get_json(api_base_url + "/api/menu")
    assert status == 200
    metrics = body["metrics"]
    total = sum(metrics["shares"].values()) + metrics["none"]
    assert abs(total - 1.0) < 1e-12


def test_substitution_get_returns_transfer_table(api_base_url):
    status, body = get_json(api_base_url + "/api/substitution?target=gamer")
    assert status == 200
    removal = body["removal"]
    received = sum(r["gain"] for r in removal["receivers"]) + removal["to_none"]
    assert abs(received - removal["released"]) < 1e-12
    addition = body["addition"]
    taken = sum(s["loss"] for s in addition["sources"]) + addition["from_none"]
    assert abs(taken - addition["gained"]) < 1e-12


def test_substitution_get_rejects_unknown_target(api_base_url):
    status, body = get_json(api_base_url + "/api/substitution?target=nope")
    assert status == 400


def test_substitution_get_supports_retired_and_never_shown_plans(api_base_url):
    status, body = get_json(api_base_url + "/api/substitution?target=senior")
    assert status == 200
    assert body["in_sample"] is True       # retired, but historically shown
    status, body = get_json(api_base_url + "/api/substitution?target=bundle")
    assert status == 200
    assert body["in_sample"] is False      # never shown in the sample
    assert body["addition"]["gained"] > 0
    assert all(row["share"] > 0 for row in body["price_sensitivity"])


def test_substitution_is_blocked_for_unusable_model(api_base_url):
    status, body = get_json(api_base_url + "/api/substitution?sample=price_wrong&target=basic")
    assert status == 422
    assert body["diagnostics"]


def test_compare_returns_balanced_displacement(api_base_url):
    _, menu = get_json(api_base_url + "/api/menu")
    baseline = [{"package_id": p["package_id"]} for p in menu["plans"]]
    scenario = [item for item in baseline
                if item["package_id"] != "gamer"]
    status, body = post_json(api_base_url + "/api/compare",
                             {"baseline": baseline, "scenario": scenario})
    assert status == 200
    assert body["displacement"]["balanced"] is True
    assert body["scenario"]["plans"]


def test_compare_requires_both_menus(api_base_url):
    status, body = post_json(api_base_url + "/api/compare",
                             {"baseline": [{"package_id": "basic"}]})
    assert status == 400


def test_search_rejects_more_than_ten_candidates(api_base_url):
    _, catalog = get_json(api_base_url + "/api/catalog")
    candidates = [{"package_id": p["id"]} for p in catalog["plans"]]
    candidates.append(candidates[0])  # 11 entries
    assert len(candidates) == 11
    status, body = post_json(api_base_url + "/api/search",
                             {"candidates": candidates, "max_size": 2})
    assert status == 400
    assert "10" in body["message"]


def test_search_respects_must_include(api_base_url):
    status, body = post_json(api_base_url + "/api/search", {
        "candidates": [{"package_id": i} for i in
                       ("basic", "lite", "student", "value", "video")],
        "max_size": 3,
        "must_include": ["lite"],
    })
    assert status == 200
    assert body["best"] is not None
    assert "lite" in body["best"]["ids"]
    for row in body["results"]:
        assert "lite" in row["ids"]
    assert body["best"]["displacement"]["balanced"] is True


def test_blocked_sample_refuses_compare_and_search(api_base_url):
    status, body = post_json(api_base_url + "/api/compare", {
        "sample": "price_wrong",
        "baseline": [{"package_id": "basic"}, {"package_id": "value"}],
        "scenario": [{"package_id": "basic"}],
    })
    assert status == 422
    assert any(d["code"] == "price_direction" for d in body["diagnostics"])

    status, body = post_json(api_base_url + "/api/search", {
        "sample": "price_wrong",
        "candidates": [{"package_id": "basic"}, {"package_id": "value"}],
    })
    assert status == 422
    assert body["diagnostics"]


def test_records_rejects_choice_not_shown(api_base_url):
    payload = {
        "sample": "main",
        "submission_id": "api-test-invalid",
        "choice": "premium",
        "day": "2026-11-01",
        "options": [
            {"package_id": "basic", "price": 19, "data_gb": 15,
             "voice_min": 200, "streaming": 0, "family": 0, "device": 0},
        ],
    }
    status, body = post_json(api_base_url + "/api/records", payload)
    assert status == 400
    assert "不在当时展示的选择集中" in body["message"]
