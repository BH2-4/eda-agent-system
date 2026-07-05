"""契约 dataclass 完整性 + _schema 元字段 + artifact_ref 工厂 + namespace 登记。

对应验收:A2 / A_contracts / A_artifact_ref / A_CONTRACT_VERSION / A_error_codes /
A_diagnose_namespace / A_heal_namespace / I11 / I12(test_namespace_registry 并入本文件)。
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass

import pytest

from eda_agent.contracts import (
    ARTIFACT_FROM_STATE,
    CONTRACT_VERSION,
    LLMProvider,
    LLMResponse,
    Message,
    RunRecord,
    RunReport,
    RunRequest,
    Skill,
    SkillResult,
    StepRecord,
    Tool,
    ToolCall,
    ToolResult,
    artifact_ref,
)
from eda_agent.errors import (
    DIAGNOSE_ARGS_INVALID,
    DIAGNOSE_NO_ERROR_FOUND,
    EDA_BUDGET_EXHAUSTED,
    EDA_INTERNAL,
    EDA_TOOL_NOT_FOUND,
    EDA_TOOL_ARGS_INVALID,
    ErrorItem,
    HEAL_REDUCED_TO_DIAGNOSE,
    HEAL_UNSUPPORTED_GOAL,
    NAMESPACES,
    SEVERITY_BY_CODE,
    namespace_of,
    severity_of,
)


def _field_names(cls):
    return {f.name for f in fields(cls)}


# ── 版本锚点 + 工厂(契约 §2 顶部) ────────────────────────────────────


def test_contract_version():
    assert CONTRACT_VERSION == "0.1.0"


def test_artifact_factory_shape():
    ref = artifact_ref("20260715_103022_a3f1", "synth/netlist.v")
    assert ref == {"run_id": "20260715_103022_a3f1", "rel_path": "synth/netlist.v"}
    assert set(ref) == {"run_id", "rel_path"}


def test_artifact_from_state_constant():
    assert ARTIFACT_FROM_STATE == "<from_state>"


# ── RunRequest(对外入口,frozen) ─────────────────────────────────────


def test_run_request_fields():
    assert _field_names(RunRequest) == {
        "kind", "goal", "rtl_path", "tb_path", "top_module",
        "lib_path", "clock_name", "max_iter", "extra",
    }


def test_run_request_frozen():
    r = RunRequest(kind="self_heal", goal="pass all tests", rtl_path="x.v")
    with pytest.raises(FrozenInstanceError):
        r.goal = "x"


def test_run_request_defaults():
    r = RunRequest(kind="diagnose", goal="g", rtl_path="x.v")
    assert r.tb_path is None and r.max_iter is None and r.extra == {}


# ── RunReport ────────────────────────────────────────────────────────


def test_run_report_fields():
    assert _field_names(RunReport) == {"run_id", "status", "report_path", "summary", "metrics"}


# ── ToolCall / ToolResult / Tool Protocol ────────────────────────────


def test_tool_call_fields():
    assert _field_names(ToolCall) == {"name", "args", "caller", "llm_tool_call_id"}
    c = ToolCall(name="yosys_synth", args={})
    assert c.caller == "planner" and c.llm_tool_call_id is None


def test_tool_result_fields_and_is_ok():
    assert _field_names(ToolResult) == {
        "status", "exit_code", "stdout", "stderr", "parsed",
        "artifacts", "duration_s", "tool", "error_code", "error_hint",
    }
    ok = ToolResult(status="ok", exit_code=0, stdout="", stderr="", parsed={},
                    artifacts=[], duration_s=0.0, tool="t")
    err = ToolResult(status="error", exit_code=1, stdout="", stderr="", parsed={},
                     artifacts=[], duration_s=0.0, tool="t")
    assert ok.is_ok() and not err.is_ok()


def test_tool_protocol_runtime_checkable():
    class T:
        name = "t"
        description = "d"
        schema = {}
        def __call__(self, call):
            return ToolResult(status="ok", exit_code=0, stdout="", stderr="",
                              parsed={}, artifacts=[], duration_s=0.0, tool="t")
    assert isinstance(T(), Tool)
    assert not isinstance("not a tool", Tool)


# ── SkillResult(A 恒定语义默认) ─────────────────────────────────────


def test_skill_result_fields():
    assert _field_names(SkillResult) == {
        "status", "iterations", "final_parsed", "trajectory", "artifacts",
        "summary", "patch_source", "convergence_cause", "best_iter",
        "error_code", "budget_used_s",
    }


def test_skill_result_a_constant_defaults():
    """A(诊断器)恒定语义:patch_source/convergence_cause='none',best_iter=-1(§2.3)。"""
    sr = SkillResult(status="ok", iterations=1, final_parsed={},
                     trajectory=[], artifacts=[], summary="")
    assert sr.patch_source == "none"
    assert sr.convergence_cause == "none"
    assert sr.best_iter == -1
    assert sr.budget_used_s == 0.0


def test_skill_protocol_runtime_checkable():
    assert hasattr(Skill, "run") and hasattr(Skill, "as_tool")


# ── RunRecord / StepRecord(工件存储 §2.4) ───────────────────────────


def test_step_record_fields():
    assert _field_names(StepRecord) == {
        "index", "tool_name", "tool_call_path", "tool_result_path",
        "started_at", "duration_s", "status", "skill_name", "iter",
    }


def test_run_record_fields_and_contract_version():
    assert _field_names(RunRecord) == {
        "run_id", "request", "created_at", "status", "steps",
        "final_report_path", "total_duration_s", "llm_calls",
        "llm_tokens_in", "llm_tokens_out", "provider_used",
        "contract_version", "config_snapshot",
    }
    rec = RunRecord(run_id="r", request={}, created_at="t", status="running",
                    steps=[], final_report_path=None, total_duration_s=0.0,
                    llm_calls=0, llm_tokens_in=0, llm_tokens_out=0,
                    provider_used="claude", contract_version=CONTRACT_VERSION,
                    config_snapshot={})
    assert rec.contract_version == CONTRACT_VERSION


# ── Message / LLMResponse / LLMProvider(§2.5) ──────────────────────


def test_message_tool_role():
    assert _field_names(Message) == {"role", "content", "tool_call_id"}
    m = Message(role="tool", content="{}", tool_call_id="tc1")
    assert m.tool_call_id == "tc1"


def test_llm_response_fields():
    assert _field_names(LLMResponse) == {
        "text", "tool_calls", "tokens_in", "tokens_out", "provider", "model", "raw",
    }


def test_llm_provider_protocol():
    assert hasattr(LLMProvider, "chat")


# ── errors: namespace 登记(I11)+ severity 表 + ErrorItem(§2.6) ────


def test_namespace_registry():
    """I11: namespace 登记表含 diagnose(A)+ heal(B)。"""
    for ns in ("eda", "synth", "sim", "sta", "llm", "diagnose", "heal"):
        assert ns in NAMESPACES, ns


def test_namespace_of_helper():
    assert namespace_of(HEAL_UNSUPPORTED_GOAL) == "heal"
    assert namespace_of(DIAGNOSE_NO_ERROR_FOUND) == "diagnose"
    assert namespace_of(EDA_TOOL_NOT_FOUND) == "eda"


def test_severity_mapping():
    """severity 与 code 对应表(§2.6)。"""
    assert severity_of(EDA_BUDGET_EXHAUSTED) == "warn"
    assert severity_of(EDA_INTERNAL) == "fatal"
    assert severity_of(EDA_TOOL_NOT_FOUND) == "error"
    assert severity_of(EDA_TOOL_ARGS_INVALID) == "error"
    assert severity_of(HEAL_REDUCED_TO_DIAGNOSE) == "info"
    assert severity_of(HEAL_UNSUPPORTED_GOAL) == "error"
    assert severity_of(DIAGNOSE_NO_ERROR_FOUND) == "warn"
    # 未登记码默认 error(保守归错)
    assert severity_of("unknown.whatever") == "error"


def test_error_item_seven_fields():
    assert _field_names(ErrorItem) == {
        "code", "namespace", "tool", "severity", "message",
        "evidence", "fix_suggestion",
    }


def test_error_item_make_derives_namespace_and_severity():
    e = ErrorItem.make(EDA_TOOL_NOT_FOUND, "registry", "no such tool",
                       evidence=["log line"], fix_suggestion="register it")
    assert e.namespace == "eda"
    assert e.severity == "error"
    assert e.evidence == ["log line"]
    assert e.fix_suggestion == "register it"
    # 手动覆盖 severity 也行
    e2 = ErrorItem.make(EDA_BUDGET_EXHAUSTED, "x", "y", severity="error")
    assert e2.severity == "error"


def test_all_contract_dataclasses_are_dataclasses():
    """确保所有契约类型都是 dataclass(asdict 可用)。"""
    for cls in (RunRequest, RunReport, ToolCall, ToolResult, SkillResult,
                RunRecord, StepRecord, Message, LLMResponse, ErrorItem):
        assert is_dataclass(cls), cls.__name__


def test_no_bare_contract_version_in_errors_module():
    """A14/I12:errors.py 不得硬编码裸 '0.1.0'(必须走 CONTRACT_VERSION 常量)。"""
    import eda_agent.errors as err_mod
    src = inspect_source(err_mod)
    # errors.py 不应出现裸版本字符串
    assert '"0.1.0"' not in src and "'0.1.0'" not in src


def inspect_source(module):
    import inspect
    return inspect.getsource(module)
