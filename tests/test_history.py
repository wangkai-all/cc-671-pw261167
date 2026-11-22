"""Validation of historical choice records.

The record must prove that the choice was one of the options actually shown on
that occasion, and a repeated submission must not grow the sample.
"""

from __future__ import annotations

import json
import os
from datetime import date

import pytest

from app.catalog import snapshot
from app.history import load_dataset, make_record, validate_choice
from tests.conftest import make_option, make_record as build


def test_choice_outside_shown_set_is_rejected():
    options = [make_option("basic"), make_option("value")]
    problems = validate_choice(options, "premium")
    assert problems
    assert "不在当时展示的选择集中" in problems[0]


def test_choice_inside_shown_set_passes():
    options = [make_option("basic"), make_option("value")]
    assert validate_choice(options, "value") == []
    assert validate_choice(options, None) == []  # "none" is always allowed


def test_record_keeps_snapshot_the_respondent_saw():
    record = make_record(["basic", "value"], "basic",
                         date(2026, 6, 15))  # inside a fictional promo
    prices = {o.package_id: o.price for o in record.options}
    assert prices["basic"] == snapshot(plan_by_id("basic"), date(2026, 6, 15))["price"]
    assert prices["basic"] != snapshot(plan_by_id("basic"), date(2026, 7, 1))["price"]
    assert "premium" not in prices  # not on the menu, absent rather than zero


def plan_by_id(plan_id):
    from app.catalog import PLAN_BY_ID
    return PLAN_BY_ID[plan_id]


def test_load_dataset_dedupes_by_submission_id(tmp_path):
    options = [make_option("basic"), make_option("value")]
    first = build("sub-1", "basic", options)
    second = build("sub-2", "basic", options)      # different respondent
    reopen = build("sub-1", "value", options)      # same form submitted again
    path = tmp_path / "records.json"
    path.write_text(json.dumps({"records": [first.to_dict(), second.to_dict(),
                                            reopen.to_dict()]}), encoding="utf-8")
    dataset = load_dataset(str(path))
    assert dataset.raw_count == 3
    assert dataset.duplicates_ignored == 1
    assert len(dataset.records) == 2
    assert {r.submission_id for r in dataset.records} == {"sub-1", "sub-2"}
    assert [r.choice for r in dataset.records] == ["basic", "basic"]


def test_load_dataset_rejects_invalid_choice_and_keeps_other_records(tmp_path):
    options = [make_option("basic"), make_option("value")]
    bad = build("sub-bad", "premium", options)
    good = build("sub-good", None, options)
    path = tmp_path / "records.json"
    path.write_text(json.dumps({"records": [bad.to_dict(), good.to_dict()]}),
                    encoding="utf-8")
    dataset = load_dataset(str(path))
    assert [r.submission_id for r in dataset.records] == ["sub-good"]
    assert len(dataset.rejected) == 1
    assert "不在当时展示的选择集中" in dataset.rejected[0]["reason"]


def test_non_shown_plan_is_not_counted_as_zero(tmp_path):
    options = [make_option("basic"), make_option("value")]
    records = [build(f"s{i}", "basic", options) for i in range(3)]
    path = tmp_path / "records.json"
    path.write_text(json.dumps({"records": [r.to_dict() for r in records]}),
                    encoding="utf-8")
    dataset = load_dataset(str(path))
    table = {row["package_id"]: row for row in dataset.plan_table()}
    assert table["basic"]["shown"] == 3
    assert table["basic"]["ever_shown"] is True
    # premium was never offered, so it has no denominator and no rate
    assert table["premium"]["shown"] == 0
    assert table["premium"]["chosen"] == 0
    assert table["premium"]["rate"] is None
    assert table["premium"]["ever_shown"] is False


def test_menu_size_range_reflects_actual_menus(tmp_path):
    two = [make_option("basic"), make_option("value")]
    three = [make_option("basic"), make_option("value"), make_option("premium")]
    records = [build("a", "basic", two), build("b", None, three)]
    path = tmp_path / "records.json"
    path.write_text(json.dumps({"records": [r.to_dict() for r in records]}),
                    encoding="utf-8")
    stats = load_dataset(str(path)).stats()
    assert stats["menu_size_range"] == [2, 3]
    assert stats["none_chosen"] == 1
    assert stats["none_rate"] == pytest.approx(0.5)


def test_duplicate_submit_through_store_does_not_add_sample(tmp_path):
    from app.history import ChoiceStore

    path = str(tmp_path / "store.json")
    options = [make_option("basic"), make_option("value")]
    store = ChoiceStore(path)
    assert store.add(build("idem-1", "basic", options)) is True
    assert store.add(build("idem-1", "basic", options)) is False
    assert len(load_dataset(path).records) == 1
