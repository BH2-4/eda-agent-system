"""report 渲染 helper(T39,可选独立模块;c_planner._write_report_md 内联实现)。

保留模块级 ``render`` 函数供外部(如 CLI/MCP)直接渲染已加载的 RunRecord。
当前 CPlanner 内部用 ``_write_report_md`` 直接落盘,本模块提供等价的纯函数版本,
便于在非 RunRecord 上下文(如重渲染历史 run)复用。
"""
from __future__ import annotations

from typing import Any

from eda_agent.planner.rule_planner import PlannerState


def render(
    run_id: str,
    state: PlannerState,
    status: str,
    metrics: dict[str, Any],
) -> str:
    """渲染 report.md 文本(纯函数,不落盘)。

    与 ``CPlanner._write_report_md`` 内容一致,便于重渲染。
    """
    lines: list[str] = []
    lines.append(f"# Run Report — {run_id}")
    lines.append("")
    lines.append(f"- status: `{status}`")
    lines.append(f"- goal: `{state.goal}`")
    lines.append(f"- goal_achieved: `{state.goal_achieved}`")
    lines.append(f"- planner_iterations: `{metrics.get('planner_iterations')}`")
    lines.append(f"- tool_calls_total: `{metrics.get('tool_calls_total')}`")
    lines.append(f"- llm_calls: `{metrics.get('llm_calls')}`")
    lines.append(f"- sim_passed: `{metrics.get('sim_passed')}`")
    lines.append(
        f"- self_heal_convergence: `{metrics.get('self_heal_convergence')}`"
    )
    wt = metrics.get("wall_time_s")
    wt_s = f"{wt:.2f}" if isinstance(wt, (int, float)) else str(wt)
    lines.append(f"- wall_time_s: `{wt_s}`")
    return "\n".join(lines)
