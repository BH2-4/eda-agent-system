"""tests/test_e2e_stub.py —— rule 模式 e2e stub 闭环(T42)。

用 stub 模拟完整闭环:
  synth ok → sim passed=False → skill_diagnose needs_rtl_patch
  → skill_self_heal all_pass → 独立验证 sim passed=True → goal_achieved。

断言:
- 独立验证步被触发(iverilog_sim 被调 ≥2 次:基线 + 独立验证);
- goal_achieved → status=ok;
- report.md 含 all_pass;
- metrics.sim_passed=True / self_heal_convergence=all_pass。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from eda_agent.contracts import RunRequest
from eda_agent.planner.c_planner import CPlanner
from eda_agent.runner import Runner

from tests._planner_helpers import (
    StubTool,
    echo_schema,
    diagnose_needs_patch,
    make_registry,
    make_settings,
    self_heal_all_pass,
    sim_result,
    synth_ok,
    FakeLLMProvider,
)


def _build_planner(tmp_path: Path):
    os.chdir(tmp_path)
    settings = make_settings(mode="rule", run_budget_s=600, max_iter=20)
    runner = Runner(runs_dir="runs", settings=settings)

    synth = StubTool("yosys_synth", [synth_ok()], schema=echo_schema())
    # iverilog_sim:第 1 次基线 passed=False;第 2 次独立验证 passed=True
    sim = StubTool(
        "iverilog_sim",
        [sim_result(False, num_passed=0, num_failed=1),
         sim_result(True, num_passed=1, num_failed=0)],
        schema=echo_schema(),
    )
    diag = StubTool("skill_diagnose", [diagnose_needs_patch()], schema=echo_schema())
    heal = StubTool("skill_self_heal", [self_heal_all_pass()], schema=echo_schema())

    reg = make_registry([
        (synth, "yosys_synth", "synth", echo_schema()),
        (sim, "iverilog_sim", "sim", echo_schema()),
        (diag, "skill_diagnose", "skill", echo_schema()),
        (heal, "skill_self_heal", "skill", echo_schema()),
    ])
    planner = CPlanner(
        registry=reg, llm=FakeLLMProvider([]),
        runner=runner, settings=settings,
    )
    return planner, runner, {"synth": synth, "sim": sim, "diag": diag, "heal": heal}


def test_e2e_self_heal_all_pass_triggers_independent_verify(tmp_path: Path) -> None:
    planner, runner, tools = _build_planner(tmp_path)
    req = RunRequest(
        kind="self_heal", goal="pass all tests",
        rtl_path="a.v", tb_path="tb.v",
    )
    rep = planner.execute(req)

    # status ok(goal_achieved 经独立验证):
    assert rep.status == "ok", f"summary={rep.summary}"
    # iverilog_sim 至少 2 次:基线 + 独立验证
    assert len(tools["sim"].calls) >= 2, (
        f"sim called {len(tools['sim'].calls)} times, expected >=2"
    )
    # skill_self_heal 调过 1 次:
    assert len(tools["heal"].calls) == 1
    # skill_diagnose 调过 1 次:
    assert len(tools["diag"].calls) == 1
    # metrics:
    assert rep.metrics["sim_passed"] is True
    assert rep.metrics["self_heal_convergence"] == "all_pass"
    assert rep.metrics["self_heal_pass_rate"] == 1.0
    # report.md 含 all_pass:
    report_md = Path("runs") / rep.run_id / "report.md"
    assert report_md.exists()
    text = report_md.read_text(encoding="utf-8")
    assert "all_pass" in text
    # run.json status=ok:
    run_data = json.loads(
        (Path("runs") / rep.run_id / "run.json").read_text(encoding="utf-8")
    )
    assert run_data["status"] == "ok"


def test_e2e_independent_verify_failure_keeps_failed(tmp_path: Path) -> None:
    """B 报 all_pass 但独立验证 sim passed=False → goal_achieved 保持 False,status=failed。

    不静默通过(契约:_reflect 里 independent_verify_passed=False 时不设 goal_achieved)。
    """
    os.chdir(tmp_path)
    settings = make_settings(mode="rule", run_budget_s=600, max_iter=20)
    runner = Runner(runs_dir="runs", settings=settings)
    synth = StubTool("yosys_synth", [synth_ok()], schema=echo_schema())
    sim = StubTool(
        "iverilog_sim",
        [sim_result(False, num_passed=0, num_failed=1),
         sim_result(False, num_passed=0, num_failed=1)],  # 独立验证也失败
        schema=echo_schema(),
    )
    diag = StubTool("skill_diagnose", [diagnose_needs_patch()], schema=echo_schema())
    heal = StubTool("skill_self_heal", [self_heal_all_pass()], schema=echo_schema())
    reg = make_registry([
        (synth, "yosys_synth", "synth", echo_schema()),
        (sim, "iverilog_sim", "sim", echo_schema()),
        (diag, "skill_diagnose", "skill", echo_schema()),
        (heal, "skill_self_heal", "skill", echo_schema()),
    ])
    planner = CPlanner(
        registry=reg, llm=FakeLLMProvider([]),
        runner=runner, settings=settings,
    )
    req = RunRequest(
        kind="self_heal", goal="pass all tests",
        rtl_path="a.v", tb_path="tb.v",
    )
    rep = planner.execute(req)
    # 独立验证失败 → status=failed(不静默通过)
    assert rep.status == "failed"
    assert rep.metrics["sim_passed"] is False
    # 仍写了 report.md:
    assert (Path("runs") / rep.run_id / "report.md").exists()


def test_e2e_sim_passes_first_time_skips_heal(tmp_path: Path) -> None:
    """sim 基线 passed=True → goal_achieved,不调 diagnose / self_heal。"""
    os.chdir(tmp_path)
    settings = make_settings(mode="rule", run_budget_s=600, max_iter=20)
    runner = Runner(runs_dir="runs", settings=settings)
    synth = StubTool("yosys_synth", [synth_ok()], schema=echo_schema())
    sim = StubTool(
        "iverilog_sim",
        [sim_result(True, num_passed=1, num_failed=0)],
        schema=echo_schema(),
    )
    diag = StubTool("skill_diagnose", [diagnose_needs_patch()], schema=echo_schema())
    heal = StubTool("skill_self_heal", [self_heal_all_pass()], schema=echo_schema())
    reg = make_registry([
        (synth, "yosys_synth", "synth", echo_schema()),
        (sim, "iverilog_sim", "sim", echo_schema()),
        (diag, "skill_diagnose", "skill", echo_schema()),
        (heal, "skill_self_heal", "skill", echo_schema()),
    ])
    planner = CPlanner(
        registry=reg, llm=FakeLLMProvider([]),
        runner=runner, settings=settings,
    )
    req = RunRequest(
        kind="self_heal", goal="pass all tests",
        rtl_path="a.v", tb_path="tb.v",
    )
    rep = planner.execute(req)
    assert rep.status == "ok"
    assert len(tools_heal_calls := heal.calls) == 0
    assert len(diag.calls) == 0
    # sim 只调 1 次(基线,无独立验证):
    assert len(sim.calls) == 1
