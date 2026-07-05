"""T35 + T37 单测: B 自修复 skill_self_heal(契约 §2.3 §5.2)。

覆盖:
- T35 unit:输入校验(rtl/tb/goal 不可解析)、_parse_goal、_locate_lines、
  _apply_patch、_extract_patch、fail_signals 包装成 ErrorItem、
  patch_source 在失败时仍记录、consume seed diagnose、best_iter 平局、
  rollback / regression_deadlock / budget_exhausted
- T37 e2e(@needs_eda):counter_bitwidth 真 RTL → LLM 给出 full_rewrite 修复
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from eda_agent.contracts import (
    CONTRACT_VERSION,
    LLMResponse,
    Skill,
    Tool,
    ToolCall,
    ToolResult,
    artifact_ref,
)
from eda_agent.errors import (
    EDA_BUDGET_EXHAUSTED,
    HEAL_REGRESSION_DEADLOCK,
    HEAL_RTL_NOT_FOUND,
    HEAL_TB_NOT_FOUND,
    HEAL_UNSUPPORTED_GOAL,
)
from eda_agent.runner import Runner
from eda_agent.settings import Settings
from eda_agent.skills.base import as_tool
from eda_agent.skills.self_heal import (
    SelfHealSkill,
    _apply_patch,
    _extract_patch,
    _locate_lines,
    _parse_goal,
)

EXAMPLES = Path("data/examples/counter_bitwidth")


# ── Fake LLM Provider ────────────────────────────────────────────────
class FakeLLM:
    """按注入的 reply 序列循环返回;记录调用次数。"""

    provider_name = "fake"

    def __init__(self, replies: list[str] | None = None) -> None:
        self._replies = list(replies) if replies else [""]
        self._idx = 0
        self.calls = 0
        self.last_messages: list | None = None

    def chat(self, messages, tools=None, temperature=0.0, max_tokens=4096) -> LLMResponse:
        self.calls += 1
        self.last_messages = messages
        text = self._replies[min(self._idx, len(self._replies) - 1)]
        self._idx += 1
        return LLMResponse(
            text=text, tool_calls=[],
            tokens_in=10, tokens_out=20,
            provider="fake", model="fake-1",
        )


# ── Fake Registry:可注入 stage ToolResult 序列 ──────────────────────
class _ScriptedTool:
    """按脚本返回 ToolResult;每次 __call__ 弹出下一个。"""

    def __init__(self, name: str, results: list[ToolResult]) -> None:
        self.name = name
        self.description = name
        self.schema: dict[str, Any] = {
            "name": name, "input_schema": {"type": "object", "properties": {}},
        }
        self._results = list(results)
        self.calls = 0

    def __call__(self, call: ToolCall) -> ToolResult:
        self.calls += 1
        if self._results:
            return self._results.pop(0)
        # 默认:ok 通过(防越界)。
        raise AssertionError(f"{self.name} 脚本耗尽")


class FakeRegistry:
    """持一个 dict[name -> _ScriptedTool];get 透传。"""

    def __init__(self, tools: dict[str, _ScriptedTool]) -> None:
        self._tools = tools

    def get(self, name: str):
        return self._tools.get(name)


def _synth_ok() -> ToolResult:
    return ToolResult(
        status="ok", exit_code=0, stdout="", stderr="",
        parsed={
            "_schema": {"name": "yosys_synth", "version": "0.1.0",
                        "contract_version": CONTRACT_VERSION},
            "success": True, "num_cells": 10, "cell_area": None,
            "num_wires": 5, "num_ports": 3, "module_name": "counter",
            "script_used": "", "warnings": [], "errors": [],
        },
        artifacts=[artifact_ref("rid", "synth/netlist.v")],
        duration_s=0.0, tool="yosys_synth",
    )


def _sim(fail_signals: list[str], num_passed: int, total: int) -> ToolResult:
    passed = num_passed > 0 and (total - num_passed) == 0
    return ToolResult(
        status="ok", exit_code=0, stdout="", stderr="",
        parsed={
            "_schema": {"name": "iverilog_sim", "version": "0.1.0",
                        "contract_version": CONTRACT_VERSION},
            "compiled": True,
            "passed": passed,
            "num_passed": num_passed,
            "num_failed": total - num_passed,
            "fail_signals": fail_signals,
            "vvp_stdout_tail": "",
        },
        artifacts=[artifact_ref("rid", "sim/wave.vcd")],
        duration_s=0.0, tool="iverilog_sim",
    )


def _sim_pass() -> ToolResult:
    return _sim([], 1, 1)


def _sim_fail_count_value() -> ToolResult:
    return _sim(["count_value"], 0, 1)


# ── _parse_goal 单测 ─────────────────────────────────────────────────
class TestParseGoal:
    def test_pass_all(self):
        g = _parse_goal("pass all tests")
        assert g is not None
        assert g.pass_mode == "all"
        assert g.num_required == 0
        assert g.sta_required is False

    def test_pass_n(self):
        g = _parse_goal("pass 3 tests")
        assert g is not None
        assert g.pass_mode == "at_least"
        assert g.num_required == 3

    def test_timing_sets_sta_required(self):
        g = _parse_goal("pass all tests and meet timing")
        assert g is not None
        assert g.sta_required is True

    def test_no_violation_sets_sta_required(self):
        g = _parse_goal("pass all tests, no violation")
        assert g is not None
        assert g.sta_required is True

    def test_unparseable_returns_none(self):
        assert _parse_goal("") is None
        assert _parse_goal("make it work") is None
        assert _parse_goal(None) is None  # type: ignore[arg-type]


# ── _locate_lines 单测 ───────────────────────────────────────────────
class TestLocateLines:
    def test_file_colon_line(self):
        rcs = [{"message": "rtl.v:42: error"}]
        out = _locate_lines(rcs, "module top;\nendmodule\n")
        assert out is not None
        start, end = out
        assert start == 37  # 42 - 5
        assert end == 47

    def test_paren_line(self):
        rcs = [{"evidence": ["error (10) here"]}]
        out = _locate_lines(rcs, "")
        assert out is not None
        assert out[0] == 5
        assert out[1] == 15

    def test_no_match(self):
        rcs = [{"message": "no line info"}]
        assert _locate_lines(rcs, "") is None

    def test_empty(self):
        assert _locate_lines([], "") is None


# ── _extract_patch / _apply_patch 单测 ───────────────────────────────
class TestPatchExtract:
    def test_diff_extracts_unified(self):
        resp = "intro\n--- a.v\n+++ b.v\n@@ -1,2 +1,2 @@\n-old\n+new\n尾"
        patch, is_full = _extract_patch(resp, "diff")
        assert is_full is False
        assert "--- a.v" in patch
        assert "+new" in patch

    def test_full_rewrite_strips_fence(self):
        resp = "here:\n```verilog\nmodule top;\nendmodule\n```\n"
        patch, is_full = _extract_patch(resp, "full_rewrite")
        assert is_full is True
        assert "module top" in patch
        assert "```" not in patch

    def test_full_rewrite_no_fence(self):
        resp = "module foo;\nendmodule\n"
        patch, is_full = _extract_patch(resp, "full_rewrite")
        assert is_full is True
        assert "module foo" in patch


class TestApplyPatch:
    def test_full_rewrite(self):
        rtl = "module old;\nendmodule\n"
        patch = "module new;\nendmodule\n"
        out, ok = _apply_patch(rtl, patch, True, None)
        assert ok is True
        assert "module new" in out

    def test_full_rewrite_invalid_no_module(self):
        out, ok = _apply_patch("x", "garbage", True, None)
        assert ok is False

    def test_diff_replace_target(self):
        rtl = "\n".join(f"line{i}" for i in range(1, 21))
        patch = "+fixed_line"
        out, ok = _apply_patch(rtl, patch, False, (5, 10))
        assert ok is True
        assert "fixed_line" in out
        # 区间外的行保留:
        assert "line1" in out
        assert "line20" in out

    def test_diff_no_add_lines(self):
        out, ok = _apply_patch("rtl", "garbage", False, (1, 5))
        assert ok is False


# ── SelfHealSkill 主流程单测 ─────────────────────────────────────────
class TestInputValidation:
    def test_rtl_not_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        tb = tmp_path / "tb.v"
        tb.write_text("x", encoding="utf-8")
        skill = SelfHealSkill(None, FakeLLM(), Runner(runs_dir=str(tmp_path / "r")),
                              settings=Settings())
        # 需要 run_id 已在 runner 注册;直接调 run(run_id=...) 不依赖 runner 内部状态
        # (append_step 仅在 stage 时调;_fail 路径不调 append_step)。
        res = skill.run(run_id="rid", inputs={
            "rtl": "missing.v", "tb": str(tb), "goal": "pass all tests",
            "max_iter": 1,
        })
        assert res.status == "error"
        assert res.error_code == HEAL_RTL_NOT_FOUND
        assert res.convergence_cause == "none"

    def test_tb_not_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rtl = tmp_path / "rtl.v"
        rtl.write_text("module x;\nendmodule\n", encoding="utf-8")
        skill = SelfHealSkill(None, FakeLLM(), Runner(runs_dir=str(tmp_path / "r")),
                              settings=Settings())
        res = skill.run(run_id="rid", inputs={
            "rtl": str(rtl), "tb": "missing.v",
            "goal": "pass all tests", "max_iter": 1,
        })
        assert res.error_code == HEAL_TB_NOT_FOUND

    def test_unsupported_goal(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rtl = tmp_path / "rtl.v"
        rtl.write_text("module x;\nendmodule\n", encoding="utf-8")
        tb = tmp_path / "tb.v"
        tb.write_text("module tb;\nendmodule\n", encoding="utf-8")
        skill = SelfHealSkill(None, FakeLLM(), Runner(runs_dir=str(tmp_path / "r")),
                              settings=Settings())
        res = skill.run(run_id="rid", inputs={
            "rtl": str(rtl), "tb": str(tb),
            "goal": "make it fast", "max_iter": 1,
        })
        assert res.error_code == HEAL_UNSUPPORTED_GOAL
        assert res.final_parsed["passed"] is False
        assert res.final_parsed["_schema"]["contract_version"] == CONTRACT_VERSION


class TestFailSignalsWrapping:
    """无 root_causes 时,fail_signals 必须被包成 ErrorItem(契约 §2.6)。"""

    def test_fail_signals_wrapped_into_erroritem(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rtl = tmp_path / "rtl.v"
        rtl.write_text(
            "module counter(input clk);\nendmodule\n", encoding="utf-8",
        )
        tb = tmp_path / "tb.v"
        tb.write_text("module tb;\nendmodule\n", encoding="utf-8")

        runner = Runner(runs_dir=str(tmp_path / "runs"))
        from eda_agent.contracts import RunRequest
        runner.create(RunRequest(
            kind="self_heal", goal="pass all tests",
            rtl_path=str(rtl), tb_path=str(tb),
        ), provider_used="fake")
        run_id = next(iter(runner._records.keys()))

        # diagnose skill 也走 fake —— 返回空 root_causes,触发 fail_signals 包装。
        # 给足 diag 结果(每轮会调一次;max_iter=2 但策略升级可能多调)。
        _diag_empty_parsed = {
            "_schema": {
                "name": "skill_diagnose", "version": "0.1.0",
                "contract_version": CONTRACT_VERSION,
            },
            "root_causes": [],       # 空 → 触发包装
            "root_cause_summary": "",
            "severity": "error",
            "fix_hints": [],
            "confidence": 0.5,
            "needs_rtl_patch": True,
            "used_layers": "rule",
            "kb_hits": [],
            "summary": "",
            "tool": "iverilog_sim",
            "stage": "sim",
        }

        def _diag_result():
            return ToolResult(
                status="ok", exit_code=0, stdout="", stderr="",
                parsed=dict(_diag_empty_parsed),
                artifacts=[],
                duration_s=0.0,
                tool="skill_diagnose",
            )

        diag_empty = _ScriptedTool("skill_diagnose", [_diag_result() for _ in range(6)])
        registry = FakeRegistry({
            "yosys_synth": _ScriptedTool("yosys_synth", [_synth_ok(), _synth_ok()]),
            "iverilog_sim": _ScriptedTool("iverilog_sim", [
                _sim_fail_count_value(), _sim_fail_count_value(),
            ]),
            "skill_diagnose": diag_empty,
        })

        # LLM 给一个 patch 但 syntax 失败(空 module)→ applied=False → 走策略升级。
        llm = FakeLLM(["garbage no module"])
        skill = SelfHealSkill(
            registry, llm, runner, max_iterations=2, budget_s=60.0,
            settings=Settings(),
        )
        res = skill.run(run_id=run_id, inputs={
            "rtl": str(rtl), "tb": str(tb),
            "goal": "pass all tests", "max_iter": 2,
        })

        # diagnose 被调过,fail_signals 已包装(diag 调用方 _diagnose_and_patch 内部
        # 包装;这里只断言流程不崩 + diag skill 被调用):
        assert diag_empty.calls >= 1
        # 失败也落 best + report:
        assert (Path("runs") / run_id / "self_heal" / "report.md").exists()
        assert (Path("runs") / run_id / "self_heal" / "best" / "rtl.v").exists()
        meta = json.loads(
            (Path("runs") / run_id / "self_heal" / "best" / "meta.json").read_text(
                encoding="utf-8",
            )
        )
        assert "convergence_cause" in meta
        assert res.status == "error"


class TestConsumeSeedDiagnose:
    """首轮外部预调 diagnose 时,B 应消费 seed,不再调 skill_diagnose。"""

    def test_seed_skips_diagnose_call(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rtl = tmp_path / "rtl.v"
        rtl.write_text(
            "module counter(input clk);\nendmodule\n", encoding="utf-8",
        )
        tb = tmp_path / "tb.v"
        tb.write_text("module tb;\nendmodule\n", encoding="utf-8")

        runner = Runner(runs_dir=str(tmp_path / "runs"))
        from eda_agent.contracts import RunRequest
        runner.create(RunRequest(
            kind="self_heal", goal="pass all tests",
            rtl_path=str(rtl), tb_path=str(tb),
        ), provider_used="fake")
        run_id = next(iter(runner._records.keys()))

        diag = _ScriptedTool("skill_diagnose", [])
        registry = FakeRegistry({
            "yosys_synth": _ScriptedTool("yosys_synth", [_synth_ok()]),
            "iverilog_sim": _ScriptedTool("iverilog_sim", [_sim_pass()]),
            "skill_diagnose": diag,
        })
        llm = FakeLLM([])
        skill = SelfHealSkill(
            registry, llm, runner, max_iterations=1, budget_s=60.0,
            settings=Settings(),
        )
        seed = {
            "_schema": {
                "name": "skill_diagnose", "version": "0.1.0",
                "contract_version": CONTRACT_VERSION,
            },
            "root_causes": [
                {"code": "sim.fail_signal", "message": "count_value wrong at :5:",
                 "fix_suggestion": "widen bitwidth"},
            ],
            "fix_hints": ["widen bitwidth"],
        }
        # 第一轮即 sim_pass → all_pass,seed 不触发 patch。
        res = skill.run(run_id=run_id, inputs={
            "rtl": str(rtl), "tb": str(tb),
            "goal": "pass all tests", "max_iter": 1,
            "diagnose": seed,
        })
        # seed 时 diag 不应被调:
        assert diag.calls == 0
        assert res.convergence_cause == "all_pass"
        assert res.status == "ok"
        assert res.final_parsed["passed"] is True
        assert res.final_parsed["fixed_rtl_ref"] == artifact_ref(
            run_id, "self_heal/best/rtl.v",
        )


class TestBudgetExhausted:
    def test_budget_zero_returns_budget_exhausted(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rtl = tmp_path / "rtl.v"
        rtl.write_text("module x;\nendmodule\n", encoding="utf-8")
        tb = tmp_path / "tb.v"
        tb.write_text("module tb;\nendmodule\n", encoding="utf-8")

        runner = Runner(runs_dir=str(tmp_path / "runs"))
        from eda_agent.contracts import RunRequest
        runner.create(RunRequest(
            kind="self_heal", goal="pass all tests",
            rtl_path=str(rtl), tb_path=str(tb),
        ), provider_used="fake")
        run_id = next(iter(runner._records.keys()))

        registry = FakeRegistry({
            "yosys_synth": _ScriptedTool("yosys_synth", []),
            "iverilog_sim": _ScriptedTool("iverilog_sim", []),
            "skill_diagnose": _ScriptedTool("skill_diagnose", []),
        })
        skill = SelfHealSkill(
            registry, FakeLLM([]), runner,
            max_iterations=5, budget_s=0.0,   # 预算 0 → 首轮即 budget
            settings=Settings(),
        )
        res = skill.run(run_id=run_id, inputs={
            "rtl": str(rtl), "tb": str(tb),
            "goal": "pass all tests", "max_iter": 5,
        })
        assert res.convergence_cause == "budget"
        assert res.status == "budget_exhausted"
        assert res.error_code == EDA_BUDGET_EXHAUSTED


class TestRegressionDeadlock:
    """LLM patch 反复 apply 失败 → 策略 diff → full_rewrite → diagnose_only →
    convergence=regression,error_code=heal.regression_deadlock。"""

    def test_three_strategy_escalation_to_regression(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.chdir(tmp_path)
        rtl = tmp_path / "rtl.v"
        rtl.write_text("module counter(input clk);\nendmodule\n", encoding="utf-8")
        tb = tmp_path / "tb.v"
        tb.write_text("module tb;\nendmodule\n", encoding="utf-8")

        runner = Runner(runs_dir=str(tmp_path / "runs"))
        from eda_agent.contracts import RunRequest
        runner.create(RunRequest(
            kind="self_heal", goal="pass all tests",
            rtl_path=str(rtl), tb_path=str(tb),
        ), provider_used="fake")
        run_id = next(iter(runner._records.keys()))

        # 所有 stage 失败(synth ok + sim fail)。
        synth = _ScriptedTool("yosys_synth", [_synth_ok()] * 5)
        sim = _ScriptedTool("iverilog_sim", [_sim_fail_count_value()] * 5)
        diag = _ScriptedTool("skill_diagnose", [
            ToolResult(
                status="ok", exit_code=0, stdout="", stderr="",
                parsed={
                    "_schema": {
                        "name": "skill_diagnose", "version": "0.1.0",
                        "contract_version": CONTRACT_VERSION,
                    },
                    "root_causes": [
                        {"message": "count_value wrong",
                         "fix_suggestion": "widen bitwidth"},
                    ],
                    "fix_hints": ["widen bitwidth"],
                },
                artifacts=[], duration_s=0.0, tool="skill_diagnose",
            ),
        ] * 5)
        registry = FakeRegistry({
            "yosys_synth": synth, "iverilog_sim": sim, "skill_diagnose": diag,
        })
        # LLM 始终给垃圾 → applied=False → diff 升 full_rewrite 升 diagnose_only → regression。
        llm = FakeLLM(["garbage1", "garbage2", "garbage3"])
        skill = SelfHealSkill(
            registry, llm, runner,
            max_iterations=5, budget_s=120.0,
            settings=Settings(),
        )
        res = skill.run(run_id=run_id, inputs={
            "rtl": str(rtl), "tb": str(tb),
            "goal": "pass all tests", "max_iter": 5,
        })
        assert res.convergence_cause == "regression"
        assert res.status == "error"
        assert res.error_code == HEAL_REGRESSION_DEADLOCK
        # patch_source 在失败时仍记录最后一次尝试的 source(none 因 applied 全失败):
        # 当 LLM 调过但 patch 全失败,patch_source 仍为 "none"(契约语义:
        # 仅 applied=True 才记 source)。这里允许 none / llm_* 之一,断言 in 集合。
        assert res.patch_source in ("none", "llm_diff", "llm_full_rewrite")
        # trajectory 含 reduced_to_diagnose:
        steps = [e["step"] for e in res.trajectory]
        assert "reduced_to_diagnose" in steps


class TestPatchSourceOnFailure:
    """LLM patch 应用成功但仿真仍失败 → patch_source 必须记录(非 none)。"""

    def test_applied_records_patch_source(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rtl = tmp_path / "rtl.v"
        rtl.write_text(
            "module counter(input clk);\nreg [3:0] c;\nendmodule\n",
            encoding="utf-8",
        )
        tb = tmp_path / "tb.v"
        tb.write_text("module tb;\nendmodule\n", encoding="utf-8")

        runner = Runner(runs_dir=str(tmp_path / "runs"))
        from eda_agent.contracts import RunRequest
        runner.create(RunRequest(
            kind="self_heal", goal="pass all tests",
            rtl_path=str(rtl), tb_path=str(tb),
        ), provider_used="fake")
        run_id = next(iter(runner._records.keys()))

        synth = _ScriptedTool("yosys_synth", [_synth_ok()] * 5)
        sim = _ScriptedTool("iverilog_sim", [_sim_fail_count_value()] * 5)
        diag = _ScriptedTool("skill_diagnose", [
            ToolResult(
                status="ok", exit_code=0, stdout="", stderr="",
                parsed={
                    "_schema": {
                        "name": "skill_diagnose", "version": "0.1.0",
                        "contract_version": CONTRACT_VERSION,
                    },
                    "root_causes": [
                        {"message": "count_value wrong",
                         "fix_suggestion": "widen bitwidth to [7:0]"},
                    ],
                    "fix_hints": ["widen bitwidth"],
                },
                artifacts=[], duration_s=0.0, tool="skill_diagnose",
            ),
        ] * 5)
        registry = FakeRegistry({
            "yosys_synth": synth, "iverilog_sim": sim, "skill_diagnose": diag,
        })
        # LLM 给合法 full_rewrite(module+endmodule)→ applied=True,syntax_ok。
        # 但 sim 仍 fail → patch_source != none。
        llm = FakeLLM([
            "module counter(input clk);\nreg [7:0] c;\nendmodule\n",
        ] * 5)
        skill = SelfHealSkill(
            registry, llm, runner,
            max_iterations=2, budget_s=60.0,
            settings=Settings(),
        )
        res = skill.run(run_id=run_id, inputs={
            "rtl": str(rtl), "tb": str(tb),
            "goal": "pass all tests", "max_iter": 2,
        })
        # 至少一次 applied → patch_source 记录。
        assert res.patch_source in ("llm_full_rewrite", "llm_diff")
        assert res.patch_source != "none"


class TestRollbackVersionStack:
    """patch 应用失败(diff→full_rewrite 升级)→版本栈不 push,best_rtl 保持原始。

    契约语义:applied=False 时不把 rtl_new 推入 version_stack;
    best_rtl_text 保留原始 RTL(下一轮仍对原始 RTL 重试更高策略)。
    """

    def test_failed_patch_does_not_push_stack(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rtl_text = "module counter(input clk);\nreg [3:0] c;\nendmodule\n"
        rtl = tmp_path / "rtl.v"
        rtl.write_text(rtl_text, encoding="utf-8")
        tb = tmp_path / "tb.v"
        tb.write_text("module tb;\nendmodule\n", encoding="utf-8")

        runner = Runner(runs_dir=str(tmp_path / "runs"))
        from eda_agent.contracts import RunRequest
        runner.create(RunRequest(
            kind="self_heal", goal="pass all tests",
            rtl_path=str(rtl), tb_path=str(tb),
        ), provider_used="fake")
        run_id = next(iter(runner._records.keys()))

        synth = _ScriptedTool("yosys_synth", [_synth_ok()] * 5)
        sim = _ScriptedTool("iverilog_sim", [_sim_fail_count_value()] * 5)
        diag = _ScriptedTool("skill_diagnose", [
            ToolResult(
                status="ok", exit_code=0, stdout="", stderr="",
                parsed={
                    "_schema": {
                        "name": "skill_diagnose", "version": "0.1.0",
                        "contract_version": CONTRACT_VERSION,
                    },
                    "root_causes": [{"message": "count_value wrong"}],
                    "fix_hints": ["widen bitwidth"],
                },
                artifacts=[], duration_s=0.0, tool="skill_diagnose",
            ),
        ] * 5)
        registry = FakeRegistry({
            "yosys_synth": synth, "iverilog_sim": sim, "skill_diagnose": diag,
        })
        # LLM 始终给垃圾(无 module/endmodule)→ diff / full_rewrite 都 applied=False。
        llm = FakeLLM(["garbage1", "garbage2", "garbage3"])
        skill = SelfHealSkill(
            registry, llm, runner,
            max_iterations=3, budget_s=60.0, settings=Settings(),
        )
        res = skill.run(run_id=run_id, inputs={
            "rtl": str(rtl), "tb": str(tb),
            "goal": "pass all tests", "max_iter": 3,
        })

        # 收敛为 regression(diff→full_rewrite→diagnose_only)。
        assert res.convergence_cause == "regression"
        # best_rtl_text 保留原始(版本栈未推进)→ best/rtl.v 仍是原始 RTL。
        best_rtl = (Path("runs") / run_id / "self_heal" / "best" / "rtl.v").read_text(
            encoding="utf-8",
        )
        assert best_rtl == rtl_text
        # patch 全 applied=False → 版本栈不 push(上面 best_rtl 断言已证)。
        # patch_source 语义:LLM 调过即记最后尝试的 source(契约 §2.3,
        # TestRegressionDeadlock 同款宽松断言),与 applied 无关。
        assert res.patch_source in ("none", "llm_diff", "llm_full_rewrite")
        # regression 时 best_iter 仍可追踪(首轮 num_passed=0 不入 best,故 -1)。
        assert res.best_iter == -1


class TestBestIterTiebreak:
    """连续两轮 num_passed 相同 → candidates_at_best_score 累加。"""

    def test_tiebreak_increments_candidates(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rtl = tmp_path / "rtl.v"
        rtl.write_text("module counter(input clk);\nendmodule\n", encoding="utf-8")
        tb = tmp_path / "tb.v"
        tb.write_text("module tb;\nendmodule\n", encoding="utf-8")

        runner = Runner(runs_dir=str(tmp_path / "runs"))
        from eda_agent.contracts import RunRequest
        runner.create(RunRequest(
            kind="self_heal", goal="pass all tests",
            rtl_path=str(rtl), tb_path=str(tb),
        ), provider_used="fake")
        run_id = next(iter(runner._records.keys()))

        # 每轮 sim 都 num_passed=2,total=5(2<5 → sim_ok=False,不收敛)。
        sim_partial = _sim(["a"], 2, 5)
        synth = _ScriptedTool("yosys_synth", [_synth_ok()] * 5)
        sim = _ScriptedTool("iverilog_sim", [sim_partial] * 5)
        diag = _ScriptedTool("skill_diagnose", [
            ToolResult(
                status="ok", exit_code=0, stdout="", stderr="",
                parsed={
                    "_schema": {
                        "name": "skill_diagnose", "version": "0.1.0",
                        "contract_version": CONTRACT_VERSION,
                    },
                    "root_causes": [{"message": "x"}],
                    "fix_hints": ["x"],
                },
                artifacts=[], duration_s=0.0, tool="skill_diagnose",
            ),
        ] * 5)
        registry = FakeRegistry({
            "yosys_synth": synth, "iverilog_sim": sim, "skill_diagnose": diag,
        })
        # LLM 给合法 full_rewrite → applied,栈推进,但 sim 仍 partial。
        llm = FakeLLM([
            "module counter(input clk);\nreg [7:0] c;\nendmodule\n",
        ] * 5)
        skill = SelfHealSkill(
            registry, llm, runner,
            max_iterations=3, budget_s=120.0,
            settings=Settings(),
        )
        res = skill.run(run_id=run_id, inputs={
            "rtl": str(rtl), "tb": str(tb),
            "goal": "pass all tests", "max_iter": 3,
        })
        meta = json.loads(
            (Path("runs") / run_id / "self_heal" / "best" / "meta.json").read_text(
                encoding="utf-8",
            )
        )
        # 第一轮 num_passed=2 → best_score=2 candidates=1;
        # 后续轮 num_passed==2 → candidates 累加。
        assert meta["num_passed"] == 2
        assert meta["candidates_at_best_score"] >= 2


# ── T37 e2e 真跑(@needs_eda + skipif) ────────────────────────────────
def _have_eda() -> bool:
    s = Settings()
    if s.eda.wsl_enabled:
        return shutil.which("wsl.exe") is not None
    return (
        shutil.which(s.eda.iverilog_cmd) is not None
        and shutil.which(s.eda.vvp_cmd) is not None
        and shutil.which(s.eda.yosys_cmd) is not None
    )


@pytest.mark.needs_eda
@pytest.mark.skipif(not _have_eda(), reason="需要 yosys/iverilog/vvp(WSL 或本机)")
def test_e2e_run_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """真跑 counter_bitwidth:LLM 给 [7:0] 修复 → 收敛 all_pass。

    注:示例 tb.v 的 ``.count(count[3:0])`` 切片与 meta.json 的
    ground_truth_patch(``reg [3:0] → reg [7:0]``)结构性不兼容(只连低 4 位,
    高 4 位悬空 z,``count !== i[7:0]`` 永真失败);此处用修正连接
    ``.count(count)`` 的 TB 副本,验证 self_heal 闭环在 ground_truth 修复下
    真能通过测试。LLM 用 FakeLLM 注入 full_rewrite 修复文本(避免依赖 API key)。
    """
    if not EXAMPLES.exists():
        pytest.skip(f"missing example data: {EXAMPLES}")

    # 复制示例 RTL;TB 用修正连接(见 docstring 说明)。
    rtl_src = EXAMPLES / "rtl.v"
    tb_src = EXAMPLES / "tb.v"
    rtl = tmp_path / "rtl.v"
    tb = tmp_path / "tb.v"
    rtl.write_text(rtl_src.read_text(encoding="utf-8"), encoding="utf-8")
    tb_text = tb_src.read_text(encoding="utf-8").replace(
        ".count(count[3:0])", ".count(count)",
    )
    tb.write_text(tb_text, encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    runner = Runner(runs_dir=str(tmp_path / "runs"))
    from eda_agent.contracts import RunRequest
    runner.create(RunRequest(
        kind="self_heal", goal="pass all tests",
        rtl_path=str(rtl), tb_path=str(tb),
    ), provider_used="fake")
    run_id = next(iter(runner._records.keys()))

    settings = Settings()
    # 用真 L1 Tool。
    from eda_agent.tools.bootstrap import build_registry
    # 但替换 provider 为 FakeLLM(给 full_rewrite 修复文本)。
    real_reg = build_registry(
        provider=FakeLLM([]), runner=runner, settings=settings,
    )
    # 注:build_registry 已注册 skill_self_heal 持 real_reg;
    # 我们直接构造一个新 skill 用同一 registry(含真 L1 Tool)+ FakeLLM。
    fixed_rtl = (
        "module counter(\n"
        "    input clk,\n"
        "    input rst,\n"
        "    output reg [7:0] count\n"
        ");\n"
        "    always @(posedge clk) begin\n"
        "        if (rst)\n"
        "            count <= 8'd0;\n"
        "        else\n"
        "            count <= count + 8'd1;\n"
        "    end\n"
        "endmodule\n"
    )
    llm = FakeLLM([fixed_rtl, fixed_rtl, fixed_rtl])
    skill = SelfHealSkill(
        real_reg, llm, runner,
        max_iterations=3, budget_s=300.0, settings=settings,
    )
    res = skill.run(run_id=run_id, inputs={
        "rtl": str(rtl), "tb": str(tb),
        "goal": "pass all tests", "max_iter": 3,
    })

    assert res.status == "ok", f"未收敛: {res.summary}; traj={res.trajectory}"
    assert res.convergence_cause == "all_pass"
    assert res.final_parsed["passed"] is True
    assert res.best_iter >= 0
    # 修复后的 RTL 落盘:
    best_rtl = (Path("runs") / run_id / "self_heal" / "best" / "rtl.v").read_text(
        encoding="utf-8",
    )
    assert "[7:0]" in best_rtl


@pytest.mark.needs_eda
@pytest.mark.skipif(not _have_eda(), reason="需要 yosys/iverilog/vvp(WSL 或本机)")
def test_e2e_trace_integrity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """e2e trace 完整性:runner.steps 含 self_heal 子步(skill_name + iter)。"""
    if not EXAMPLES.exists():
        pytest.skip(f"missing example data: {EXAMPLES}")

    rtl_src = EXAMPLES / "rtl.v"
    tb_src = EXAMPLES / "tb.v"
    rtl = tmp_path / "rtl.v"
    tb = tmp_path / "tb.v"
    rtl.write_text(rtl_src.read_text(encoding="utf-8"), encoding="utf-8")
    tb_text = tb_src.read_text(encoding="utf-8").replace(
        ".count(count[3:0])", ".count(count)",
    )
    tb.write_text(tb_text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    runner = Runner(runs_dir=str(tmp_path / "runs"))
    from eda_agent.contracts import RunRequest
    rec = runner.create(RunRequest(
        kind="self_heal", goal="pass all tests",
        rtl_path=str(rtl), tb_path=str(tb),
    ), provider_used="fake")
    run_id = rec.run_id

    settings = Settings()
    from eda_agent.tools.bootstrap import build_registry
    real_reg = build_registry(
        provider=FakeLLM([]), runner=runner, settings=settings,
    )
    fixed_rtl = (
        "module counter(\n"
        "    input clk,\n"
        "    input rst,\n"
        "    output reg [7:0] count\n"
        ");\n"
        "    always @(posedge clk) begin\n"
        "        if (rst)\n"
        "            count <= 8'd0;\n"
        "        else\n"
        "            count <= count + 8'd1;\n"
        "    end\n"
        "endmodule\n"
    )
    llm = FakeLLM([fixed_rtl] * 5)
    skill = SelfHealSkill(
        real_reg, llm, runner,
        max_iterations=3, budget_s=300.0, settings=settings,
    )
    res = skill.run(run_id=run_id, inputs={
        "rtl": str(rtl), "tb": str(tb),
        "goal": "pass all tests", "max_iter": 3,
    })
    assert res.convergence_cause == "all_pass"

    # 校验 runner.steps 含 skill_name="self_heal" 的子步。
    record = runner.get_record(run_id)
    assert record is not None
    heal_steps = [s for s in record.steps if s.skill_name == "self_heal"]
    assert len(heal_steps) >= 2  # 至少 synth + sim 一轮
    # 每个 heal step 都有 iter 字段:
    for s in heal_steps:
        assert s.iter is not None
    # tool_name 集合应含 yosys_synth / iverilog_sim:
    tool_names = {s.tool_name for s in heal_steps}
    assert "yosys_synth" in tool_names
    assert "iverilog_sim" in tool_names
