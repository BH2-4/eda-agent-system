"""智谱 GLM LLM Provider(契约 §2.5;OpenAI 兼容端点 bigmodel.cn)。

把统一 ``Message`` / ``tools`` 中间格式翻译成 OpenAI SDK 调用——智谱 GLM
全兼容 OpenAI 接口,仅 ``base_url`` 指向 ``open.bigmodel.cn/api/paas/v4``、
``api_key`` 用智谱 key(从 .env 的 ``GLM_API_KEY`` 读)。不捕获 API 异常
(上层 ``_call_llm_safe`` 处理,统一落 ``llm.llm_call_failed``)。

形状要点(OpenAI 格式与 Anthropic 的差异):
- ``role=="system"`` 的 Message **保留在 messages 数组**(OpenAI 支持 system 角色,
  Anthropic 需拆到顶层 ``system`` 字符串)。
- ``role=="tool"`` 的 Message 翻成 ``{"role":"tool","content":...,"tool_call_id":...}``
  (OpenAI 原生 tool 结果格式)。
- ``tools`` 来自 ``registry.to_llm_tools`` 的 ``{"name","description","input_schema"``,
  翻成 OpenAI 的 ``[{"type":"function","function":{"name","description","parameters"}}]``。
"""
from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI

from eda_agent.contracts import LLMProvider, LLMResponse, Message
from eda_agent.settings import Settings


class GLMProvider:
    """``LLMProvider`` Protocol 的智谱 GLM 实现(OpenAI 兼容)。"""

    provider_name = "glm"

    def __init__(self, settings: Settings, api_key: str | None = None) -> None:
        self._settings = settings
        self._model = settings.llm.glm_model
        self._api_key = api_key or os.environ.get(settings.llm.glm_api_key_env)
        if not self._api_key:
            raise RuntimeError(
                f"GLM API key 未设置:请在项目根 .env 文件填入 "
                f"{settings.llm.glm_api_key_env}=<your-zhipu-key>"
                f"(从 https://open.bigmodel.cn 控制台获取)"
            )
        self._client = OpenAI(
            api_key=self._api_key,
            base_url=settings.llm.glm_base_url,
        )

    def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        # 默认值走 settings(契约 §2.5:provider 透传 settings.llm 默认 temperature/max_tokens)。
        if temperature is None:
            temperature = self._settings.llm.temperature
        if max_tokens is None:
            max_tokens = self._settings.llm.max_tokens

        msgs = self._translate_messages(messages)
        tools_arg = self._translate_tools(tools)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools_arg:
            kwargs["tools"] = tools_arg
        # GLM-5.2 思考模式 + 推理努力档(智谱文档 migrate-to-glm-new)。
        # 用 extra_body 透传给 OpenAI SDK(避免 SDK 对非标准字段 thinking 的校验)。
        extra_body: dict[str, Any] = {}
        if self._settings.llm.glm_thinking:
            extra_body["thinking"] = {"type": "enabled"}
        if self._settings.llm.glm_reasoning_effort:
            extra_body["reasoning_effort"] = self._settings.llm.glm_reasoning_effort
        if extra_body:
            kwargs["extra_body"] = extra_body

        resp = self._client.chat.completions.create(**kwargs)
        return self._translate_response(resp)

    # ── 翻译辅助 ────────────────────────────────────────────────────────

    @staticmethod
    def _translate_messages(messages: list[Message]) -> list[dict[str, Any]]:
        """统一 Message → OpenAI messages 数组。

        OpenAI 支持 system 角色在 messages 数组里(不像 Anthropic 要拆顶层)。
        tool 角色翻成 ``{"role":"tool","content":...,"tool_call_id":...}``。
        """
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "tool":
                out.append({
                    "role": "tool",
                    "content": m.content,
                    "tool_call_id": m.tool_call_id or "",
                })
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    @staticmethod
    def _translate_tools(tools: list[dict] | None) -> list[dict] | None:
        """registry.to_llm_tools 中间格式 → OpenAI tools 数组。

        OpenAI 格式:``[{"type":"function","function":{"name","description","parameters"}}]``。
        registry 输出 ``input_schema``;兼容 ``schema`` 别名;缺则空 object(SDK 必填)。
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
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": schema,
                },
            })
        return out

    @staticmethod
    def _translate_response(resp: Any) -> LLMResponse:
        """OpenAI 响应 → ``LLMResponse``。

        OpenAI:``choices[0].message.content`` 是文本;``tool_calls[i].function.arguments``
        是 JSON 字符串(需解析为 dict)。usage 字段名 prompt_tokens/completion_tokens。
        """
        choice = resp.choices[0]
        msg = choice.message
        text = getattr(msg, "content", None) or ""

        tool_calls: list[dict] = []
        raw_tcs = getattr(msg, "tool_calls", None) or []
        for tc in raw_tcs:
            func = tc.function
            args: dict[str, Any] = {}
            raw_args = getattr(func, "arguments", None)
            if raw_args:
                try:
                    args = json.loads(raw_args)
                except (json.JSONDecodeError, ValueError):
                    args = {}
            tool_calls.append({
                "id": tc.id,
                "name": func.name,
                "args": args,
            })

        usage = getattr(resp, "usage", None)
        tokens_in = getattr(usage, "prompt_tokens", 0) if usage else 0
        tokens_out = getattr(usage, "completion_tokens", 0) if usage else 0

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            provider="glm",
            model=getattr(resp, "model", "glm"),
            raw=None,
        )
