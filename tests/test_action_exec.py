"""tests/test_action_exec.py —— _execute_action 单测(T41)。

覆盖:
- 正常执行落 steps/001_<tool>/ 目录;
- ARTIFACT_FROM_STATE(netlist)解析为实际路径;
- tool_not_found 回灌(不中断,error_code=eda.tool_not_found);
- args_invalid 回灌(不中断,error_code=eda.tool_args_invalid)。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from eda_agent.contracts import (
    ARTIFACT_FROM_STATE,
    CONTRACT_VERSION,
    RunRequest,
    ToolCall,
    ToolResult,
    artifact_ref,
)
from eda_agent.errors import EDA_TOOL_ARGS_INVALID, EDA_TOOL_NOT_FOUND
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


def _setup(tmp_path: Path, tools=None):
    os.chdir(tmp_path)
    settings = make_settings(mode="rule", run_budget_s=60)
    runner = Runner(runs_dir="runs", settings=settings)
    tools = tools or []
    reg = make_registry(tools)
    planner = CPlanner(
        registry=reg, llm=FakeLLMProvider([]),
        runner=runner, settings=settings,
    )
    return planner, runner, settings


def _create_record(runner: Runner):
    req = RunRequest(
        kind="self_heal", goal="pass all tests",
        rtl_path="a.v", tb_path="tb.v",
    )
    rec = runner.create(req, provider_used="fake", config_snapshot={})
    return rec, req


def test_execute_action_writes_step_dir(tmp_path: Path) -> None:
    synth = StubTool(
        "yosys_synth",
        [_ok_parsed("yosys_synth", {"success": True})],
        schema=echo_schema(),
    )
    planner, runner, _ = _setup(
        tmp_path, [(synth, "yosys_synth", "synth", echo_schema())]
    )
    rec, _ = _create_record(runner)
    state = PlannerState(rtl_path="a.v", tb_path="tb.v", goal="g", max_iter=5)
    action = Action("yosys_synth", {"rtl": "a.v", "top_module": ""})
    result = planner._execute_action(action, state, rec)

    assert result.status == "ok"
    assert result.tool == "yosys_synth"
    # iteration 单一计数出口:
    assert state.iteration == 1
    assert len(state.history) == 1
    # steps/001_yosys_synth/ 目录与 tool_call.json / tool_result.json:
    step_dir = Path("runs") / rec.run_id / "steps" / "001_yosys_synth"
    assert step_dir.exists()
    assert (step_dir / "tool_call.json").exists()
    assert (step_dir / "tool_result.json").exists()
    # run.json 也应记录该 step:
    run_data = json.loads(
        (Path("runs") / rec.run_id / "run.json").read_text(encoding="utf-8")
    )
    assert len(run_data["steps"]) == 1
    assert run_data["steps"][0]["tool_name"] == "yosys_synth"
    # tool_call.json 内容:caller=planner,name,args 原样落盘:
    call_data = json.loads(
        (step_dir / "tool_call.json").read_text(encoding="utf-8")
    )
    assert call_data["name"] == "yosys_synth"
    assert call_data["caller"] == "planner"
    assert call_data["args"]["rtl"] == "a.v"
    # tool_result.json 同 run.json step 对应的 status=ok:
    result_data = json.loads(
        (step_dir / "tool_result.json").read_text(encoding="utf-8")
    )
    assert result_data["status"] == "ok"
    assert result_data["tool"] == "yosys_synth"


def test_resolve_args_artifact_from_state_for_netlist(tmp_path: Path) -> None:
    """netlist=ARTIFACT_FROM_STATE 时,用 state.netlist_ref 解析为实际路径。"""
    planner, runner, _ = _setup(tmp_path)
    rec, _ = _create_record(runner)
    state = PlannerState(rtl_path="a.v", tb_path="tb.v", goal="g", max_iter=5)
    state.netlist_ref = artifact_ref(rec.run_id, "synth/netlist.v")
    args = {
        "netlist": ARTIFACT_FROM_STATE,
        "lib": "sky.lib",
        "clock": "clk",
    }
    resolved = planner._resolve_args(args, state, rec)
    expected = str(Path("runs") / rec.run_id / "synth/netlist.v")
    assert resolved["netlist"] == expected
    assert resolved["lib"] == "sky.lib"
    assert resolved["clock"] == "clk"


def test_resolve_args_artifact_ref_replaces_rtl(tmp_path: Path) -> None:
    """独立验证步:rtl=ARTIFACT_FROM_STATE + _artifact_ref → rtl 被替换。"""
    planner, runner, _ = _setup(tmp_path)
    rec, _ = _create_record(runner)
    state = PlannerState(rtl_path="a.v", tb_path="tb.v", goal="g", max_iter=5)
    best = artifact_ref(rec.run_id, "self_heal/best/rtl.v")
    args = {
        "rtl": ARTIFACT_FROM_STATE,
        "_artifact_ref": best,
        "tb": "tb.v",
    }
    resolved = planner._resolve_args(args, state, rec)
    expected = str(Path("runs") / rec.run_id / "self_heal/best/rtl.v")
    assert resolved["rtl"] == expected
    # _artifact_ref 已 pop:
    assert "_artifact_ref" not in resolved


def test_tool_not_found_returns_error_result_not_raising(tmp_path: Path) -> None:
    """registry.get 返回 None 时,_execute_action 构造 tool_not_found 回灌,不抛。"""
    planner, runner, _ = _setup(tmp_path)  # 空 registry
    rec, _ = _create_record(runner)
    state = PlannerState(rtl_path="a.v", tb_path="tb.v", goal="g", max_iter=5)
    action = Action("nonexistent_tool", {"x": 1})
    result = planner._execute_action(action, state, rec)

    assert result.status == "error"
    assert result.error_code == EDA_TOOL_NOT_FOUND
    assert result.tool == "nonexistent_tool"
    assert result.parsed["_schema"]["contract_version"] == CONTRACT_VERSION
    assert result.parsed["error"] == "tool not found"
    assert "not in registry" in (result.error_hint or "")
    # 仍落 step + 计 iteration(回灌不中断):
    assert state.iteration == 1
    assert len(state.history) == 1


def test_args_invalid_returns_error_result_not_raising(tmp_path: Path) -> None:
    """args 不匹配 schema 时,构造 args_invalid 回灌,不抛。"""
    # 注册一个严格 schema 的 tool:required=[rtl],无 rtl 时校验失败
    strict_schema = {
        "name": "strict_tool",
        "description": "strict",
        "input_schema": {
            "type": "object",
            "properties": {"rtl": {"type": "string"}},
            "required": ["rtl"],
        },
    }
    strict_tool = StubTool(
        "strict_tool", [_ok_parsed("strict_tool", {})], schema=strict_schema,
    )
    planner, runner, _ = _setup(
        tmp_path, [(strict_tool, "strict_tool", "util", strict_schema)]
    )
    rec, _ = _create_record(runner)
    state = PlannerState(rtl_path="a.v", tb_path="tb.v", goal="g", max_iter=5)
    # 不传 rtl → required 校验失败
    action = Action("strict_tool", {"foo": 1})
    result = planner._execute_action(action, state, rec)

    assert result.status == "error"
    assert result.error_code == EDA_TOOL_ARGS_INVALID
    assert result.tool == "strict_tool"
    assert result.parsed["error"] == "args invalid"
    assert "fail schema" in (result.error_hint or "")
    # 仍落 step + 计 iteration:
    assert state.iteration == 1
    # strict_tool 实际未被调用(stub.calls 为空):
    assert len(strict_tool.calls) == 0


def test_args_match_schema_strips_underscore_prefix(tmp_path: Path) -> None:
    """下划线前缀字段(_remaining_budget_s 等)豁免 schema 校验。"""
    strict_schema = {
        "name": "strict_tool",
        "description": "strict",
        "input_schema": {
            "type": "object",
            "properties": {"rtl": {"type": "string"}},
            "required": ["rtl"],
            # 注:registry.to_llm_tools 会剥离 _ 前缀,但 _args_match_schema
            # 直接对 tool.schema 校验;此处 _remaining_budget_s 即便在 schema
            # 里也豁免(visible 过滤)。
        },
    }
    strict_tool = StubTool(
        "strict_tool", [_ok_parsed("strict_tool", {})], schema=strict_schema,
    )
    planner, runner, _ = _setup(
        tmp_path, [(strict_tool, "strict_tool", "util", strict_schema)]
    )
    rec, _ = _create_record(runner)
    state = PlannerState(rtl_path="a.v", tb_path="tb.v", goal="g", max_iter=5)
    # 传 rtl + _remaining_budget_s:_前缀豁免,rtl 满足 required → ok
    action = Action(
        "strict_tool",
        {"rtl": "a.v", "_remaining_budget_s": 30.0, "_artifact_ref": {"x": 1}},
    )
    result = planner._execute_action(action, state, rec)
    assert result.status == "ok"
    assert strict_tool.calls[0].args["rtl"] == "a.v"
    # _ 前缀字段保留在 call.args 里(供 skill 拆包):
    assert strict_tool.calls[0].args["_remaining_budget_s"] == 30.0
