"""Generate the fictional samples that ship with the analyser.

The samples are written to ``data/choices.json`` (the main sample) and
``data/samples/*.json`` (diagnostic samples).  Everything is generated from the
made-up catalogue; no real transactions are read or written.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .catalog import PLANS, available_ids
from .history import Record, build_options


@dataclass(frozen=True)
class Scenario:
    key: str
    name: str
    description: str
    n: int
    seed: int
    start: date
    end: date
    price_coef: float
    data_coef: float
    voice_coef: float
    streaming_coef: float
    family_coef: float
    device_coef: float
    none_intercept: float
    menu_mode: str = "history"     # history | pair | narrow
    never_chosen: tuple[str, ...] = ()
    always_chosen: str | None = None
    menu_override: tuple[str, ...] | None = None
    # Demo-only: inject double submissions and a couple of data-entry errors so
    # the validation / de-duplication path is visible instead of always empty.
    inject_duplicates: int = 0
    inject_invalid: int = 0


SAMPLES: tuple[Scenario, ...] = (
    Scenario(
        key="main", name="主样本（1,200 条）",
        description=(
            "2025-11 至 2026-11 的虚构历史选择。菜单随上市、停售和价格调整变化，"
            "约两成记录只展示了不完整菜单；另含 18 次重复提交与 3 条选项越界记录，"
            "用来演示去重与校验。"
        ),
        n=1200, seed=20261123,
        start=date(2025, 11, 1), end=date(2026, 11, 20),
        price_coef=-0.062, data_coef=0.028, voice_coef=0.0011,
        streaming_coef=0.55, family_coef=0.60, device_coef=0.40,
        none_intercept=-0.18,
        inject_duplicates=18, inject_invalid=3,
    ),
    Scenario(
        key="separation", name="完全分离样本（80 条）",
        description=(
            "只有两档套餐且每档都带终端补贴；尊享档一旦出现就被选中。"
            "用来展示完全分离时诊断而非经营建议。"
        ),
        n=80, seed=41,
        start=date(2026, 9, 1), end=date(2026, 11, 1),
        price_coef=-0.02, data_coef=0.01, voice_coef=0.0,
        streaming_coef=0.0, family_coef=0.0, device_coef=0.0,
        none_intercept=-0.1,
        menu_mode="pair", always_chosen="premium",
    ),
    Scenario(
        key="price_wrong", name="价格方向异常样本（300 条）",
        description=(
            "虚构促销里“越贵越被选”，价格系数估计为正。用来展示方向校验会拦下结论。"
        ),
        n=300, seed=9,
        start=date(2026, 1, 1), end=date(2026, 10, 1),
        price_coef=0.045, data_coef=0.012, voice_coef=0.0002,
        streaming_coef=0.1, family_coef=0.1, device_coef=0.1,
        none_intercept=-0.2,
    ),
    Scenario(
        key="collinear", name="属性同向变化样本（240 条）",
        description=(
            "菜单里“家庭共享”和“终端补贴”总是同时出现或同时缺席，"
            "两项偏好无法分开估计。"
        ),
        n=240, seed=17,
        start=date(2026, 4, 1), end=date(2026, 10, 1),
        price_coef=-0.05, data_coef=0.02, voice_coef=0.0005,
        streaming_coef=0.3, family_coef=0.4, device_coef=0.3,
        none_intercept=-0.1,
        menu_mode="narrow",
        menu_override=("basic", "premium"),
    ),
)

SAMPLE_BY_KEY = {s.key: s for s in SAMPLES}


def _utility(option, scenario: Scenario) -> float:
    return (
        scenario.price_coef * option.price
        + scenario.data_coef * option.data_gb
        + scenario.voice_coef * option.voice_min
        + scenario.streaming_coef * option.streaming
        + scenario.family_coef * option.family
        + scenario.device_coef * option.device
    )


def _menus(scenario: Scenario, rng: random.Random):
    start = scenario.start
    span = (scenario.end - scenario.start).days or 1
    for _ in range(scenario.n):
        day = start + timedelta(days=rng.randrange(span))
        if scenario.menu_override:
            ids = list(scenario.menu_override)
        else:
            ids = list(available_ids(day))
        if scenario.menu_mode == "history" and len(ids) > 2:
            roll = rng.random()
            if roll < 0.14:
                ids.remove(rng.choice(ids))
            elif roll < 0.22:
                for extra in rng.sample(ids, 2):
                    if len(ids) > 3:
                        ids.remove(extra)
        yield day, ids


def _choose(options, scenario: Scenario, rng: random.Random) -> str | None:
    if scenario.always_chosen:
        return scenario.always_chosen
    utilities = [_utility(o, scenario) for o in options] + [scenario.none_intercept]
    top = max(utilities)
    weights = [math.exp(u - top) for u in utilities]
    index = rng.choices(range(len(weights)), weights=weights, k=1)[0]
    if index == len(options):
        return None
    return options[index].package_id


def generate(scenario: Scenario) -> list[Record]:
    rng = random.Random(scenario.seed)
    records: list[Record] = []
    for index, (day, ids) in enumerate(_menus(scenario, rng)):
        options = build_options(ids, day)
        if not options:
            continue
        if scenario.never_chosen:
            ids = [o.package_id for o in options if o.package_id not in scenario.never_chosen]
            if not ids:
                continue
            options = build_options(ids, day)
        choice = _choose(options, scenario, rng)
        # Two respondents can share a day and an outcome; only the idempotency
        # key (the submission id) decides whether a copy is a duplicate.
        stamp = f"{day.isoformat()}T{(8 + index % 12):02d}:{(index * 7) % 60:02d}:{index % 60:02d}+00:00"
        submission_id = f"{scenario.key}-{index:05d}"
        record = Record(record_id="", submission_id=submission_id, submitted_at=stamp,
                        choice=choice, options=options)
        records.append(Record(record_id=record.content_fingerprint()[:16],
                              submission_id=submission_id, submitted_at=stamp,
                              choice=choice, options=options))
    if scenario.inject_duplicates:
        # A form double-submitted with the same idempotency key: it must be
        # ignored by the loader, so the fitted sample does not grow.
        rng_dup = random.Random(scenario.seed + 1)
        for record in rng_dup.sample(records, min(scenario.inject_duplicates, len(records))):
            records.append(record)
    if scenario.inject_invalid:
        # Data-entry errors where the recorded choice was not on that menu.
        rng_bad = random.Random(scenario.seed + 2)
        for record in rng_bad.sample(records, min(scenario.inject_invalid, len(records))):
            absent = [p.id for p in PLANS if p.id not in {o.package_id for o in record.options}]
            if not absent:
                continue
            records.append(Record(
                record_id="", submission_id=record.submission_id + "-bad",
                submitted_at=record.submitted_at, choice=absent[0],
                options=record.options))
    return records


def _write_json(path: str, records: list[Record]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"records": [r.to_dict() for r in records]}, fh,
                  ensure_ascii=False, indent=1)


def write_samples(root: str) -> list[dict]:
    """Write the diagnostic samples.  The main sample lives at ``data/choices.json``."""
    os.makedirs(root, exist_ok=True)
    written = []
    for scenario in SAMPLES:
        if scenario.key == "main":
            continue
        records = generate(scenario)
        path = os.path.join(root, f"{scenario.key}.json")
        _write_json(path, records)
        written.append({
            "key": scenario.key,
            "name": scenario.name,
            "description": scenario.description,
            "path": path,
            "records": len(records),
        })
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.path.join(os.path.dirname(__file__), "..", "data"))
    args = parser.parse_args()
    root = os.path.abspath(args.root)
    written = write_samples(os.path.join(root, "samples"))
    main_path = os.path.join(root, "choices.json")
    main_records = generate(SAMPLE_BY_KEY["main"])
    _write_json(main_path, main_records)
    main_scenario = SAMPLE_BY_KEY["main"]
    written.insert(0, {
        "key": "main",
        "name": main_scenario.name,
        "description": main_scenario.description,
        "path": main_path,
        "records": len(main_records),
    })
    manifest = {
        "samples": written,
        "main": main_path,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "fictional": True,
    }
    with open(os.path.join(root, "samples.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    for item in written:
        print(f"{item['key']:12s} {item['records']:5d} records -> {item['path']}")
    print(f"main sample -> {manifest['main']}")


if __name__ == "__main__":
    main()
