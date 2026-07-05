"""A 诊断器 skill_diagnose(契约 §2.2 §5.4 v1.2)。

输入:一组 L1 Tool 的 ``ToolResult``(B 自修复期间产);A 读其 stdout/stderr/parsed
与 runs/<run_id>/steps/<idx>_*/<tool>.full.log,先规则层(ErrorKB 正则)抽根因,
按需触发 LLM 归因,产出 ``DiagnosisReport`` 并落 ``runs/<run_id>/diagnose/``。

A 恒定语义:不修复不迭代,故 ``patch_source="none"`` / ``convergence_cause="none"``
/ ``best_iter=-1`` / ``iterations=1``(契约 §2.3 行 131-132)。

契约硬规则:``contract_version`` 必须 ``from eda_agent.contracts import CONTRACT_VERSION``
引用(禁止裸 "0.1.0");``parsed`` 必须含 ``_schema`` 元字段;``artifacts`` 元素必须用
``artifact_ref`` 工厂;``ToolResult.status`` 只有 ok/error/timeout 三态。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import monotonic
from typing import Any, Literal

from eda_agent.contracts import (
    CONTRACT_VERSION,
    LLMProvider,
    Message,
    Skill,
    SkillResult,
    Tool,
    ToolCall,
    ToolResult,
    artifact_ref,
)
from eda_agent.errors import (
    DIAGNOSE_KB_CORRUPTED,
    DIAGNOSE_NO_ERROR_FOUND,
    ErrorItem,
    Severity,
    namespace_of,
    severity_of,
)
from eda_agent.settings import Settings

# ─────────────────────────────────────────────────────────────────────
# confidence 公式(v1.2 写死,覆盖 LLM 自填)
# ─────────────────────────────────────────────────────────────────────

# 饱和上限:min(len(evidence), 5) 后不再加分(避免恒 1.0)。
_CONF_EVIDENCE_CAP = 5
# Jaccard 阈值:LLM root_cause vs 规则 errors[0].message 关键词重叠率低于此 → 矛盾。
_JACCARD_CONTRADICTION = 0.3


def compute_confidence(evidence: list[str], has_contradiction: bool) -> float:
    """v1.2 写死的 confidence 公式。

        val = 0.5 + 0.1 * min(len(evidence), 5) - 0.2 * (1 if has_contradiction else 0)
        clip 到 [0.0, 1.0]。

    LLMAttributor 即使 prompt 要求给 confidence 也忽略,统一用此公式自算。
    """
    val = 0.5 + 0.1 * min(len(evidence), _CONF_EVIDENCE_CAP)
    if has_contradiction:
        val -= 0.2
    return max(0.0, min(1.0, val))


def _jaccard(a: str, b: str) -> float:
    """MVP 简单分词 Jaccard:两文本 split() 分词后 |A∩B| / |A∪B|。

    任一空 → 0.0。
    """
    sa = set(a.split())
    sb = set(b.split())
    if not sa or not sb:
        return 0.0
    inter = sa & sb
    union = sa | sb
    return len(inter) / len(union) if union else 0.0


def _max_severity(errors: list[ErrorItem]) -> Severity:
    """取 errors 中最严重等级;空则 "info"。顺序 info<warn<error<fatal。"""
    if not errors:
        return "info"
    order = {"info": 0, "warn": 1, "error": 2, "fatal": 3}
    worst: Severity = "info"
    for e in errors:
        if order.get(e.severity, 0) > order.get(worst, 0):
            worst = e.severity
    return worst


# ─────────────────────────────────────────────────────────────────────
# ErrorPattern / ErrorKB
# ─────────────────────────────────────────────────────────────────────


@dataclass
class ErrorPattern:
    r"""一条错误 KB 模式:正则 + 修复模板。

    ``regex`` 用命名分组抽行号 / 信号名(如 ``(?P<signal>\w+)``),
    ``fix_hint_template`` 支持 ``{signal}`` / ``{line}`` 的 ``.format``。
    ``example_log`` 自检:``re.search(regex, example_log)`` 必须命中。
    """

    pid: str                                       # 如 "synth.multidrive_001"
    tool: str                                      # yosys_synth / iverilog_sim / opensta_timing / "*"
    error_code: str                                # 规范二段式,如 "synth.multi_driver"
    severity: Severity
    regex: str
    fix_hint_template: str
    example_log: str
    source: Literal["seed", "llm_curated", "human"] = "seed"
    hit_count: int = 0
    added_at: str = ""


class ErrorKB:
    """错误知识库:load / save / lookup / add_case / suggest_from_llm / snapshot_to。"""

    def __init__(
        self,
        patterns: list[ErrorPattern] | None = None,
        version: str = "0.1.0",
    ) -> None:
        self.patterns: list[ErrorPattern] = list(patterns) if patterns else []
        self.version = version

    # ── load / save ──────────────────────────────────────────────────
    @classmethod
    def load(cls, path: str | Path) -> "ErrorKB":
        """json → 对象;解析失败返回空 KB(不抛,记 diagnose.kb_corrupted 由调用方)。

        若文件不存在也返回空 KB(首跑 / Phase2 种子未注入)。
        """
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # 调用方按返回的空 KB 走"无规则命中"分支,并自己记 KB_CORRUPTED。
            # 这里无法直接构造 ErrorItem(无 tool 上下文),只回空 KB。
            return cls()
        version = str(data.get("version", "0.1.0"))
        patterns: list[ErrorPattern] = []
        for raw in data.get("patterns", []):
            try:
                patterns.append(ErrorPattern(
                    pid=str(raw["pid"]),
                    tool=str(raw["tool"]),
                    error_code=str(raw["error_code"]),
                    severity=raw["severity"],          # type: ignore[arg-type]
                    regex=str(raw["regex"]),
                    fix_hint_template=str(raw["fix_hint_template"]),
                    example_log=str(raw.get("example_log", "")),
                    source=raw.get("source", "seed"),  # type: ignore[arg-type]
                    hit_count=int(raw.get("hit_count", 0)),
                    added_at=str(raw.get("added_at", "")),
                ))
            except (KeyError, TypeError, ValueError):
                # 单条坏 pattern 跳过,不让整体崩。
                continue
        return cls(patterns=patterns, version=version)

    def save(self, path: str | Path | None = None) -> None:
        """原子写:tmp 文件 + os.replace。``path`` 为 None 时写到 load 时的路径(若记录)。

        注意:本实现只接受显式 path(避免误写);None 时静默跳过。
        """
        if path is None:
            return
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.version,
            "patterns": [asdict(x) for x in self.patterns],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)

    # ── lookup ───────────────────────────────────────────────────────
    def lookup(
        self,
        tool: str,
        log_text: str,
    ) -> list[tuple[ErrorPattern, "re.Match[str]"]]:
        """过滤 p.tool in (tool, "*") 且 ``re.search`` 命中。返回 (pattern, match) 列表。"""
        out: list[tuple[ErrorPattern, "re.Match[str]"]] = []
        if not log_text:
            return out
        for p in self.patterns:
            if p.tool not in (tool, "*"):
                continue
            try:
                m = re.search(p.regex, log_text)
            except re.error:
                continue
            if m is not None:
                out.append((p, m))
        return out

    # ── add_case ─────────────────────────────────────────────────────
    def add_case(self, pattern: ErrorPattern, dedupe: bool = True) -> bool:
        """按 ``(tool, error_code, regex)`` 去重;重复返回 False。"""
        if dedupe:
            for ex in self.patterns:
                if (ex.tool, ex.error_code, ex.regex) == (
                    pattern.tool, pattern.error_code, pattern.regex,
                ):
                    return False
        self.patterns.append(pattern)
        return True

    # ── suggest_from_llm ─────────────────────────────────────────────
    def suggest_from_llm(
        self,
        llm_output: dict,
        tool: str,
    ) -> ErrorPattern | None:
        """从 LLM 提议构造 ``source="llm_curated"`` pattern,不入库。

        ``llm_output`` 形状:{"new_pattern": {"regex":.., "error_code":..,
        "fix_hint_template":..}, ...}。任一关键字段缺失 → None。
        """
        new = llm_output.get("new_pattern")
        if not isinstance(new, dict):
            return None
        regex = new.get("regex")
        error_code = new.get("error_code")
        hint = new.get("fix_hint_template")
        if not (regex and error_code and hint):
            return None
        # severity 走查表(未登记默认 error)。
        sev: Severity = severity_of(str(error_code))
        return ErrorPattern(
            # pid 用 sha256(跨进程确定);Python 内置 hash() 受 PYTHONHASHSEED
            # 随机化影响,跨 run 不可比(违反契约 §2.6 "跨 run 可比"语义)。
            pid=f"llm.{hashlib.sha256(f'{tool}|{error_code}|{regex}'.encode('utf-8')).hexdigest()[:8]}",
            tool=tool,
            error_code=str(error_code),
            severity=sev,
            regex=str(regex),
            fix_hint_template=str(hint),
            example_log="",                 # LLM 提议无 example,自检跳过
            source="llm_curated",
        )

    # ── snapshot_to ──────────────────────────────────────────────────
    def snapshot_to(self, run_id: str) -> None:
        """落 ``runs/<run_id>/diagnose/error_kb_snapshot.json``。"""
        base = Path("runs") / str(run_id) / "diagnose"
        base.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.version,
            "patterns": [asdict(x) for x in self.patterns],
        }
        (base / "error_kb_snapshot.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# ─────────────────────────────────────────────────────────────────────
# 规则层(零 LLM,模块级函数)
# ─────────────────────────────────────────────────────────────────────


def _build_error_item(
    pattern: ErrorPattern,
    match: "re.Match[str]",
    tool_name: str,
) -> ErrorItem:
    """从命中 pattern + match 构造 ErrorItem。"""
    g = match.groupdict()
    if g:
        try:
            msg = pattern.fix_hint_template.format(**g)
        except (KeyError, IndexError):
            msg = pattern.fix_hint_template
    else:
        msg = pattern.fix_hint_template
    return ErrorItem(
        code=pattern.error_code,
        namespace=namespace_of(pattern.error_code),
        tool=tool_name,
        severity=pattern.severity,
        message=msg,
        evidence=[match.group(0)],
        fix_suggestion=msg,
    )


def rule_match(
    kb: ErrorKB,
    tool: str,
    log_text: str,
) -> list[ErrorItem]:
    """对单个 (tool, log_text) 跑 KB 正则,产 ErrorItem 列表(零 LLM)。"""
    items: list[ErrorItem] = []
    for pattern, match in kb.lookup(tool, log_text):
        items.append(_build_error_item(pattern, match, tool))
    return items


# ─────────────────────────────────────────────────────────────────────
# DiagnosisReport + 13 字段 parsed 装配
# ─────────────────────────────────────────────────────────────────────


@dataclass
class DiagnosisReport:
    """内部诊断报告。``errors`` 字段在 parsed 里改名为 ``root_causes``(m1 命名裁决)。"""

    tool: str
    stage: Literal["synth", "sim", "sta", "multi"]
    errors: list[ErrorItem]
    root_cause: str
    severity: Severity
    fix_hints: list[str]
    confidence: float
    evidence: list[str] = field(default_factory=list)
    used_layers: Literal["rule", "llm", "rule+llm"] = "rule"
    kb_hits: list[str] = field(default_factory=list)

    def to_parsed(self) -> dict[str, Any]:
        """映射到 A 诊断器 13 字段 parsed(含 ``_schema`` 元字段)。

        needs_rtl_patch 按 severity 派生(v1.2,禁止按 namespace 前缀)。
        """
        root_causes = [asdict(e) for e in self.errors]
        needs_rtl_patch = any(
            e.severity in ("error", "fatal") for e in self.errors
        )
        return {
            "_schema": {
                "name": "skill_diagnose",
                "version": "0.1.0",
                "contract_version": CONTRACT_VERSION,
            },
            "skill": "diagnose",
            "tool": self.tool,
            "stage": self.stage,
            "root_causes": root_causes,
            "root_cause_summary": self.root_cause,
            "severity": self.severity,
            "fix_hints": list(self.fix_hints),
            "confidence": float(self.confidence),
            "needs_rtl_patch": needs_rtl_patch,
            "used_layers": self.used_layers,
            "kb_hits": list(self.kb_hits),
            "summary": self.root_cause,
        }


# ─────────────────────────────────────────────────────────────────────
# LLM 归因层
# ─────────────────────────────────────────────────────────────────────


# LLM 输出 JSON schema(契约 §5.4:必含 root_cause/fix_hints/needs_patch/new_pattern)。
_LLM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string"},
        "fix_hints": {"type": "array", "items": {"type": "string"}},
        "needs_patch": {"type": "boolean"},
        "new_pattern": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "regex": {"type": "string"},
                        "error_code": {"type": "string"},
                        "fix_hint_template": {"type": "string"},
                    },
                    "required": ["regex", "error_code", "fix_hint_template"],
                },
            ],
        },
    },
    "required": ["root_cause", "fix_hints", "needs_patch", "new_pattern"],
}


def _extract_json(text: str) -> dict | None:
    """从 LLM 文本里找首个 ``{`` 并 ``JSONDecoder.raw_decode`` 解析。失败返回 None。"""
    if not text:
        return None
    idx = text.find("{")
    if idx < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[idx:])
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _validate(raw: dict) -> bool:
    """jsonschema 校验 LLM 输出。"""
    try:
        import jsonschema  # type: ignore[import-not-found]
    except ImportError:
        # jsonschema 缺失时退化为关键字段存在性检查(测试环境通常装 jsonschema)。
        return (
            isinstance(raw.get("root_cause"), str)
            and isinstance(raw.get("fix_hints"), list)
            and isinstance(raw.get("needs_patch"), bool)
            and ("new_pattern" in raw)
        )
    try:
        jsonschema.validate(raw, _LLM_SCHEMA)
    except Exception:
        return False
    return True


class LLMAttributor:
    """LLM 归因器:调 LLM 拿 root_cause / fix_hints / 新 pattern 提议。

    所有异常吞掉返回降级元组(不抛,让 A 走纯规则层结果)。
    """

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    def attribute(
        self,
        errors: list[dict],
        context: dict,
        deadline: float,
    ) -> tuple[str, list[str], bool, dict]:
        """返回 ``(root_cause, fix_hints, llm_found_new, raw)``。

        - deadline 已过 → 空结果(无 root_cause)。
        - LLM 调用异常 → root_cause="(LLM call failed)"。
        - 解析失败 → root_cause="(LLM parse failed)"。
        """
        if monotonic() >= deadline:
            return ("", [], False, {})
        from eda_agent.skills.prompts_diagnose import build_diagnose_prompt

        msgs = build_diagnose_prompt(errors, context)
        try:
            resp = self._llm.chat(msgs, temperature=0.0, max_tokens=2048)
        except Exception:
            return ("(LLM call failed)", [], False, {})
        raw = _extract_json(resp.text)
        if not raw:
            return ("(LLM parse failed)", [], False, {})
        if not _validate(raw):
            return ("(LLM parse failed)", [], False, raw)
        new = raw.get("new_pattern")
        return (
            str(raw["root_cause"]),
            list(raw.get("fix_hints", [])),
            bool(new),
            raw,
        )


# ─────────────────────────────────────────────────────────────────────
# ReportWriter(同模块类,不建子包)
# ─────────────────────────────────────────────────────────────────────


class ReportWriter:
    """落 ``runs/<run_id>/diagnose/report.{md,json}``。"""

    def write(self, run_id: str, report: DiagnosisReport, kb: ErrorKB) -> None:
        base = Path("runs") / str(run_id) / "diagnose"
        base.mkdir(parents=True, exist_ok=True)
        (base / "report.md").write_text(self._render_md(report), encoding="utf-8")
        (base / "report.json").write_text(
            json.dumps(report.to_parsed(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _render_md(self, r: DiagnosisReport) -> str:
        lines: list[str] = []
        lines.append(f"# Diagnosis Report — {r.tool}")
        lines.append("")
        lines.append(f"- stage: `{r.stage}`")
        lines.append(f"- severity: `{r.severity}`")
        lines.append(f"- confidence: `{r.confidence:.2f}`")
        lines.append(f"- used_layers: `{r.used_layers}`")
        contradiction = (
            r.used_layers in ("llm", "rule+llm")
            and bool(r.errors)
            and _jaccard(r.root_cause, r.errors[0].message) < _JACCARD_CONTRADICTION
        )
        lines.append(f"- contradiction: `{contradiction}`")
        lines.append("")
        lines.append("## Root Cause")
        lines.append("")
        lines.append(r.root_cause or "(none)")
        lines.append("")
        if r.errors:
            lines.append("## Root Causes (errors)")
            lines.append("")
            lines.append("| code | severity | tool | message |")
            lines.append("| --- | --- | --- | --- |")
            for e in r.errors:
                msg = (e.message or "").replace("|", "\\|")
                lines.append(f"| `{e.code}` | `{e.severity}` | `{e.tool}` | {msg} |")
            lines.append("")
        if r.fix_hints:
            lines.append("## Fix Hints")
            lines.append("")
            for h in r.fix_hints:
                lines.append(f"- {h}")
            lines.append("")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# DiagnoseSkill(9 步主流程)
# ─────────────────────────────────────────────────────────────────────


# tool → stage 映射;未知 tool 走 "multi"。
_TOOL_STAGE: dict[str, str] = {
    "yosys_synth": "synth",
    "iverilog_sim": "sim",
    "opensta_timing": "sta",
}


class DiagnoseSkill:
    """A 诊断器 Skill(契约 §2.3 §5.4)。``name="skill_diagnose"``。"""

    name = "skill_diagnose"
    description = "诊断 EDA 工具日志错误,输出根因+修复建议"

    def __init__(
        self,
        kb: ErrorKB,
        llm: LLMProvider,
        runner: Any,
        settings: Settings,
    ) -> None:
        self._kb = kb
        self._llm = llm
        self._runner = runner
        self._settings = settings
        self.max_iterations = 1
        self.budget_s = float(settings.skill_diagnose_budget_s)
        self.schema: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "tool_results": {"type": "array"},
                    "run_id": {"type": "string"},
                    "_remaining_budget_s": {"type": "number"},
                },
                "required": ["tool_results"],
            },
        }

    # ── Skill Protocol ───────────────────────────────────────────────
    def run(
        self,
        run_id: str | None,
        inputs: dict[str, Any],
        remaining_budget_s: float | None = None,
    ) -> SkillResult:
        t0 = monotonic()
        # 区分 None 与 0.0:None 表示"用我自己的 budget_s";具体数值(含 0.0)表示
        # "就这么多预算"。避免 `0.0 or self.budget_s` 因 0.0 falsy 回退到 60s 的真值
        # 陷阱(违反双层预算仲裁:C 下传 0.0 表示无剩余,应跳过 LLM)。
        if remaining_budget_s is None:
            budget = self.budget_s
        else:
            budget = min(self.budget_s, max(0.0, float(remaining_budget_s)))
        deadline = monotonic() + budget
        run_id_s = str(run_id) if run_id is not None else ""

        trajectory: list[dict] = []

        # ── 步骤 0:入参校验 ──────────────────────────────────────────
        tool_results = inputs.get("tool_results")
        if not isinstance(tool_results, list) or not tool_results:
            # 空 tool_results:返回 ok + 空 root_causes(不抛,让 B 降级)。
            return self._empty_result(run_id_s, t0, trajectory, "no tool_results")

        # 每条必须是 dict 且含 tool + parsed;否则跳过该条但不整体失败。
        valid_results: list[dict] = []
        for tr in tool_results:
            if isinstance(tr, dict) and "tool" in tr and "parsed" in tr:
                valid_results.append(tr)
        if not valid_results:
            trajectory.append({"step": "start", "iter": 0,
                               "detail": f"{len(tool_results)} tool_results (0 valid)"})
            return self._empty_result(run_id_s, t0, trajectory,
                                       "no valid tool_results")
        trajectory.append({"step": "start", "iter": 0,
                           "detail": f"{len(valid_results)} tool_results"})

        # ── 步骤 1:收集日志 ──────────────────────────────────────────
        logs = self._collect_logs(run_id_s, valid_results)
        trajectory.append({
            "step": "collect_logs",
            "iter": 0,
            "detail": ",".join(sorted(logs.keys())) or "(none)",
        })

        # ── 步骤 2:规则层匹配(零 LLM) ─────────────────────────────
        errors: list[ErrorItem] = []
        for tool_name, log_text in logs.items():
            errors.extend(rule_match(self._kb, tool_name, log_text))
        trajectory.append({"step": "rule", "iter": 0,
                           "detail": f"{len(errors)} errors"})

        # ── 步骤 3:决定是否触发 LLM ─────────────────────────────────
        any_failed = any(tr.get("status") != "ok" for tr in valid_results)
        failed_tools = {
            tr["tool"] for tr in valid_results if tr.get("status") != "ok"
        }
        cross_tool = len(failed_tools) > 1
        needs_llm = (len(errors) == 0 and any_failed) or cross_tool

        used_layers: Literal["rule", "llm", "rule+llm"] = "rule"
        root_cause = ""
        fix_hints: list[str] = []
        llm_found_new = False
        raw: dict = {}

        if needs_llm and monotonic() < deadline:
            used_layers = "llm" if not errors else "rule+llm"
            primary_tool = self._primary_tool(valid_results)
            (root_cause, fix_hints, llm_found_new, raw) = LLMAttributor(
                self._llm
            ).attribute(
                errors=[asdict(e) for e in errors],
                context={"tool_results": valid_results, "logs": logs},
                deadline=deadline,
            )
            if not root_cause and not errors:
                errors.append(ErrorItem.make(
                    DIAGNOSE_NO_ERROR_FOUND,
                    tool="multi",
                    message="no error found",
                    severity="warn",
                ))

        # ── 步骤 4:root_cause 回填 ──────────────────────────────────
        if not root_cause:
            if errors:
                root_cause = f"{len(errors)} 个错误,首要:{errors[0].message}"
            else:
                root_cause = "无错误"
        if not fix_hints and errors:
            fix_hints = [
                e.fix_suggestion or e.message for e in errors[:3]
            ]

        # ── 步骤 5:evidence + confidence ────────────────────────────
        evidence_set: list[str] = []
        seen: set[str] = set()
        for e in errors:
            for ev in e.evidence:
                if ev and ev not in seen:
                    evidence_set.append(ev)
                    seen.add(ev)
        # 关键日志行(每个 tool 末尾若干非空行,去重,补到 ≤10 条)。
        for tool_name, log_text in logs.items():
            for line in (log_text or "").splitlines():
                line = line.strip()
                if not line or line in seen:
                    continue
                if len(evidence_set) >= 10:
                    break
                evidence_set.append(line)
                seen.add(line)
        evidence = evidence_set[:10]

        has_contradiction = (
            used_layers in ("llm", "rule+llm")
            and bool(errors)
            and _jaccard(root_cause, errors[0].message) < _JACCARD_CONTRADICTION
        )
        confidence = compute_confidence(evidence, has_contradiction)

        # ── 步骤 6:LLM 提议新 pattern → propose_pending ─────────────
        if llm_found_new and raw:
            primary_tool = self._primary_tool(valid_results)
            cand = self._kb.suggest_from_llm(raw, tool=primary_tool)
            if cand is not None:
                self._write_propose_pending(run_id_s, cand)

        # ── 步骤 7:装配 DiagnosisReport + 落盘 ──────────────────────
        primary_tool = self._primary_tool(valid_results)
        stage = self._stage_of(primary_tool, valid_results)
        severity = _max_severity(errors)
        kb_hits = [e.code for e in errors]
        report = DiagnosisReport(
            tool=primary_tool,
            stage=stage,                          # type: ignore[arg-type]
            errors=errors,
            root_cause=root_cause,
            severity=severity,
            fix_hints=fix_hints,
            confidence=confidence,
            evidence=evidence,
            used_layers=used_layers,
            kb_hits=kb_hits,
        )
        ReportWriter().write(run_id_s, report, self._kb)
        self._kb.snapshot_to(run_id_s)

        artifacts = [
            artifact_ref(run_id_s, "diagnose/report.md"),
            artifact_ref(run_id_s, "diagnose/report.json"),
        ]

        # ── 步骤 8:返回 SkillResult ─────────────────────────────────
        return SkillResult(
            status="ok",
            iterations=1,
            final_parsed=report.to_parsed(),
            trajectory=trajectory,
            artifacts=artifacts,
            summary=root_cause,
            patch_source="none",
            convergence_cause="none",
            best_iter=-1,
            error_code=None,
            budget_used_s=float(monotonic() - t0),
        )

    def as_tool(self) -> Tool:
        """委托 ``skills.base.as_tool``。"""
        from eda_agent.skills.base import as_tool
        return as_tool(self)

    # ── 内部 helper ──────────────────────────────────────────────────
    def _empty_result(
        self,
        run_id: str,
        t0: float,
        trajectory: list[dict],
        detail: str,
    ) -> SkillResult:
        """空 tool_results / 无有效条目时的降级 SkillResult(ok + 空 root_causes)。"""
        trajectory.append({"step": "empty", "iter": 0, "detail": detail})
        empty = DiagnosisReport(
            tool="multi",
            stage="multi",
            errors=[],
            root_cause=detail,
            severity="info",
            fix_hints=[],
            confidence=compute_confidence([], False),
            evidence=[],
            used_layers="rule",
            kb_hits=[],
        )
        if run_id:
            ReportWriter().write(run_id, empty, self._kb)
            self._kb.snapshot_to(run_id)
        artifacts = (
            [artifact_ref(run_id, "diagnose/report.md"),
             artifact_ref(run_id, "diagnose/report.json")]
            if run_id else []
        )
        return SkillResult(
            status="ok",
            iterations=1,
            final_parsed=empty.to_parsed(),
            trajectory=trajectory,
            artifacts=artifacts,
            summary=detail,
            patch_source="none",
            convergence_cause="none",
            best_iter=-1,
            error_code=None,
            budget_used_s=float(monotonic() - t0),
        )

    def _collect_logs(
        self,
        run_id: str,
        tool_results: list[dict],
    ) -> dict[str, str]:
        """优先读 ``runs/<run_id>/steps/<idx>_*/<tool>.full.log``;fallback stdout。

        找最新匹配 step 目录(按 idx 排序取最大)。
        """
        logs: dict[str, str] = {}
        for tr in tool_results:
            tool_name = str(tr.get("tool", ""))
            if not tool_name:
                continue
            text = ""
            if run_id:
                steps_dir = Path("runs") / run_id / "steps"
                if steps_dir.exists():
                    cands = sorted(
                        steps_dir.glob(f"*_{tool_name}"),
                        key=lambda p: p.name,
                    )
                    for cand in reversed(cands):
                        for fname in (f"{tool_name}.full.log", "stdout.full.log"):
                            fp = cand / fname
                            if fp.exists():
                                try:
                                    text = fp.read_text(encoding="utf-8")
                                    break
                                except OSError:
                                    continue
                        if text:
                            break
            if not text:
                text = str(tr.get("stdout", "") or "")
            logs[tool_name] = text
        return logs

    def _primary_tool(self, tool_results: list[dict]) -> str:
        """取首个失败 tool;全 ok 则取首个 tool;空 → "multi"。"""
        for tr in tool_results:
            if tr.get("status") != "ok":
                return str(tr.get("tool", "multi"))
        if tool_results:
            return str(tool_results[0].get("tool", "multi"))
        return "multi"

    def _stage_of(
        self,
        primary_tool: str,
        tool_results: list[dict],
    ) -> str:
        """单 tool → 该 tool 的 stage;多 tool / 未知 → "multi"。"""
        tools = {str(tr.get("tool", "")) for tr in tool_results}
        if len(tools) == 1:
            only = next(iter(tools))
            return _TOOL_STAGE.get(only, "multi")
        return "multi"

    def _write_propose_pending(self, run_id: str, cand: ErrorPattern) -> None:
        if not run_id:
            return
        base = Path("runs") / run_id / "diagnose" / "propose_pending"
        base.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        (base / f"{uid}.json").write_text(
            json.dumps(asdict(cand), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# 模块加载自检:DiagnoseSkill 满足 Skill Protocol(便于 import 时早失败)。
# 实例化需要 kb/llm/runner/settings,这里只做静态结构断言——略;改为契约运行时校验。
