"""tests/test_experiment_manifest.py —— T51/T53 manifest 落盘 + summarize(不需 LLM/EDA)。

用 stub Tool(yosys ok / iverilog 基线失败 → diagnose needs_patch
→ self_heal all_pass + 独立验证通过)+ FakeLLMProvider,跑 CPlanner.execute,
断言:
- runs/<run_id>/experiment_manifest.json 存在且 14 字段齐全;
- baseline_run_id / design_id / fault_type / contract_version 透传正确;
- self_heal_pass_rate==1.0(convergence=all_pass);
- B 的 best/meta.json 字段 candidates_at_best_score 被 manifest 正确读取
  (用 SelfHealStubTool 在 __call__ 内副作用落 best/meta.json);
- summarize_eval.summarize(manifests) 聚合 by_fault_type.bitwidth.total>=1。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from eda_agent.contracts import (
    CONTRACT_VERSION,
    ToolCall,
    ToolResult,
    artifact_ref,
)  # noqa: F401  (ToolResult/ToolCall 用于 stub 构造)
from eda_agent.planner.c_planner import CPlanner
from eda_agent.runner import Runner

from tests._planner_helpers import (
    StubTool,
    diagnose_needs_patch,
    echo_schema,
    make_registry,
    make_settings,
    sim_result,
    synth_ok,
    FakeLLMProvider,
)

# 复用 summarize 纯函数(脚本头部 sys.path 已在 pyproject pythonpath=src 配好)
from scripts.summarize_eval import summarize  # noqa: E402


# ── SelfHealStubTool:在 __call__ 内副作用落 best/meta.json(让 manifest 读到) ──
class _SelfHealStubWithMeta(StubTool):
    """skill_self_heal stub:返回 all_pass + 副作用落 best/meta.json。

    ``best_meta`` 默认 {"best_iter":0,"num_passed":1,"convergence_cause":"all_pass",
    "candidates_at_best_score":3};落盘到 ``runs/<run_id>/self_heal/best/meta.json``。
    run_id 从注入的 ``runner`` 当前活跃 RunRecord 取(CPlanner 调 skill_self_heal 时
    父 RunRecord 已 create,runner._records 最后一项即本次 run_id)。
    """

    def __init__(
        self,
        runner: Runner,
        best_meta: dict[str, Any] | None = None,
    ) -> None:
        # result 用 _planner_helpers.self_heal_all_pass 的同款 parsed
        r = ToolResult(
            status="ok",
            exit_code=0,
            stdout="",
            stderr="",
            parsed={
                "_schema": {
                    "name": "skill_self_heal",
                    "version": "0.1.0",
                    "contract_version": CONTRACT_VERSION,
                },
                "passed": True,
                "iterations": 1,
                "best_iter": 0,
                "convergence_cause": "all_pass",
                "patch_source": "llm_full_rewrite",
                "fixed_rtl_ref": artifact_ref("<run_id>", "self_heal/best/rtl.v"),
            },
            artifacts=[artifact_ref("<run_id>", "self_heal/best/rtl.v")],
            duration_s=0.001,
            tool="skill_self_heal",
            error_code=None,
            error_hint=None,
        )
        super().__init__("skill_self_heal", [r], schema=echo_schema())
        self._runner = runner
        self._best_meta = best_meta or {
            "best_iter": 0,
            "num_passed": 1,
            "convergence_cause": "all_pass",
            "candidates_at_best_score": 3,
        }

    def __call__(self, call: ToolCall) -> ToolResult:
        # 副作用:落 best/meta.json;run_id 取 runner 当前活跃 RunRecord(最后一项)。
        run_id = "stub"
        records = getattr(self._runner, "_records", {}) or {}
        if records:
            # dict 保留插入序,最后一项是最新 create 的父 run
            run_id = list(records.keys())[-1]
        best_dir = Path("runs") / run_id / "self_heal" / "best"
        best_dir.mkdir(parents=True, exist_ok=True)
        (best_dir / "rtl.v").write_text("// stub fixed rtl\n", encoding="utf-8")
        (best_dir / "meta.json").write_text(
            json.dumps(self._best_meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return super().__call__(call)


# ── 14 字段集(契约 §2.4 self_heal manifest) ──────────────────────────
_MANIFEST_FIELDS = {
    "run_id", "baseline_run_id", "design_id", "fault_type",
    "baseline_pass_rate", "self_heal_pass_rate", "self_heal_convergence",
    "self_heal_best_iter", "planner_iterations", "llm_calls",
    "tokens_total", "wall_time_s", "candidates_at_best_score",
    "contract_version",
}


def _build_planner(tmp_path: Path):
    os.chdir(tmp_path)
    settings = make_settings(mode="rule", run_budget_s=600, max_iter=20)
    runner = Runner(runs_dir="runs", settings=settings)

    synth = StubTool("yosys_synth", [synth_ok()], schema=echo_schema())
    # sim:基线失败 → 独立验证通过
    sim = StubTool(
        "iverilog_sim",
        [sim_result(False, num_passed=0, num_failed=1),
         sim_result(True, num_passed=1, num_failed=0)],
        schema=echo_schema(),
    )
    diag = StubTool("skill_diagnose", [diagnose_needs_patch()], schema=echo_schema())
    heal = _SelfHealStubWithMeta(runner=runner)  # 副作用落 best/meta.json
    reg = make_registry([
        (synth, "yosys_synth", "synth", echo_schema()),
        (sim, "iverilog_sim", "sim", echo_schema()),
        (diag, "skill_diagnose", "skill", echo_schema()),
        (heal, "skill_self_heal", "skill", echo_schema()),
    ])
    planner = CPlanner(
        registry=reg, llm=FakeLLMProvider([]),
        runner=runner, settings=settings,
    )
    return planner


def test_self_heal_manifest_14_fields_and_transparency(tmp_path: Path) -> None:
    """self_heal run 落 experiment_manifest.json(14 字段齐全 + extra 透传)。"""
    planner = _build_planner(tmp_path)
    req = _self_heal_request(
        baseline_run_id="bl_xxx", baseline_pass_rate=0.0,
        design_id="counter_bitwidth", fault_type="bitwidth",
    )
    rep = planner.execute(req)

    assert rep.status == "ok", f"summary={rep.summary}"
    mf_path = Path("runs") / rep.run_id / "experiment_manifest.json"
    assert mf_path.exists(), f"manifest not found: {mf_path}"
    mf = json.loads(mf_path.read_text(encoding="utf-8"))

    # 14 字段齐全
    missing = _MANIFEST_FIELDS - set(mf.keys())
    assert not missing, f"manifest missing fields: {missing}"
    extra_keys = set(mf.keys()) - _MANIFEST_FIELDS
    assert not extra_keys, f"manifest unexpected fields: {extra_keys}"

    # extra 透传
    assert mf["run_id"] == rep.run_id
    assert mf["baseline_run_id"] == "bl_xxx"
    assert mf["design_id"] == "counter_bitwidth"
    assert mf["fault_type"] == "bitwidth"
    assert mf["baseline_pass_rate"] == 0.0
    # self_heal 终态(convergence=all_pass)
    assert mf["self_heal_convergence"] == "all_pass"
    assert mf["self_heal_pass_rate"] == 1.0
    assert mf["self_heal_best_iter"] == 0
    assert mf["contract_version"] == CONTRACT_VERSION
    # B 的 best/meta.json 字段被 manifest 正确读取(stub 副作用落盘)
    assert mf["candidates_at_best_score"] == 3
    # 数值字段类型
    assert isinstance(mf["planner_iterations"], int)
    assert isinstance(mf["llm_calls"], int)
    assert isinstance(mf["tokens_total"], int)
    assert isinstance(mf["wall_time_s"], (int, float))


def test_baseline_only_run_does_not_write_manifest(tmp_path: Path) -> None:
    """baseline_only run 不落 experiment_manifest.json(无 self_heal 终态)。"""
    planner = _build_planner(tmp_path)
    req = _baseline_request(
        design_id="counter_bitwidth", fault_type="bitwidth",
    )
    rep = planner.execute(req)
    assert rep.status == "ok"  # baseline 跑完即 ok
    mf_path = Path("runs") / rep.run_id / "experiment_manifest.json"
    assert not mf_path.exists(), (
        f"baseline_only run should NOT write manifest: {mf_path}"
    )
    # baseline_pass_rate 派生(sim 失败 → 0.0)
    assert rep.metrics["baseline_pass_rate"] == 0.0
    assert rep.metrics["sim_passed"] is False


def test_summarize_aggregates_by_fault_type(tmp_path: Path) -> None:
    """summarize 纯函数:聚合 manifest 列表 → by_fault_type.bitwidth.total>=1。"""
    planner = _build_planner(tmp_path)
    req = _self_heal_request(
        baseline_run_id="bl_xxx", baseline_pass_rate=0.0,
        design_id="counter_bitwidth", fault_type="bitwidth",
    )
    rep = planner.execute(req)
    mf_path = Path("runs") / rep.run_id / "experiment_manifest.json"
    assert mf_path.exists()
    manifests = [json.loads(mf_path.read_text(encoding="utf-8"))]
    summary = summarize(manifests)

    assert summary["total_runs"] == 1
    assert summary["total_passed"] == 1  # self_heal_pass_rate==1.0
    assert summary["overall_pass_rate"] == 1.0
    assert "bitwidth" in summary["by_fault_type"]
    bw = summary["by_fault_type"]["bitwidth"]
    assert bw["total"] >= 1
    assert bw["passed"] == 1
    assert bw["pass_rate"] == 1.0
    assert bw["mean_iters"] >= 1
    assert bw["mean_best_iter"] == 0
    # min_group_pass_rate 只一类(bitwidth)=1.0
    assert summary["min_group_pass_rate"] == 1.0


def test_self_heal_regression_manifest_still_written_and_zero(tmp_path: Path) -> None:
    """self_heal run stub regression:convergence != all_pass → self_heal_pass_rate==0.0;
    manifest 仍落盘(终态落盘不依赖收敛与否)。"""
    planner = _build_planner_with_regression(tmp_path)
    req = _self_heal_request(
        baseline_run_id="bl_yyy", baseline_pass_rate=0.0,
        design_id="adder_pipe_comb", fault_type="comb_logic",
    )
    rep = planner.execute(req)

    mf_path = Path("runs") / rep.run_id / "experiment_manifest.json"
    assert mf_path.exists(), "regression run 仍应落 manifest(终态落盘不依赖收敛)"
    mf = json.loads(mf_path.read_text(encoding="utf-8"))
    missing = _MANIFEST_FIELDS - set(mf.keys())
    assert not missing, f"manifest missing fields: {missing}"
    # regression(convergence != all_pass)→ self_heal_pass_rate==0.0
    assert mf["self_heal_pass_rate"] == 0.0, (
        f"regression expected pass_rate=0.0, got {mf['self_heal_pass_rate']}"
    )
    # convergence 字段透传(stub 设 "regression")
    assert mf["self_heal_convergence"] == "regression"


def test_candidates_at_best_score_none_when_no_best_meta(tmp_path: Path) -> None:
    """B 未落 best/meta.json(如 budget_exhausted)→ manifest.candidates_at_best_score=None。"""
    # 用无副作用 stub(不落 best/meta.json)构造 planner
    settings = make_settings(mode="rule", run_budget_s=600, max_iter=20)
    runner = Runner(runs_dir="runs", settings=settings)
    os.chdir(tmp_path)

    synth = StubTool("yosys_synth", [synth_ok()], schema=echo_schema())
    sim = StubTool(
        "iverilog_sim",
        [sim_result(False, num_passed=0, num_failed=1),
         sim_result(True, num_passed=1, num_failed=0)],
        schema=echo_schema(),
    )
    diag = StubTool("skill_diagnose", [diagnose_needs_patch()], schema=echo_schema())
    # 不带 best/meta.json 副作用的 all_pass heal
    heal_no_meta = StubTool(
        "skill_self_heal",
        [ToolResult(
            status="ok", exit_code=0, stdout="", stderr="",
            parsed={
                "_schema": {"name": "skill_self_heal", "version": "0.1.0",
                            "contract_version": CONTRACT_VERSION},
                "passed": True, "iterations": 1, "best_iter": 0,
                "convergence_cause": "all_pass",
                "patch_source": "llm_full_rewrite",
                "fixed_rtl_ref": artifact_ref("<run_id>", "self_heal/best/rtl.v"),
            },
            artifacts=[artifact_ref("<run_id>", "self_heal/best/rtl.v")],
            duration_s=0.001, tool="skill_self_heal",
            error_code=None, error_hint=None,
        )],
        schema=echo_schema(),
    )
    reg = make_registry([
        (synth, "yosys_synth", "synth", echo_schema()),
        (sim, "iverilog_sim", "sim", echo_schema()),
        (diag, "skill_diagnose", "skill", echo_schema()),
        (heal_no_meta, "skill_self_heal", "skill", echo_schema()),
    ])
    planner = CPlanner(
        registry=reg, llm=FakeLLMProvider([]),
        runner=runner, settings=settings,
    )
    req = _self_heal_request(
        baseline_run_id="bl_zzz", baseline_pass_rate=0.0,
        design_id="counter_syntax", fault_type="syntax",
    )
    rep = planner.execute(req)

    mf_path = Path("runs") / rep.run_id / "experiment_manifest.json"
    assert mf_path.exists()
    mf = json.loads(mf_path.read_text(encoding="utf-8"))
    assert mf["candidates_at_best_score"] is None, (
        f"无 best/meta.json 时应 None, got {mf['candidates_at_best_score']}"
    )
    # 其余字段仍齐全
    missing = _MANIFEST_FIELDS - set(mf.keys())
    assert not missing, f"manifest missing fields: {missing}"


def _build_planner_with_regression(tmp_path: Path):
    """regression 场景 planner:sim 失败后 heal 收敛非 all_pass('regression')。"""
    settings = make_settings(mode="rule", run_budget_s=600, max_iter=20)
    runner = Runner(runs_dir="runs", settings=settings)
    os.chdir(tmp_path)

    synth = StubTool("yosys_synth", [synth_ok()], schema=echo_schema())
    sim = StubTool(
        "iverilog_sim",
        [sim_result(False, num_passed=0, num_failed=1)],  # sim 一直失败 → regression
        schema=echo_schema(),
    )
    diag = StubTool("skill_diagnose", [diagnose_needs_patch()], schema=echo_schema())
    # heal 收敛非 all_pass:convergence_cause="regression", passed=False
    heal_reg = StubTool(
        "skill_self_heal",
        [ToolResult(
            status="ok", exit_code=0, stdout="", stderr="",
            parsed={
                "_schema": {"name": "skill_self_heal", "version": "0.1.0",
                            "contract_version": CONTRACT_VERSION},
                "passed": False, "iterations": 3, "best_iter": 1,
                "convergence_cause": "regression",
                "patch_source": "llm_full_rewrite",
                "fixed_rtl_ref": artifact_ref("<run_id>", "self_heal/best/rtl.v"),
            },
            artifacts=[artifact_ref("<run_id>", "self_heal/best/rtl.v")],
            duration_s=0.001, tool="skill_self_heal",
            error_code=None, error_hint=None,
        )],
        schema=echo_schema(),
    )
    reg = make_registry([
        (synth, "yosys_synth", "synth", echo_schema()),
        (sim, "iverilog_sim", "sim", echo_schema()),
        (diag, "skill_diagnose", "skill", echo_schema()),
        (heal_reg, "skill_self_heal", "skill", echo_schema()),
    ])
    return CPlanner(
        registry=reg, llm=FakeLLMProvider([]),
        runner=runner, settings=settings,
    )


def test_summarize_unknown_fault_type_buckets_as_other() -> None:
    """未知 fault_type 归 'other' 桶;空 runs 返回合理默认。"""
    m_unknown = {
        "fault_type": "glitch",  # 非四类
        "self_heal_pass_rate": 0.0,
        "planner_iterations": 5,
        "self_heal_best_iter": 2,
    }
    summary = summarize([m_unknown])
    assert "other" in summary["by_fault_type"]
    assert summary["by_fault_type"]["other"]["total"] == 1
    assert summary["by_fault_type"]["other"]["passed"] == 0
    assert summary["min_group_pass_rate"] == 0.0


def test_summarize_empty_runs() -> None:
    """无 manifest → total_runs=0 / overall_pass_rate=0 / min_group_pass_rate=1.0。"""
    s = summarize([])
    assert s["total_runs"] == 0
    assert s["total_passed"] == 0
    assert s["overall_pass_rate"] == 0.0
    assert s["min_group_pass_rate"] == 1.0  # 无观测,不拖低
    assert s["by_fault_type"] == {}


# ── 工厂:RunRequest(self_heal / baseline_only) ───────────────────────
def _self_heal_request(
    *,
    baseline_run_id: str,
    baseline_pass_rate: float,
    design_id: str,
    fault_type: str,
):
    from eda_agent.contracts import RunRequest
    return RunRequest(
        kind="self_heal",
        goal="pass all tests",
        rtl_path="a.v",
        tb_path="tb.v",
        extra={
            "baseline_run_id": baseline_run_id,
            "baseline_pass_rate": baseline_pass_rate,
            "design_id": design_id,
            "fault_type": fault_type,
        },
    )


def _baseline_request(*, design_id: str, fault_type: str):
    from eda_agent.contracts import RunRequest
    return RunRequest(
        kind="self_heal",
        goal="baseline only",
        rtl_path="a.v",
        tb_path="tb.v",
        extra={
            "baseline_only": True,
            "design_id": design_id,
            "fault_type": fault_type,
        },
    )
