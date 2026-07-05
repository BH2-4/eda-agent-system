"""scripts/run_self_heal.py —— T54 self_heal 全流程实验(契约 §6.4 §2.4)。

与 run_baseline.py 的区别:baseline_only=False(真修复),传 extra(design_id/
fault_type)使 experiment_manifest.json 字段完整(c_planner._write_manifest 从
request.extra 取 design_id/fault_type;CLI self-heal 子命令不传 extra→字段为
"unknown",summarize 会归 "other" 无法按 fault_type 聚合算 S1 门槛)。

用法:
    python scripts/run_self_heal.py \\
        --rtl data/examples/counter_bitwidth/rtl.v \\
        --tb data/examples/counter_bitwidth/tb.v \\
        --top-module counter \\
        --design-id counter_bitwidth \\
        --fault-type bitwidth \\
        [--goal <txt>] [--lib <p>] [--clock <n>] \\
        [--max-iter <n>] [--mode rule|llm] [--run-budget <s>]

行为:构造 ``RunRequest(kind="self_heal", extra={"design_id", "fault_type"})``,
经 ``run_pipeline`` 跑 CPlanner(LLM ReAct 或 rule)+ B 自修复闭环,落
``runs/<run_id>/experiment_manifest.json``(14 字段)。输出 JSON 到 stdout:
    {"run_id", "status", "report_path", "manifest_path",
     "self_heal_pass_rate", "self_heal_convergence", "self_heal_best_iter",
     "planner_iterations", "sim_passed", "wall_time_s"}

退出码:0(ok)/ 1(failed)/ 2(budget_exhausted)。脚本不经子进程跑 EDA,
全部经 ``run_pipeline`` → CPlanner → ToolRegistry。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 头部 sys.path.insert 加 src,使脚本可从项目根直接 ``python scripts/run_self_heal.py``
_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))

from eda_agent.cli import run_pipeline  # noqa: E402
from eda_agent.contracts import RunRequest  # noqa: E402
from eda_agent.settings import load_settings  # noqa: E402

_EXIT_CODES = {"ok": 0, "failed": 1, "budget_exhausted": 2}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_self_heal.py",
        description="T54 self_heal 全流程实验:C+B 自修复闭环,落 experiment_manifest.json",
    )
    p.add_argument("--rtl", required=True, help="RTL 源路径")
    p.add_argument("--tb", required=True, help="TB 源路径")
    p.add_argument("--top-module", dest="top_module", default=None,
                   help="顶层模块名;省略则由 Yosys 推断")
    p.add_argument("--design-id", dest="design_id", required=True,
                   help="设计标识(inject bug name,如 counter_bitwidth)")
    p.add_argument("--fault-type", dest="fault_type", required=True,
                   help="故障类型(bitwidth/comb_logic/syntax/timing_reset)")
    p.add_argument("--goal", default=None,
                   help="修复目标(需含 'pass all tests' / 'pass N tests';"
                        "省略则按 design_id 生成默认)")
    p.add_argument("--lib", default=None, help="liberty(.lib);省略则不跑 STA")
    p.add_argument("--clock", default=None, help="时钟信号名;省略则不跑 STA")
    p.add_argument("--max-iter", dest="max_iter", type=int, default=None,
                   help="B 自修复最大迭代(覆盖 settings.skill_max_iterations)")
    p.add_argument("--mode", choices=["rule", "llm"], default=None,
                   help="planner 模式(覆盖 settings.planner.mode)")
    p.add_argument("--run-budget", dest="run_budget", type=int, default=None,
                   help="C 总预算秒(覆盖 settings.budget.run_budget_s)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = load_settings()
    if args.mode:
        settings.planner.mode = args.mode
    if args.run_budget:
        settings.budget.run_budget_s = args.run_budget

    goal = args.goal or f"修复 {args.design_id} 使测试台 pass all tests"
    req = RunRequest(
        kind="self_heal",
        goal=goal,
        rtl_path=args.rtl,
        tb_path=args.tb,
        top_module=args.top_module,
        lib_path=args.lib,
        clock_name=args.clock,
        max_iter=args.max_iter,
        extra={
            "design_id": args.design_id,
            "fault_type": args.fault_type,
        },
    )
    rep = run_pipeline(req, settings)
    metrics = rep.metrics or {}
    out = {
        "run_id": rep.run_id,
        "status": rep.status,
        "report_path": rep.report_path,
        "manifest_path": f"runs/{rep.run_id}/experiment_manifest.json",
        "self_heal_pass_rate": metrics.get("self_heal_pass_rate"),
        "self_heal_convergence": metrics.get("self_heal_convergence"),
        "self_heal_best_iter": metrics.get("self_heal_best_iter"),
        "planner_iterations": metrics.get("planner_iterations"),
        "sim_passed": metrics.get("sim_passed"),
        "wall_time_s": metrics.get("wall_time_s"),
    }
    print(json.dumps(out, ensure_ascii=False))
    return _EXIT_CODES.get(rep.status, 1)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
