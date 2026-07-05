"""Tool Registry(契约 §3 v1.2)。

L3 单例。所有 Tool(EDA 工具封装 + A/B skill 的 as_tool 包装)在进程启动时注册进去,
C 只通过 Registry 取 Tool,不直接 import 具体实现。找不到返回 None,由调用方(C)
构造 ``eda.tool_not_found`` 的 ToolResult 回灌 LLM(不抛异常)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from eda_agent.contracts import Tool


@dataclass
class ToolEntry:
    tool: Tool                                       # 实际可调用 Tool(__call__(ToolCall)->ToolResult)
    name: str                                        # 唯一,如 "yosys_synth"
    category: str                                    # 开放 str,推荐前缀见下(非 Literal 约束)
    schema: dict[str, Any]                           # args 的 JSON Schema,给 LLM 用(不含 reserved 字段)
    parsed_schema_ref: dict[str, str]                # {"name": "yosys_synth", "version": "0.1.0"}
    display_category: str | None = None              # 可选,UI 分组用


# category 推荐前缀(开放,加新值无需改本文件,兑现开闭原则):
#   synth / sim / sta / pnr / layout / drc / skill / util


class ToolRegistry:
    """Tool 注册发现中心。进程启动时由 build_registry(provider, runner, settings) 填充。"""

    def __init__(self) -> None:
        self._entries: dict[str, ToolEntry] = {}

    def register(self, entry: ToolEntry) -> None:
        """显式注册(MVP 默认,见 bootstrap.py)。重名覆盖(后注册者赢,便于测试替换)。"""
        self._entries[entry.name] = entry

    def get(self, name: str) -> Tool | None:
        """按名取 Tool;找不到返回 None(C 据此构造 eda.tool_not_found 回灌)。"""
        e = self._entries.get(name)
        return e.tool if e is not None else None

    def get_entry(self, name: str) -> ToolEntry | None:
        """按名取完整 ToolEntry(需要 schema/parsed_schema_ref 时用)。"""
        return self._entries.get(name)

    def list(self, category: str | None = None) -> list[ToolEntry]:
        """列全部(或按 category 过滤)。"""
        if category is None:
            return list(self._entries.values())
        return [e for e in self._entries.values() if e.category == category]

    def names(self) -> list[str]:
        return list(self._entries.keys())

    def to_llm_tools(self) -> list[dict]:
        """把所有 Tool 的 schema 转成 LLM provider 接受的工具描述列表。

        输出每个 tool 名与 Registry name 一致(LLM 幻觉调用由 C 的 registry.get 拦截)。
        v1.2:剥离下划线前缀 reserved 字段(如 ``_remaining_budget_s`` / ``_artifact_ref``),
        不进 LLM 可见 schema。

        输出元素形状(provider 无关中间格式,provider 层再翻成 Anthropic/OpenAI 原生):
            {"name": str, "description": str, "input_schema": <json schema, 无 _ 前缀字段>}
        """
        out: list[dict] = []
        for entry in self._entries.values():
            stripped = _strip_reserved_schema(entry.schema)
            out.append({
                "name": entry.name,
                "description": entry.tool.description,   # description 在 Tool Protocol 上(§2.1)
                "input_schema": stripped,
            })
        return out


def _strip_reserved_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    """剥离下划线前缀字段(reserved: _remaining_budget_s / _artifact_ref)。

    §2.3 args schema 校验规则:下划线前缀字段豁免 jsonschema 校验,由调用方处理;
    不进 LLM 可见 schema。处理 properties 与 required 两个键。
    """
    if not schema:
        return {"type": "object", "properties": {}}
    out = dict(schema)
    props = schema.get("properties")
    if isinstance(props, dict):
        out["properties"] = {k: v for k, v in props.items() if not k.startswith("_")}
    required = schema.get("required")
    if isinstance(required, list):
        out["required"] = [r for r in required if not str(r).startswith("_")]
    return out
