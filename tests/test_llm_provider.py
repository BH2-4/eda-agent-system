"""ClaudeProvider + factory 单测(契约 §2.5;不发真实请求)。

用 monkeypatch 替换 ``ClaudeProvider`` 实例的 ``self._client.messages.create``,
用 ``types.SimpleNamespace`` 构造 fake response。覆盖:
- chat 返回 ``LLMResponse`` 字段映射(text/tool_calls/tokens/provider/model)。
- system message 翻译到顶层 ``system`` 参数,messages 数组里不再含 system 角色。
- tool message 翻译成 user 角色 + tool_result block。
- tools 中间格式(input_schema / schema 别名 / 缺省)翻译成 Anthropic tools。
- ``make_provider`` 返回 ``CountingProvider`` 包装,内层是 ``ClaudeProvider``。
"""
from __future__ import annotations

import os
import types

import pytest

from eda_agent.contracts import LLMResponse, Message
from eda_agent.llm.claude_provider import ClaudeProvider
from eda_agent.llm.counting import CountingProvider
from eda_agent.llm.factory import make_provider
from eda_agent.settings import Settings


def _fake_response() -> types.SimpleNamespace:
    """模拟 ``client.messages.create`` 的响应对象(SDK 0.116 形状)。"""
    return types.SimpleNamespace(
        content=[
            types.SimpleNamespace(type="text", text="hi"),
            types.SimpleNamespace(
                type="tool_use", id="tu1", name="yosys_synth", input={"rtl": "a.v"}
            ),
        ],
        usage=types.SimpleNamespace(input_tokens=12, output_tokens=8),
        model="claude-x",
    )


def _make_provider_with_fake_create() -> tuple[ClaudeProvider, dict]:
    """构造一个 ``ClaudeProvider``,把 ``_client.messages.create`` 换成 capture mock。

    返回 (provider, captured_kwargs),后者记录最近一次 create 调用的 kwargs。
    """
    # 给一个 dummy key,避免环境里没有 ANTHROPIC_API_KEY 时 __init__ 报错。
    prov = ClaudeProvider(Settings(), api_key="test-key")

    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _fake_response()

    # messages 是 SDK 资源对象,直接替换其 create 方法。
    prov._client.messages.create = fake_create  # type: ignore[attr-defined]
    return prov, captured


# ── 字段映射 ────────────────────────────────────────────────────────────


def test_chat_returns_llmresponse_with_correct_fields() -> None:
    prov, _ = _make_provider_with_fake_create()
    resp = prov.chat([Message(role="user", content="hello")])

    assert isinstance(resp, LLMResponse)
    assert resp.text == "hi"
    assert resp.tool_calls == [
        {"id": "tu1", "name": "yosys_synth", "args": {"rtl": "a.v"}}
    ]
    assert resp.tokens_in == 12
    assert resp.tokens_out == 8
    assert resp.provider == "claude"
    assert resp.model == "claude-x"


# ── system 角色翻译 ─────────────────────────────────────────────────────


def test_system_message_translated_to_top_level_system_kwarg() -> None:
    prov, captured = _make_provider_with_fake_create()
    prov.chat([
        Message(role="system", content="be terse"),
        Message(role="user", content="hi"),
    ])

    # system 走顶层 system kwarg
    assert "system" in captured
    assert "be terse" in captured["system"]
    # messages 数组里不再含 system 角色
    assert all(m["role"] != "system" for m in captured["messages"])
    # user 角色保留
    assert any(m["role"] == "user" and m["content"] == "hi" for m in captured["messages"])


def test_no_system_message_means_no_system_kwarg() -> None:
    prov, captured = _make_provider_with_fake_create()
    prov.chat([Message(role="user", content="hi")])
    assert "system" not in captured


# ── tool 角色翻译 ────────────────────────────────────────────────────────


def test_tool_message_translated_to_tool_result_block() -> None:
    prov, captured = _make_provider_with_fake_create()
    prov.chat([
        Message(role="assistant", content=""),
        Message(role="tool", content="result-json", tool_call_id="tu1"),
    ])

    msgs = captured["messages"]
    # tool 角色应翻成 user + tool_result block
    tool_msgs = [m for m in msgs if m["role"] == "user" and isinstance(m["content"], list)]
    assert len(tool_msgs) == 1
    block = tool_msgs[0]["content"][0]
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "tu1"
    assert block["content"] == "result-json"


# ── tools 翻译 ──────────────────────────────────────────────────────────


def test_tools_translated_to_anthropic_tools() -> None:
    prov, captured = _make_provider_with_fake_create()
    tools_in = [{
        "name": "yosys_synth",
        "description": "synth rtl",
        "input_schema": {"type": "object", "properties": {"rtl": {"type": "string"}}},
    }]
    prov.chat([Message(role="user", content="x")], tools=tools_in)

    tools_out = captured["tools"]
    assert tools_out == [{
        "name": "yosys_synth",
        "description": "synth rtl",
        "input_schema": {"type": "object", "properties": {"rtl": {"type": "string"}}},
    }]


def test_tools_schema_alias_fallback() -> None:
    prov, captured = _make_provider_with_fake_create()
    tools_in = [{"name": "t1", "schema": {"type": "object", "properties": {}}}]
    prov.chat([Message(role="user", content="x")], tools=tools_in)
    assert captured["tools"][0]["input_schema"] == {
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
    prov = ClaudeProvider(s, api_key="test-key")
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _fake_response()

    prov._client.messages.create = fake_create  # type: ignore[attr-defined]
    prov.chat([Message(role="user", content="x")])

    assert captured["temperature"] == 0.7
    assert captured["max_tokens"] == 2048


# ── provider_name ────────────────────────────────────────────────────────


def test_provider_name_is_claude() -> None:
    prov = ClaudeProvider(Settings(), api_key="test-key")
    assert prov.provider_name == "claude"


# ── factory:make_provider 返回 CountingProvider 包装 ────────────────────


def test_make_provider_returns_counting_wrapper_around_claude() -> None:
    # 默认 provider=glm;显式切 claude 测 claude 分支。
    # make_provider 内部会 anthropic.Anthropic(api_key=...),给一个 dummy key 避免联网校验。
    monkeypatch_env = pytest.MonkeyPatch()
    monkeypatch_env.setenv("ANTHROPIC_API_KEY", "test-key")
    s = Settings()
    s.llm.provider = "claude"
    try:
        wrapped = make_provider(s)
    finally:
        monkeypatch_env.undo()

    assert isinstance(wrapped, CountingProvider)
    assert wrapped.provider_name == "claude"
    assert isinstance(wrapped._inner, ClaudeProvider)


def test_make_provider_unknown_provider_raises() -> None:
    s = Settings()
    s.llm.provider = "nope"
    with pytest.raises(ValueError):
        make_provider(s)
