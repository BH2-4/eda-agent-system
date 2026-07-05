"""as_tool 适配层(契约 §2.3, contracts.py 行 152-159 权威注释)。

把任意 ``Skill Protocol`` 包装成 ``Tool Protocol``:拆包 reserved 字段
(``_remaining_budget_s`` / ``run_id``)、调 ``skill.run``、把 ``SkillResult``
映射成 ``ToolResult`` 并补六个 ``_skill_*`` 元字段。

不在本文件重定义 Skill / SkillResult / Tool / ToolCall / ToolResult / artifact_ref /
CONTRACT_VERSION,一律 ``from eda_agent.contracts import``(验收 C2)。
"""
from __future__ import annotations

from typing import Any

from eda_agent.contracts import (
    CONTRACT_VERSION,
    Skill,
    Tool,
    ToolCall,
    ToolResult,
)
from eda_agent.errors import EDA_BUDGET_EXHAUSTED


class SkillAdapter:
    """把 Skill 包成 Tool 的最简适配器(契约 §2.3)。

    实现 ``Tool Protocol``(``name`` / ``description`` / ``schema`` / ``__call__``)。
    持有原 skill 引用,``__call__`` 时拆包 reserved 字段并下传给 ``skill.run``。
    """

    def __init__(self, skill: Skill) -> None:
        self._skill = skill

    # ── Tool Protocol 属性 ───────────────────────────────────────────
    @property
    def name(self) -> str:
        return self._skill.name

    @property
    def description(self) -> str:
        return self._skill.description

    @property
    def schema(self) -> dict[str, Any]:
        """优先取 skill 自带 schema;若无则构造最小空 schema。

        reserved 字段(``_remaining_budget_s`` / ``_artifact_ref`` 等)即便存在于
        skill.schema 的 properties 中也不应进 LLM 可见 schema —— 它们由本适配器拆包,
        registry.to_llm_tools 会再次剥离下划线前缀(见 registry.py)。这里原样透传,
        剥离职责归 registry,避免双处维护。
        """
        own = getattr(self._skill, "schema", None)
        if own:
            return own
        return {
            "name": self._skill.name,
            "description": self._skill.description,
            "input_schema": {"type": "object", "properties": {}},
        }

    # ── Tool Protocol 调用 ───────────────────────────────────────────
    def __call__(self, call: ToolCall) -> ToolResult:
        # 1. 浅拷贝 args,避免污染调用方的 dict。
        args: dict[str, Any] = dict(call.args)
        # 2/3. 拆包 reserved:_remaining_budget_s 与 run_id 由本适配器消费。
        remaining = args.pop("_remaining_budget_s", None)
        run_id = args.pop("run_id", None)
        # 4. 剩余字段作为 skill inputs(_artifact_ref 等其他 reserved 保留给 skill 用)。
        inputs = args
        # 5. 调 skill.run。
        sr = self._skill.run(run_id=run_id, inputs=inputs, remaining_budget_s=remaining)

        # 6. SkillResult → ToolResult 映射(契约行 154-157)。
        status: str = "ok" if sr.status == "ok" else "error"
        # parsed 浅拷贝,补六个 _skill_* 元字段(不覆盖已有 _schema)。
        parsed: dict[str, Any] = dict(sr.final_parsed)
        parsed["_skill_status"] = sr.status
        parsed["_skill_iterations"] = sr.iterations
        parsed["_skill_trajectory"] = sr.trajectory
        parsed["_skill_patch_source"] = sr.patch_source
        parsed["_skill_convergence_cause"] = sr.convergence_cause
        parsed["_skill_best_iter"] = sr.best_iter

        # error_code:budget_exhausted 覆写为 eda.budget_exhausted(契约覆写);否则透传。
        if sr.status == "budget_exhausted":
            error_code: str | None = EDA_BUDGET_EXHAUSTED
        else:
            error_code = sr.error_code

        # error_hint:失败或预算耗尽时给一句话提示,便于 LLM 与日志追溯。
        if error_code is not None or sr.status != "ok":
            error_hint: str | None = f"skill {self._skill.name} status={sr.status}"
        else:
            error_hint = None

        return ToolResult(
            status=status,  # type: ignore[arg-type]
            exit_code=None,                 # skill 无子进程
            stdout="",                      # skill 无子进程
            stderr="",                      # skill 无子进程
            parsed=parsed,
            artifacts=list(sr.artifacts),   # 透传(元素已是 artifact_ref 形状)
            duration_s=float(sr.budget_used_s),
            tool=self._skill.name,
            error_code=error_code,
            error_hint=error_hint,
        )


def as_tool(skill: Skill) -> Tool:
    """把 ``Skill`` 包成 ``Tool``(契约 §2.3)。

    返回 ``SkillAdapter`` 实例;该实例满足 ``Tool Protocol``(runtime_checkable)。
    """
    return SkillAdapter(skill)
