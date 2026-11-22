"""Shared fixtures for the plan-preference tests.

Everything here uses the fictional catalogue and in-memory records; no test
reads or writes a real transaction store.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.catalog import PLAN_BY_ID, snapshot  # noqa: E402
from app.history import Option, Record  # noqa: E402


def make_option(plan_id: str, day: date = date(2026, 11, 1), **overrides) -> Option:
    card = snapshot(PLAN_BY_ID[plan_id], day)
    card.update(overrides)
    card["package_id"] = plan_id
    return Option.from_dict(card)


def make_record(submission_id: str, choice: str | None, options, *,
                submitted_at: str = "2026-11-01T10:00:00+00:00") -> Record:
    options = tuple(options)
    record = Record(record_id="", submission_id=submission_id,
                    submitted_at=submitted_at, choice=choice, options=options)
    return Record(record_id=record.content_fingerprint()[:16],
                  submission_id=submission_id, submitted_at=submitted_at,
                  choice=choice, options=options)


def simple_choice_records(n: int = 300, seed: int = 7):
    """Deterministic records from the fictional history generator.

    The generator spreads the menu over the launch/retire timeline, so every
    attribute moves at least sometimes; a handful of fixed menus would leave
    the model unidentifiable.
    """
    from app.synth import Scenario, generate

    scenario = Scenario(
        key="unit", name="测试样本", description="单元测试用虚构样本",
        n=n, seed=seed, start=date(2026, 1, 1), end=date(2026, 11, 1),
        price_coef=-0.05, data_coef=0.03, voice_coef=0.0006,
        streaming_coef=0.4, family_coef=0.5, device_coef=0.3,
        none_intercept=-0.2,
    )
    return generate(scenario)


@pytest.fixture(scope="session")
def main_analysis():
    from app.analysis import load_analysis
    return load_analysis("main")


@pytest.fixture(scope="session")
def api_base_url():
    """Run the real stdlib server on an ephemeral port for API tests."""
    import threading

    from app.server import build_server

    server = build_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


def post_json(url: str, payload: dict) -> tuple[int, dict]:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def get_json(url: str) -> tuple[int, dict]:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))
