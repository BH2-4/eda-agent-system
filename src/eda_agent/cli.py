"""eda CLI(T47,契约 §2.0)。

三个子命令:
- ``eda self-heal``:执行 C + B 自修复闭环,落 runs/<run_id>/report.md。
- ``eda diagnose``:执行 C + A 诊断器(不修复)。
- ``eda report <run_id>``:打印已落盘的 report.md。

退出码:0=ok,1=failed,2=budget_exhausted,64=report 不存在(契约 §2.0)。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from eda_agent.contracts import RunRequest, RunReport
from eda_agent.llm.factory import make_provider
from eda_agent.planner.c_planner import CPlanner
from eda_agent.registry import ToolRegistry
from eda_agent.runner import Runner
from eda_agent.settings import Settings, load_settings
from eda_agent.tools.bootstrap import build_registry

# status → 退出码映射(契约 §2.0)
_EXIT_CODES: dict[str, int] = {"ok": 0, "failed": 1, "budget_exhausted": 2}
_EXIT_NOT_FOUND = 64


def run_pipeline(request: RunRequest, settings: Settings) -> RunReport:
    """构造 provider/runner/registry/planner 并执行 RunRequest。"""
    provider = make_provider(settings)
    runner = Runner(runs_dir="runs", settings=settings)
    runner.scavenge_zombies(settings.run_budget_s)
    registry: ToolRegistry = build_registry(
        provider=provider, runner=runner, settings=settings
    )
    planner = CPlanner(
        registry=registry, llm=provider, runner=runner, settings=settings
    )
    return planner.execute(request)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eda")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sh = sub.add_parser("self-heal", help="C + B 自修复闭环")
    p_sh.add_argument("--rtl", required=True)
    p_sh.add_argument("--tb", required=True)
    p_sh.add_argument("--goal", required=True)
    p_sh.add_argument("--top-module", dest="top_module")
    p_sh.add_argument("--lib")
    p_sh.add_argument("--clock")
    p_sh.add_argument("--max-iter", dest="max_iter", type=int)
    p_sh.add_argument("--mode", choices=["rule", "llm"])
    p_sh.add_argument("--run-budget", dest="run_budget", type=int)

    p_dg = sub.add_parser("diagnose", help="C + A 诊断器(不修复)")
    p_dg.add_argument("--rtl", required=True)
    p_dg.add_argument("--tb", required=True)

    p_rp = sub.add_parser("report", help="打印已落盘的 report.md")
    p_rp.add_argument("run_id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    settings = load_settings()

    if args.cmd == "self-heal":
        if args.mode:
            settings.planner.mode = args.mode
        if args.run_budget:
            settings.budget.run_budget_s = args.run_budget
        req = RunRequest(
            kind="self_heal",
            goal=args.goal,
            rtl_path=args.rtl,
            tb_path=args.tb,
            top_module=args.top_module,
            lib_path=args.lib,
            clock_name=args.clock,
            max_iter=args.max_iter,
        )
        rep = run_pipeline(req, settings)
        print(rep.report_path)
        return _EXIT_CODES[rep.status]
    elif args.cmd == "diagnose":
        req = RunRequest(
            kind="diagnose",
            goal="diagnose",
            rtl_path=args.rtl,
            tb_path=args.tb,
        )
        rep = run_pipeline(req, settings)
        print(rep.report_path)
        return _EXIT_CODES[rep.status]
    elif args.cmd == "report":
        md = Path("runs") / args.run_id / "report.md"
        if not md.exists():
            print(f"run_id {args.run_id} not found", file=sys.stderr)
            return _EXIT_NOT_FOUND
        print(md.read_text(encoding="utf-8"))
        return 0

    return _EXIT_CODES["failed"]  # 不可达:argparse required=True


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
