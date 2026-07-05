"""LLM provider 工厂(契约 §2.5 v1.2 行 688-698)。

按 ``settings.llm.provider`` 取实现,并**强制**用 ``CountingProvider`` 包装后返回
(契约 §2.5:工厂必须返回 ``CountingProvider`` 包装,``RunRecord`` 的 llm_calls /
tokens_in / tokens_out 计数依赖此装饰)。

支持的 provider:
- ``"glm"``(默认):智谱 GLM,OpenAI 兼容端点 open.bigmodel.cn,key 走 .env 的 GLM_API_KEY。
- ``"claude"``(备用):Anthropic Claude,key 走 .env 的 ANTHROPIC_API_KEY。
"""
from __future__ import annotations

from eda_agent.contracts import LLMProvider
from eda_agent.llm.claude_provider import ClaudeProvider
from eda_agent.llm.counting import CountingProvider
from eda_agent.llm.glm_provider import GLMProvider
from eda_agent.settings import Settings


def make_provider(settings: Settings) -> LLMProvider:
    """按 settings.llm.provider 构造被 ``CountingProvider`` 包装的 provider。"""
    name = settings.llm.provider
    if name == "glm":
        inner = GLMProvider(settings)
    elif name == "claude":
        inner = ClaudeProvider(settings)
    else:
        raise ValueError(f"unknown llm provider: {name}")
    return CountingProvider(inner)
