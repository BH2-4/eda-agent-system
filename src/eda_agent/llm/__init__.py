"""LLM provider 统一入口包(契约 §2.5)。

- base.py:re-export ``Message`` / ``LLMResponse`` / ``LLMProvider`` 自 ``contracts``。
- counting.py:``CountingProvider`` 装饰器 + ``LLMStats`` 计数 dataclass。
"""
from __future__ import annotations

from eda_agent.llm.base import LLMProvider, LLMResponse, Message
from eda_agent.llm.counting import CountingProvider, LLMStats

__all__ = ["Message", "LLMResponse", "LLMProvider", "LLMStats", "CountingProvider"]
