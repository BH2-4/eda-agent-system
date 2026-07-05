"""A 诊断器的 LLM prompt 构造(契约 §2.2 §5.4)。

只产 ``list[Message]``;实际调用与解析在 ``skills/diagnose.py`` 的 ``LLMAttributor``。
"""
from __future__ import annotations

from typing import Any

from eda_agent.contracts import Message

# 每个 tool 的日志末尾截取长度(prompt 体积保护)。
_LOG_TAIL_LEN = 2000

_SYSTEM = (
    "你是 EDA 诊断专家。给定结构化错误列表与工具日志,产出 JSON 对象,字段:"
    "root_cause(str),fix_hints(list[str]),needs_patch(bool),"
    "new_pattern(null 或 {regex,error_code,fix_hint_template})。"
    "只输出 JSON,不要解释。"
)


def build_diagnose_prompt(
    errors: list[dict],
    context: dict,
) -> list[Message]:
    """构造 A 诊断 LLM 的 system+user 消息。

    ``errors`` 是 ErrorItem.asdict 列表(规则层产出,可能为空);
    ``context`` 含 ``tool_results`` 与 ``logs: {tool_name: log_text}``。
    每个工具日志取末尾 ``_LOG_TAIL_LEN`` 字符,避免 prompt 爆长。
    """
    import json

    logs_raw = context.get("logs") or {}
    logs_tail: dict[str, str] = {}
    if isinstance(logs_raw, dict):
        for tool_name, text in logs_raw.items():
            s = text or ""
            logs_tail[str(tool_name)] = s[-_LOG_TAIL_LEN:] if s else ""

    user_payload: dict[str, Any] = {
        "errors": errors,
        "logs_tail_per_tool": logs_tail,
    }
    return [
        Message("system", _SYSTEM),
        Message("user", json.dumps(user_payload, ensure_ascii=False)),
    ]
