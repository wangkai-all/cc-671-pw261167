"""Menu scenarios, substitution and profit search on top of the fitted model.

All share numbers come from *one* multinomial over the complete scenario menu
(including "none").  Per-plan numbers are never added together to form a
total -- that would double count the outside option.

Substitution is measured with a share-shift (logit) experiment: the baseline
menu and the scenario menu share the same outside option, and the change in
each remaining plan's share is read from the two multinomials.  The shares the
removed plan held are distributed across the remaining plans and "none" in the
proportion the model implies; the table reports where they went.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Sequence

from .catalog import PLAN_BY_ID, PLANS
from .history import Option
from .model import Estimator, option_for


@dataclass
class ScenarioPlan:
    package_id: str
    price: float
    data_gb: float
    voice_min: float
    streaming: int
    family: int
    device: int
    unit_cost: float

    @classmethod
    def from_option(cls, option: Option, unit_cost: float | None = None) -> "ScenarioPlan":
        plan = PLAN_BY_ID.get(option.package_id)
        return cls(
            package_id=option.package_id,
            price=option.price,
            data_gb=option.data_gb,
            voice_min=option.voice_min,
            streaming=option.streaming,
            family=option.family,
            device=option.device,
            unit_cost=plan.unit_cost if unit_cost is None and plan else (unit_cost or 0.0),
        )

    @property
    def name(self) -> str:
        plan = PLAN_BY_ID.get(self.package_id)
        return plan.name if plan else self.package_id

    @property
    def margin(self) -> float:
        return self.price - self.unit_cost

    def option(self) -> Option:
        return Option(self.package_id, self.price, self.data_gb, self.voice_min,
                      self.streaming, self.family, self.device)

    def to_dict(self, estimator: Estimator | None = None) -> dict:
        data = {
            "package_id": self.package_id,
            "name": self.name,
            "price": round(self.price, 2),
            "data_gb": self.data_gb,
            "voice_min": self.voice_min,
            "streaming": self.streaming,
            "family": self.family,
            "device": self.device,
            "unit_cost": self.unit_cost,
            "margin": round(self.margin, 2),
        }
        if estimator is not None:
            data["utility"] = estimator.utility(self.option())
        return data


def default_scenario(estimator: Estimator, ids: Sequence[str] | None = None) -> list[ScenarioPlan]:
    ids = ids or [p.id for p in PLANS if p.status == "active"]
    plans = []
    for plan_id in ids:
        option = option_for(plan_id)
        plans.append(ScenarioPlan.from_option(option))
    return plans


def evaluate(estimator: Estimator, plans: Sequence[ScenarioPlan]) -> dict:
    if not plans:
        return {"shares": {}, "none": 1.0, "total_plan_share": 0.0,
                "expected_revenue": 0.0, "expected_profit": 0.0}
    options = [p.option() for p in plans]
    utilities = [estimator.utility(o) for o in options] + [estimator.none_constant]
    probs = _softmax(utilities)
    shares = {p.package_id: probs[i] for i, p in enumerate(plans)}
    revenue = sum(shares[p.package_id] * p.price for p in plans)
    profit = sum(shares[p.package_id] * p.margin for p in plans)
    return {
        "shares": shares,
        "none": probs[-1],
        "total_plan_share": sum(shares.values()),
        "expected_revenue": revenue,
        "expected_profit": profit,
    }


def _softmax(values: Sequence[float]) -> list[float]:
    top = max(values)
    exps = [math.exp(v - top) for v in values]
    total = sum(exps)
    return [e / total for e in exps]


@dataclass
class MenuOption:
    plan: ScenarioPlan
    share: float
    baseline_share: float

    @property
    def delta(self) -> float:
        return self.share - self.baseline_share


def compare(baseline: Sequence[ScenarioPlan], scenario: Sequence[ScenarioPlan],
            estimator: Estimator) -> dict:
    """Compare two menus.  Both are evaluated as whole menus, never summed parts."""
    base = evaluate(estimator, baseline)
    scen = evaluate(estimator, scenario)
    base_ids = {p.package_id for p in baseline}
    scen_ids = {p.package_id for p in scenario}
    rows = []
    for plan in scenario:
        pid = plan.package_id
        rows.append({
            "package_id": pid,
            "name": plan.name,
            "price": plan.price,
            "unit_cost": plan.unit_cost,
            "share_before": base["shares"].get(pid, 0.0),
            "share_after": scen["shares"][pid],
            "delta": scen["shares"][pid] - base["shares"].get(pid, 0.0),
            "entered": pid not in base_ids,
        })
    for plan in baseline:
        if plan.package_id not in scen_ids:
            rows.append({
                "package_id": plan.package_id,
                "name": plan.name,
                "price": plan.price,
                "unit_cost": plan.unit_cost,
                "share_before": base["shares"][plan.package_id],
                "share_after": 0.0,
                "delta": -base["shares"][plan.package_id],
                "removed": True,
            })
    return {
        "baseline": base,
        "scenario": scen,
        "rows": rows,
        "none_delta": scen["none"] - base["none"],
        "profit_delta": scen["expected_profit"] - base["expected_profit"],
        "revenue_delta": scen["expected_revenue"] - base["expected_revenue"],
    }


def substitution(estimator: Estimator, baseline: Sequence[ScenarioPlan],
                 target: str, action: str = "remove") -> dict:
    """Where does a plan's share go when it is removed (or added)?"""
    base = evaluate(estimator, baseline)
    target_plan = next((p for p in baseline if p.package_id == target), None)
    if target_plan is None:
        raise ValueError(f"套餐 {target} 不在基准菜单中")
    if action == "remove":
        scenario = [p for p in baseline if p.package_id != target]
    elif action == "add":
        scenario = list(baseline)
    else:
        raise ValueError("action 只能是 remove 或 add")
    scen = evaluate(estimator, scenario)
    released = base["shares"][target]
    receivers = []
    for plan in scenario:
        pid = plan.package_id
        gain = scen["shares"][pid] - base["shares"][pid]
        receivers.append({
            "package_id": pid,
            "name": plan.name,
            "share_before": base["shares"][pid],
            "share_after": scen["shares"][pid],
            "gain": gain,
            "absorption": (gain / released) if released > 0 else 0.0,
        })
    none_gain = scen["none"] - base["none"]
    receivers.sort(key=lambda r: r["gain"], reverse=True)
    return {
        "target": target,
        "target_name": target_plan.name,
        "action": action,
        "released": released,
        "to_none": none_gain,
        "to_none_absorption": (none_gain / released) if released > 0 else 0.0,
        "receivers": receivers,
        "none_before": base["none"],
        "none_after": scen["none"],
    }


def inbound_sources(estimator: Estimator, menu: Sequence[ScenarioPlan],
                    target: str) -> dict:
    """When ``target`` is newly added, whose share does it take and from where?"""
    target_plan = next((p for p in menu if p.package_id == target), None)
    if target_plan is None:
        raise ValueError(f"套餐 {target} 不在菜单中")
    baseline = [p for p in menu if p.package_id != target]
    add_option = target_plan.option()
    base_opt = {p.package_id: p.option() for p in baseline}
    base_share = evaluate(estimator, baseline)
    with_share = evaluate(estimator, menu)
    gained = with_share["shares"][target]
    sources = []
    for plan in baseline:
        pid = plan.package_id
        loss = base_share["shares"][pid] - with_share["shares"][pid]
        sources.append({
            "package_id": pid,
            "name": plan.name,
            "share_without": base_share["shares"][pid],
            "share_with": with_share["shares"][pid],
            "loss": loss,
            "contribution": (loss / gained) if gained > 0 else 0.0,
        })
    none_loss = base_share["none"] - with_share["none"]
    sources.sort(key=lambda r: r["loss"], reverse=True)
    return {
        "target": target,
        "target_name": target_plan.name,
        "gained": gained,
        "from_none": none_loss,
        "from_none_contribution": (none_loss / gained) if gained > 0 else 0.0,
        "sources": sources,
    }


def price_sensitivity(estimator: Estimator, menu: Sequence[ScenarioPlan], target: str,
                      steps: Sequence[float] = (-0.1, -0.05, 0.0, 0.05, 0.1)) -> list[dict]:
    """Move only the target plan's price; re-evaluate the whole menu each time."""
    plan = next((p for p in menu if p.package_id == target), None)
    if plan is None:
        raise ValueError(f"套餐 {target} 不在菜单中")
    rows = []
    for delta in steps:
        price = round(plan.price * (1 + delta), 2)
        variant = ScenarioPlan(
            package_id=plan.package_id, price=price, data_gb=plan.data_gb,
            voice_min=plan.voice_min, streaming=plan.streaming,
            family=plan.family, device=plan.device, unit_cost=plan.unit_cost,
        )
        variant_menu = [variant if p.package_id == target else p for p in menu]
        metrics = evaluate(estimator, variant_menu)
        rows.append({
            "price": price,
            "change": delta,
            "share": metrics["shares"][target],
            "none": metrics["none"],
            "expected_profit": metrics["expected_profit"],
            "expected_revenue": metrics["expected_revenue"],
        })
    return rows


def portfolio_value(estimator: Estimator, plans: Sequence[ScenarioPlan]) -> float:
    return evaluate(estimator, plans)["expected_profit"]


def search_portfolio(estimator: Estimator, candidates: Sequence[ScenarioPlan],
                     max_size: int = 6, min_size: int = 1,
                     must_include: Sequence[str] = (), max_candidates: int = 10,
                     top_k: int = 8) -> dict:
    """Exhaustive search over subsets of at most ``max_candidates`` candidates.

    Only menu *combinations* are scored.  Each candidate set is evaluated as one
    multinomial, so the reported profit is never a sum of independently
    predicted per-plan profits.
    """
    candidates = list(candidates)[:max_candidates]
    ids = [p.package_id for p in candidates]
    by_id = {p.package_id: p for p in candidates}
    unknown = [m for m in must_include if m not in by_id]
    if unknown:
        raise ValueError("必须保留的套餐不在候选中：" + "、".join(unknown))
    min_size = max(min_size, len(must_include), 1)
    max_size = max(min_size, min(max_size, len(candidates)))
    results = []
    for size in range(min_size, max_size + 1):
        for combo in itertools.combinations(ids, size):
            if any(m not in combo for m in must_include):
                continue
            plans = [by_id[c] for c in combo]
            metrics = evaluate(estimator, plans)
            results.append({
                "ids": list(combo),
                "shares": metrics["shares"],
                "none": metrics["none"],
                "expected_profit": metrics["expected_profit"],
                "expected_revenue": metrics["expected_revenue"],
                "plans": [by_id[c].to_dict() for c in combo],
            })
    results.sort(key=lambda r: r["expected_profit"], reverse=True)
    best = results[0] if results else None
    return {
        "candidates": [p.to_dict(estimator) for p in candidates],
        "searched": len(results),
        "max_candidates": max_candidates,
        "max_size": max_size,
        "min_size": min_size,
        "must_include": list(must_include),
        "results": results[:top_k],
        "total_results": len(results),
        "best": best,
    }


def best_alternative_single(estimator: Estimator, baseline: Sequence[ScenarioPlan],
                            candidate: ScenarioPlan) -> dict | None:
    """Which single existing plan does adding ``candidate`` cannibalise most?"""
    info = inbound_sources(estimator, list(baseline) + [candidate], candidate.package_id)
    if not info["sources"]:
        return None
    top = info["sources"][0]
    return {"package_id": top["package_id"], "name": top["name"],
            "loss": top["loss"], "contribution": top["contribution"]}


def displacement(estimator: Estimator, baseline: Sequence[ScenarioPlan],
                 scenario: Sequence[ScenarioPlan]) -> dict:
    """Split the change into displaced share and where it landed.

    The two sides are read from the same multinomial, so they balance exactly:
    every point of share a baseline plan loses is a point another plan (or
    "none") gains.  Nothing is summed across independent per-plan forecasts.
    """
    base = evaluate(estimator, baseline)
    scen = evaluate(estimator, scenario)
    scen_ids = {p.package_id for p in scenario}
    removed = []
    for plan in baseline:
        before = base["shares"][plan.package_id]
        after = scen["shares"].get(plan.package_id, 0.0)
        if after < before:
            removed.append({
                "package_id": plan.package_id,
                "name": plan.name,
                "share_before": before,
                "share_after": after,
                "displaced": before - after,
                "removed_from_menu": plan.package_id not in scen_ids,
            })
    removed.sort(key=lambda r: r["displaced"], reverse=True)
    destinations = []
    for plan in scenario:
        before = base["shares"].get(plan.package_id, 0.0)
        after = scen["shares"][plan.package_id]
        if after > before:
            destinations.append({
                "package_id": plan.package_id,
                "name": plan.name,
                "share_before": before,
                "share_after": after,
                "gained": after - before,
            })
    none_gain = scen["none"] - base["none"]
    total_displaced = sum(r["displaced"] for r in removed)
    total_gained = sum(d["gained"] for d in destinations) + max(0.0, none_gain)
    if total_displaced > 0:
        for row in removed:
            row["share_of_displaced"] = row["displaced"] / total_displaced
        for row in destinations:
            row["share_of_gained"] = row["gained"] / total_gained if total_gained else 0.0
    destinations.sort(key=lambda r: r["gained"], reverse=True)
    return {
        "removed": removed,
        "destinations": destinations,
        "to_none": none_gain,
        "to_none_share": (max(0.0, none_gain) / total_gained) if total_gained else 0.0,
        "total_displaced": total_displaced,
        "total_gained": total_gained,
        "balanced": abs(total_displaced - total_gained) < 1e-9,
    }
