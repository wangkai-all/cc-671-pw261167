"""Fictional plan catalogue and historical menu snapshots.

Everything in this module is synthetic.  The catalogue lists the plans that
exist in the made-up sample; :func:`snapshot` returns the price and allowance
card a shopper would have seen on a given day (list price, price step changes
and short fictional promotions).  No real customer, tariff or transaction data
is involved.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
from typing import Iterable


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    tier: str
    price: float
    data_gb: float
    voice_min: float
    streaming: int
    family: int
    device: int
    unit_cost: float
    status: str = "active"
    note: str = ""

    @property
    def is_entry(self) -> bool:
        return self.tier == "entry"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["is_entry"] = self.is_entry
        return data


# Current list prices (fictional, CNY / month).  ``unit_cost`` is the modelled
# monthly cost-to-serve used by the profit search.  The design deliberately
# separates allowances from price: some plans carry more data at a lower voice
# allowance, and the family plan includes sharing but not streaming.
PLANS: tuple[Plan, ...] = (
    Plan("basic", "轻享 15G", "entry", 19, 15, 200, 0, 0, 0, 8.0,
         note="在售入门档"),
    Plan("lite", "轻享 25G", "entry", 27, 25, 300, 0, 0, 0, 9.5,
         note="在售入门档"),
    Plan("student", "校园 60G", "entry", 29, 60, 100, 0, 0, 0, 12.0,
         note="2026 年 1 月上市的入门档"),
    Plan("value", "畅享 40G", "mid", 42, 40, 500, 0, 0, 0, 16.0,
         note="2026 年 3 月流量升级"),
    Plan("video", "影音 70G", "mid", 55, 70, 400, 1, 0, 0, 25.0,
         note="含影音权益，语音较少"),
    Plan("gamer", "游戏 100G", "mid", 65, 100, 800, 1, 0, 0, 32.0,
         note="2026 年 9 月降价"),
    Plan("family", "家庭 120G", "premium", 95, 120, 1500, 0, 1, 0, 44.0,
         note="含家庭共享，不含影音权益"),
    Plan("premium", "尊享 160G", "premium", 139, 160, 2000, 0, 1, 1, 80.0,
         note="含终端补贴"),
    Plan("senior", "长者 5G", "entry", 15, 5, 100, 0, 0, 0, 6.0,
         status="retired", note="2026 年 3 月停售，可作为情景候选"),
    Plan("bundle", "全家享 200G", "premium", 159, 200, 2000, 1, 1, 1, 98.0,
         status="candidate", note="尚未上市的情景候选"),
)

PLAN_BY_ID = {p.id: p for p in PLANS}
CURRENT_MENU = tuple(p.id for p in PLANS if p.status == "active")
ENTRY_PLAN_IDS = tuple(p.id for p in PLANS if p.is_entry)


# Fictional price timeline (list price).  Records keep the snapshot they saw.
PRICE_CHANGES: dict[str, tuple[tuple[str, float], ...]] = {
    "basic": (("2025-11", 19),),
    "lite": (("2025-11", 25), ("2026-07", 27)),
    "student": (("2026-01", 29),),
    "value": (("2025-11", 39), ("2026-05", 42)),
    "video": (("2025-11", 59), ("2026-07", 55)),
    "gamer": (("2025-11", 69), ("2026-09", 65)),
    "family": (("2025-11", 89), ("2026-08", 95)),
    "premium": (("2025-11", 129), ("2026-06", 139)),
    "senior": (("2025-11", 15),),
    "bundle": (("2027-01", 159),),
}

DATA_CHANGES: dict[str, tuple[tuple[str, float], ...]] = {
    "basic": (("2025-11", 15),),
    "lite": (("2025-11", 25),),
    "student": (("2026-01", 60),),
    "value": (("2025-11", 50), ("2026-03", 40)),
    "video": (("2025-11", 50), ("2026-02", 70)),
    "gamer": (("2025-11", 100),),
    "family": (("2025-11", 120),),
    "premium": (("2025-11", 140), ("2026-06", 160)),
    "senior": (("2025-11", 5),),
    "bundle": (("2027-01", 200),),
}

VOICE_CHANGES: dict[str, tuple[tuple[str, float], ...]] = {
    "video": (("2025-11", 400),),
    "family": (("2025-11", 1000), ("2026-07", 1500)),
    "premium": (("2025-11", 1500), ("2026-06", 2000)),
}

LAUNCH = {
    "student": "2026-01-01",
    "bundle": "2027-01-01",
}
RETIRE = {
    "senior": "2026-03-01",
}

# Short fictional promotions.  These exist so historical records carry a price
# that differs from the list price on the same day -- one reason each record
# must store the price it actually showed instead of looking prices up later.
PROMOTIONS: dict[str, tuple[tuple[str, str, float], ...]] = {
    "basic": (("2026-06-01", "2026-06-30", 17),),
    "lite": (("2026-02-01", "2026-02-28", 22),),
    "student": (("2026-08-01", "2026-08-31", 25),),
    "value": (("2025-12-01", "2026-01-15", 35),),
    "video": (("2026-02-01", "2026-02-28", 49),),
    "gamer": (("2026-05-01", "2026-05-31", 59),),
    "family": (("2026-09-01", "2026-09-30", 79),),
    "premium": (("2026-04-01", "2026-04-30", 119),),
}


def _month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def value_at(timeline: tuple[tuple[str, float], ...], day: date, fallback: float) -> float:
    key = _month_key(day)
    value = fallback
    for effective, candidate in timeline:
        if key >= effective:
            value = candidate
    return value


def promo_price(plan_id: str, day: date) -> float | None:
    day_str = day.isoformat()
    for start, end, price in PROMOTIONS.get(plan_id, ()):
        if start <= day_str <= end:
            return float(price)
    return None


def snapshot(plan: Plan, day: date) -> dict:
    """Return the price/attribute card a shopper would have seen on ``day``."""
    price = promo_price(plan.id, day)
    if price is None:
        price = float(value_at(PRICE_CHANGES.get(plan.id, ()), day, plan.price))
    return {
        "package_id": plan.id,
        "price": price,
        "data_gb": float(value_at(DATA_CHANGES.get(plan.id, ()), day, plan.data_gb)),
        "voice_min": float(value_at(VOICE_CHANGES.get(plan.id, ()), day, plan.voice_min)),
        "streaming": int(plan.streaming),
        "family": int(plan.family),
        "device": int(plan.device),
    }


def available_ids(day: date) -> tuple[str, ...]:
    day_str = day.isoformat()
    ids = []
    for plan in PLANS:
        if day_str < LAUNCH.get(plan.id, "2020-01-01"):
            continue
        if plan.id in RETIRE and day_str >= RETIRE[plan.id]:
            continue
        if plan.status == "candidate" and plan.id not in LAUNCH:
            continue
        ids.append(plan.id)
    return tuple(ids)


def current_menu() -> tuple[str, ...]:
    return CURRENT_MENU


def plans_payload(today: date | None = None) -> list[dict]:
    today = today or date(2026, 11, 23)
    result = []
    for plan in PLANS:
        item = plan.to_dict()
        item["snapshot"] = snapshot(plan, today)
        item["in_current_menu"] = plan.id in CURRENT_MENU
        result.append(item)
    return result


def plan_ids(plans: Iterable[Plan]) -> tuple[str, ...]:
    return tuple(p.id for p in plans)
