"""tests/test_llm_planner.py —— LLM ReAct 回环单测(T43)。

用 FakeLLMProvider 覆盖:
- 首轮 tool_calls → Action(LLM 发起 tool 调用);
- 不存在的 tool 名 → tool_not_found 回灌(不中断);
- tool_calls 空 → _llm_next_action 返回 None(转 REPORTING);
- chat 异常 → 降级(空 tool_calls,转 REPORTING)。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from eda_agent.contracts import (
    CONTRACT_VERSION,
    LLMResponse,
    RunRequest,
    ToolCall,
    artifact_ref,
)
from eda_agent.errors import EDA_TOOL_NOT_FOUND
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


def _setup(tmp_path: Path, *, responses=None, raise_on_call=False, tools=None):
    os.chdir(tmp_path)
    settings = make_settings(mode="llm", run_budget_s=60, max_iter=8)
    runner = Runner(runs_dir="runs", settings=settings)
    reg = make_registry(tools or [])
    llm = FakeLLMProvider(
        responses or [], provider_name="fake", raise_on_call=raise_on_call,
    )
    planner = CPlanner(
        registry=reg, llm=llm, runner=runner, settings=settings,
    )
    return planner, runner, llm, reg


def _create_record(runner: Runner):
    req = RunRequest(
        kind="self_heal", goal="pass all tests",
        rtl_path="a.v", tb_path="tb.v",
    )
    return runner.create(req, provider_used="fake", config_snapshot={}), req


def test_llm_first_tool_call_produces_action(tmp_path: Path) -> None:
    """LLM 首轮返回 tool_calls → _llm_next_action 构造 Action。"""
    echo = StubTool("echo", [_ok_parsed("echo", {"v": 1})], schema=echo_schema())
    planner, runner, llm, reg = _setup(
        tmp_path,
        responses=[LLMResponse(
            text="call echo",
            tool_calls=[{"id": "tc1", "name": "echo", "args": {"x": 1}}],
            tokens_in=10, tokens_out=5, provider="fake", model="m",
        )],
        tools=[(echo, "echo", "util", echo_schema())],
    )
    rec, req = _create_record(runner)
    state = PlannerState(rtl_path="a.v", tb_path="tb.v", goal="g", max_iter=5)
    from eda_agent.planner.budget import Budget
    from time import monotonic
    budget = Budget(60, monotonic())
    action = planner._llm_next_action(state, rec, budget)
    assert action is not None
    assert action.tool_name == "echo"
    assert action.args == {"x": 1}
    assert action.llm_tool_call_id == "tc1"
    assert "call echo" in action.rationale
    # LLM 被调一次,tools 参数传了:
    assert len(llm.calls) == 1
    assert llm.calls[0]["tools"] == reg.to_llm_tools()


def test_llm_nonexistent_tool_returns_tool_not_found(tmp_path: Path) -> None:
    """LLM 调不存在的 tool → _execute_action 回灌 tool_not_found,不中断。"""
    planner, runner, llm, reg = _setup(
        tmp_path,
        responses=[LLMResponse(
            text="call ghost",
            tool_calls=[{"id": "tc1", "name": "ghost_tool", "args": {}}],
            tokens_in=5, tokens_out=2, provider="fake", model="m",
        )],
        tools=[],  # 空 registry
    )
    rec, req = _create_record(runner)
    rep = planner.execute(req)
    # tool_not_found 回灌后 LLM 第二轮空 tool_calls → REPORTING → failed:
    assert rep.status == "failed"
    # 至少一次 LLM 调用(发了 ghost_tool),回灌后继续:
    assert len(llm.calls) >= 1
    # run.json 落了 ghost_tool step,error_code=eda.tool_not_found:
    import json
    run_data = json.loads(
        (Path("runs") / rep.run_id / "run.json").read_text(encoding="utf-8")
    )
    ghost_steps = [s for s in run_data["steps"] if s["tool_name"] == "ghost_tool"]
    assert len(ghost_steps) == 1
    assert ghost_steps[0]["status"] == "error"
    step_result = json.loads(
        (Path("runs") / rep.run_id / "steps" / "001_ghost_tool"
         / "tool_result.json").read_text(encoding="utf-8")
    )
    assert step_result["error_code"] == EDA_TOOL_NOT_FOUND


def test_llm_empty_tool_calls_returns_none(tmp_path: Path) -> None:
    """LLM 返回空 tool_calls → _llm_next_action 返回 None(目标达成或降级)。"""
    echo = StubTool("echo", [_ok_parsed("echo", {})], schema=echo_schema())
    planner, runner, llm, reg = _setup(
        tmp_path,
        responses=[LLMResponse(
            text="done",
            tool_calls=[],
            tokens_in=3, tokens_out=1, provider="fake", model="m",
        )],
        tools=[(echo, "echo", "util", echo_schema())],
    )
    rec, req = _create_record(runner)
    state = PlannerState(rtl_path="a.v", tb_path="tb.v", goal="g", max_iter=5)
    from eda_agent.planner.budget import Budget
    from time import monotonic
    budget = Budget(60, monotonic())
    action = planner._llm_next_action(state, rec, budget)
    assert action is None


def test_llm_chat_exception_degrades_to_empty_tool_calls(tmp_path: Path) -> None:
    """chat 抛异常 → _call_llm_safe 降级返回空 tool_calls → _llm_next_action 返回 None。"""
    echo = StubTool("echo", [_ok_parsed("echo", {})], schema=echo_schema())
    planner, runner, llm, reg = _setup(
        tmp_path, raise_on_call=True,
        tools=[(echo, "echo", "util", echo_schema())],
    )
    rec, req = _create_record(runner)
    state = PlannerState(rtl_path="a.v", tb_path="tb.v", goal="g", max_iter=5)
    from eda_agent.planner.budget import Budget
    from time import monotonic
    budget = Budget(60, monotonic())
    # 不应抛异常:
    action = planner._llm_next_action(state, rec, budget)
    assert action is None  # 降级后空 tool_calls


def test_llm_mode_max_iter_caps_actions(tmp_path: Path) -> None:
    """planner_max_iterations 上限:iteration 达上限 → _llm_next_action 返回 None。"""
    echo = StubTool("echo", [_ok_parsed("echo", {})], schema=echo_schema())
    planner, runner, llm, reg = _setup(
        tmp_path,
        responses=[LLMResponse(
            text="t",
            tool_calls=[{"id": "tc", "name": "echo", "args": {}}],
            tokens_in=1, tokens_out=1, provider="fake", model="m",
        )] * 100,
        tools=[(echo, "echo", "util", echo_schema())],
    )
    rec, req = _create_record(runner)
    rep = planner.execute(req)
    # max_iter=8:LLM 每轮发 echo,iteration 到 8 后停 → failed(goal 未达成)
    assert rep.metrics["planner_iterations"] == 8
    assert rep.status == "failed"


def test_build_messages_includes_system_user_and_tool_history(tmp_path: Path) -> None:
    """_build_messages 拼 [system, user, ...tool] 含 history tool 结果。"""
    echo = StubTool("echo", [_ok_parsed("echo", {"v": 1})], schema=echo_schema())
    planner, runner, llm, reg = _setup(
        tmp_path, tools=[(echo, "echo", "util", echo_schema())],
    )
    rec, req = _create_record(runner)
    state = PlannerState(rtl_path="a.v", tb_path="tb.v", goal="g", max_iter=5)
    # 注入一段 history:
    planner._execute_action(
        Action("echo", {}, llm_tool_call_id="tc_prev"), state, rec,
    )
    msgs = planner._build_messages(state, rec)
    # system + user + 1 tool:
    assert msgs[0].role == "system"
    assert msgs[1].role == "user"
    assert msgs[2].role == "tool"
    assert msgs[2].tool_call_id == "tc_prev"
    import json as _json
    payload = _json.loads(msgs[2].content)
    assert payload["status"] == "ok"
    assert payload["parsed"]["v"] == 1
