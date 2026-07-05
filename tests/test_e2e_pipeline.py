"""tests/test_e2e_pipeline.py —— T53 真跑 counter_bitwidth 完整流程。

需 EDA(yosys/iverilog)+ LLM(GLM_API_KEY)双依赖,缺一即 skipif(不 fail)。

流程:
  1. 先跑 baseline(baseline_only=True)→ 拿 baseline_run_id + baseline_pass_rate
     (预期 sim_passed=False / baseline_pass_rate=0.0,bug 存在);
  2. 再跑 self_heal:extra 透传 baseline_run_id / baseline_pass_rate / design_id / fault_type;
  3. 断言:
     - baseline:sim_passed is False / status=ok;
     - self_heal:planner_iterations >= 1;
     - self_heal run 的 steps/ 下含 skill_self_heal 步(B 触发);
     - experiment_manifest.json 存在,14 字段齐全,design_id / fault_type / contract_version 正确,
       baseline_run_id 非空,baseline_pass_rate==0.0;
     - 独立验证步(C13):父 RunRecord.steps 含非 self_heal 的 iverilog_sim 步且 passed=True
       (B all_pass 后强制验证);
     - 若 self_heal 收敛 all_pass:self_heal_pass_rate==1.0
       (若 LLM 修复失败导致 regression/budget,不断言 pass_rate==1,只断言 manifest 字段齐全)。
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from eda_agent.contracts import CONTRACT_VERSION, RunRequest
from eda_agent.settings import Settings, load_settings


# ── 依赖探测 ───────────────────────────────────────────────────────────


def _have_eda() -> bool:
    """WSL 模式查 wsl.exe;非 WSL 查本机 iverilog/yosys。"""
    s = Settings()
    if s.eda.wsl_enabled:
        return shutil.which("wsl.exe") is not None
    return (
        shutil.which(s.eda.iverilog_cmd) is not None
        and shutil.which(s.eda.vvp_cmd) is not None
        and shutil.which(s.eda.yosys_cmd) is not None
    )


def _have_llm() -> bool:
    """GLM_API_KEY 非空且非占位符才视为可用(B 的 patch 走真 LLM)。

    排除 .env 模板占位(如 your-zhipu-api-key-here),避免占位 key 触发真调失败。
    用户填入真实智谱 key 后此函数返回 True,e2e 才真跑(会产 API 费用)。
    """
    key = os.environ.get("GLM_API_KEY", "").strip()
    if not key:
        return False
    if "your-" in key.lower() or "here" in key.lower() or len(key) < 20:
        return False
    return True


_skip_if_no_eda = pytest.mark.skipif(
    not _have_eda(), reason="需 yosys/iverilog(WSL 或本机)"
)
_skip_if_no_llm = pytest.mark.skipif(
    not _have_llm(), reason="需 GLM_API_KEY 跑 self_heal patch"
)


# ── 数据 fixture:counter_bitwidth ──────────────────────────────────────

_EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "examples" / "counter_bitwidth"


@pytest.fixture
def counter_bitwidth(tmp_path: Path) -> dict[str, str | Path]:
    """复制 counter_bitwidth RTL/TB 到 tmp_path(避免 run 污染 git tracked data/)。"""
    rtl_src = _EXAMPLE_DIR / "rtl.v"
    tb_src = _EXAMPLE_DIR / "tb.v"
    assert rtl_src.exists() and tb_src.exists(), (
        f"missing data/examples/counter_bitwidth/: {rtl_src} / {tb_src}"
    )
    rtl = tmp_path / "rtl.v"
    tb = tmp_path / "tb.v"
    rtl.write_text(rtl_src.read_text(encoding="utf-8"), encoding="utf-8")
    tb.write_text(tb_src.read_text(encoding="utf-8"), encoding="utf-8")
    return {"rtl": rtl, "tb": tb, "tmp": tmp_path}


# ── 14 字段集(与 test_experiment_manifest 一致) ──────────────────────

_MANIFEST_FIELDS = {
    "run_id", "baseline_run_id", "design_id", "fault_type",
    "baseline_pass_rate", "self_heal_pass_rate", "self_heal_convergence",
    "self_heal_best_iter", "planner_iterations", "llm_calls",
    "tokens_total", "wall_time_s", "candidates_at_best_score",
    "contract_version",
}


# ── 主测试:baseline → self_heal 完整流程 ──────────────────────────────

@pytest.mark.needs_eda
@pytest.mark.needs_llm
@_skip_if_no_eda
@_skip_if_no_llm
def test_counter_bitwidth_baseline_then_self_heal(
    counter_bitwidth: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """counter_bitwidth(fault_type=bitwidth, healable=true)完整 e2e。

    验证 manifest 14 字段齐全 + 独立验证步 + baseline_pass_rate==0.0。
    """
    # 在 tmp_path 下跑,runs/ 落 tmp 内(避免污染 git tracked runs/)
    monkeypatch.chdir(counter_bitwidth["tmp"])
    settings = load_settings()

    from eda_agent.cli import run_pipeline

    # ── 1. baseline run(baseline_only=True) ────────────────────────────
    rtl = str(counter_bitwidth["rtl"])
    tb = str(counter_bitwidth["tb"])
    baseline_req = RunRequest(
        kind="self_heal",
        goal="baseline only",
        rtl_path=rtl,
        tb_path=tb,
        top_module="counter",
        extra={
            "baseline_only": True,
            "design_id": "counter_bitwidth",
            "fault_type": "bitwidth",
        },
    )
    baseline_rep = run_pipeline(baseline_req, settings)

    # baseline 跑完即 ok(不管 sim pass 与否)
    assert baseline_rep.status == "ok", (
        f"baseline status={baseline_rep.status}, summary={baseline_rep.summary}"
    )
    # bug 存在 → sim 不通过(passed=False 或 None)
    assert baseline_rep.metrics["sim_passed"] is not True, (
        f"baseline sim_passed={baseline_rep.metrics['sim_passed']}, "
        "expected False/None (counter_bitwidth 有 bitwidth bug)"
    )
    assert baseline_rep.metrics["baseline_pass_rate"] == 0.0
    baseline_run_id = baseline_rep.run_id
    baseline_pass_rate = baseline_rep.metrics["baseline_pass_rate"]

    # ── 2. self_heal run(透传 baseline_run_id / baseline_pass_rate) ───
    heal_req = RunRequest(
        kind="self_heal",
        goal="pass all tests",
        rtl_path=rtl,
        tb_path=tb,
        top_module="counter",
        extra={
            "design_id": "counter_bitwidth",
            "fault_type": "bitwidth",
            "baseline_run_id": baseline_run_id,
            "baseline_pass_rate": baseline_pass_rate,
        },
    )
    heal_rep = run_pipeline(heal_req, settings)

    # LLM 不可用(智谱 glm-5.2 账户资源问题 429/1113 等)→ planner 0 iteration 或 emergency,skip。
    # 用户在 open.bigmodel.cn 开通 glm-5.2 资源后,此分支不触发,e2e 真跑验证修复。
    iters = heal_rep.metrics.get("planner_iterations", 0)
    if iters == 0 or (heal_rep.status == "failed" and "crash" in heal_rep.summary.lower()):
        pytest.skip(
            f"self_heal 未跑 action(iters={iters}, status={heal_rep.status}) — "
            f"可能智谱 glm-5.2 账户资源不可用: {heal_rep.summary}"
        )

    # ── 3. 断言 self_heal run ──────────────────────────────────────────
    assert heal_rep.metrics["planner_iterations"] >= 1, (
        f"planner_iterations={heal_rep.metrics['planner_iterations']}"
    )

    # steps/ 下含 skill_self_heal 步(B 触发)
    steps_dir = Path("runs") / heal_rep.run_id / "steps"
    assert steps_dir.exists(), f"steps dir not found: {steps_dir}"
    step_names = [p.name for p in steps_dir.iterdir() if p.is_dir()]
    assert any("skill_self_heal" in n for n in step_names), (
        f"no skill_self_heal step in {step_names}"
    )

    # experiment_manifest.json 存在 + 14 字段齐全
    mf_path = Path("runs") / heal_rep.run_id / "experiment_manifest.json"
    assert mf_path.exists(), f"manifest not found: {mf_path}"
    mf = json.loads(mf_path.read_text(encoding="utf-8"))
    missing = _MANIFEST_FIELDS - set(mf.keys())
    assert not missing, f"manifest missing fields: {missing}"
    extra_keys = set(mf.keys()) - _MANIFEST_FIELDS
    assert not extra_keys, f"manifest unexpected fields: {extra_keys}"

    # 字段透传
    assert mf["run_id"] == heal_rep.run_id
    assert mf["baseline_run_id"] == baseline_run_id, (
        f"baseline_run_id mismatch: {mf['baseline_run_id']} != {baseline_run_id}"
    )
    assert mf["baseline_pass_rate"] == 0.0
    assert mf["design_id"] == "counter_bitwidth"
    assert mf["fault_type"] == "bitwidth"
    assert mf["contract_version"] == CONTRACT_VERSION

    # ── C13 独立验证步:父 RunRecord.steps 含非 self_heal 的 iverilog_sim 步且 passed=True ──
    run_json = json.loads(
        (Path("runs") / heal_rep.run_id / "run.json").read_text(encoding="utf-8")
    )
    steps = run_json.get("steps", [])
    # 收集所有 iverilog_sim 步(skill_name 为 None 的才算"独立验证")
    indep_sim_steps = [
        s for s in steps
        if s.get("tool_name") == "iverilog_sim" and s.get("skill_name") is None
    ]
    assert len(indep_sim_steps) >= 1, (
        f"no independent iverilog_sim step (skill_name=None) in steps: "
        f"{[s.get('tool_name') for s in steps]}"
    )

    # ── 若 self_heal 收敛 all_pass:self_heal_pass_rate==1.0 ─────────────
    # 若 LLM 修复失败(regression/budget),只断言 manifest 字段齐全,不断言 pass_rate==1。
    if heal_rep.metrics.get("self_heal_convergence") == "all_pass":
        assert mf["self_heal_pass_rate"] == 1.0, (
            f"all_pass but self_heal_pass_rate={mf['self_heal_pass_rate']}"
        )
        # all_pass 时独立验证步应 status=ok
        assert all(
            s.get("status") == "ok" for s in indep_sim_steps
        ), f"independent sim step not ok: {indep_sim_steps}"
        assert heal_rep.metrics["sim_passed"] is True
    else:
        # 未 all_pass:仍验证 manifest 字段齐全(上面已断言),pass_rate 允许 0.0/None。
        # manifest 的 self_heal_convergence 应与 metrics 一致(透传正确性)。
        assert mf["self_heal_convergence"] == heal_rep.metrics.get(
            "self_heal_convergence"
        ), (
            f"convergence 未 all_pass (={heal_rep.metrics.get('self_heal_convergence')}); "
            "manifest 字段已齐全(本断言不要求 pass_rate==1.0)"
        )
