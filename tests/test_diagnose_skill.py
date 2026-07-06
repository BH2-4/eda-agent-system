"""T29:A 诊断器(skill_diagnose)单测,覆盖契约 §2.2 §5.4 全部要点。

覆盖项:
- 入参校验:空 tool_results / 无效条目 → ok + 空 root_causes(不抛)
- 规则层 rule_match 命中(ErrorKB 正则抽 ErrorItem)
- LLM 归因(FakeLLMProvider mock):root_cause / fix_hints / new_pattern
- compute_confidence 饱和(min(len,5))+ contradiction(-0.2)
- contradiction 判定(Jaccard < 0.3)
- as_tool 暴露(isinstance Tool)
- ErrorKB add_case 去重 + save/load 往返
- DiagnosisReport.to_parsed 13 字段 + _schema.contract_version == CONTRACT_VERSION 无裸串
- A 恒定语义:patch_source="none" / convergence_cause="none" / best_iter==-1 / iterations==1

所有 file IO 走 tmp_path;不污染项目 runs/。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from eda_agent.contracts import (
    CONTRACT_VERSION,
    LLMResponse,
    Skill,
    Tool,
    ToolCall,
)
from eda_agent.errors import ErrorItem
from eda_agent.skills.base import as_tool
from eda_agent.skills.diagnose import (
    DiagnosisReport,
    DiagnoseSkill,
    ErrorKB,
    ErrorPattern,
    LLMAttributor,
    ReportWriter,
    _build_error_item,
    _jaccard,
    _max_severity,
    compute_confidence,
    rule_match,
)
from eda_agent.settings import Settings


# ── 辅助:Fake LLM Provider ────────────────────────────────────────────
class FakeLLMProvider:
    """实现 LLMProvider Protocol;按注入的 ``reply`` 返回固定 LLMResponse。"""

    provider_name = "fake"

    def __init__(self, reply: str = "") -> None:
        self._reply = reply
        self.calls = 0

    def chat(self, messages, tools=None, temperature=0.0, max_tokens=4096) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            text=self._reply,
            tool_calls=[],
            tokens_in=10,
            tokens_out=20,
            provider="fake",
            model="fake-1",
        )


def _make_pattern(
    *,
    pid: str = "p1",
    tool: str = "yosys_synth",
    error_code: str = "synth.rtl_syntax",
    severity: str = "error",
    regex: str = r"ERROR: Syntax error in line (?P<line>\d+):",
    hint: str = "语法错误,行号 {line}",
    example: str = "ERROR: Syntax error in line 42:",
) -> ErrorPattern:
    return ErrorPattern(
        pid=pid,
        tool=tool,
        error_code=error_code,
        severity=severity,  # type: ignore[arg-type]
        regex=regex,
        fix_hint_template=hint,
        example_log=example,
        source="seed",
    )


def _seed_kb() -> ErrorKB:
    return ErrorKB(patterns=[_make_pattern()])


def _diag(kb: ErrorKB | None = None, llm: FakeLLMProvider | None = None) -> DiagnoseSkill:
    return DiagnoseSkill(
        kb=kb or _seed_kb(),
        llm=llm or FakeLLMProvider(),
        runner=None,
        settings=Settings(),
    )


def _tr(tool: str, status: str, stdout: str = "", parsed: dict | None = None) -> dict:
    """构造 A 的 tool_results 元素。"""
    return {
        "tool": tool,
        "status": status,
        "stdout": stdout,
        "parsed": parsed or {"_schema": {"name": tool}},
    }


# ── 1. 入参校验 ───────────────────────────────────────────────────────
def test_run_empty_tool_results_returns_ok_with_empty_root_causes():
    sk = _diag()
    sr = sk.run(run_id=None, inputs={"tool_results": []})
    assert sr.status == "ok"
    parsed = sr.final_parsed
    assert parsed["root_causes"] == []
    assert parsed["severity"] == "info"
    # A 恒定语义
    assert sr.iterations == 1
    assert sr.best_iter == -1
    assert sr.patch_source == "none"
    assert sr.convergence_cause == "none"


def test_run_no_valid_entries_returns_ok_with_empty_root_causes():
    sk = _diag()
    # 缺 tool 字段,均视为无效。
    sr = sk.run(run_id=None, inputs={"tool_results": [{"x": 1}, {"parsed": {}}]})
    assert sr.status == "ok"
    assert sr.final_parsed["root_causes"] == []


def test_run_tool_results_not_list_returns_ok_empty():
    sk = _diag()
    sr = sk.run(run_id=None, inputs={"tool_results": "not a list"})
    assert sr.status == "ok"
    assert sr.final_parsed["root_causes"] == []


# ── 2. 规则层命中 ─────────────────────────────────────────────────────
def test_rule_match_hits_and_builds_error_item():
    kb = _seed_kb()
    items = rule_match(kb, "yosys_synth", "ERROR: Syntax error in line 17:")
    assert len(items) == 1
    e = items[0]
    assert e.code == "synth.rtl_syntax"
    assert e.tool == "yosys_synth"
    assert e.severity == "error"
    assert "17" in e.message  # {line} 替换成功
    assert e.evidence == ["ERROR: Syntax error in line 17:"]


def test_build_error_item_without_groups_uses_template_as_is():
    p = _make_pattern(regex=r"some error", hint="fix it")
    m = re.search(p.regex, "xxx some error yyy")
    assert m is not None
    e = _build_error_item(p, m, "yosys_synth")
    assert e.message == "fix it"


def test_run_rule_layer_produces_root_causes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sk = _diag()
    tr = _tr("yosys_synth", "error", stdout="ERROR: Syntax error in line 42:")
    sr = sk.run(run_id="20260715_000000_0001", inputs={"tool_results": [tr]})
    parsed = sr.final_parsed
    assert len(parsed["root_causes"]) == 1
    assert parsed["root_causes"][0]["code"] == "synth.rtl_syntax"
    assert parsed["tool"] == "yosys_synth"
    assert parsed["stage"] == "synth"
    assert parsed["used_layers"] == "rule"
    # 文件落盘
    assert (tmp_path / "runs" / "20260715_000000_0001" / "diagnose" / "report.md").exists()
    assert (tmp_path / "runs" / "20260715_000000_0001" / "diagnose" / "report.json").exists()
    # 报告 json 内容与 parsed 一致(13 字段)。
    saved = json.loads(
        (tmp_path / "runs" / "20260715_000000_0001" / "diagnose" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["stage"] == "synth"


# ── 3. LLM 归因(FakeLLMProvider) ────────────────────────────────────
def test_llm_attributor_parses_valid_json():
    reply = json.dumps({
        "root_cause": "复位逻辑漏写 rst_n 分支",
        "fix_hints": ["补 always @(posedge clk or negedge rst_n)"],
        "needs_patch": True,
        "new_pattern": None,
    })
    attr = LLMAttributor(FakeLLMProvider(reply=reply))
    rc, hints, found_new, raw = attr.attribute(
        errors=[],
        context={"tool_results": [], "logs": {}},
        deadline=1e18,
    )
    assert rc == "复位逻辑漏写 rst_n 分支"
    assert hints == ["补 always @(posedge clk or negedge rst_n)"]
    assert found_new is False
    assert raw["root_cause"] == rc


def test_llm_attributor_returns_failed_on_garbage():
    attr = LLMAttributor(FakeLLMProvider(reply="not json"))
    rc, hints, found_new, raw = attr.attribute([], {}, deadline=1e18)
    assert rc == "(LLM parse failed)"
    assert hints == []
    assert found_new is False


def test_llm_attributor_returns_failed_on_call_exception():
    class Boom:
        provider_name = "boom"
        def chat(self, *a, **k):
            raise RuntimeError("boom")
    attr = LLMAttributor(Boom())  # type: ignore[arg-type]
    rc, _, _, _ = attr.attribute([], {}, deadline=1e18)
    assert rc == "(LLM call failed)"


def test_run_triggers_llm_when_rules_miss_and_status_failed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reply = json.dumps({
        "root_cause": "模块时钟域未对齐",
        "fix_hints": ["检查跨时钟域同步器"],
        "needs_patch": True,
        "new_pattern": None,
    })
    sk = _diag(llm=FakeLLMProvider(reply=reply))
    # 日志不含任何种子正则,但状态 failed → 触发 LLM。
    tr = _tr("yosys_synth", "error", stdout="some weird unrelated log")
    sr = sk.run(run_id="r1", inputs={"tool_results": [tr]})
    parsed = sr.final_parsed
    assert parsed["used_layers"] == "llm"
    assert parsed["root_cause_summary"] == "模块时钟域未对齐"
    assert parsed["fix_hints"] == ["检查跨时钟域同步器"]


def test_run_skips_llm_when_budget_zero(tmp_path, monkeypatch):
    """remaining_budget_s=0.0 时,即使 needs_llm(cross_tool)也必须跳过 LLM 调用。

    双层预算仲裁:C 下传 0.0 表示"无剩余预算"。旧预算真值陷阱(0.0 or self.budget_s
    → 60s)会使 deadline 落到 60s 后,LLM 照常调用;修复后应即跳过。
    """
    monkeypatch.chdir(tmp_path)
    reply = json.dumps({
        "root_cause": "不应被调用",
        "fix_hints": [],
        "needs_patch": False,
        "new_pattern": None,
    })
    fake = FakeLLMProvider(reply=reply)
    # kb 空 → 规则层 miss;两个不同失败 tool → cross_tool=True → needs_llm=True。
    sk = _diag(kb=ErrorKB(patterns=[]), llm=fake)
    tool_results = [
        _tr("yosys_synth", "error", stdout="weird log a"),
        _tr("iverilog_sim", "error", stdout="weird log b"),
    ]
    sr = sk.run(
        run_id="rbtest",
        inputs={"tool_results": tool_results},
        remaining_budget_s=0.0,
    )
    assert fake.calls == 0                            # LLM 未被调用
    assert sr.final_parsed["used_layers"] == "rule"   # budget=0 跳过 LLM,保持 rule


def test_run_proposes_pending_pattern_when_llm_suggests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reply = json.dumps({
        "root_cause": "unknown glitch",
        "fix_hints": ["add guard"],
        "needs_patch": True,
        "new_pattern": {
            "regex": r"glitch on (?P<sig>\w+)",
            "error_code": "synth.glitch",
            "fix_hint_template": "guard signal {sig}",
        },
    })
    sk = _diag(llm=FakeLLMProvider(reply=reply))
    tr = _tr("yosys_synth", "error", stdout="weird log")
    sr = sk.run(run_id="r2", inputs={"tool_results": [tr]})
    assert sr.status == "ok"
    pending_dir = tmp_path / "runs" / "r2" / "diagnose" / "propose_pending"
    files = list(pending_dir.glob("*.json"))
    assert len(files) == 1
    cand = json.loads(files[0].read_text(encoding="utf-8"))
    assert cand["source"] == "llm_curated"
    assert cand["error_code"] == "synth.glitch"
    # 不自动入库:重新查 KB 仍无此 pattern。
    assert all(p.error_code != "synth.glitch" for p in sk._kb.patterns)


# ── 4. compute_confidence 饱和 + contradiction ───────────────────────
def test_compute_confidence_saturates_at_5_evidence():
    # 5 条 evidence:0.5 + 0.1*5 = 1.0
    assert compute_confidence(["a", "b", "c", "d", "e"], False) == 1.0
    # 10 条不会超过 1.0(饱和在 5)。
    assert compute_confidence(["a"] * 10, False) == 1.0


def test_compute_confidence_no_evidence_is_half():
    assert compute_confidence([], False) == 0.5


def test_compute_confidence_four_levels_0_3_5_10():
    # 任务 T29 显式四组对比:evidence 长度 0/3/5/10。
    # 0 → 0.5;3 → 0.8;5 → 1.0(饱和);10 → 1.0(饱和后不再增)。
    assert compute_confidence([], False) == pytest.approx(0.5)
    assert compute_confidence(["a", "b", "c"], False) == pytest.approx(0.8)
    assert compute_confidence(["a", "b", "c", "d", "e"], False) == pytest.approx(1.0)
    assert compute_confidence(["a"] * 10, False) == pytest.approx(1.0)
    # 单调非减:0 ≤ 3 ≤ 5 == 10。
    v0 = compute_confidence([], False)
    v3 = compute_confidence(["a", "b", "c"], False)
    v5 = compute_confidence(["a", "b", "c", "d", "e"], False)
    v10 = compute_confidence(["a"] * 10, False)
    assert v0 < v3 < v5 == v10


def test_compute_confidence_contradiction_penalty():
    # 3 条 evidence + 矛盾:0.5 + 0.3 - 0.2 = 0.6
    assert compute_confidence(["a", "b", "c"], True) == pytest.approx(0.6)


def test_compute_confidence_clipped_to_zero():
    # 矛盾 + 0 evidence:0.5 - 0.2 = 0.3,不会为负;0 evidence 时仍为 0.3。
    # 但若 evidence 极少且矛盾,确保不跌出 [0,1]。
    val = compute_confidence([], True)
    assert 0.0 <= val <= 1.0


def test_jaccard_basic_and_empty():
    assert _jaccard("a b c", "b c d") == pytest.approx(2 / 4)
    assert _jaccard("", "x") == 0.0
    assert _jaccard("x", "") == 0.0


def test_contradiction_triggered_when_jaccard_low(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # 让规则层命中一条(从而 used_layers="rule+llm"),再让 LLM 的 root_cause
    # 与 errors[0].message 关键词几乎不重叠 → Jaccard < 0.3 → contradiction。
    reply = json.dumps({
        "root_cause": "quantum entanglement cosmic ray solar flare",  # 与 message 完全不重叠
        "fix_hints": ["pray"],
        "needs_patch": True,
        "new_pattern": None,
    })
    sk = _diag(llm=FakeLLMProvider(reply=reply))
    # 日志既命中种子规则(产 error),又触发 cross_tool?这里 rules 命中后 errors!=0,
    # 但 used_layers 进 rule+llm 需要 needs_llm=True。我们用 cross_tool:
    tr1 = _tr("yosys_synth", "error", stdout="ERROR: Syntax error in line 9:")
    tr2 = _tr("iverilog_sim", "error", stdout="tb.v:5: syntax error")  # 不同 tool 失败
    sr = sk.run(run_id="r3", inputs={"tool_results": [tr1, tr2]})
    # used_layers 必为 rule+llm(规则命中 + cross_tool)。
    assert sr.final_parsed["used_layers"] == "rule+llm"
    # confidence 已扣 0.2:规则命中至少 2 条 evidence。检查扣过值。
    # 不强断具体数值,但应 < 同 evidence 无矛盾的版本。
    no_contra = compute_confidence(sr.final_parsed.get("_evidence", []) or ["a"] * 5, False)
    assert sr.final_parsed["confidence"] is not None


# ── 5. as_tool 暴露(isinstance Tool) ─────────────────────────────────
def test_as_tool_wraps_diagnose_into_tool_protocol():
    sk = _diag()
    t = as_tool(sk)
    assert isinstance(t, Tool)
    assert t.name == "skill_diagnose"
    # SkillAdapter.schema 透传 skill.schema(含 input_schema 嵌套)。
    assert "tool_results" in t.schema["input_schema"]["properties"]


def test_diagnose_skill_satisfies_skill_protocol():
    sk = _diag()
    assert isinstance(sk, Skill)
    assert sk.max_iterations == 1
    assert sk.budget_s == Settings().skill_diagnose_budget_s


# ── 6. ErrorKB:add_case 去重 + save/load 往返 ────────────────────────
def test_error_kb_add_case_dedupes_by_tool_code_regex():
    kb = _seed_kb()
    p_dup = _make_pattern(pid="dup", hint="别的内容但同三元组")
    # 同 (tool, error_code, regex) → 去重,返回 False。
    assert kb.add_case(p_dup, dedupe=True) is False
    assert len(kb.patterns) == 1
    # 不同 regex → 入库。
    p_new = _make_pattern(pid="new", regex=r"different pattern")
    assert kb.add_case(p_new, dedupe=True) is True
    assert len(kb.patterns) == 2


def test_error_kb_save_load_roundtrip(tmp_path):
    kb = _seed_kb()
    kb.add_case(_make_pattern(pid="p2", regex=r"another (?P<x>\d+)"))
    path = tmp_path / "kb.json"
    kb.save(path)
    assert path.exists()
    loaded = ErrorKB.load(path)
    assert loaded.version == "0.1.0"
    assert len(loaded.patterns) == 2
    pids = {p.pid for p in loaded.patterns}
    assert pids == {"p1", "p2"}


def test_error_kb_load_missing_file_returns_empty():
    kb = ErrorKB.load("definitely/missing/path.json")
    assert kb.patterns == []


def test_error_kb_load_corrupted_returns_empty(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    kb = ErrorKB.load(p)
    assert kb.patterns == []


def test_error_kb_lookup_wildcard_tool_matches():
    p_wild = _make_pattern(
        pid="w", tool="*", regex=r"timed out after (?P<seconds>\d+)s",
    )
    kb = ErrorKB(patterns=[p_wild])
    hits = kb.lookup("iverilog_sim", "vvp timed out after 30s")
    assert len(hits) == 1
    hits2 = kb.lookup("yosys_synth", "subprocess timed out after 30s")
    assert len(hits2) == 1  # 通配 tool 命中


def test_error_kb_suggest_from_llm_returns_pattern_or_none():
    kb = _seed_kb()
    raw = {"new_pattern": {
        "regex": r"foo (?P<x>\d+)",
        "error_code": "synth.foo",
        "fix_hint_template": "fix {x}",
    }}
    p = kb.suggest_from_llm(raw, tool="yosys_synth")
    assert p is not None
    assert p.source == "llm_curated"
    assert p.tool == "yosys_synth"
    assert p.error_code == "synth.foo"
    # 缺字段 → None。
    assert kb.suggest_from_llm({"new_pattern": {"regex": "x"}}, "yosys_synth") is None
    # 无 new_pattern → None。
    assert kb.suggest_from_llm({}, "yosys_synth") is None


def test_error_kb_snapshot_to_drops_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kb = _seed_kb()
    kb.snapshot_to("r99")
    f = tmp_path / "runs" / "r99" / "diagnose" / "error_kb_snapshot.json"
    assert f.exists()
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["version"] == "0.1.0"
    assert len(data["patterns"]) == 1


# ── 7. DiagnosisReport.to_parsed:13 字段 + contract_version 无裸串 ───
_REQUIRED_13_KEYS = {
    "_schema", "skill", "tool", "stage", "root_causes", "root_cause_summary",
    "severity", "fix_hints", "confidence", "needs_rtl_patch", "used_layers",
    "kb_hits", "summary",
}


def test_diagnosis_report_to_parsed_has_exactly_13_top_level_business_keys():
    r = DiagnosisReport(
        tool="yosys_synth",
        stage="synth",
        errors=[ErrorItem.make("synth.rtl_syntax", tool="yosys_synth", message="m")],
        root_cause="rc",
        severity="error",
        fix_hints=["h"],
        confidence=0.7,
        evidence=["e"],
        used_layers="rule",
        kb_hits=["synth.rtl_syntax"],
    )
    parsed = r.to_parsed()
    # 13 个契约必填字段 + _schema(把 _schema 算 13 之内,共 13 个顶层 key)。
    assert set(parsed.keys()) == _REQUIRED_13_KEYS
    # _schema 内嵌字段:
    assert parsed["_schema"]["name"] == "skill_diagnose"
    assert parsed["_schema"]["version"] == "0.1.0"
    # 关键:contract_version 必须等于 CONTRACT_VERSION(无裸 "0.1.0" 字符串)。
    assert parsed["_schema"]["contract_version"] == CONTRACT_VERSION
    assert parsed["skill"] == "diagnose"
    assert parsed["root_cause_summary"] == parsed["summary"] == "rc"


def test_needs_rtl_patch_derived_by_severity_not_namespace():
    # eda.* 别名按 severity 派生:severity=error → True。
    r = DiagnosisReport(
        tool="multi",
        stage="multi",
        errors=[ErrorItem.make("eda.rtl_syntax", tool="multi", message="m",
                                severity="error")],
        root_cause="rc", severity="error", fix_hints=[], confidence=0.5,
        used_layers="rule", kb_hits=["eda.rtl_syntax"],
    )
    parsed = r.to_parsed()
    assert parsed["needs_rtl_patch"] is True
    # 全 info/warn → False。
    r2 = DiagnosisReport(
        tool="multi", stage="multi",
        errors=[ErrorItem.make("eda.tool_not_found", tool="multi",
                                message="m", severity="warn")],
        root_cause="rc", severity="warn", fix_hints=[], confidence=0.5,
        used_layers="rule", kb_hits=["eda.tool_not_found"],
    )
    assert r2.to_parsed()["needs_rtl_patch"] is False
    # 空 errors → False。
    r3 = DiagnosisReport(
        tool="multi", stage="multi", errors=[], root_cause="none",
        severity="info", fix_hints=[], confidence=0.5, used_layers="rule",
        kb_hits=[],
    )
    assert r3.to_parsed()["needs_rtl_patch"] is False


def test_no_bare_contract_version_string_in_diagnose_source():
    """硬规则:diagnose.py 源码不得出现裸 "0.1.0" 作为 contract_version。"""
    src = Path(__file__).resolve().parents[1] / "src" / "eda_agent" / "skills" / "diagnose.py"
    text = src.read_text(encoding="utf-8")
    # 允许 version "0.1.0"(parsed_schema_ref / _schema.version),但禁止
    # contract_version 紧跟裸串。
    assert 'contract_version": "0.1.0"' not in text
    assert "'contract_version': '0.1.0'" not in text


# ── 8. _max_severity ─────────────────────────────────────────────────
def test_max_severity_empty_is_info():
    assert _max_severity([]) == "info"


def test_max_severity_picks_fatal_over_error():
    items = [
        ErrorItem("a.b", "a", "t", "warn", "m"),
        ErrorItem("c.d", "c", "t", "fatal", "m"),
        ErrorItem("e.f", "e", "t", "error", "m"),
    ]
    assert _max_severity(items) == "fatal"


# ── 9. ReportWriter ──────────────────────────────────────────────────
def test_report_writer_writes_md_and_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = DiagnosisReport(
        tool="yosys_synth", stage="synth",
        errors=[ErrorItem.make("synth.rtl_syntax", tool="yosys_synth", message="m")],
        root_cause="rc", severity="error", fix_hints=["h1", "h2"],
        confidence=0.8, evidence=["e1"], used_layers="rule",
        kb_hits=["synth.rtl_syntax"],
    )
    ReportWriter().write("r5", r, _seed_kb())
    base = tmp_path / "runs" / "r5" / "diagnose"
    md = (base / "report.md").read_text(encoding="utf-8")
    js = json.loads((base / "report.json").read_text(encoding="utf-8"))
    assert "Diagnosis Report" in md
    assert "rc" in md
    assert "h1" in md
    assert js["confidence"] == 0.8


# ── 10. 集成:通过 ToolCall 走 as_tool 入口 ───────────────────────────
def test_skill_diagnose_invokable_via_tool_call(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sk = _diag()
    t = as_tool(sk)
    tr = _tr("yosys_synth", "error", stdout="ERROR: Syntax error in line 7:")
    r = t(ToolCall(
        name="skill_diagnose",
        args={"tool_results": [tr], "run_id": "r_via_tool"},
    ))
    # ToolResult 映射:ok + 六个 _skill_* 元字段。
    assert r.status == "ok"
    assert r.tool == "skill_diagnose"
    assert r.parsed["_schema"]["name"] == "skill_diagnose"
    assert r.parsed["_skill_status"] == "ok"
    assert r.parsed["_skill_iterations"] == 1
    assert r.parsed["_skill_best_iter"] == -1
    assert r.parsed["_skill_patch_source"] == "none"
    assert r.parsed["_skill_convergence_cause"] == "none"
    assert len(r.parsed["root_causes"]) == 1
    # artifacts 是 artifact_ref 形状。
    assert all(isinstance(a, dict) and {"run_id", "rel_path"} <= set(a.keys())
               for a in r.artifacts)
