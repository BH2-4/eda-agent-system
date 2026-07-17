"""错误码与结构化 ErrorItem(契约 §2.6 v1.2)。

二段式 ``namespace.code``;namespace 登记表正式含 ``diagnose``(A)与 ``heal``(B)。
MVP 13 码保留为 ``eda.*`` 别名(向后兼容)。C 读 error_code 时**按 namespace 聚类**:
namespace in {"diagnose","heal"} 走各自语义;namespace == "eda" 走 13 码语义;
不做双向字符串相等比较。

规范码是 ``diagnose.*`` / ``heal.*`` / ``synth|sim|sta|...``;``eda.*`` 是兼容别名。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["info", "warn", "error", "fatal"]

# ── namespace 登记表(§2.6 v1.2,含 diagnose/heal) ─────────────────────
NAMESPACES: tuple[str, ...] = (
    "eda",       # 框架级 + 现有 13 个 MVP 码(别名)
    "synth",     # Yosys 综合领域
    "sim",       # iverilog 仿真领域
    "sta",       # OpenSTA 时序领域
    "drc",       # KLayout DRC(可选)
    "pnr",       # nextpnr 布局布线(可选)
    "llm",       # LLM 调用领域
    "diagnose",  # A 诊断器领域(v1.2 登记)
    "heal",      # B 自修复领域(v1.2 登记)
)

# ── MVP 13 个核心码(保留为 eda.* 别名,§2.6) ──────────────────────────
# 值即规范二段式字符串,供 ToolResult.error_code / ErrorItem.code 使用。
EDA_TOOL_NOT_FOUND     = "eda.tool_not_found"
EDA_TOOL_ARGS_INVALID  = "eda.tool_args_invalid"
EDA_SUBPROCESS_FAILED  = "eda.subprocess_failed"
EDA_SUBPROCESS_TIMEOUT = "eda.subprocess_timeout"
EDA_PARSE_FAILED       = "eda.parse_failed"
EDA_RTL_SYNTAX         = "eda.rtl_syntax"
EDA_SIM_COMPILE_FAILED = "eda.sim_compile_failed"
EDA_SIM_ASSERT_FAILED  = "eda.sim_assert_failed"
EDA_TIMING_VIOLATION   = "eda.timing_violation"
EDA_LLM_CALL_FAILED    = "eda.llm_call_failed"
EDA_BUDGET_EXHAUSTED   = "eda.budget_exhausted"
EDA_INTERNAL           = "eda.internal"
EDA_SCHEMA_MISMATCH    = "eda.schema_mismatch"

# ── diagnose namespace 错误码(A §5.4) ────────────────────────────────
DIAGNOSE_NO_ERROR_FOUND  = "diagnose.no_error_found"        # severity=warn
DIAGNOSE_LLM_CALL_FAILED = "diagnose.llm_call_failed"       # 别名 eda.llm_call_failed
DIAGNOSE_KB_CORRUPTED    = "diagnose.kb_corrupted"          # severity=warn
DIAGNOSE_ARGS_INVALID    = "diagnose.args_invalid"          # 别名 eda.tool_args_invalid

# ── heal namespace 错误码(B §5.2) ────────────────────────────────────
HEAL_UNSUPPORTED_GOAL     = "heal.unsupported_goal"
HEAL_RTL_NOT_FOUND        = "heal.rtl_not_found"
HEAL_TB_NOT_FOUND         = "heal.tb_not_found"
HEAL_DIAGNOSE_FAILED      = "heal.diagnose_failed"
HEAL_PATCH_SYNTAX_INVALID = "heal.patch_syntax_invalid"     # severity=warn
HEAL_REGRESSION_DEADLOCK  = "heal.regression_deadlock"      # severity=warn
HEAL_REDUCED_TO_DIAGNOSE  = "heal.reduced_to_diagnose"      # severity=info


# ── severity 与 code 对应关系表(§2.6,降低 LLM 自由度,跨 run 可比) ────
SEVERITY_BY_CODE: dict[str, Severity] = {
    EDA_TIMING_VIOLATION:    "error",
    EDA_RTL_SYNTAX:          "error",
    EDA_SIM_COMPILE_FAILED:  "error",
    EDA_SIM_ASSERT_FAILED:   "error",
    EDA_BUDGET_EXHAUSTED:    "warn",
    EDA_TOOL_NOT_FOUND:      "error",
    EDA_TOOL_ARGS_INVALID:   "error",
    EDA_SUBPROCESS_TIMEOUT:  "error",
    EDA_INTERNAL:            "fatal",
    EDA_SCHEMA_MISMATCH:     "error",
    # 未列出的 eda.* (subprocess_failed/parse_failed/llm_call_failed)
    # 默认 error/subprocess 由 severity_of 兜底,需精确时调用方覆盖。
    DIAGNOSE_NO_ERROR_FOUND:  "warn",
    DIAGNOSE_LLM_CALL_FAILED: "error",
    DIAGNOSE_KB_CORRUPTED:    "warn",
    DIAGNOSE_ARGS_INVALID:    "error",
    HEAL_UNSUPPORTED_GOAL:     "error",
    HEAL_RTL_NOT_FOUND:        "error",
    HEAL_TB_NOT_FOUND:         "error",
    HEAL_DIAGNOSE_FAILED:      "error",
    HEAL_PATCH_SYNTAX_INVALID: "warn",
    HEAL_REGRESSION_DEADLOCK:  "warn",
    HEAL_REDUCED_TO_DIAGNOSE:  "info",
    # 领域规范码(synth/sta/sim namespace,各 Tool 模块产出的 error_code 字面量)。
    # sta.no_liberty 是配置缺失(liberty 缺失,opensta_timing.py:45)非失败 → warn;
    # 其余默认 error 已与 severity_of 兜底一致,显式登记消除"未列出"歧义。
    "synth.synth_failed":      "error",
    "sta.sta_unavailable":     "error",
    "sta.sta_failed":          "error",
    "sta.no_liberty":          "warn",
    "sim.fail_signal":         "error",
}


def namespace_of(code: str) -> str:
    """派生 namespace = code.split('.')[0]。便于 C 按 namespace 聚类(§2.6)。"""
    return code.split(".", 1)[0] if "." in code else code


def severity_of(code: str) -> Severity:
    """查表返回 code 对应 severity;未登记的默认 'error'(保守归错)。"""
    return SEVERITY_BY_CODE.get(code, "error")


@dataclass
class ErrorItem:
    code: str                                          # 二段式 namespace.code
    namespace: str                                     # 派生 = code.split('.')[0]
    tool: str                                          # 哪个 Tool/skill 产生
    severity: Severity
    message: str                                       # 一句话
    evidence: list[str] = field(default_factory=list)  # 日志原文片段(优先读 .full.log)
    fix_suggestion: str | None = None                  # 修复建议(A 诊断器填)

    @classmethod
    def make(
        cls,
        code: str,
        tool: str,
        message: str,
        *,
        evidence: list[str] | None = None,
        fix_suggestion: str | None = None,
        severity: Severity | None = None,
    ) -> "ErrorItem":
        """便捷构造:自动派生 namespace 与 severity(查表,可覆盖)。"""
        return cls(
            code=code,
            namespace=namespace_of(code),
            tool=tool,
            severity=severity if severity is not None else severity_of(code),
            message=message,
            evidence=list(evidence) if evidence else [],
            fix_suggestion=fix_suggestion,
        )
