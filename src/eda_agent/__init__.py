"""Agentic EDA 系统(契约 CONTRACT_VERSION = 0.1.0)。

对外入口 run_pipeline / RunRequest 见 eda_agent.contracts;CLI 见 eda_agent.cli。
组件:A(skill_diagnose)/ B(skill_self_heal)/ C(CPlanner),契约权威见 CONTRACTS.md。
"""
from __future__ import annotations

from eda_agent.contracts import (
    ARTIFACT_FROM_STATE,
    CONTRACT_VERSION,
    LLMProvider,
    LLMResponse,
    Message,
    RunRecord,
    RunReport,
    RunRequest,
    Skill,
    SkillResult,
    StepRecord,
    Tool,
    ToolCall,
    ToolResult,
    artifact_ref,
)

__version__ = CONTRACT_VERSION

__all__ = [
    "CONTRACT_VERSION",
    "ARTIFACT_FROM_STATE",
    "artifact_ref",
    "RunRequest",
    "RunReport",
    "ToolCall",
    "ToolResult",
    "Tool",
    "SkillResult",
    "Skill",
    "RunRecord",
    "StepRecord",
    "Message",
    "LLMResponse",
    "LLMProvider",
    "__version__",
]
