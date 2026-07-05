"""scripts/run_baseline.py —— T50 baseline-only 实验(契约 §6.4 实验)。

用法:
    python scripts/run_baseline.py \
        --rtl data/examples/counter_bitwidth/rtl.v \
        --tb data/examples/counter_bitwidth/tb.v \
        --top-module counter \
        --design-id counter_bitwidth \
        --fault-type bitwidth \
        [--lib <p>] [--clock <n>]

行为:构造 ``RunRequest(kind="self_heal", extra={"baseline_only": True, ...})``,
经 ``run_pipeline`` 跑 synth+sim(不修复),输出 JSON 到 stdout:
    {"baseline_run_id": <id>, "baseline_pass_rate": <0.0|1.0>, "sim_passed": <bool|null>}

退出码:0(ok)/ 1(failed)/ 2(budget_exhausted)。脚本不经子进程跑 EDA,
全部经 ``run_pipeline`` → CPlanner → ToolRegistry(契约硬规则:脚本可调 subprocess,
但 MVP 优先复用 run_pipeline,不经子进程)。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 头部 sys.path.insert 加 src,使脚本可从项目根直接 ``python scripts/run_baseline.py``
_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))

from eda_agent.cli import run_pipeline  # noqa: E402
from eda_agent.contracts import RunRequest  # noqa: E402
from eda_agent.settings import load_settings  # noqa: E402

# 与 cli.py / RunReport.status 一致的退出码映射
_EXIT_CODES = {"ok": 0, "failed": 1, "budget_exhausted": 2}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_baseline.py",
        description="T50 baseline-only 实验:对 inject bug RTL 跑 synth+sim(不修复)",
    )
    p.add_argument("--rtl", required=True, help="RTL 源路径")
    p.add_argument("--tb", required=True, help="TB 源路径")
    p.add_argument("--top-module", dest="top_module", default=None,
                   help="顶层模块名;省略则由 Yosys 推断")
    p.add_argument("--design-id", dest="design_id", required=True,
                   help="设计标识(inject bug name,如 counter_bitwidth)")
    p.add_argument("--fault-type", dest="fault_type", required=True,
                   help="故障类型(bitwidth/comb_logic/syntax/timing_reset)")
    p.add_argument("--lib", default=None, help="liberty(.lib);省略则不跑 STA")
    p.add_argument("--clock", default=None, help="时钟信号名;省略则不跑 STA")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = load_settings()
    req = RunRequest(
        kind="self_heal",
        goal="baseline only",
        rtl_path=args.rtl,
        tb_path=args.tb,
        top_module=args.top_module,
        lib_path=args.lib,
        clock_name=args.clock,
        extra={
            "baseline_only": True,
            "design_id": args.design_id,
            "fault_type": args.fault_type,
        },
    )
    rep = run_pipeline(req, settings)
    metrics = rep.metrics or {}
    out = {
        "baseline_run_id": rep.run_id,
        "baseline_pass_rate": metrics.get("baseline_pass_rate"),
        "sim_passed": metrics.get("sim_passed"),
    }
    print(json.dumps(out, ensure_ascii=False))
    return _EXIT_CODES.get(rep.status, 1)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
