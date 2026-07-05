"""scripts/summarize_eval.py —— T52 聚合 experiment_manifest → summary。

用法:
    python scripts/summarize_eval.py [--runs-dir runs] [--out experiment_summary.json]

行为:
    扫描 ``<runs-dir>/*/experiment_manifest.json``(只 self_heal run 才落 manifest,
    baseline_only run 无此文件,天然跳过),按 ``fault_type`` 聚合四类
    (bitwidth / comb_logic / syntax / timing_reset)+ 其他;输出:
        {
          "overall_pass_rate": float,
          "min_group_pass_rate": float,
          "by_fault_type": {<type>: {total, passed, pass_rate, mean_iters, mean_best_iter}},
          "total_runs": int,
          "total_passed": int
        }

门槛(契约 §7):``min_group_pass_rate >= 0.50`` 且 ``bitwidth`` 类 >= 1 all_pass。
summarize 不判定门槛,只产出数据(门槛人工/脚本判)。退出码 0。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 比赛四类故障;其余归 "other"。
_KNOWN_FAULT_TYPES = ("bitwidth", "comb_logic", "syntax", "timing_reset")
_OTHER = "other"


def _load_manifests(runs_dir: Path) -> list[dict[str, Any]]:
    """扫描 ``runs/<run_id>/experiment_manifest.json``;不存在则跳过。"""
    out: list[dict[str, Any]] = []
    if not runs_dir.exists():
        return out
    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        mf = run_dir / "experiment_manifest.json"
        if not mf.exists():
            continue
        try:
            out.append(json.loads(mf.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def summarize(manifests: list[dict[str, Any]]) -> dict[str, Any]:
    """聚合 manifest 列表 → summary dict。

    纯函数(不读盘),供测试 import 复用。manifest 缺字段时按合理默认处理。
    """
    # 按 fault_type 分桶
    buckets: dict[str, list[dict[str, Any]]] = {}
    for m in manifests:
        ft = m.get("fault_type") or _OTHER
        if ft not in _KNOWN_FAULT_TYPES:
            ft = _OTHER
        buckets.setdefault(ft, []).append(m)

    by_fault_type: dict[str, dict[str, Any]] = {}
    for ft, items in buckets.items():
        total = len(items)
        passed = sum(
            1 for m in items if m.get("self_heal_pass_rate") == 1.0
        )
        pass_rate = (passed / total) if total else 1.0
        mean_iters = (
            sum(_as_int(m.get("planner_iterations")) for m in items) / total
            if total else 0.0
        )
        best_iters = [_as_int(m.get("self_heal_best_iter")) for m in items]
        best_iters_valid = [b for b in best_iters if b is not None]
        mean_best_iter = (
            sum(best_iters_valid) / len(best_iters_valid)
            if best_iters_valid else None
        )
        by_fault_type[ft] = {
            "total": total,
            "passed": passed,
            "pass_rate": pass_rate,
            "mean_iters": mean_iters,
            "mean_best_iter": mean_best_iter,
        }

    total_runs = len(manifests)
    total_passed = sum(
        1 for m in manifests if m.get("self_heal_pass_rate") == 1.0
    )
    overall_pass_rate = (
        total_passed / total_runs if total_runs else 0.0
    )
    # min_group_pass_rate:类空不参与(空类无观测,不拖低 min);无任何类则 1.0
    if by_fault_type:
        min_group_pass_rate = min(
            v["pass_rate"] for v in by_fault_type.values()
        )
    else:
        min_group_pass_rate = 1.0 if total_runs == 0 else 0.0

    return {
        "overall_pass_rate": overall_pass_rate,
        "min_group_pass_rate": min_group_pass_rate,
        "by_fault_type": by_fault_type,
        "total_runs": total_runs,
        "total_passed": total_passed,
    }


def _as_int(v: Any) -> int | None:
    """容错把数值/None 转 int;非数值返回 None。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(v)
    return None


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="summarize_eval.py",
        description="T52 聚合 runs/*/experiment_manifest.json → experiment_summary.json",
    )
    p.add_argument("--runs-dir", dest="runs_dir", default="runs",
                   help="runs 目录(默认 runs)")
    p.add_argument("--out", default="experiment_summary.json",
                   help="输出 summary JSON 路径(默认 experiment_summary.json)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    runs_dir = Path(args.runs_dir)
    manifests = _load_manifests(runs_dir)
    summary = summarize(manifests)
    out_path = Path(args.out)
    out_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # stdout 也打印一份精简结果,便于 CI 日志直接看
    print(json.dumps({
        "total_runs": summary["total_runs"],
        "total_passed": summary["total_passed"],
        "overall_pass_rate": summary["overall_pass_rate"],
        "min_group_pass_rate": summary["min_group_pass_rate"],
    }, ensure_ascii=False))
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
