"""tests/test_planner_smoke.py —— CPlanner smoke 测试(T39)。

用 stub echo tool + RunRequest,断言:
- execute 返回 RunReport;
- status 三态之一(ok/failed/budget_exhausted);
- run_id 匹配 YYYYmmdd_HHMMSS_<4hex>;
- runs/<run_id>/run.json 存在且 status != running。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from eda_agent.contracts import RunRequest, ToolCall, ToolResult
from eda_agent.planner.c_planner import CPlanner
from eda_agent.runner import Runner, RUN_ID_RE

from tests._planner_helpers import (
    StubTool,
    make_registry,
    make_settings,
    echo_schema,
    _ok_parsed,
)

# RunReport.metrics 标准字段集(契约 §2.4 v1.2 锁定 15 字段,c_planner._build_metrics)。
EXPECTED_METRICS_15 = {
    "planner_iterations", "tool_calls_total", "llm_calls",
    "llm_tokens_in", "llm_tokens_out", "sim_passed",
    "num_passed", "num_failed", "wns_ns", "tns_ns",
    "self_heal_convergence", "self_heal_best_iter",
    "self_heal_pass_rate", "baseline_pass_rate", "wall_time_s",
}


def _echo_tool(name: str = "echo") -> StubTool:
    """返回固定 ok 的 echo tool(无副作用)。"""
    return StubTool(name, [_ok_parsed(name, {"echo": True})], schema=echo_schema())


def _make_planner(tmp_path: Path, settings=None) -> tuple[CPlanner, Runner]:
    """构造 CPlanner + Runner(cwd=tmp_path,runs 目录隔离)。"""
    import os
    os.chdir(tmp_path)
    settings = settings or make_settings(mode="rule", run_budget_s=60)
    runner = Runner(runs_dir="runs", settings=settings)
    # 注册一个 echo tool;初始 plan 会调 yosys_synth / iverilog_sim,
    # 用同名 stub 注册避免 tool_not_found(尽管 sim/synth 失败也走 failed,符合 smoke)。
    echo = _echo_tool("yosys_synth")
    sim = StubTool("iverilog_sim",
                   [_ok_parsed("iverilog_sim", {"passed": True, "num_passed": 1})],
                   schema=echo_schema())
    reg = make_registry([
        (echo, "yosys_synth", "synth", echo_schema()),
        (sim, "iverilog_sim", "sim", echo_schema()),
    ])
    from tests._planner_helpers import FakeLLMProvider
    llm = FakeLLMProvider([], provider_name="fake")
    planner = CPlanner(registry=reg, llm=llm, runner=runner, settings=settings)
    return planner, runner


def test_execute_returns_runreport_with_valid_run_id(tmp_path: Path) -> None:
    planner, runner = _make_planner(tmp_path)
    req = RunRequest(
        kind="self_heal",
        goal="pass all tests",
        rtl_path="data/rtl/counter.v",
        tb_path="data/tb/tb_counter.v",
    )
    rep = planner.execute(req)
    # RunReport 字段:
    assert rep.run_id  # 非空
    assert re.match(RUN_ID_RE.pattern, rep.run_id), f"bad run_id: {rep.run_id}"
    assert rep.status in ("ok", "failed", "budget_exhausted")
    assert isinstance(rep.summary, str)
    assert isinstance(rep.metrics, dict)


def test_run_json_exists_and_not_running(tmp_path: Path) -> None:
    planner, runner = _make_planner(tmp_path)
    req = RunRequest(
        kind="self_heal",
        goal="pass all tests",
        rtl_path="data/rtl/counter.v",
        tb_path="data/tb/tb_counter.v",
    )
    rep = planner.execute(req)
    run_json = Path("runs") / rep.run_id / "run.json"
    assert run_json.exists(), f"run.json missing at {run_json}"
    data = json.loads(run_json.read_text(encoding="utf-8"))
    assert data["run_id"] == rep.run_id
    assert data["status"] != "running"
    assert data["status"] == rep.status
    # request.json 也应存在:
    assert (Path("runs") / rep.run_id / "request.json").exists()


def test_status_ok_when_sim_passes(tmp_path: Path) -> None:
    """sim passed=True → goal_achieved → status=ok(rule 模式初始 plan 跑完即 ok)。"""
    planner, runner = _make_planner(tmp_path)
    req = RunRequest(
        kind="self_heal",
        goal="pass all tests",
        rtl_path="data/rtl/counter.v",
        tb_path="data/tb/tb_counter.v",
    )
    rep = planner.execute(req)
    assert rep.status == "ok"
    assert rep.metrics["sim_passed"] is True
    assert rep.metrics["planner_iterations"] >= 2  # synth + sim
    assert set(rep.metrics.keys()) == EXPECTED_METRICS_15  # 15 字段锁定


def test_status_failed_when_sim_fails_and_no_heal(tmp_path: Path) -> None:
    """sim passed=False 且无 skill_diagnose / skill_self_heal 注册 → failed。"""
    import os
    os.chdir(tmp_path)
    settings = make_settings(mode="rule", run_budget_s=60)
    runner = Runner(runs_dir="runs", settings=settings)
    synth = StubTool("yosys_synth",
                     [_ok_parsed("yosys_synth", {"success": True})],
                     schema=echo_schema())
    sim = StubTool("iverilog_sim",
                   [_ok_parsed("iverilog_sim", {"passed": False, "num_passed": 0})],
                   schema=echo_schema())
    reg = make_registry([
        (synth, "yosys_synth", "synth", echo_schema()),
        (sim, "iverilog_sim", "sim", echo_schema()),
    ])
    from tests._planner_helpers import FakeLLMProvider
    planner = CPlanner(
        registry=reg, llm=FakeLLMProvider([]),
        runner=runner, settings=settings,
    )
    req = RunRequest(
        kind="self_heal", goal="pass all tests",
        rtl_path="data/rtl/counter.v", tb_path="data/tb/tb_counter.v",
    )
    rep = planner.execute(req)
    assert rep.status == "failed"
    assert rep.metrics["sim_passed"] is False


def test_status_budget_exhausted(tmp_path: Path) -> None:
    """run_budget_s=0 → 立即 budget_exhausted,覆盖 status 三态之一(smoke 层)。"""
    planner, runner = _make_planner(
        tmp_path, settings=make_settings(mode="rule", run_budget_s=0),
    )
    req = RunRequest(
        kind="self_heal", goal="pass all tests",
        rtl_path="data/rtl/counter.v", tb_path="data/tb/tb_counter.v",
    )
    rep = planner.execute(req)
    assert rep.status == "budget_exhausted"
    assert rep.metrics["planner_iterations"] == 0


def test_diagnose_kind_runs_synth_and_sim(tmp_path: Path) -> None:
    """RunRequest(kind=diagnose)也能跑完 synth+sim;契约要求 self_heal/diagnose 两种 kind 均通。"""
    planner, runner = _make_planner(tmp_path)
    req = RunRequest(
        kind="diagnose",
        goal="diagnose",
        rtl_path="data/rtl/counter.v",
        tb_path="data/tb/tb_counter.v",
    )
    rep = planner.execute(req)
    assert rep.status in ("ok", "failed", "budget_exhausted")
    run_json = Path("runs") / rep.run_id / "run.json"
    data = json.loads(run_json.read_text(encoding="utf-8"))
    # request.json 里 kind=diagnose:
    req_json = json.loads(
        (Path("runs") / rep.run_id / "request.json").read_text(encoding="utf-8")
    )
    assert req_json["kind"] == "diagnose"
