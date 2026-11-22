"""Analysis service: caching, sample loading, result assembly for the API."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import date

from . import stats
from .catalog import PLANS, PLAN_BY_ID
from .history import Dataset, load_dataset
from .model import Estimator, fit_model, option_for
from .scenario import (ScenarioPlan, compare, default_scenario, displacement,
                       evaluate, inbound_sources, price_sensitivity,
                       search_portfolio, substitution)
from .synth import SAMPLE_BY_KEY, SAMPLES, generate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
DEFAULT_SAMPLE = "main"

FEATURE_META = [
    {
        "key": f.key,
        "label": f.label,
        "unit": f.unit,
        "scale": f.scale,
        "description": f.description,
        "group": f.group,
    }
    for f in stats.FEATURES
]


class SampleError(ValueError):
    pass


def sample_path(key: str) -> str:
    if key == "main":
        return os.path.join(DATA_DIR, "choices.json")
    if key not in SAMPLE_BY_KEY:
        raise SampleError(f"未知样本 {key}")
    return os.path.join(DATA_DIR, "samples", f"{key}.json")


def sample_catalog() -> list[dict]:
    items = [{
        "key": "main",
        "name": "主样本（1,200 条）",
        "description": SAMPLES[0].description,
        "path": sample_path("main"),
    }]
    for scenario in SAMPLES:
        items.append({
            "key": scenario.key,
            "name": scenario.name,
            "description": scenario.description,
            "path": sample_path(scenario.key),
        })
    # de-duplicate the "main" entry that SAMPLES also contains
    seen, out = set(), []
    for item in items:
        if item["key"] in seen:
            continue
        seen.add(item["key"])
        out.append(item)
    return out


def ensure_default_sample() -> None:
    for key in ["main", *SAMPLE_BY_KEY]:
        path = sample_path(key)
        if not os.path.exists(path):
            from .synth import _write_json
            _write_json(path, generate(SAMPLE_BY_KEY[key]))


def ensure_sample(key: str) -> None:
    path = sample_path(key)
    if os.path.exists(path):
        return
    from .synth import _write_json
    _write_json(path, generate(SAMPLE_BY_KEY[key]))


class AnalysisCache:
    """In-process cache keyed by dataset content hash."""

    def __init__(self):
        self._lock = threading.Lock()
        self._store: dict[str, dict] = {}

    def key_for(self, dataset: Dataset) -> str:
        payload = json.dumps(
            [[r.submission_id, r.submitted_at, r.choice,
              [o.to_dict() for o in r.options]] for r in dataset.records],
            sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, dataset: Dataset) -> dict:
        key = self.key_for(dataset)
        with self._lock:
            if key in self._store:
                return self._store[key]
        estimator = fit_model(dataset.records)
        entry = {"key": key, "estimator": estimator}
        with self._lock:
            if len(self._store) > 24:
                self._store.clear()
            self._store[key] = entry
        return entry


CACHE = AnalysisCache()


@dataclass
class Analysis:
    key: str
    sample: str
    dataset: Dataset
    estimator: Estimator


def load_analysis(sample: str = DEFAULT_SAMPLE) -> Analysis:
    path = sample_path(sample)
    if not os.path.exists(path):
        ensure_sample(sample)
    dataset = load_dataset(path)
    entry = CACHE.get(dataset)
    return Analysis(key=entry["key"], sample=sample, dataset=dataset,
                    estimator=entry["estimator"])


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def coefficients_payload(estimator: Estimator) -> list[dict]:
    rows = []
    for feature in stats.FEATURES:
        key = feature.key
        beta = estimator.beta[key]
        se = estimator.se[key]
        z = estimator.z[key]
        p = estimator.p_value[key]
        wtp = None
        if feature.key != "price":
            ratio = estimator.wtp(key)
            if ratio is not None:
                wtp = ratio * feature.scale
        rows.append({
            "key": key,
            "label": feature.label,
            "unit": feature.unit,
            "scale": feature.scale,
            "beta": beta,
            "beta_scaled": beta * feature.scale,
            "se": se,
            "z": z,
            "p_value": p,
            "significant": (p is not None and p < 0.05),
            "wtp_per_unit": wtp,
            "wtp_unit": feature.unit,
            "description": feature.description,
        })
    rows.append({
        "key": "none",
        "label": "都不选",
        "unit": "常数项",
        "scale": 1.0,
        "beta": estimator.none_constant,
        "beta_scaled": estimator.none_constant,
        "se": estimator.se.get("none"),
        "z": estimator.z.get("none"),
        "p_value": estimator.p_value.get("none"),
        "significant": (estimator.p_value.get("none") is not None
                        and estimator.p_value["none"] < 0.05),
        "wtp_per_unit": None,
        "wtp_unit": "",
        "description": "所有套餐都不选时的基准效用（未选择按 0 计入）",
    })
    return rows


def correlation_payload(dataset: Dataset) -> dict:
    keys = [f.key for f in stats.FEATURES]
    rows = [[getattr(o, key) / f.scale for key, f in zip(keys, stats.FEATURES)]
            for r in dataset.records for o in r.options]
    if not rows:
        return {"keys": keys, "matrix": []}
    matrix = stats.correlation_matrix([[row[k] for row in rows] for k in range(len(keys))])
    return {"keys": keys, "matrix": matrix}


def model_payload(analysis: Analysis) -> dict:
    est = analysis.estimator
    stats_info = analysis.dataset.stats()
    corr = correlation_payload(analysis.dataset)
    return {
        "sample": analysis.sample,
        "data_hash": analysis.key,
        "fictional": True,
        "n_records": est.n_records,
        "n_alternatives": est.n_alternatives,
        "loglik": est.loglik,
        "loglik_null": est.loglik_null,
        "pseudo_r2": (1 - est.loglik / est.loglik_null) if est.loglik_null else None,
        "iterations": est.iterations,
        "converged": est.converged,
        "gradient_norm": est.gradient_norm,
        "hessian_condition": est.hessian_condition,
        "hessian_positive": est.hessian_positive,
        "diagnostics": est.diagnostics,
        "blocked": est.blocked,
        "coefficients": coefficients_payload(est),
        "features": FEATURE_META,
        "correlation": corr,
        "dataset": stats_info,
        "plans": analysis.dataset.plan_table(),
        "examples": analysis.dataset.examples(),
        "notes": [
            "每次选择按整条记录参与拟合；同一 submission_id 的重复提交只计一次。",
            "未在当天菜单出现的套餐不进入该记录的选择集，也不会被当成零次成交。",
            "拟合结果来自本地虚构样本，不连接真实交易。",
        ],
    }


def _scenario_plan_from_request(estimator: Estimator, item: dict) -> ScenarioPlan:
    plan_id = item.get("package_id")
    if plan_id not in PLAN_BY_ID:
        raise SampleError(f"未知套餐 {plan_id}")
    option = option_for(plan_id, item.get("price"),
                        {k: item[k] for k in ("data_gb", "voice_min", "streaming",
                                              "family", "device") if k in item})
    cost = item.get("unit_cost")
    return ScenarioPlan.from_option(option, cost if cost is not None else None)


def scenario_from_request(estimator: Estimator, items: list[dict]) -> list[ScenarioPlan]:
    seen = set()
    plans = []
    for item in items:
        plan = _scenario_plan_from_request(estimator, item)
        if plan.package_id in seen:
            raise SampleError(f"菜单中套餐重复：{plan.package_id}")
        seen.add(plan.package_id)
        plans.append(plan)
    return plans


def current_scenario(estimator: Estimator) -> list[ScenarioPlan]:
    return default_scenario(estimator)


def scenario_payload(estimator: Estimator, plans: list[ScenarioPlan]) -> list[dict]:
    return [p.to_dict(estimator) for p in plans]


def menu_state(analysis: Analysis) -> dict:
    est = analysis.estimator
    plans = current_scenario(est)
    metrics = evaluate(est, plans)
    return {
        "plans": scenario_payload(est, plans),
        "metrics": metrics,
        "catalog": [
            {
                **p.to_dict(),
                "snapshot": p.to_dict(),
                "in_current_menu": p.status == "active",
            }
            for p in PLANS
        ],
    }


def compare_payload(analysis: Analysis, baseline_items: list[dict],
                    scenario_items: list[dict]) -> dict:
    est = analysis.estimator
    baseline = scenario_from_request(est, baseline_items)
    scenario = scenario_from_request(est, scenario_items)
    result = compare(baseline, scenario, est)
    disp = displacement(est, baseline, scenario)
    return {
        "blocked": est.blocked,
        "diagnostics": est.diagnostics,
        "baseline": result["baseline"],
        "scenario": result["scenario"],
        "rows": result["rows"],
        "none_delta": result["none_delta"],
        "profit_delta": result["profit_delta"],
        "revenue_delta": result["revenue_delta"],
        "displacement": disp,
        "baseline_plans": scenario_payload(est, baseline),
        "scenario_plans": scenario_payload(est, scenario),
    }


def substitution_payload(analysis: Analysis, baseline_items: list[dict], target: str) -> dict:
    est = analysis.estimator
    baseline = scenario_from_request(est, baseline_items) if baseline_items else current_scenario(est)
    if target not in {p.package_id for p in baseline}:
        raise SampleError("目标套餐不在基准菜单中")
    removed = substitution(est, baseline, target, "remove")
    added = inbound_sources(est, baseline, target)
    plan = next(p for p in baseline if p.package_id == target)
    shown = next((row for row in analysis.dataset.plan_table()
                  if row["package_id"] == target), None)
    current = current_scenario(est)
    if target in {p.package_id for p in current}:
        sensitivity = price_sensitivity(est, current, target)
    else:
        sensitivity = price_sensitivity(est, baseline, target)
    return {
        "blocked": est.blocked,
        "diagnostics": est.diagnostics,
        "target": plan.to_dict(est),
        "in_sample": bool(shown and shown["ever_shown"]),
        "removal": removed,
        "addition": added,
        "price_sensitivity": sensitivity,
    }


def search_payload(analysis: Analysis, request: dict) -> dict:
    est = analysis.estimator
    candidate_items = request.get("candidates") or []
    if not candidate_items:
        ids = [p.id for p in PLANS if p.status == "active"][:10]
        candidate_items = [{"package_id": i} for i in ids]
    try:
        candidates = scenario_from_request(est, candidate_items)
    except SampleError as exc:
        raise SampleError(str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise SampleError(f"候选格式错误：{exc}") from exc
    if len(candidates) > 10:
        raise SampleError("候选套餐最多 10 个")
    max_size = int(request.get("max_size") or min(6, len(candidates)))
    min_size = int(request.get("min_size") or 1)
    must_include = request.get("must_include") or []
    if isinstance(must_include, str):
        must_include = [must_include]
    try:
        result = search_portfolio(est, candidates, max_size=max_size, min_size=min_size,
                                  must_include=must_include,
                                  max_candidates=10, top_k=int(request.get("top_k") or 8))
    except ValueError as exc:
        raise SampleError(str(exc)) from exc
    baseline_items = request.get("baseline")
    if baseline_items:
        baseline = scenario_from_request(est, baseline_items)
    else:
        baseline = current_scenario(est)
    fixed = {
        "plans": scenario_payload(est, baseline),
        "metrics": evaluate(est, baseline),
    }
    best = result.get("best")
    if best:
        best_plans = scenario_from_request(est, [{"package_id": i} for i in best["ids"]])
        best["plans_full"] = scenario_payload(est, best_plans)
        best["displacement"] = displacement(est, baseline, best_plans)
        best["compare"] = compare(baseline, best_plans, est)
    return {
        "blocked": est.blocked,
        "diagnostics": est.diagnostics,
        "fixed": fixed,
        **result,
    }


def entry_options() -> list[dict]:
    return [
        {"package_id": p.id, "name": p.name, "tier": p.tier, "price": p.price}
        for p in PLANS
    ]
