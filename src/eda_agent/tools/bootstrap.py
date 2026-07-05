"""tools/bootstrap.py — build_registry 工厂(契约 §3 / §6.1 行 855)。

显式注册(MVP):集中 ``registry.register(...)``,一目了然(契约 §6.1 选显式注册,
避免装饰器扫描的隐式性)。

- L1 子进程 Tool(yosys/iverilog/opensta)只持 ``settings``,不持 provider。
- L2 Skill(skill_diagnose / skill_self_heal)持 ``provider`` + ``runner`` —— 在
  Phase3(T26 / T36)实现后于此补注册;本文件随 skill 落地逐步扩展。
"""
from __future__ import annotations

from eda_agent.contracts import LLMProvider
from eda_agent.registry import ToolEntry, ToolRegistry
from eda_agent.runner import Runner
from eda_agent.settings import Settings
from eda_agent.tools.iverilog_sim import IverilogSimTool
from eda_agent.tools.opensta_timing import OpenSTATimingTool
from eda_agent.tools.yosys_synth import YosysSynthTool

# 各 Tool parsed_schema_ref 的 version(契约 §2.2 parsed_schema_ref 形状)。
_SCHEMA_VERSION = "0.1.0"


def build_registry(
    provider: LLMProvider,
    runner: Runner,
    settings: Settings,
) -> ToolRegistry:
    """构造进程级 ``ToolRegistry``(契约 §6.1 行 855)。

    ``provider`` / ``runner`` 为 L2 Skill 的注入点(Phase3 用);L1 EDA Tool 只用
    ``settings``。签名三参数固定(契约硬规则,审计 blocker B2 已修),Phase3 在此函数
    末尾补 skill 注册时直接消费 provider/runner,不改签名。
    """
    registry = ToolRegistry()

    # ── L1 EDA 子进程 Tool(不持 provider)──────────────────────────────
    l1_tools = [
        (YosysSynthTool(settings), "yosys_synth", "synth"),
        (IverilogSimTool(settings), "iverilog_sim", "sim"),
        (OpenSTATimingTool(settings), "opensta_timing", "sta"),
    ]
    for tool, name, category in l1_tools:
        registry.register(
            ToolEntry(
                tool=tool,
                name=name,
                category=category,
                schema=tool.schema["input_schema"],
                parsed_schema_ref={"name": name, "version": _SCHEMA_VERSION},
            )
        )

    # ── L2 Skill(持 provider + runner)─────────────────────────────────
    # A 诊断器(T26):rule 层(ErrorKB 正则)+ 按需 LLM 归因,产 13 字段 parsed。
    from eda_agent.skills.base import as_tool
    from eda_agent.skills.diagnose import DiagnoseSkill, ErrorKB

    diag = DiagnoseSkill(
        kb=ErrorKB.load(settings.error_kb_path),
        llm=provider,
        runner=runner,
        settings=settings,
    )
    registry.register(
        ToolEntry(
            tool=as_tool(diag),
            name="skill_diagnose",
            category="skill",
            schema=diag.schema["input_schema"],
            parsed_schema_ref={"name": "skill_diagnose", "version": _SCHEMA_VERSION},
        )
    )
    # B 自修复(T36):持 registry(调 L1 Tool + skill_diagnose);必须在
    # skill_diagnose 注册之后(本函数内 diag 已注册)。
    from eda_agent.skills.self_heal import SelfHealSkill

    heal = SelfHealSkill(
        registry=registry,
        llm=provider,
        runner=runner,
        max_iterations=settings.skill_max_iterations,
        budget_s=float(settings.skill_self_heal_budget_s),
        settings=settings,
    )
    registry.register(
        ToolEntry(
            tool=as_tool(heal),
            name="skill_self_heal",
            category="skill",
            schema=heal.schema["input_schema"],
            parsed_schema_ref={"name": "skill_self_heal", "version": _SCHEMA_VERSION},
        )
    )

    return registry
