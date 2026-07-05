"""LLM 系统提示与任务渲染(T43,契约 §6.2)。

LLM 模式 ReAct 回环用:``_build_messages`` 拼 ``[system, user, ...tool]`` 数组。
MVP 不做多轮上下文压缩;每次重建 messages(包含全部 history tool 结果)。
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eda_agent.planner.rule_planner import PlannerState
    from eda_agent.runner import RunRecord

# 系统提示(契约 §6.2):明确字段读取约定,降低 LLM 自由度。
SYSTEM_PROMPT = (
    "你是 EDA 修复编排 agent。可用工具经 tools 参数给出。"
    "根据用户目标(让 RTL 通过 TB 测试)与各工具返回的结构化结果,"
    "逐步选择下一个工具调用。"
    "读 parsed.passed(iverilog_sim)/parsed.success(yosys_synth)"
    "/parsed.wns(opensta_timing)"
    "/_skill_convergence_cause(skill_self_heal) 等字段决策。"
    "每次只发一个 tool_call。"
    "目标达成(仿真 passed=True 且已独立验证)后不再发 tool_call。"
)


def _render_task(state: "PlannerState", record: "RunRecord") -> str:
    """渲染 user message(任务上下文 JSON)。

    含 goal + RTL/TB 路径 + 可选 lib/clock;不含 history(history 走 tool 角色)。
    """
    payload = {
        "run_id": record.run_id,
        "goal": state.goal,
        "rtl": state.rtl_path,
        "tb": state.tb_path,
        "top_module": state.top_module,
        "lib": state.lib_path,
        "clock": state.clock_name,
    }
    return json.dumps(payload, ensure_ascii=False, default=str)
