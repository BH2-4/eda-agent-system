"""Anthropic Claude provider(契约 §2.5 v1.2)。

把统一的 ``Message`` / ``tools`` 中间格式翻译成 Anthropic SDK 的原生调用形状,
再把 ``messages.create`` 响应翻回 ``LLMResponse``。不捕获 API 异常(上层
``_call_llm_safe`` 处理,统一落 ``llm.llm_call_failed``)。

形状要点:
- ``role=="system"`` 的 Message 拼成顶层 ``system`` 字符串(Anthropic 不支持
  messages 数组里出现 system 角色),其余按顺序成 ``[{"role", "content"}]``。
- ``role=="tool"`` 的 Message 翻成 ``{"role":"user","content":[{"type":"tool_result",
  "tool_use_id":..., "content":...}]}``。
- ``tools`` 来自 ``registry.to_llm_tools`` 的 ``{"name","description","input_schema"}``,
  provider 层只做名称归一(``input_schema`` 优先,缺则退 ``schema``,再缺给空 object)。
"""
from __future__ import annotations

import os
from typing import Any

import anthropic

from eda_agent.contracts import LLMProvider, LLMResponse, Message
from eda_agent.settings import Settings


class ClaudeProvider:
    """``LLMProvider`` Protocol 的 Anthropic Claude 实现。"""

    provider_name = "claude"

    def __init__(self, settings: Settings, api_key: str | None = None) -> None:
        self._settings = settings
        self._model = settings.llm.claude_model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = anthropic.Anthropic(api_key=self._api_key)

    def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        # 默认值走 settings(契约 §2.5:provider 透传 settings.llm 的默认 temperature/max_tokens)。
        if temperature is None:
            temperature = self._settings.llm.temperature
        if max_tokens is None:
            max_tokens = self._settings.llm.max_tokens

        system_text, msgs = self._translate_messages(messages)
        tools_arg = self._translate_tools(tools)

        # SDK 不接受 tools=None 之外的空 list 语义被混用;显式按契约:空/None 不传 tools。
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system_text:
            kwargs["system"] = system_text
        if tools_arg:
            kwargs["tools"] = tools_arg

        resp = self._client.messages.create(**kwargs)
        return self._translate_response(resp)

    # ── 翻译辅助 ────────────────────────────────────────────────────────

    @staticmethod
    def _translate_messages(
        messages: list[Message],
    ) -> tuple[str, list[dict[str, Any]]]:
        """拆 system 角色到顶层字符串,其余按顺序成 SDK messages 数组。"""
        system_parts: list[str] = []
        msgs: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
            elif m.role == "tool":
                # Anthropic tool_result 走 user 角色 + content block 数组。
                msgs.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id,
                        "content": m.content,
                    }],
                })
            else:
                msgs.append({"role": m.role, "content": m.content})
        return ("\n\n".join(system_parts), msgs)

    @staticmethod
    def _translate_tools(tools: list[dict] | None) -> list[dict] | None:
        """registry.to_llm_tools 的中间格式 → Anthropic tools 数组。

        registry 输出形状 ``{"name","description","input_schema"}``,SDK 期望同名
        ``input_schema``;兼容 ``schema`` 别名,再缺则给空 object(满足 SDK 必填)。
        """
        if not tools:
            return None
        out: list[dict] = []
        for t in tools:
            schema = t.get("input_schema") or t.get("schema") or {
                "type": "object",
                "properties": {},
            }
            out.append({
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": schema,
            })
        return out

    @staticmethod
    def _translate_response(resp: Any) -> LLMResponse:
        """SDK 响应 content blocks → ``LLMResponse``。"""
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for b in resp.content:
            btype = getattr(b, "type", None)
            if btype == "text":
                text_parts.append(b.text)
            elif btype == "tool_use":
                tool_calls.append({
                    "id": b.id,
                    "name": b.name,
                    "args": b.input,
                })
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            tokens_in=resp.usage.input_tokens,
            tokens_out=resp.usage.output_tokens,
            provider="claude",
            model=resp.model,
            raw=None,
        )
