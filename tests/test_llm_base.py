"""LLM base re-export 与 CountingProvider 计数累加(契约 §2.5)。

对应验收:C2(re-export 不重定义)/ 计数器统一语义。不依赖网络/SDK:FakeProvider
实现 ``LLMProvider`` Protocol 返回固定 ``LLMResponse``。
"""
from __future__ import annotations

from eda_agent.contracts import (
    LLMProvider,
    LLMResponse,
    Message,
)
from eda_agent.llm.base import (
    LLMProvider as ReExportedLLMProvider,
    LLMResponse as ReExportedLLMResponse,
    Message as ReExportedMessage,
)
from eda_agent.llm.counting import CountingProvider, LLMStats


class _FakeProvider:
    """实现 LLMProvider Protocol;chat 永远返回固定响应。"""

    provider_name = "fake"

    def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        return LLMResponse(
            text="ok",
            tool_calls=[],
            tokens_in=10,
            tokens_out=5,
            provider="fake",
            model="fake-1",
        )


def test_base_reexports_same_symbols_as_contracts() -> None:
    """base.py 必须是 re-export:同名符号是同一对象(验收 C2,禁止重定义)。"""
    assert ReExportedMessage is Message
    assert ReExportedLLMResponse is LLMResponse
    assert ReExportedLLMProvider is LLMProvider


def test_counting_provider_passes_runtime_protocol_check() -> None:
    fake = _FakeProvider()
    wrapped = CountingProvider(fake)
    # CountingProvider 满足 LLMProvider Protocol(契约 §2.5)
    assert isinstance(wrapped, LLMProvider)


def test_provider_name_is_delegated_to_inner() -> None:
    wrapped = CountingProvider(_FakeProvider())
    assert wrapped.provider_name == "fake"


def test_counts_accumulate_over_repeated_calls() -> None:
    wrapped = CountingProvider(_FakeProvider())
    msg = Message(role="user", content="hi")
    for _ in range(3):
        wrapped.chat([msg])

    stats = wrapped.get_stats()
    assert stats.llm_calls == 3
    assert stats.tokens_in == 30
    assert stats.tokens_out == 15


def test_get_stats_returns_same_accumulating_object() -> None:
    wrapped = CountingProvider(_FakeProvider())
    msg = Message(role="user", content="hi")

    first = wrapped.get_stats()
    wrapped.chat([msg])
    wrapped.chat([msg])
    second = wrapped.get_stats()

    # 同一对象实例(持续累加,不每次新建)
    assert first is second
    assert second.llm_calls == 2
    assert second.tokens_in == 20
    assert second.tokens_out == 10


def test_llm_stats_default_zero() -> None:
    stats = LLMStats()
    assert stats.llm_calls == 0
    assert stats.tokens_in == 0
    assert stats.tokens_out == 0
