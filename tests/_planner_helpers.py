"""tests/_planner_helpers.py —— CPlanner 单测共享 stub 与 fake。

集中放:
- ``StubTool``:返回固定 ToolResult 的可配置 Tool(满足 Tool Protocol)。
- ``FakeLLMProvider``:按预设队列返回 LLMResponse 的 fake provider。
- ``make_settings`` / ``make_registry``:构造测试用 settings 与最小 registry。

不进 ``__init__`` 的 conftest(避免污染其他测试);各测试文件按需 import。
"""
from __future__ import annotations

from typing import Any

from eda_agent.contracts import (
    CONTRACT_VERSION,
    LLMProvider,
    LLMResponse,
    Message,
    Tool,
    ToolCall,
    ToolResult,
    artifact_ref,
)
from eda_agent.registry import ToolEntry, ToolRegistry
from eda_agent.settings import Settings


# ── StubTool ──────────────────────────────────────────────────────────
class StubTool:
    """可配置返回 ToolResult 的 stub Tool(满足 Tool Protocol)。

    ``results`` 是按调用顺序消费的列表;若设 ``loop_last=True``,列表消费完后
    重复返回最后一个元素(便于 e2e 不预先估步数)。
    """

    def __init__(
        self,
        name: str,
        results: list[ToolResult],
        *,
        description: str = "stub",
        schema: dict[str, Any] | None = None,
        loop_last: bool = True,
    ) -> None:
        self.name = name
        self.description = description
        self.results = list(results)
        self._idx = 0
        self.loop_last = loop_last
        self.calls: list[ToolCall] = []
        # 默认 schema:接受任意属性(便于不传 schema 时通过 _args_match_schema)
        self.schema = schema or {
            "name": name,
            "description": description,
            "input_schema": {"type": "object", "properties": {}},
        }

    def __call__(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        if self._idx < len(self.results):
            r = self.results[self._idx]
            self._idx += 1
            return r
        if self.loop_last and self.results:
            return self.results[-1]
        # 无可返回:给一个 ok 占位(测试不应走到这)
        return _ok_parsed(self.name, {})


def _ok_parsed(tool: str, parsed: dict[str, Any]) -> ToolResult:
    """构造一个 status=ok 的 ToolResult,自动补 _schema 元字段。"""
    full = {"_schema": {"name": tool, "version": "0.1.0",
                        "contract_version": CONTRACT_VERSION}}
    full.update(parsed)
    return ToolResult(
        status="ok",
        exit_code=0,
        stdout="",
        stderr="",
        parsed=full,
        artifacts=[],
        duration_s=0.001,
        tool=tool,
        error_code=None,
        error_hint=None,
    )


def synth_ok(netlist_rel: str = "synth/netlist.v") -> ToolResult:
    """yosys_synth 成功结果(含 netlist artifact)。"""
    r = _ok_parsed(
        "yosys_synth",
        {"success": True, "num_cells": 42, "num_wires": 10},
    )
    # artifact_ref 需要 run_id;测试里用占位 run_id,实际路径解析依赖 record.run_id
    r.artifacts.append(artifact_ref("<run_id>", netlist_rel))
    return r


def sim_result(passed: bool, num_passed: int = 0, num_failed: int = 0) -> ToolResult:
    """iverilog_sim 结果。"""
    return _ok_parsed(
        "iverilog_sim",
        {
            "passed": passed,
            "num_passed": num_passed,
            "num_failed": num_failed,
        },
    )


def self_heal_all_pass(fixed_rel: str = "self_heal/best/rtl.v") -> ToolResult:
    """skill_self_heal 报 all_pass 的结果(含 fixed_rtl_ref)。"""
    r = _ok_parsed(
        "skill_self_heal",
        {
            "passed": True,
            "iterations": 1,
            "best_iter": 0,
            "convergence_cause": "all_pass",
            "patch_source": "llm_full_rewrite",
            "fixed_rtl_ref": artifact_ref("<run_id>", fixed_rel),
        },
    )
    return r


def diagnose_needs_patch() -> ToolResult:
    """skill_diagnose 结果(needs_rtl_patch=True)。"""
    return _ok_parsed(
        "skill_diagnose",
        {
            "skill": "diagnose",
            "stage": "sim",
            "root_cause_summary": "test failure",
            "needs_rtl_patch": True,
            "root_causes": [
                {
                    "code": "sim.fail_signal",
                    "namespace": "sim",
                    "tool": "iverilog_sim",
                    "severity": "error",
                    "message": "expected 1 got 0",
                    "evidence": ["test.v:42: 1 != 0"],
                    "fix_suggestion": "fix assignment",
                }
            ],
        },
    )


# ── FakeLLMProvider ───────────────────────────────────────────────────
class FakeLLMProvider:
    """按预设队列返回 LLMResponse 的 fake provider(满足 LLMProvider Protocol)。

    ``responses`` 按调用顺序消费;``raise_on_call`` 触发异常以测试降级。
    """

    def __init__(
        self,
        responses: list[LLMResponse],
        *,
        provider_name: str = "fake",
        raise_on_call: bool = False,
    ) -> None:
        self._responses = list(responses)
        self._idx = 0
        self._provider_name = provider_name
        self._raise = raise_on_call
        self.calls: list[dict[str, Any]] = []

    @property
    def provider_name(self) -> str:
        return self._provider_name

    def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self.calls.append(
            {"messages": messages, "tools": tools,
             "temperature": temperature, "max_tokens": max_tokens}
        )
        if self._raise:
            raise RuntimeError("fake llm error")
        if self._idx < len(self._responses):
            r = self._responses[self._idx]
            self._idx += 1
            return r
        # 默认:返回空 tool_calls(让主循环转 REPORTING)
        return LLMResponse(
            text="",
            tool_calls=[],
            tokens_in=0,
            tokens_out=0,
            provider=self._provider_name,
            model="fake-model",
        )


# ── 工厂 ──────────────────────────────────────────────────────────────
def make_settings(*, mode: str = "rule", run_budget_s: int = 600,
                  max_iter: int = 8) -> Settings:
    """构造测试用 Settings(rule 模式默认,可覆盖)。"""
    s = Settings()
    s.planner.mode = mode
    s.budget.run_budget_s = run_budget_s
    s.budget.planner_max_iterations = max_iter
    return s


def make_registry(tools: list[tuple[Tool, str, str, dict]]) -> ToolRegistry:
    """从 (tool, name, category, schema) 列表构造 ToolRegistry。"""
    reg = ToolRegistry()
    for tool, name, category, schema in tools:
        reg.register(
            ToolEntry(
                tool=tool,
                name=name,
                category=category,
                schema=schema,
                parsed_schema_ref={"name": name, "version": "0.1.0"},
            )
        )
    return reg


def echo_schema() -> dict[str, Any]:
    """宽松 schema:接受任意属性(便于 stub 通过 _args_match_schema)。"""
    return {
        "name": "echo",
        "description": "echo stub",
        "input_schema": {"type": "object", "properties": {}},
    }
