"""LLM provider 抽象的统一入口(re-export 自 ``eda_agent.contracts``,契约 §2.5)。

``Message`` / ``LLMResponse`` / ``LLMProvider`` 的权威定义在 ``eda_agent.contracts``
中,本模块仅作 re-export,禁止重定义同名结构(验收 C2)。所有调用方应
``from eda_agent.llm.base import Message, LLMResponse, LLMProvider``。
"""
from __future__ import annotations

from eda_agent.contracts import LLMProvider, LLMResponse, Message

__all__ = ["Message", "LLMResponse", "LLMProvider"]
