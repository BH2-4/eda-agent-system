"""GLMProvider + factory(glm 分支)单测(契约 §2.5;不发真实请求)。

用 monkeypatch 替换 ``GLMProvider`` 实例的 ``self._client.chat.completions.create``,
用 ``types.SimpleNamespace`` 构造 fake response(OpenAI 格式)。覆盖:
- chat 返回 ``LLMResponse`` 字段映射(text/tool_calls/tokens/provider/model)。
- system 角色保留在 messages 数组(OpenAI 支持,与 Anthropic 不同)。
- tool message 翻译成 role="tool" + tool_call_id(OpenAI 原生格式)。
- tools 中间格式翻译成 OpenAI function 数组(type=function)。
- ``make_provider`` 返回 ``CountingProvider`` 包装,内层是 ``GLMProvider``。
- 无 API key 时报错(提示填 .env)。
"""
from __future__ import annotations

import types

import pytest

from eda_agent.contracts import LLMResponse, Message
from eda_agent.llm.counting import CountingProvider
from eda_agent.llm.factory import make_provider
from eda_agent.llm.glm_provider import GLMProvider
from eda_agent.settings import Settings


def _fake_response() -> types.SimpleNamespace:
    """模拟 OpenAI ``client.chat.completions.create`` 响应。"""
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(
                content="hi",
                tool_calls=[types.SimpleNamespace(
                    id="call_1",
                    function=types.SimpleNamespace(
                        name="yosys_synth",
                        arguments='{"rtl": "a.v"}',
                    ),
                )],
            ),
        )],
        usage=types.SimpleNamespace(prompt_tokens=15, completion_tokens=10),
        model="glm-5.2",
    )


def _make_provider_with_fake_create() -> tuple[GLMProvider, dict]:
    """构造 GLMProvider,把 ``_client.chat.completions.create`` 换成 capture mock。"""
    prov = GLMProvider(Settings(), api_key="test-key")
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _fake_response()

    # openai SDK:client.chat.completions.create
    prov._client.chat.completions.create = fake_create  # type: ignore[attr-defined]
    return prov, captured


# ── 字段映射 ────────────────────────────────────────────────────────────


def test_chat_returns_llmresponse_with_correct_fields() -> None:
    prov, _ = _make_provider_with_fake_create()
    resp = prov.chat([Message(role="user", content="hello")])

    assert isinstance(resp, LLMResponse)
    assert resp.text == "hi"
    assert resp.tool_calls == [
        {"id": "call_1", "name": "yosys_synth", "args": {"rtl": "a.v"}}
    ]
    assert resp.tokens_in == 15
    assert resp.tokens_out == 10
    assert resp.provider == "glm"
    assert resp.model == "glm-5.2"


# ── system 角色翻译(OpenAI:保留在 messages 数组)──────────────────────


def test_system_message_kept_in_messages_array() -> None:
    prov, captured = _make_provider_with_fake_create()
    prov.chat([
        Message(role="system", content="be terse"),
        Message(role="user", content="hi"),
    ])
    roles = [m["role"] for m in captured["messages"]]
    assert "system" in roles
    assert "user" in roles


# ── tool 角色翻译(OpenAI:role="tool" + tool_call_id)───────────────────


def test_tool_message_translated_to_tool_role() -> None:
    prov, captured = _make_provider_with_fake_create()
    prov.chat([
        Message(role="assistant", content=""),
        Message(role="tool", content="result-json", tool_call_id="call_1"),
    ])
    tool_msgs = [m for m in captured["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"] == "result-json"
    assert tool_msgs[0]["tool_call_id"] == "call_1"


# ── tools 翻译(OpenAI function 数组)───────────────────────────────────


def test_tools_translated_to_openai_function_array() -> None:
    prov, captured = _make_provider_with_fake_create()
    tools_in = [{
        "name": "yosys_synth",
        "description": "synth rtl",
        "input_schema": {"type": "object", "properties": {"rtl": {"type": "string"}}},
    }]
    prov.chat([Message(role="user", content="x")], tools=tools_in)

    tools_out = captured["tools"]
    assert tools_out[0]["type"] == "function"
    assert tools_out[0]["function"]["name"] == "yosys_synth"
    assert tools_out[0]["function"]["parameters"]["properties"]["rtl"]["type"] == "string"


def test_tools_schema_alias_fallback() -> None:
    prov, captured = _make_provider_with_fake_create()
    tools_in = [{"name": "t1", "schema": {"type": "object", "properties": {}}}]
    prov.chat([Message(role="user", content="x")], tools=tools_in)
    assert captured["tools"][0]["function"]["parameters"] == {
        "type": "object", "properties": {}
    }


def test_tools_empty_means_no_tools_kwarg() -> None:
    prov, captured = _make_provider_with_fake_create()
    prov.chat([Message(role="user", content="x")], tools=None)
    assert "tools" not in captured


# ── temperature/max_tokens 默认走 settings ──────────────────────────────


def test_temperature_max_tokens_default_from_settings() -> None:
    s = Settings()
    s.llm.temperature = 0.7
    s.llm.max_tokens = 2048
    prov = GLMProvider(s, api_key="test-key")
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _fake_response()

    prov._client.chat.completions.create = fake_create  # type: ignore[attr-defined]
    prov.chat([Message(role="user", content="x")])

    assert captured["temperature"] == 0.7
    assert captured["max_tokens"] == 2048


# ── tool_calls arguments JSON 解析 ───────────────────────────────────────


def test_tool_calls_arguments_json_parsed() -> None:
    prov, _ = _make_provider_with_fake_create()
    resp = prov.chat([Message(role="user", content="x")])
    # arguments 是 JSON 字符串,应解析成 dict
    assert resp.tool_calls[0]["args"] == {"rtl": "a.v"}


# ── provider_name ────────────────────────────────────────────────────────


def test_provider_name_is_glm() -> None:
    prov = GLMProvider(Settings(), api_key="test-key")
    assert prov.provider_name == "glm"


# ── 无 API key 报错(提示 .env)──────────────────────────────────────────


def test_glm_provider_missing_api_key_raises() -> None:
    mp = pytest.MonkeyPatch()
    mp.delenv("GLM_API_KEY", raising=False)
    try:
        with pytest.raises(RuntimeError, match="GLM_API_KEY"):
            GLMProvider(Settings())
    finally:
        mp.undo()


# ── factory:make_provider 默认返回 CountingProvider 包装 glm ────────────


def test_make_provider_default_returns_counting_wrapper_around_glm() -> None:
    mp = pytest.MonkeyPatch()
    mp.setenv("GLM_API_KEY", "test-key")
    try:
        wrapped = make_provider(Settings())   # 默认 provider=glm
    finally:
        mp.undo()

    assert isinstance(wrapped, CountingProvider)
    assert wrapped.provider_name == "glm"
    assert isinstance(wrapped._inner, GLMProvider)
