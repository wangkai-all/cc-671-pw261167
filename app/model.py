"""Fit the price/attribute choice model and expose scenario arithmetic.

Model
-----
For a choice occasion ``t`` the respondent sees a set of plans ``S_t`` plus the
outside option "none".  Choosing plan ``j`` has utility::

    U_j = b_price * price_j + b_data * data_j / 10 + b_voice * voice_j / 100
          + b_streaming * streaming_j + b_family * family_j + b_device * device_j
          + eps_j
    U_none = 0 + eps_none

with i.i.d. type-1 extreme value errors, so the probability of picking ``j`` is
``exp(U_j) / (exp(U_none) + sum_{k in S_t} exp(U_k))``.  ``price`` and ``data``
are in the same per-10 units as the coefficient table so the ratio of the two
is a willingness to pay in CNY per GB.

Every record contributes exactly one likelihood term with the menu that was
actually shown.  A plan that was not shown on that occasion is absent from the
denominator -- it is neither a zero nor a choice.

Shares in a menu scenario therefore come from one multinomial over the whole
menu, never from adding per-plan forecasts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from . import stats
from .catalog import PLAN_BY_ID
from .history import Option, Record

FEATURE_KEYS = tuple(f.key for f in stats.FEATURES) + ("none",)
FEATURE_LABELS = {f.key: f.label for f in stats.FEATURES}
FEATURE_LABELS["none"] = "都不选"


class ModelError(RuntimeError):
    pass


def _scaled_row(option: Option) -> list[float]:
    row = []
    for feature in stats.FEATURES:
        value = getattr(option, feature.key)
        row.append(float(value) / feature.scale)
    row.append(0.0)  # "none" dummy for plan rows
    return row


NONE_ROW = [0.0] * len(stats.FEATURES) + [1.0]


def _choice_data(records: Sequence[Record]):
    data = []
    for record in records:
        rows = [_scaled_row(o) for o in record.options] + [list(NONE_ROW)]
        if record.choice is None:
            chosen = len(record.options)
        else:
            ids = [o.package_id for o in record.options]
            if record.choice not in ids:
                continue  # defensive: legacy record, skip rather than invent
            chosen = ids.index(record.choice)
        data.append((rows, chosen))
    return data


@dataclass
class Estimator:
    beta: dict[str, float]          # per unit of the raw attribute
    se: dict[str, float | None]
    z: dict[str, float | None]
    p_value: dict[str, float | None]
    loglik: float
    loglik_null: float
    n_records: int
    n_alternatives: int
    iterations: int
    converged: bool
    gradient_norm: float
    hessian_condition: float | None
    hessian_positive: bool
    none_constant: float
    warnings: list[str] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
    blocked: bool = False

    def utility(self, option: Option) -> float:
        total = 0.0
        for feature in stats.FEATURES:
            total += self.beta[feature.key] * getattr(option, feature.key)
        return total

    def shares(self, options: Sequence[Option]) -> dict:
        utilities = [self.utility(o) for o in options] + [self.none_constant]
        probs = stats._softmax(utilities)
        plan_share = {o.package_id: probs[i] for i, o in enumerate(options)}
        return {"plans": plan_share, "none": probs[-1], "total": sum(plan_share.values())}

    def wtp(self, key: str) -> float | None:
        """CNY per raw unit of ``key`` (price coefficient must be negative)."""
        price_coef = self.beta["price"]
        if price_coef >= 0:
            return None
        return -self.beta[key] / price_coef

    def as_dict(self) -> dict:
        return {
            "beta": self.beta,
            "se": self.se,
            "z": self.z,
            "p_value": self.p_value,
            "none_constant": self.none_constant,
            "loglik": self.loglik,
            "loglik_null": self.loglik_null,
            "n_records": self.n_records,
            "n_alternatives": self.n_alternatives,
            "iterations": self.iterations,
            "converged": self.converged,
            "gradient_norm": self.gradient_norm,
            "hessian_condition": self.hessian_condition,
            "hessian_positive": self.hessian_positive,
            "warnings": self.warnings,
            "diagnostics": self.diagnostics,
            "blocked": self.blocked,
        }


def _diagnose(fit: stats.FitResult, records: Sequence[Record]) -> tuple[list[dict], bool]:
    diagnostics: list[dict] = []
    blocked = False
    if not fit.converged:
        blocked = True
        diagnostics.append({
            "code": "not_converged",
            "severity": "critical",
            "message": "模型未收敛，不能用于情景预测。",
            "detail": (
                f"迭代 {fit.iterations} 次后梯度范数 {fit.gradient_norm:.2e}，"
                f"达到收敛阈值前中止。"
                if fit.warnings else
                f"迭代 {fit.iterations} 次后仍未收敛（梯度范数 {fit.gradient_norm:.2e}）。"
            ),
        })
    if not fit.hessian_positive:
        blocked = True
        diagnostics.append({
            "code": "information_singular",
            "severity": "critical",
            "message": "信息矩阵非正定，参数不可识别。",
            "detail": "可能存在完全分离、空选择集或属性在历史菜单中从不变化。",
        })
    max_coef = max((abs(v) for v in fit.beta_scaled), default=0.0)
    if max_coef > 12:
        blocked = True
        diagnostics.append({
            "code": "separation",
            "severity": "critical",
            "message": "检测到完全分离迹象（系数绝对值过大）。",
            "detail": (
                "至少一个套餐在所有展示记录中从未被选中，或某属性完美预测选择，"
                "似然被推向边界。此时系数与份额无法解释。"
            ),
        })
    price_coef = fit.beta_raw[0] if fit.beta_raw else 0.0
    if price_coef >= 0:
        blocked = True
        diagnostics.append({
            "code": "price_direction",
            "severity": "critical",
            "message": "价格系数不为负，与“更贵更少被选”的基本预期矛盾。",
            "detail": f"估计值 {price_coef:+.4f}（每元月费）。不能据此给出经营建议。",
        })
    elif fit.se_raw and fit.se_raw[0]:
        z = price_coef / fit.se_raw[0]
        if z > -1.645:
            blocked = True
            diagnostics.append({
                "code": "price_not_significant",
                "severity": "warning",
                "message": "价格系数为负但统计上不显著（|z| < 1.645）。",
                "detail": f"z = {z:+.2f}；价格方向缺乏证据，份额情景仅供参考。",
            })

    if fit.hessian_condition is not None and fit.hessian_condition > 1e8:
        diagnostics.append({
            "code": "ill_conditioned",
            "severity": "warning",
            "message": "信息矩阵病态，部分系数标准误偏大。",
            "detail": f"条件数 {fit.hessian_condition:.1e}。菜单中属性高度同向变化时常见。",
        })

    # Attribute collinearity in the *shown* alternatives.
    shown_rows = [[getattr(o, f.key) / f.scale for f in stats.FEATURES]
                  for r in records for o in r.options]
    pairs = stats.collinear_pairs(
        shown_rows, [f.key for f in stats.FEATURES], threshold=0.9)
    for left, right, corr in pairs:
        diagnostics.append({
            "code": "collinear",
            "severity": "warning",
            "message": (
                f"{FEATURE_LABELS.get(left, left)}与{FEATURE_LABELS.get(right, right)}"
                f"在历史菜单中几乎总是同向变化（相关 {corr:+.2f}），二者偏好无法分开估计。"
            ),
            "detail": "只能报告两项合起来的效应；单独解读其中一项会夸大结论。",
            "pair": [left, right],
            "correlation": corr,
        })
    return diagnostics, blocked


def fit_model(records: Sequence[Record]) -> Estimator:
    if len(records) < 3:
        raise ModelError("历史记录不足 3 条，无法拟合。")
    data = _choice_data(records)
    if not data:
        raise ModelError("没有可用于拟合的有效选择记录。")
    k = len(FEATURE_KEYS)
    fit = stats.fit_mnl(data, k)
    scales = [f.scale for f in stats.FEATURES] + [1.0]
    beta_scaled = fit.beta_scaled
    beta_raw = [b / s for b, s in zip(beta_scaled, scales)]
    se_raw = [None if s is None else s / sc for s, sc in zip(fit.se_scaled, scales)]
    z = [None if (s is None or s == 0) else b / s for b, s in zip(beta_raw, se_raw)]
    pvals = [None if zz is None else stats.two_sided_p(zz) for zz in z]
    fit.beta_raw, fit.se_raw, fit.z, fit.p_value = beta_raw, se_raw, z, pvals
    fit.none_constant_raw = beta_raw[-1]
    diagnostics, blocked = _diagnose(fit, records)
    names = list(FEATURE_KEYS)
    return Estimator(
        beta=dict(zip(names, beta_raw)),
        se=dict(zip(names, se_raw)),
        z=dict(zip(names, z)),
        p_value=dict(zip(names, pvals)),
        loglik=fit.loglik,
        loglik_null=fit.loglik_null,
        n_records=fit.n_records,
        n_alternatives=fit.n_alternatives,
        iterations=fit.iterations,
        converged=fit.converged,
        gradient_norm=fit.gradient_norm,
        hessian_condition=fit.hessian_condition,
        hessian_positive=fit.hessian_positive,
        none_constant=fit.none_constant_raw,
        warnings=fit.warnings,
        diagnostics=diagnostics,
        blocked=blocked,
    )


# --------------------------------------------------------------------------
# Menu scenarios
# --------------------------------------------------------------------------


def option_for(plan_id: str, price: float | None = None,
               overrides: dict | None = None) -> Option:
    plan = PLAN_BY_ID[plan_id]
    base = Option(
        package_id=plan_id,
        price=plan.price,
        data_gb=plan.data_gb,
        voice_min=plan.voice_min,
        streaming=plan.streaming,
        family=plan.family,
        device=plan.device,
    )
    if price is None and not overrides:
        return base
    values = base.to_dict()
    if price is not None:
        values["price"] = float(price)
    if overrides:
        values.update(overrides)
    return Option.from_dict(values)


def menu_from_ids(estimator: Estimator, ids: Sequence[str],
                  prices: dict[str, float] | None = None) -> list[Option]:
    prices = prices or {}
    return [option_for(i, prices.get(i)) for i in ids]
