"""Small, dependency-free numeric helpers and a multinomial logit fit.

The app ships with nothing beyond the Python standard library at runtime.  The
linear algebra here is deliberately tiny: the model has six plan attributes plus
one "none" constant, so Gaussian elimination and a Jacobi eigenvalue routine are
enough.  The estimator is a conditional (multinomial) logit fitted by Newton's
method with a backtracking line search.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence


# --------------------------------------------------------------------------
# Linear algebra
# --------------------------------------------------------------------------


def solve(matrix: Sequence[Sequence[float]], rhs: Sequence[float]) -> list[float] | None:
    """Solve ``Ax = b`` with partial pivoting.  Returns ``None`` if singular."""
    n = len(matrix)
    if n == 0:
        return []
    aug = [list(matrix[i]) + [float(rhs[i])] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            return None
        if pivot != col:
            aug[col], aug[pivot] = aug[pivot], aug[col]
        pivot_value = aug[col][col]
        for row in range(col + 1, n):
            factor = aug[row][col] / pivot_value
            if factor == 0.0:
                continue
            for c in range(col, n + 1):
                aug[row][c] -= factor * aug[col][c]
    x = [0.0] * n
    for row in range(n - 1, -1, -1):
        total = aug[row][n] - sum(aug[row][c] * x[c] for c in range(row + 1, n))
        x[row] = total / aug[row][row]
    return x


def inverse(matrix: Sequence[Sequence[float]]) -> list[list[float]] | None:
    n = len(matrix)
    columns = []
    for k in range(n):
        unit = [1.0 if i == k else 0.0 for i in range(n)]
        col = solve(matrix, unit)
        if col is None:
            return None
        columns.append(col)
    return [[columns[j][i] for j in range(n)] for i in range(n)]


def symmetric_eigenvalues(matrix: Sequence[Sequence[float]], sweeps: int = 60) -> list[float]:
    """Eigenvalues of a small symmetric matrix via cyclic Jacobi rotations."""
    n = len(matrix)
    if n == 0:
        return []
    a = [list(row) for row in matrix]
    for _ in range(sweeps):
        off = 0.0
        for p in range(n):
            for q in range(p + 1, n):
                off += a[p][q] * a[p][q]
        if off < 1e-24:
            break
        for p in range(n):
            for q in range(p + 1, n):
                if abs(a[p][q]) < 1e-18:
                    continue
                theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
                t = (1.0 if theta >= 0 else -1.0) / (abs(theta) + math.sqrt(theta * theta + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for k in range(n):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p] = c * akp - s * akq
                    a[k][q] = s * akp + c * akq
                for k in range(n):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k] = c * apk - s * aqk
                    a[q][k] = s * apk + c * aqk
    return sorted(a[i][i] for i in range(n))


def condition_number(matrix: Sequence[Sequence[float]]) -> float | None:
    values = [abs(v) for v in symmetric_eigenvalues(matrix)]
    if not values or min(values) <= 1e-14:
        return None
    return max(values) / min(values)


# --------------------------------------------------------------------------
# Distributions
# --------------------------------------------------------------------------


def normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def two_sided_p(z: float) -> float:
    return max(0.0, min(1.0, 2.0 * (1.0 - normal_cdf(abs(z)))))


# --------------------------------------------------------------------------
# Multinomial logit
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    unit: str
    scale: float
    description: str
    wtp_increment: float
    group: str = "plan"


FEATURES: tuple[Feature, ...] = (
    Feature("price", "月费", "元/月", 10.0,
            "该条记录展示给用户的月费；未成交按 0 计入“都不选”", 10.0, "price"),
    Feature("data_gb", "流量", "GB", 10.0,
            "套餐包含的月度流量", 10.0),
    Feature("voice_min", "语音", "分钟", 100.0,
            "套餐包含的月度语音分钟数", 100.0),
    Feature("streaming", "影音权益", "有/无", 1.0,
            "是否包含影音会员类权益", 1.0),
    Feature("family", "家庭共享", "有/无", 1.0,
            "是否支持家庭成员共享", 1.0),
    Feature("device", "终端补贴", "有/无", 1.0,
            "是否包含购机补贴", 1.0),
)

FEATURE_BY_KEY = {f.key: f for f in FEATURES}


@dataclass
class FitResult:
    beta_scaled: list[float]
    beta_raw: list[float]
    se_scaled: list[float | None]
    se_raw: list[float | None]
    z: list[float | None]
    p_value: list[float | None]
    loglik: float
    loglik_null: float
    iterations: int
    converged: bool
    gradient_norm: float
    hessian_condition: float | None
    hessian_positive: bool
    n_records: int
    n_alternatives: int
    none_constant_raw: float
    warnings: list[str]


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _softmax(utilities: Sequence[float]) -> list[float]:
    top = max(utilities)
    exps = [math.exp(u - top) for u in utilities]
    total = sum(exps)
    return [e / total for e in exps]


def _loglik(data, beta: Sequence[float]) -> float:
    total = 0.0
    for rows, chosen in data:
        utilities = [_dot(row, beta) for row in rows]
        top = max(utilities)
        exps = [math.exp(u - top) for u in utilities]
        total += utilities[chosen] - top - math.log(sum(exps))
    return total


def _gradient_hessian(data, beta: Sequence[float], k: int):
    grad = [0.0] * k
    hess = [[0.0] * k for _ in range(k)]
    for rows, chosen in data:
        utilities = [_dot(row, beta) for row in rows]
        probs = _softmax(utilities)
        xbar = [0.0] * k
        second = [[0.0] * k for _ in range(k)]
        for p, row in zip(probs, rows):
            for a in range(k):
                xbar[a] += p * row[a]
            for a in range(k):
                pa = p * row[a]
                for b in range(a, k):
                    second[a][b] += pa * row[b]
        for a in range(k):
            grad[a] += rows[chosen][a] - xbar[a]
        for a in range(k):
            for b in range(a, k):
                value = second[a][b] - xbar[a] * xbar[b]
                hess[a][b] -= value
                if b != a:
                    hess[b][a] -= value
    return grad, hess


def fit_mnl(data, k: int, *, max_iter: int = 80, tol: float = 1e-8) -> FitResult:
    """Fit ``data`` (list of (choice-set rows, chosen index)).  Rows are scaled."""
    beta = [0.0] * k
    ll = _loglik(data, beta)
    ll_null = ll
    converged = False
    iterations = 0
    warnings: list[str] = []
    grad = [0.0] * k
    hess = [[0.0] * k for _ in range(k)]
    for iteration in range(1, max_iter + 1):
        iterations = iteration
        grad, hess = _gradient_hessian(data, beta, k)
        norm = max(abs(g) for g in grad) if grad else 0.0
        if norm < tol:
            converged = True
            break
        step = solve(hess, [-g for g in grad])
        if step is None:
            warnings.append("信息矩阵奇异，牛顿步无法求解")
            break
        alpha = 1.0
        accepted = False
        current = ll
        for _ in range(40):
            candidate = [b + alpha * s for b, s in zip(beta, step)]
            candidate_ll = _loglik(data, candidate)
            if candidate_ll >= current - 1e-12:
                beta = candidate
                ll = candidate_ll
                accepted = True
                break
            alpha *= 0.5
        if not accepted:
            warnings.append("线搜索无法提高对数似然")
            break
        if max(abs(alpha * s) for s in step) < 1e-10:
            converged = True
            break
    else:
        warnings.append(f"达到最大迭代次数 {max_iter}")

    grad, hess = _gradient_hessian(data, beta, k)
    gradient_norm = max(abs(g) for g in grad) if grad else 0.0
    if gradient_norm < tol:
        converged = True

    neg_hess = [[-hess[a][b] for b in range(k)] for a in range(k)]
    eigenvalues = symmetric_eigenvalues(neg_hess)
    positive = all(v > 0 for v in eigenvalues)
    cond = condition_number(neg_hess)
    inv = inverse(neg_hess)
    se = [math.sqrt(inv[a][a]) if inv and inv[a][a] > 0 else None for a in range(k)]
    return FitResult(
        beta_scaled=list(beta),
        beta_raw=[],
        se_scaled=se,
        se_raw=[],
        z=[],
        p_value=[],
        loglik=ll,
        loglik_null=ll_null,
        iterations=iterations,
        converged=converged,
        gradient_norm=gradient_norm,
        hessian_condition=cond,
        hessian_positive=positive,
        n_records=len(data),
        n_alternatives=sum(len(rows) for rows, _ in data),
        none_constant_raw=0.0,
        warnings=warnings,
    )


def correlation_matrix(columns: Sequence[Sequence[float]]) -> list[list[float]]:
    n = len(columns)
    result = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            a, b = columns[i], columns[j]
            if not a:
                continue
            mean_a = sum(a) / len(a)
            mean_b = sum(b) / len(b)
            cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
            var_a = sum((x - mean_a) ** 2 for x in a)
            var_b = sum((y - mean_b) ** 2 for y in b)
            if var_a < 1e-18 or var_b < 1e-18:
                corr = 0.0
            else:
                corr = cov / math.sqrt(var_a * var_b)
            corr = max(-1.0, min(1.0, corr))
            result[i][j] = result[j][i] = corr
    return result


def collinear_pairs(
    rows: Iterable[Sequence[float]], names: Sequence[str], *, threshold: float = 0.9
) -> list[tuple[str, str, float]]:
    """All pairs whose absolute correlation reaches ``threshold``, strongest first."""
    rows = list(rows)
    if not rows:
        return []
    columns = [[row[k] for row in rows] for k in range(len(names))]
    corr = correlation_matrix(columns)
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if abs(corr[i][j]) >= threshold:
                pairs.append((names[i], names[j], corr[i][j]))
    pairs.sort(key=lambda p: abs(p[2]), reverse=True)
    return pairs
