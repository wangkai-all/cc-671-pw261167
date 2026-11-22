"""Minimal JSON HTTP server for the plan-preference workspace.

Only the Python standard library is used.  The server serves the static page
from ``web/`` and a small JSON API under ``/api/``.  All data is the fictional
local sample; nothing connects to a real transaction system.
"""

from __future__ import annotations

import json
import mimetypes
import os
import posixpath
import urllib.parse
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .analysis import (Analysis, SampleError, current_scenario,
                       ensure_default_sample, entry_options, load_analysis,
                       menu_state, model_payload, sample_catalog, sample_path,
                       scenario_payload, search_payload, substitution_payload)
from .catalog import PLANS, PLAN_BY_ID, plans_payload
from .history import ChoiceStore, validate_choice
from .model import ModelError, option_for
from .scenario import ScenarioPlan

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")

MAX_BODY = 1_000_000


class ApiError(Exception):
    def __init__(self, status: int, message: str, detail: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail or {}


def _read_json(handler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    if length > MAX_BODY:
        raise ApiError(413, "请求体过大")
    raw = handler.rfile.read(length)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ApiError(400, f"JSON 解析失败：{exc}")
    if not isinstance(payload, dict):
        raise ApiError(400, "请求体必须是 JSON 对象")
    return payload


def _plans_for_menu(items) -> list[ScenarioPlan]:
    if not isinstance(items, list):
        raise ApiError(400, "菜单必须是数组")
    plans = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ApiError(400, "菜单项必须是对象")
        plan_id = item.get("package_id")
        if plan_id not in PLAN_BY_ID:
            raise ApiError(400, f"未知套餐 {plan_id}")
        if plan_id in seen:
            raise ApiError(400, f"菜单中套餐重复：{plan_id}")
        seen.add(plan_id)
        overrides = {k: item[k] for k in ("data_gb", "voice_min", "streaming",
                                          "family", "device") if k in item}
        option = option_for(plan_id, item.get("price"), overrides)
        if option.price <= 0:
            raise ApiError(400, "价格必须为正")
        cost = item.get("unit_cost")
        plans.append(ScenarioPlan.from_option(option, float(cost) if cost is not None else None))
    if not plans:
        raise ApiError(400, "菜单不能为空")
    return plans


class Handler(BaseHTTPRequestHandler):
    server_version = "PlanPrefs/0.1"

    # -- helpers ---------------------------------------------------------
    def log_message(self, fmt, *args):
        if os.environ.get("PLANPREFS_QUIET") == "1":
            return
        super().log_message(fmt, *args)

    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: str) -> None:
        if not os.path.isfile(path):
            self._send_json(404, {"error": "not_found", "message": "文件不存在"})
            return
        ctype, _ = mimetypes.guess_type(path)
        with open(path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _analysis(self, sample: str) -> Analysis:
        try:
            return load_analysis(sample)
        except SampleError as exc:
            raise ApiError(404, str(exc))
        except ModelError as exc:
            raise ApiError(422, f"无法拟合：{exc}")
        except FileNotFoundError:
            raise ApiError(404, "样本文件不存在")

    # -- routing ---------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                self._api_get(path, query)
            else:
                self._static(path)
        except ApiError as exc:
            self._send_json(exc.status, {"error": "api_error", "message": exc.message, **exc.detail})
        except Exception as exc:  # pragma: no cover - defensive
            self._send_json(500, {"error": "internal", "message": str(exc)})

    def do_POST(self):
        try:
            self._api_post()
        except ApiError as exc:
            self._send_json(exc.status, {"error": "api_error", "message": exc.message, **exc.detail})
        except Exception as exc:  # pragma: no cover - defensive
            self._send_json(500, {"error": "internal", "message": str(exc)})

    # -- static ----------------------------------------------------------
    def _static(self, path: str) -> None:
        if path in ("/", ""):
            path = "/index.html"
        safe = posixpath.normpath(path).lstrip("/")
        if safe.startswith(".."):
            raise ApiError(400, "非法路径")
        self._send_file(os.path.join(WEB_DIR, safe))

    # -- API: GET --------------------------------------------------------
    def _api_get(self, path: str, query: dict) -> None:
        sample = (query.get("sample") or ["main"])[0]

        if path == "/api/health":
            self._send_json(200, {"ok": True, "fictional": True})
            return

        if path == "/api/catalog":
            self._send_json(200, {
                "plans": plans_payload(),
                "current_menu": [p.id for p in PLANS if p.status == "active"],
                "entry_options": [{"package_id": p.id, "name": p.name,
                                   "tier": p.tier, "price": p.price}
                                  for p in PLANS],
                "fictional": True,
            })
            return

        if path == "/api/samples":
            ensure_default_sample()
            self._send_json(200, {"samples": sample_catalog(), "default": "main"})
            return

        if path == "/api/model":
            analysis = self._analysis(sample)
            self._send_json(200, model_payload(analysis))
            return

        if path == "/api/menu":
            analysis = self._analysis(sample)
            state = menu_state(analysis)
            self._send_json(200, {
                **state,
                "blocked": analysis.estimator.blocked,
                "diagnostics": analysis.estimator.diagnostics,
                "catalog": plans_payload(),
                "entry_options": entry_options(),
                "fictional": True,
            })
            return


        if path == "/api/substitution":
            analysis = self._analysis(sample)
            target = (query.get("target") or [""])[0]
            if target not in PLAN_BY_ID:
                raise ApiError(400, "target 必须是已知套餐")
            if analysis.estimator.blocked:
                raise ApiError(422, "模型诊断未通过，不能生成转移分析",
                               {"diagnostics": analysis.estimator.diagnostics})
            try:
                plans = current_scenario(analysis.estimator)
                if target not in {p.package_id for p in plans}:
                    # retired / not-yet-launched plans can still be inspected:
                    # add them to the current menu as a what-if, explicitly.
                    plans = plans + [_plan_from_id(target)]
                payload = substitution_payload(analysis, [p.to_dict() for p in plans], target)
            except SampleError as exc:
                raise ApiError(400, str(exc))
            self._send_json(200, payload)
            return

        raise ApiError(404, f"未知接口 {path}")

    # -- API: POST -------------------------------------------------------
    def _api_post(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        body = _read_json(self)

        if path == "/api/compare":
            sample = body.get("sample", "main")
            analysis = self._analysis(sample)
            baseline = body.get("baseline")
            scenario = body.get("scenario")
            if not isinstance(baseline, list) or not isinstance(scenario, list):
                raise ApiError(400, "baseline 与 scenario 必须是数组")
            try:
                plans_baseline = _plans_for_menu(baseline)
                plans_scenario = _plans_for_menu(scenario)
            except SampleError as exc:
                raise ApiError(400, str(exc))
            except ApiError:
                raise
            if analysis.estimator.blocked:
                raise ApiError(422, "模型诊断未通过，不能生成份额情景",
                               {"diagnostics": analysis.estimator.diagnostics})
            from .scenario import compare, displacement
            result = compare(plans_baseline, plans_scenario, analysis.estimator)
            disp = displacement(analysis.estimator, plans_baseline, plans_scenario)
            self._send_json(200, {
                "baseline": {**result["baseline"],
                             "plans": scenario_payload(analysis.estimator, plans_baseline)},
                "scenario": {**result["scenario"],
                             "plans": scenario_payload(analysis.estimator, plans_scenario)},
                "rows": result["rows"],
                "none_delta": result["none_delta"],
                "profit_delta": result["profit_delta"],
                "revenue_delta": result["revenue_delta"],
                "displacement": disp,
            })
            return

        if path == "/api/substitution":
            sample = body.get("sample", "main")
            analysis = self._analysis(sample)
            target = body.get("target")
            if target not in PLAN_BY_ID:
                raise ApiError(400, "target 必须是已知套餐")
            baseline = body.get("baseline")
            try:
                if baseline:
                    plans = _plans_for_menu(baseline)
                else:
                    plans = current_scenario(analysis.estimator)
                if target not in {p.package_id for p in plans}:
                    # what-if: inspect a retired / not-yet-launched plan by
                    # adding it to the current menu explicitly.
                    plans = plans + [_plan_from_id(target)]
                payload = substitution_payload(analysis,
                                               [p.to_dict() for p in plans], target)
            except SampleError as exc:
                raise ApiError(400, str(exc))
            except ApiError:
                raise
            self._send_json(200, payload)
            return

        if path == "/api/search":
            sample = body.get("sample", "main")
            analysis = self._analysis(sample)
            request = dict(body)
            try:
                candidates = request.get("candidates")
                if candidates is not None and not isinstance(candidates, list):
                    raise ApiError(400, "candidates 必须是数组")
                if candidates and len(candidates) > 10:
                    raise ApiError(400, "候选套餐最多 10 个")
                if analysis.estimator.blocked:
                    raise ApiError(422, "模型诊断未通过，不能搜索组合",
                                   {"diagnostics": analysis.estimator.diagnostics})
                payload = search_payload(analysis, request)
            except SampleError as exc:
                raise ApiError(400, str(exc))
            self._send_json(200, payload)
            return

        if path == "/api/records":
            sample = body.get("sample", "main")
            path_on_disk = sample_path(sample)
            options = body.get("options")
            choice = body.get("choice")
            day_raw = body.get("day")
            try:
                day = date.fromisoformat(day_raw) if day_raw else date.today()
            except ValueError:
                raise ApiError(400, "day 必须是 YYYY-MM-DD")
            if not isinstance(options, list) or not options:
                raise ApiError(400, "options 必须是非空数组")
            from .history import Option
            try:
                parsed = []
                for item in options:
                    parsed.append(Option.from_dict({
                        "package_id": item["package_id"],
                        "price": item.get("price", 0),
                        "data_gb": item.get("data_gb", 0),
                        "voice_min": item.get("voice_min", 0),
                        "streaming": item.get("streaming", 0),
                        "family": item.get("family", 0),
                        "device": item.get("device", 0),
                    }))
            except Exception as exc:
                raise ApiError(400, f"选项格式错误：{exc}")
            choice_id = None if choice in (None, "", "none", "都不选") else str(choice)
            problems = validate_choice(parsed, choice_id)
            if problems:
                raise ApiError(400, "；".join(problems))
            store = ChoiceStore(path_on_disk)
            from .history import Record
            submission_id = body.get("submission_id")
            stamp = body.get("submitted_at")
            record = Record(record_id="", submission_id=submission_id or "",
                            submitted_at=stamp or date.today().isoformat(),
                            choice=choice_id, options=tuple(parsed))
            if not record.submission_id:
                record = Record(record_id=record.content_fingerprint()[:16],
                                submission_id="content-" + record.content_fingerprint()[:20],
                                submitted_at=record.submitted_at,
                                choice=record.choice, options=record.options)
            added = store.add(record)
            self._send_json(200, {"added": added, "record": record.to_dict(),
                                  "duplicate": not added})
            return

        raise ApiError(404, f"未知接口 {path}")


def _plan_from_id(plan_id: str) -> ScenarioPlan:
    return ScenarioPlan.from_option(option_for(plan_id))


def build_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    ensure_default_sample()
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="套餐偏好分析本地服务（虚构样本）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = build_server(args.host, args.port)
    print(f"套餐偏好分析已启动：http://{args.host}:{args.port}  （全部为本地虚构样本）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
