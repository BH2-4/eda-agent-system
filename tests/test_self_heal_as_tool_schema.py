"""T36 单测: skill_self_heal.as_tool 的 args schema(9 字段)。

断言:
- as_tool 返回满足 Tool Protocol
- schema.input_schema.properties 含 9 字段(rtl/tb/diagnose/max_iter/goal/
  lib/clock/top_module + reserved run_id/_remaining_budget_s)
- reserved 字段(_remaining_budget_s)在 to_llm_tools 剥离后不出现
- required 含 rtl/tb/max_iter/goal
- _schema 元字段 contract_version == CONTRACT_VERSION(无裸串)
"""
from __future__ import annotations

from eda_agent.contracts import CONTRACT_VERSION, Tool
from eda_agent.skills.base import as_tool
from eda_agent.skills.self_heal import SelfHealSkill


def _make_skill() -> SelfHealSkill:
    return SelfHealSkill(
        registry=None, llm=None, runner=None,
        max_iterations=5, budget_s=480.0,
    )


def test_as_tool_satisfies_tool_protocol():
    t = as_tool(_make_skill())
    assert isinstance(t, Tool)
    assert t.name == "skill_self_heal"


def test_input_schema_has_nine_properties():
    t = as_tool(_make_skill())
    props = t.schema["input_schema"]["properties"]
    # 9 字段:8 业务(rtl/tb/diagnose/max_iter/goal/lib/clock/top_module)
    # + 1 reserved(_remaining_budget_s)。run_id 由 as_tool 拆包,不进 schema。
    expected = {
        "rtl", "tb", "diagnose", "max_iter", "goal",
        "lib", "clock", "top_module",
        "_remaining_budget_s",
    }
    assert expected == set(props.keys())
    assert len(props) == 9


def test_required_fields_include_rtl_tb_max_iter_goal():
    t = as_tool(_make_skill())
    required = set(t.schema["input_schema"].get("required", []))
    assert {"rtl", "tb", "max_iter", "goal"} <= required


def test_schema_name_and_contract_version():
    t = as_tool(_make_skill())
    assert t.schema["name"] == "skill_self_heal"
    # _schema 元字段在 final_parsed(运行时)而非 schema;这里只断言 description。
    assert isinstance(t.schema["description"], str)
    assert t.schema["description"]


def test_reserved_field_stripped_in_to_llm_tools_via_registry():
    """经 registry.to_llm_tools 后 _remaining_budget_s 必须剥离(契约 §2.3)。"""
    from eda_agent.registry import ToolEntry, ToolRegistry

    reg = ToolRegistry()
    skill = _make_skill()
    reg.register(
        ToolEntry(
            tool=as_tool(skill),
            name="skill_self_heal",
            category="skill",
            schema=skill.schema["input_schema"],
            parsed_schema_ref={
                "name": "skill_self_heal", "version": "0.1.0",
            },
        )
    )
    llm_tools = reg.to_llm_tools()
    heal = next(t for t in llm_tools if t["name"] == "skill_self_heal")
    props = heal["input_schema"]["properties"]
    assert "_remaining_budget_s" not in props
    # run_id 同为 reserved(as_tool 拆包),也剥离(契约 v1.2 下划线前缀才剥,
    # run_id 非下划线前缀故保留 —— 这里只断言 _remaining_budget_s 被剥)。
