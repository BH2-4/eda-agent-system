"""ToolRegistry 注册/发现/to_llm_tools reserved 剥离(契约 §3)。

对应验收:A3 / A_registry / I11(Registry 侧)。
"""
from __future__ import annotations

import pytest

from eda_agent.contracts import Tool, ToolCall, ToolResult
from eda_agent.registry import ToolEntry, ToolRegistry, _strip_reserved_schema


class _Stub:
    """实现 Tool Protocol 的桩。"""

    def __init__(self, name="stub", desc="stub tool"):
        self.name = name
        self.description = desc
        self.schema = {}

    def __call__(self, call: ToolCall) -> ToolResult:
        return ToolResult(status="ok", exit_code=0, stdout="", stderr="",
                          parsed={}, artifacts=[], duration_s=0.0, tool=self.name)


def _entry(name="stub", category="util", schema=None, desc="stub tool"):
    t = _Stub(name=name, desc=desc)
    t.schema = schema if schema is not None else {}
    return ToolEntry(tool=t, name=name, category=category,
                     schema=t.schema,
                     parsed_schema_ref={"name": name, "version": "0.1.0"})


def test_register_and_get():
    reg = ToolRegistry()
    e = _entry("yosys_synth", "synth")
    reg.register(e)
    assert reg.get("yosys_synth") is e.tool          # get 返回 Tool 实例
    assert reg.get("nope") is None                    # 找不到返回 None(不抛)
    assert reg.get_entry("yosys_synth") is e


def test_get_returns_tool_not_entry():
    reg = ToolRegistry()
    e = _entry("t1", "util")
    reg.register(e)
    got = reg.get("t1")
    assert isinstance(got, _Stub)
    assert got is e.tool
    assert isinstance(got, Tool)                      # Tool Protocol


def test_list_and_category_filter():
    reg = ToolRegistry()
    reg.register(_entry("yosys_synth", "synth"))
    reg.register(_entry("iverilog_sim", "sim"))
    reg.register(_entry("opensta_timing", "sta"))
    assert set(reg.names()) == {"yosys_synth", "iverilog_sim", "opensta_timing"}
    assert len(reg.list()) == 3
    assert [e.name for e in reg.list(category="synth")] == ["yosys_synth"]
    assert reg.list(category="pnr") == []


def test_reregister_overwrites():
    reg = ToolRegistry()
    reg.register(_entry("t", "util", desc="v1"))
    reg.register(_entry("t", "util", desc="v2"))
    assert len(reg.list()) == 1
    assert reg.get("t").description == "v2"


def test_to_llm_tools_strips_reserved_fields():
    """v1.2: 下划线前缀 reserved 字段(_remaining_budget_s/_artifact_ref)不进 LLM 可见 schema。"""
    reg = ToolRegistry()
    schema = {
        "type": "object",
        "properties": {
            "rtl": {"type": "string"},
            "_remaining_budget_s": {"type": "number"},
            "_artifact_ref": {"type": "object"},
        },
        "required": ["rtl", "_remaining_budget_s"],
    }
    reg.register(_entry("skill_self_heal", "skill", schema=schema))
    tools = reg.to_llm_tools()
    assert len(tools) == 1
    t = tools[0]
    assert t["name"] == "skill_self_heal"
    assert t["description"] == "stub tool"
    props = t["input_schema"]["properties"]
    assert "rtl" in props
    assert "_remaining_budget_s" not in props
    assert "_artifact_ref" not in props
    assert "_remaining_budget_s" not in t["input_schema"].get("required", [])


def test_to_llm_tools_names_match_registry():
    reg = ToolRegistry()
    reg.register(_entry("yosys_synth", "synth"))
    reg.register(_entry("iverilog_sim", "sim"))
    names = [t["name"] for t in reg.to_llm_tools()]
    assert names == ["yosys_synth", "iverilog_sim"]


def test_strip_reserved_schema_edges():
    assert _strip_reserved_schema(None) == {"type": "object", "properties": {}}
    out = _strip_reserved_schema({"type": "object", "properties": {"a": 1, "_x": 2}})
    assert "_x" not in out["properties"] and out["properties"]["a"] == 1
