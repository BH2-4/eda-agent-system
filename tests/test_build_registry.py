"""T19 + T26 + T36 单测: build_registry(契约 §6.1 行 855)。

L1 EDA Tool(3 个)+ A 诊断器 skill_diagnose + B 自修复 skill_self_heal
(L2 Skill)注册验证。provider/runner 传 None —— L1 Tool 不消费;
L2 Skill 构造签名能容忍 None runner / None provider(其 run 流程只在
真跑工具时才用到;构造期不触)。
"""
from __future__ import annotations

from eda_agent.contracts import Tool
from eda_agent.registry import ToolRegistry
from eda_agent.settings import Settings
from eda_agent.tools.bootstrap import build_registry


def _make_registry() -> ToolRegistry:
    return build_registry(provider=None, runner=None, settings=Settings())


def test_build_registry_returns_registry_instance():
    reg = _make_registry()
    assert isinstance(reg, ToolRegistry)


def test_build_registry_registers_three_l1_eda_tools_plus_two_skills():
    reg = _make_registry()
    names = set(reg.names())
    assert {"yosys_synth", "iverilog_sim", "opensta_timing"} <= names
    # T26:A 诊断器注册为 skill_diagnose。
    assert "skill_diagnose" in names
    # T36:B 自修复注册为 skill_self_heal。
    assert "skill_self_heal" in names
    # 共 5 个 tool(3 个 L1 + 2 个 L2 skill)。
    assert len(names) == 5


def test_registered_l1_tools_satisfy_tool_protocol():
    reg = _make_registry()
    for name in ("yosys_synth", "iverilog_sim", "opensta_timing"):
        tool = reg.get(name)
        assert tool is not None, f"{name} not registered"
        assert isinstance(tool, Tool)


def test_skill_diagnose_registered_as_skill_and_satisfies_tool_protocol():
    reg = _make_registry()
    entry = reg.get_entry("skill_diagnose")
    assert entry is not None
    assert entry.category == "skill"
    assert entry.parsed_schema_ref == {"name": "skill_diagnose", "version": "0.1.0"}
    # as_tool 后满足 Tool Protocol(runtime_checkable)。
    tool = reg.get("skill_diagnose")
    assert tool is not None
    assert isinstance(tool, Tool)
    assert tool.name == "skill_diagnose"


def test_skill_self_heal_registered_as_skill_and_satisfies_tool_protocol():
    reg = _make_registry()
    entry = reg.get_entry("skill_self_heal")
    assert entry is not None
    assert entry.category == "skill"
    assert entry.parsed_schema_ref == {
        "name": "skill_self_heal", "version": "0.1.0",
    }
    tool = reg.get("skill_self_heal")
    assert tool is not None
    assert isinstance(tool, Tool)
    assert tool.name == "skill_self_heal"


def test_to_llm_tools_exposes_five_with_required_fields():
    reg = _make_registry()
    llm_tools = reg.to_llm_tools()
    assert len(llm_tools) == 5
    names = {t["name"] for t in llm_tools}
    assert names == {
        "yosys_synth", "iverilog_sim", "opensta_timing",
        "skill_diagnose", "skill_self_heal",
    }
    for t in llm_tools:
        assert "description" in t
        assert "input_schema" in t
        assert isinstance(t["input_schema"], dict)
    # skill_diagnose / skill_self_heal 的 reserved 字段被剥离。
    diag = next(t for t in llm_tools if t["name"] == "skill_diagnose")
    props = diag["input_schema"].get("properties", {})
    assert "_remaining_budget_s" not in props
    heal = next(t for t in llm_tools if t["name"] == "skill_self_heal")
    hprops = heal["input_schema"].get("properties", {})
    assert "_remaining_budget_s" not in hprops


def test_entries_carry_category_and_parsed_schema_ref():
    reg = _make_registry()
    expected_categories = {
        "yosys_synth": "synth",
        "iverilog_sim": "sim",
        "opensta_timing": "sta",
        "skill_diagnose": "skill",
        "skill_self_heal": "skill",
    }
    for name, category in expected_categories.items():
        entry = reg.get_entry(name)
        assert entry is not None
        assert entry.category == category
        assert entry.parsed_schema_ref == {"name": name, "version": "0.1.0"}
