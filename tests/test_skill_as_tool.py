"""tests/test_skill_as_tool.py —— as_tool 适配层单测(契约 §2.3)。

不依赖任何真实 skill:用 fake 类实现 Skill Protocol,断言 reserved 字段拆包、
SkillResult → ToolResult 映射、_skill_* 元字段注入、budget_exhausted 覆写、
error 透传等语义。
"""
from __future__ import annotations

from typing import Any

from eda_agent.contracts import (
    CONTRACT_VERSION,
    Skill,
    Tool,
    ToolCall,
    SkillResult,
    artifact_ref,
)
from eda_agent.errors import EDA_BUDGET_EXHAUSTED
from eda_agent.skills import as_tool


# ── Fake Skill 实现 ──────────────────────────────────────────────────
class _FakeSkill:
    """实现 Skill Protocol;记录 run 的入参供断言。"""

    def __init__(
        self,
        *,
        name: str = "skill_x",
        description: str = "d",
        max_iterations: int = 1,
        budget_s: float = 60.0,
        schema: dict[str, Any] | None = None,
        result: SkillResult | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.max_iterations = max_iterations
        self.budget_s = budget_s
        self.schema = schema or {
            "name": name,
            "description": description,
            "input_schema": {"type": "object", "properties": {}},
        }
        self._result = result
        # 记录最后一次 run 的入参:
        self.last_remaining: float | None = None
        self.last_inputs: dict[str, Any] | None = None
        self.last_run_id: str | None = None

    def run(
        self,
        run_id: str,
        inputs: dict[str, Any],
        remaining_budget_s: float | None = None,
    ) -> SkillResult:
        self.last_run_id = run_id
        self.last_inputs = inputs
        self.last_remaining = remaining_budget_s
        assert self._result is not None
        return self._result


def _ok_result() -> SkillResult:
    return SkillResult(
        status="ok",
        iterations=2,
        final_parsed={
            "_schema": {
                "name": "skill_x",
                "version": "0.1.0",
                "contract_version": CONTRACT_VERSION,
            },
            "foo": "bar",
        },
        trajectory=[{"step": "t"}],
        artifacts=[artifact_ref("rid", "x.v")],
        summary="ok",
        patch_source="none",
        convergence_cause="all_pass",
        best_iter=1,
        error_code=None,
        budget_used_s=12.5,
    )


# ── 测试 ─────────────────────────────────────────────────────────────
def test_as_tool_returns_tool_and_basic_mapping():
    fake = _FakeSkill(result=_ok_result())
    t = as_tool(fake)

    # Tool Protocol(runtime_checkable)与 name:
    assert isinstance(t, Tool)
    assert t.name == "skill_x"
    assert t.description == "d"

    r = t(ToolCall(
        name="skill_x",
        args={
            "run_id": "rid",
            "_remaining_budget_s": 30.0,
            "rtl": "a.v",
        },
    ))

    # reserved 字段拆包:
    assert fake.last_remaining == 30.0
    assert fake.last_run_id == "rid"
    assert fake.last_inputs is not None
    assert "rtl" in fake.last_inputs
    assert "_remaining_budget_s" not in fake.last_inputs  # 已 pop
    assert "run_id" not in fake.last_inputs                # 已 pop

    # ToolResult 基本字段:
    assert r.status == "ok"
    assert r.tool == "skill_x"
    assert r.exit_code is None
    assert r.duration_s == 12.5
    assert r.artifacts == [{"run_id": "rid", "rel_path": "x.v"}]

    # parsed:原字段保留 + 六个 _skill_* 元字段:
    assert r.parsed["foo"] == "bar"
    assert r.parsed["_skill_status"] == "ok"
    assert r.parsed["_skill_iterations"] == 2
    assert r.parsed["_skill_best_iter"] == 1
    assert r.parsed["_skill_convergence_cause"] == "all_pass"
    assert r.parsed["_skill_patch_source"] == "none"
    assert "_skill_trajectory" in r.parsed
    assert r.parsed["_skill_trajectory"] == [{"step": "t"}]
    # _schema 未被覆盖:
    assert r.parsed["_schema"]["contract_version"] == CONTRACT_VERSION

    # ok 时无 error_code / error_hint:
    assert r.error_code is None
    assert r.error_hint is None


def test_as_tool_budget_exhausted_overrides_error_code():
    sr = SkillResult(
        status="budget_exhausted",
        iterations=5,
        final_parsed={"_schema": {"name": "skill_x", "version": "0.1.0",
                                  "contract_version": CONTRACT_VERSION}},
        trajectory=[],
        artifacts=[],
        summary="budget out",
        patch_source="none",
        convergence_cause="budget",
        best_iter=-1,
        error_code=None,                # 即使原 None 也覆写
        budget_used_s=60.0,
    )
    fake = _FakeSkill(result=sr)
    t = as_tool(fake)
    r = t(ToolCall(name="skill_x", args={"run_id": "rid"}))

    assert r.status == "error"
    assert r.error_code == EDA_BUDGET_EXHAUSTED  # 覆写
    assert r.duration_s == 60.0
    assert r.error_hint is not None and "skill_x" in r.error_hint
    # 元字段仍注入:
    assert r.parsed["_skill_status"] == "budget_exhausted"
    assert r.parsed["_skill_convergence_cause"] == "budget"


def test_as_tool_error_status_passes_through_error_code():
    sr = SkillResult(
        status="error",
        iterations=3,
        final_parsed={"_schema": {"name": "skill_x", "version": "0.1.0",
                                  "contract_version": CONTRACT_VERSION}},
        trajectory=[],
        artifacts=[],
        summary="deadlock",
        patch_source="llm_full_rewrite",
        convergence_cause="regression",
        best_iter=2,
        error_code="heal.regression_deadlock",   # 透传
        budget_used_s=20.0,
    )
    fake = _FakeSkill(result=sr)
    t = as_tool(fake)
    r = t(ToolCall(name="skill_x", args={"run_id": "rid"}))

    assert r.status == "error"
    assert r.error_code == "heal.regression_deadlock"  # 透传,不覆写
    assert r.error_hint is not None
    assert r.parsed["_skill_patch_source"] == "llm_full_rewrite"


def test_as_tool_schema_fallback_when_skill_has_no_schema():
    # 没 schema 属性的 fake:删除属性以触发 getattr None 分支。
    fake = _FakeSkill(result=_ok_result())
    del fake.schema  # type: ignore[attr-defined]

    t = as_tool(fake)
    s = t.schema
    assert s["name"] == "skill_x"
    assert s["description"] == "d"
    assert s["input_schema"] == {"type": "object", "properties": {}}


def test_as_tool_args_without_reserved_defaults_to_none():
    # 不传 run_id / _remaining_budget_s:应不抛、传 None 给 skill.run。
    fake = _FakeSkill(result=_ok_result())
    t = as_tool(fake)
    t(ToolCall(name="skill_x", args={"rtl": "a.v"}))
    assert fake.last_run_id is None
    assert fake.last_remaining is None
    assert fake.last_inputs == {"rtl": "a.v"}
