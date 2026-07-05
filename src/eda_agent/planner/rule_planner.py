"""Rule planner 数据结构与状态(T39-T40,契约 §6.1)。

定义 C 状态机用的可变上下文 ``PlannerState``、动作 ``Action``、计划 ``Plan``、
相位枚举 ``PlannerPhase``。``make_plan`` 与 ``rule_after_reflect`` 的逻辑实现
放在 ``c_planner.py``(因为依赖 ``Budget`` / ``ARTIFACT_FROM_STATE`` / ``PlannerState``
与 self._settings,作为 CPlanner 方法更自然;此处只放纯数据)。

契约硬规则:Action.args 里的 reserved 字段(``_remaining_budget_s`` / ``_artifact_ref``)
以下划线前缀豁免 schema 校验,``_save_plan`` 落盘时剥离(见 c_planner.py)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


# 相位机:PLANNING → EXECUTING → REFLECTING → REPORTING → DONE
PlannerPhase = Literal["PLANNING", "EXECUTING", "REFLECTING", "REPORTING", "DONE"]


@dataclass
class Action:
    """一次 Tool 调用意图(LLM 或 rule 产生)。

    - ``llm_tool_call_id``:LLM 发起时关联 ``tool_calls.id``;rule 发起为 None。
    - ``args`` 含 reserved 字段时(``_remaining_budget_s`` / ``_artifact_ref``),
      由 ``_resolve_args`` / ``_args_match_schema`` 处理(下划线前缀豁免)。
    """

    tool_name: str
    args: dict[str, Any]
    rationale: str = ""
    llm_tool_call_id: str | None = None


@dataclass
class Plan:
    """rule 模式的有序动作列表;llm 模式为空(actions=[])只标记 mode。

    llm 模式每步动作在 REFLECTING 相位由 ``_llm_next_action`` 即时产生,不入此 list。
    """

    actions: list[Action] = field(default_factory=list)
    mode: Literal["rule", "llm"] = "rule"


@dataclass
class PlannerState:
    """C 状态机的可变上下文(单一实例贯穿整个 execute)。

    - ``iteration``:已执行 Tool/Skill 次数(单一计数出口在 ``_execute_action`` 末尾)。
    - ``history``:每次执行的 ``{"call": ToolCall, "result": ToolResult}``,供 LLM
      回灌与 _collect_upstream_results 读。
    - ``netlist_ref``:yosys_synth 的 netlist artifact_ref(ARTIFACT_FROM_STATE 占位
      的解析目标)。
    - ``best_rtl_ref``:skill_self_heal 报 all_pass 后的 ``fixed_rtl_ref``,供独立
      验证步(_verify_after_self_heal)替换 rtl。
    - ``pending_self_heal_all_pass``:B 报 all_pass 但**未**经 C 独立验证;通过才
      ``goal_achieved=True``。
    """

    iteration: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    netlist_ref: dict[str, str] | None = None
    last_synth: dict[str, Any] | None = None
    last_sim: dict[str, Any] | None = None
    last_sta: dict[str, Any] | None = None
    last_diagnose: dict[str, Any] | None = None
    best_rtl_ref: dict[str, str] | None = None
    pending_self_heal_all_pass: bool = False
    independent_verify_passed: bool | None = None
    goal_achieved: bool = False
    fatal: bool = False
    rtl_path: str = ""
    tb_path: str | None = None
    top_module: str | None = None
    lib_path: str | None = None
    clock_name: str | None = None
    goal: str = ""
    max_iter: int = 5
