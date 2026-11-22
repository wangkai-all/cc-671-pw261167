"""Synthetic historical choice records.

Each record is a *snapshot* of one choice occasion: the plans that were actually
offered, the price and allowances each one carried at that moment, which plan
(or "none") the respondent picked, and when the record was submitted.  Plans
that were not offered on that day are absent from the choice set; they are
never counted as zero choices and never enter that record's likelihood.

A record also carries a ``submission_id`` (an idempotency key assigned when the
form is opened).  Submitting the same form twice produces the same key and the
second copy is ignored, so a double click does not grow the sample.  Two
different respondents who happen to see the same menu and make the same choice
have different submission ids and each count once.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable, Sequence

from .catalog import PLANS, PLAN_BY_ID, available_ids, snapshot  # noqa: F401


SNAPSHOT_FIELDS = ("price", "data_gb", "voice_min", "streaming", "family", "device")


def _plan_sort_key(plan_id: str) -> tuple:
    order = {p.id: i for i, p in enumerate(PLANS)}
    return (order.get(plan_id, 999), plan_id)


@dataclass(frozen=True)
class Option:
    package_id: str
    price: float
    data_gb: float
    voice_min: float
    streaming: int
    family: int
    device: int

    def to_dict(self) -> dict:
        return {
            "package_id": self.package_id,
            "price": round(self.price, 2),
            "data_gb": round(self.data_gb, 2),
            "voice_min": round(self.voice_min, 2),
            "streaming": int(self.streaming),
            "family": int(self.family),
            "device": int(self.device),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Option":
        return cls(
            package_id=str(data["package_id"]),
            price=float(data["price"]),
            data_gb=float(data["data_gb"]),
            voice_min=float(data["voice_min"]),
            streaming=int(data["streaming"]),
            family=int(data["family"]),
            device=int(data["device"]),
        )


@dataclass(frozen=True)
class Record:
    record_id: str
    submission_id: str
    submitted_at: str
    choice: str | None
    options: tuple[Option, ...]

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "submission_id": self.submission_id,
            "submitted_at": self.submitted_at,
            "choice": self.choice,
            "options": [o.to_dict() for o in self.options],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Record":
        choice = data.get("choice")
        if choice in (None, "", "none", "都不选", "NONE"):
            choice = None
        return cls(
            record_id=str(data.get("record_id", "")),
            submission_id=str(data.get("submission_id", "")),
            submitted_at=str(data.get("submitted_at", "")),
            choice=choice,
            options=tuple(Option.from_dict(o) for o in data.get("options", [])),
        )

    def menu_signature(self) -> str:
        payload = [o.to_dict() for o in self.options]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def content_fingerprint(self) -> str:
        payload = {
            "choice": self.choice,
            "options": [o.to_dict() for o in self.options],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def submission_key(self) -> str:
        """Idempotency key: explicit submission id, else content fingerprint."""
        if self.submission_id:
            return f"id:{self.submission_id}"
        return f"content:{self.content_fingerprint()}"


class ValidationError(ValueError):
    pass


def validate_options(options: Sequence[Option]) -> list[str]:
    problems: list[str] = []
    if not options:
        problems.append("选择集为空")
        return problems
    ids = [o.package_id for o in options]
    if len(ids) != len(set(ids)):
        problems.append("选择集中同一套餐出现多次")
    unknown = [i for i in ids if i not in PLAN_BY_ID]
    if unknown:
        problems.append("未知套餐：" + "、".join(unknown))
    if any(o.price <= 0 for o in options):
        problems.append("展示价格必须为正")
    if any(o.price > 10000 for o in options):
        problems.append("展示价格超出合理范围")
    return problems


def validate_choice(options: Sequence[Option], choice: str | None) -> list[str]:
    problems = validate_options(options)
    if problems:
        return problems
    if choice is not None:
        ids = [o.package_id for o in options]
        if choice not in ids:
            problems.append(f"所选套餐 {choice} 不在当时展示的选择集中")
    return problems


def build_options(menu: Iterable[str], day: date) -> tuple[Option, ...]:
    seen: set[str] = set()
    options = []
    for plan_id in sorted(menu, key=_plan_sort_key):
        if plan_id in seen or plan_id not in PLAN_BY_ID:
            continue
        seen.add(plan_id)
        card = snapshot(PLAN_BY_ID[plan_id], day)
        options.append(Option(
            package_id=plan_id,
            price=card["price"],
            data_gb=card["data_gb"],
            voice_min=card["voice_min"],
            streaming=card["streaming"],
            family=card["family"],
            device=card["device"],
        ))
    return tuple(options)


def make_record(menu: Iterable[str], choice: str | None, day: date,
                submitted_at: str | None = None,
                submission_id: str | None = None) -> Record:
    options = build_options(menu, day)
    problems = validate_choice(options, choice)
    if problems:
        raise ValidationError("；".join(problems))
    stamp = submitted_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    seed = submission_id or f"{stamp}|{choice}|{json.dumps([o.to_dict() for o in options], sort_keys=True)}"
    sub_id = submission_id or hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
    record = Record(record_id="", submission_id=sub_id, submitted_at=stamp,
                    choice=choice, options=options)
    return Record(record_id=record.content_fingerprint()[:16], submission_id=sub_id,
                  submitted_at=stamp, choice=choice, options=options)


@dataclass
class Dataset:
    path: str
    records: list[Record]
    raw_count: int
    duplicates_ignored: int
    rejected: list[dict]

    def plan_table(self) -> list[dict]:
        shown = {p.id: 0 for p in PLANS}
        chosen = {p.id: 0 for p in PLANS}
        price_min: dict[str, float] = {}
        price_max: dict[str, float] = {}
        for record in self.records:
            for option in record.options:
                shown[option.package_id] = shown.get(option.package_id, 0) + 1
                price_min[option.package_id] = min(price_min.get(option.package_id, option.price), option.price)
                price_max[option.package_id] = max(price_max.get(option.package_id, option.price), option.price)
            if record.choice:
                chosen[record.choice] = chosen.get(record.choice, 0) + 1
        rows = []
        for plan in PLANS:
            n = shown.get(plan.id, 0)
            c = chosen.get(plan.id, 0)
            rows.append({
                "package_id": plan.id,
                "name": plan.name,
                "tier": plan.tier,
                "status": plan.status,
                "is_entry": plan.is_entry,
                "shown": n,
                "chosen": c,
                "rate": (c / n) if n else None,
                "price_range": [price_min[plan.id], price_max[plan.id]] if n else None,
                "ever_shown": n > 0,
                "in_current_menu": plan.status == "active",
            })
        return rows

    def stats(self) -> dict:
        records = self.records
        none = sum(1 for r in records if r.choice is None)
        sizes = [len(r.options) for r in records]
        dates = [r.submitted_at[:10] for r in records if r.submitted_at]
        return {
            "records": len(records),
            "raw_count": self.raw_count,
            "duplicates_ignored": self.duplicates_ignored,
            "rejected": self.rejected,
            "rejected_count": len(self.rejected),
            "none_chosen": none,
            "none_rate": (none / len(records)) if records else None,
            "choice_sets": len(records),
            "options_shown": sum(sizes),
            "menu_size_avg": (sum(sizes) / len(records)) if records else None,
            "menu_size_range": [min(sizes), max(sizes)] if sizes else None,
            "date_range": [min(dates), max(dates)] if dates else None,
            "distinct_menus": len({r.menu_signature() for r in records}),
        }

    def examples(self, count: int = 4) -> list[dict]:
        """A few records spread across the date range, snapshot intact."""
        if not self.records:
            return []
        ordered = sorted(self.records, key=lambda r: r.submitted_at)
        if len(ordered) <= count:
            picked = ordered
        else:
            step = (len(ordered) - 1) / (count - 1)
            picked = [ordered[round(i * step)] for i in range(count)]
        out = []
        for record in picked:
            out.append({
                "record_id": record.record_id,
                "submitted_at": record.submitted_at,
                "choice": record.choice,
                "choice_name": PLAN_BY_ID[record.choice].name if record.choice else "都不选",
                "options": [o.to_dict() for o in record.options],
            })
        return out


def load_dataset(path: str) -> Dataset:
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return Dataset(path=path, records=[], raw_count=0,
                       duplicates_ignored=0, rejected=[])
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    items = raw.get("records", [])
    records: list[Record] = []
    rejected: list[dict] = []
    seen: dict[str, Record] = {}
    duplicates = 0
    for index, item in enumerate(items):
        try:
            record = Record.from_dict(item)
        except Exception as exc:  # malformed payload
            rejected.append({
                "index": index,
                "reason": f"记录无法解析：{exc}",
                "choice": item.get("choice"),
                "options": [o.get("package_id") for o in item.get("options", [])],
            })
            continue
        problems = validate_choice(record.options, record.choice)
        if problems:
            rejected.append({
                "index": index,
                "record_id": record.record_id,
                "reason": "；".join(problems),
                "choice": record.choice,
                "options": [o.package_id for o in record.options],
            })
            continue
        key = record.submission_key()
        if key in seen:
            duplicates += 1
            continue
        seen[key] = record
        records.append(record)
    return Dataset(path=path, records=records, raw_count=len(items),
                   duplicates_ignored=duplicates, rejected=rejected)


class ChoiceStore:
    """Append-only JSON store used when a new (fictional) record is added."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self._records: list[Record] = []
        self._keys: set[str] = set()
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        dataset = load_dataset(self.path)
        self._records = dataset.records
        self._keys = {r.submission_key() for r in dataset.records}
        self._loaded = True

    def records(self) -> list[Record]:
        self._ensure_loaded()
        return list(self._records)

    def add(self, record: Record) -> bool:
        self._ensure_loaded()
        problems = validate_choice(record.options, record.choice)
        if problems:
            raise ValidationError("；".join(problems))
        key = record.submission_key()
        if key in self._keys:
            return False
        self._keys.add(key)
        self._records.append(record)
        self._flush()
        return True

    def _flush(self) -> None:
        directory = os.path.dirname(self.path)
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".choices-", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"records": [r.to_dict() for r in self._records]},
                          fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
