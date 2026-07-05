"""CountingProvider 装饰器(契约 §2.5 "计数器统一")。

包裹任意 ``LLMProvider``,每次 ``chat`` 透传并累加
``llm_calls`` / ``tokens_in`` / ``tokens_out`` 三项计数,供 ``Runner.finalize`` 读取
填入 ``RunRecord``(契约 §2.4 ``llm_calls`` / ``llm_tokens_in`` / ``llm_tokens_out``)。

不引入新的 LLM 抽象,``CountingProvider`` 是 ``LLMProvider`` Protocol 的透明装饰器。
"""
from __future__ import annotations

from dataclasses import dataclass

from eda_agent.contracts import LLMProvider, LLMResponse, Message


@dataclass
class LLMStats:
    """LLM 调用计数累加器(RunRecord.finalize 读)。"""

    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


class CountingProvider:
    """``LLMProvider`` 的计数装饰器。

    实现 ``LLMProvider`` Protocol:暴露 ``provider_name`` 透传给被包裹的 inner
    provider;``chat`` 透传并累加计数。``get_stats`` 返回内部累加器实例(同一对象,
    字段在多次调用间持续累加)。
    """

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner
        self._stats = LLMStats()

    @property
    def provider_name(self) -> str:
        return self._inner.provider_name

    def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        resp = self._inner.chat(
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self._stats.llm_calls += 1
        self._stats.tokens_in += resp.tokens_in
        self._stats.tokens_out += resp.tokens_out
        return resp

    def get_stats(self) -> LLMStats:
        return self._stats
