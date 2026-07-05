"""tests/test_planner_limits.py —— 边界与上限单测(T45)。

覆盖:
- budget_exhausted → status=budget_exhausted,REPORTING;
- planner_max_iterations 上限(rule 模式);
- fatal(eda.internal error_code)→ 立即 REPORTING;
- iteration 单一计数出口(_reflect 不重复加)。

注:测试用 ``cwd_tmp`` fixture chdir 到 tmp_path,结束后 monkeypatch 自动恢复,
避免 cwd 泄漏到后续测试(tmp_path 已被 pytest 删除)。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from eda_agent.contracts import (
    CONTRACT_VERSION,
    RunRequest,
    ToolResult,
)
from eda_agent.errors import EDA_INTERNAL
from eda_agent.planner.c_planner import CPlanner
from eda_agent.planner.rule_planner import Action, PlannerState
from eda_agent.runner import Runner

from tests._planner_helpers import (
    StubTool,
    echo_schema,
    make_registry,
    make_settings,
    _ok_parsed,
    FakeLLMProvider,
)


@pytest.fixture
def cwd_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """chdir 到 tmp_path,测试结束自动恢复(避免 cwd 泄漏到后续测试)。"""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _setup(cwd: Path, *, mode="rule", run_budget_s=600, max_iter=8, tools=None):
    settings = make_settings(mode=mode, run_budget_s=run_budget_s, max_iter=max_iter)
    runner = Runner(runs_dir="runs", settings=settings)
    reg = make_registry(tools or [])
    planner = CPlanner(
        registry=reg, llm=FakeLLMProvider([]),
        runner=runner, settings=settings,
    )
    return planner, runner, settings


def _req():
    return RunRequest(
        kind="self_heal", goal="pass all tests",
        rtl_path="a.v", tb_path="tb.v",
    )


def test_budget_exhausted_status(cwd_tmp: Path) -> None:
    """run_budget_s=0 → 立即 exhausted → status=budget_exhausted。"""
    synth = StubTool(
        "yosys_synth", [_ok_parsed("yosys_synth", {"success": True})],
        schema=echo_schema(),
    )
    planner, runner, _ = _setup(
        cwd_tmp, run_budget_s=0,
        tools=[(synth, "yosys_synth", "synth", echo_schema())],
    )
    rep = planner.execute(_req())
    assert rep.status == "budget_exhausted"
    assert rep.metrics["planner_iterations"] == 0


def test_rule_mode_max_iter_caps_loop(cwd_tmp: Path) -> None:
    """rule 模式 max_iter=3 → 最多 3 步,即使 sim 一直失败也停。"""
    synth = StubTool(
        "yosys_synth", [_ok_parsed("yosys_synth", {"success": True})],
        schema=echo_schema(),
    )
    sim = StubTool(
        "iverilog_sim",
        [_ok_parsed("iverilog_sim", {"passed": False, "num_failed": 1})],
        schema=echo_schema(),
    )
    diag = StubTool("skill_diagnose", [_ok_parsed("skill_diagnose", {})],
                    schema=echo_schema())
    planner, runner, _ = _setup(
        cwd_tmp, mode="rule", max_iter=3,
        tools=[
            (synth, "yosys_synth", "synth", echo_schema()),
            (sim, "iverilog_sim", "sim", echo_schema()),
            (diag, "skill_diagnose", "skill", echo_schema()),
        ],
    )
    rep = planner.execute(_req())
    assert rep.metrics["planner_iterations"] <= 3
    assert rep.status == "failed"


def test_fatal_error_code_stops_immediately(cwd_tmp: Path) -> None:
    """tool 返回 error_code=eda.internal(severity=fatal)→ state.fatal → REPORTING。"""
    fatal_result = ToolResult(
        status="error", exit_code=None, stdout="", stderr="",
        parsed={"_schema": {"name": "yosys_synth", "version": "0.1.0",
                            "contract_version": CONTRACT_VERSION}},
        artifacts=[], duration_s=0.0, tool="yosys_synth",
        error_code=EDA_INTERNAL, error_hint="internal crash",
    )
    synth = StubTool("yosys_synth", [fatal_result], schema=echo_schema())
    planner, runner, _ = _setup(
        cwd_tmp, tools=[(synth, "yosys_synth", "synth", echo_schema())],
    )
    rep = planner.execute(_req())
    assert rep.status == "failed"
    assert rep.metrics["planner_iterations"] == 1  # fatal 立即 REPORTING


def test_iteration_single_counter_no_duplicate(cwd_tmp: Path) -> None:
    """_reflect 不碰 iteration;_execute_action 末尾单一计数出口。"""
    synth = StubTool(
        "yosys_synth", [_ok_parsed("yosys_synth", {"success": True})],
        schema=echo_schema(),
    )
    sim = StubTool(
        "iverilog_sim",
        [_ok_parsed("iverilog_sim", {"passed": True, "num_passed": 1})],
        schema=echo_schema(),
    )
    planner, runner, _ = _setup(
        cwd_tmp, mode="rule", max_iter=20,
        tools=[
            (synth, "yosys_synth", "synth", echo_schema()),
            (sim, "iverilog_sim", "sim", echo_schema()),
        ],
    )
    rec = runner.create(_req(), provider_used="fake", config_snapshot={})
    state = PlannerState(rtl_path="a.v", tb_path="tb.v", goal="g", max_iter=5)
    for a in [
        Action("yosys_synth", {"rtl": "a.v"}),
        Action("iverilog_sim", {"rtl": "a.v", "tb": "tb.v"}),
        Action("yosys_synth", {"rtl": "a.v"}),
    ]:
        planner._execute_action(a, state, rec)
        planner._reflect(state)
    assert state.iteration == 3
    assert len(state.history) == 3


def test_emergency_report_on_crash(
    cwd_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """execute 内部抛异常 → _emergency_report 兜底,不抛给调用方。"""
    synth = StubTool(
        "yosys_synth", [_ok_parsed("yosys_synth", {"success": True})],
        schema=echo_schema(),
    )
    planner, runner, _ = _setup(
        cwd_tmp, tools=[(synth, "yosys_synth", "synth", echo_schema())],
    )

    def boom(_state):
        raise RuntimeError("boom")

    monkeypatch.setattr(planner, "_reflect", boom)
    rep = planner.execute(_req())
    assert rep.status == "failed"
    assert "crash" in rep.summary
    assert "boom" in rep.summary
    assert rep.metrics == {}
