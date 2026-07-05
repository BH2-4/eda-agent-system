"""scripts/build_corpus.py —— T27 语料校验 + 分桶统计(对应 data/logs_corpus/corpus.jsonl)。

用法:
    python scripts/build_corpus.py [--corpus data/logs_corpus/corpus.jsonl]
                                   [--kb data/error_kb.json]
                                   [--validate]

行为:
    1. 读 corpus.jsonl(JSON Lines,每行一条样本)。
    2. schema 必填字段存在性校验 + ground_truth 子字段校验。
    3. ``--validate`` 时对每条样本跑 ``re.search(pattern.regex, log_text)`` 自检
       (seed/grown 桶的 expected_pattern_id 必须命中;novel 桶允许 miss)。
    4. needs_rtl_patch 一致性断言:severity in {error,fatal} ⟺ needs_rtl_patch == True。
    5. 打印 JSON 到 stdout:total / buckets / difficulty / seed_novel / a11_pass / regex_fail_ids。

退出码:0(校验通过)/ 1(校验失败,如必填字段缺失或 regex 不命中)/ 2(运行错,
如 corpus 文件不存在)。

字段口径兼容:既接受 SCOUT v1.0 标准字段名(bucket in {seed,grown,novel},
ground_truth.expected_severity / expected_error_code),也兼容既有 _batch_*.jsonl
的简写(bucket=<stage>、ground_truth.severity / error_code)。读取时统一规范化
为内部标准形,校验只认标准形。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))

from eda_agent.skills.diagnose import ErrorKB  # noqa: E402

# A11 门槛(对齐 验收标准.md)
_A11_TOTAL = 30
_A11_STAGE_MIN = 5
_A11_HARD_MIN = 3

_REQUIRED_TOP = ("id", "tool", "bucket", "difficulty", "log_text", "ground_truth")
_REQUIRED_GT = (
    "expected_pattern_id",
    "expected_error_code",
    "expected_severity",
    "root_cause_keywords",
    "fix_hint_keywords",
    "expected_needs_rtl_patch",
)
_VALID_BUCKETS = {"seed", "grown", "novel"}
_VALID_STAGES = {"synth", "sim", "sta", "multi"}
_VALID_DIFF = {"easy", "medium", "hard"}
_VALID_SEV = {"info", "warn", "error", "fatal"}


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """把 _batch_*.jsonl 简写归一化到 SCOUT 标准形(原地修改副本)。

    兼容映射:
      bucket:<stage> 或 "easy_synth" → 按 difficulty/tool 推断 seed/grown/novel 缺省 seed
      gt.severity            → gt.expected_severity
      gt.error_code          → gt.expected_error_code
      gt.expected_pattern_id 缺失 → 用 ""(novel 桶允许)
    """
    out = dict(raw)
    gt = dict(raw.get("ground_truth") or {})
    out["ground_truth"] = gt

    # bucket 规范化:若不是 seed/grown/novel,则按规则推断
    b = str(out.get("bucket", ""))
    if b not in _VALID_BUCKETS:
        # 既有 _batch_*.jsonl 用 stage 名当 bucket;无 grown/novel 标记的全归 seed
        out["bucket"] = "seed"

    # severity / error_code 字段名兼容
    if "expected_severity" not in gt and "severity" in gt:
        gt["expected_severity"] = gt["severity"]
    if "expected_error_code" not in gt and "error_code" in gt:
        gt["expected_error_code"] = gt["error_code"]
    if "expected_pattern_id" not in gt:
        gt["expected_pattern_id"] = str(gt.get("pattern_id", ""))

    # 派生 expected_needs_rtl_patch(severity → bool,契约 §2.2 锁死)
    sev = str(gt.get("expected_severity", "")).lower()
    if sev in _VALID_SEV:
        gt["expected_needs_rtl_patch"] = sev in ("error", "fatal")

    # stage 派生(若缺):tool → stage 映射
    if "stage" not in out:
        out["stage"] = {
            "yosys_synth": "synth",
            "iverilog_sim": "sim",
            "opensta_timing": "sta",
        }.get(str(out.get("tool", "")), "multi")

    return out


def _load_corpus(path: Path) -> list[dict[str, Any]]:
    """读 jsonl;空行跳过;非法 JSON 直接抛(让上层退 2)。"""
    out: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8")
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError as e:
            raise ValueError(f"line {i}: JSON decode failed: {e}") from e
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _check_schema(samples: list[dict[str, Any]]) -> list[str]:
    """必填字段存在性;返回错误信息列表(空 = 通过)。"""
    errs: list[str] = []
    for s in samples:
        sid = str(s.get("id", "<no-id>"))
        for k in _REQUIRED_TOP:
            if k not in s:
                errs.append(f"{sid}: missing top field '{k}'")
        gt = s.get("ground_truth")
        if not isinstance(gt, dict):
            errs.append(f"{sid}: ground_truth not an object")
            continue
        for k in _REQUIRED_GT:
            if k not in gt:
                errs.append(f"{sid}: missing ground_truth.{k}")
        # 枚举值校验
        if str(s.get("bucket", "")) not in _VALID_BUCKETS:
            errs.append(f"{sid}: bad bucket '{s.get('bucket')}'")
        if str(s.get("stage", "")) not in _VALID_STAGES:
            errs.append(f"{sid}: bad stage '{s.get('stage')}'")
        if str(s.get("difficulty", "")) not in _VALID_DIFF:
            errs.append(f"{sid}: bad difficulty '{s.get('difficulty')}'")
        sev = str(gt.get("expected_severity", "")).lower()
        if sev not in _VALID_SEV:
            errs.append(f"{sid}: bad expected_severity '{gt.get('expected_severity')}'")
        # keywords 至少 3 token(A1 防 Jaccard 恒 0)
        for ktok in ("root_cause_keywords", "fix_hint_keywords"):
            toks = gt.get(ktok)
            if isinstance(toks, list) and len(toks) < 3:
                errs.append(f"{sid}: {ktok} needs >=3 tokens (got {len(toks)})")
        # needs_rtl_patch 一致性
        sev = str(gt.get("expected_severity", "")).lower()
        expected_patch = sev in ("error", "fatal")
        actual = gt.get("expected_needs_rtl_patch")
        if isinstance(actual, bool) and actual != expected_patch:
            errs.append(
                f"{sid}: expected_needs_rtl_patch={actual} but severity={sev} "
                f"implies {expected_patch}"
            )
    return errs


def _check_regex(
    samples: list[dict[str, Any]],
    kb: ErrorKB,
) -> list[str]:
    """对 seed/grown 桶跑 re.search 自检;novel 桶允许 miss。返回失败 id 列表。"""
    fail: list[str] = []
    by_pid = {p.pid: p for p in kb.patterns}
    for s in samples:
        sid = str(s.get("id", ""))
        bucket = str(s.get("bucket", ""))
        if bucket == "novel":
            continue
        gt = s.get("ground_truth") or {}
        pid = str(gt.get("expected_pattern_id", ""))
        if not pid:
            # grown/seed 桶但缺 pid → 视为失败
            fail.append(f"{sid}: empty expected_pattern_id in {bucket} bucket")
            continue
        pat = by_pid.get(pid)
        if pat is None:
            fail.append(f"{sid}: pid '{pid}' not found in ErrorKB")
            continue
        log_text = str(s.get("log_text", ""))
        try:
            m = re.search(pat.regex, log_text)
        except re.error as e:
            fail.append(f"{sid}: regex compile error for pid '{pid}': {e}")
            continue
        if m is None:
            fail.append(f"{sid}: regex of pid '{pid}' did not match log_text")
    return fail


def _bucket_stats(samples: list[dict[str, Any]]) -> dict[str, Any]:
    by_stage: dict[str, int] = {}
    by_bucket: dict[str, int] = {}
    by_diff: dict[str, int] = {}
    for s in samples:
        st = str(s.get("stage", "multi"))
        bk = str(s.get("bucket", "seed"))
        df = str(s.get("difficulty", "easy"))
        by_stage[st] = by_stage.get(st, 0) + 1
        by_bucket[bk] = by_bucket.get(bk, 0) + 1
        by_diff[df] = by_diff.get(df, 0) + 1
    synth = by_stage.get("synth", 0)
    sim = by_stage.get("sim", 0)
    sta = by_stage.get("sta", 0)
    hard = by_diff.get("hard", 0)
    a11_pass = (
        len(samples) >= _A11_TOTAL
        and synth >= _A11_STAGE_MIN
        and sim >= _A11_STAGE_MIN
        and sta >= _A11_STAGE_MIN
        and hard >= _A11_HARD_MIN
    )
    return {
        "total": len(samples),
        "buckets": {"synth": synth, "sim": sim, "sta": sta},
        "difficulty": {
            "easy": by_diff.get("easy", 0),
            "medium": by_diff.get("medium", 0),
            "hard": hard,
        },
        "seed_novel": {
            "seed": by_bucket.get("seed", 0),
            "grown": by_bucket.get("grown", 0),
            "novel": by_bucket.get("novel", 0),
        },
        "a11_pass": a11_pass,
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="build_corpus.py",
        description="T27 诊断器语料校验 + 分桶统计(对应 data/logs_corpus/corpus.jsonl)",
    )
    p.add_argument(
        "--corpus",
        default="data/logs_corpus/corpus.jsonl",
        help="corpus.jsonl 路径(默认 data/logs_corpus/corpus.jsonl)",
    )
    p.add_argument(
        "--kb",
        default="data/error_kb.json",
        help="ErrorKB 路径(默认 data/error_kb.json)",
    )
    p.add_argument(
        "--validate",
        action="store_true",
        help="对 seed/grown 桶样本跑 re.search 自检",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    corpus_path = Path(args.corpus)
    if not corpus_path.exists():
        print(json.dumps(
            {"error": f"corpus not found: {corpus_path}", "exit": 2},
            ensure_ascii=False,
        ))
        return 2
    try:
        raw_samples = _load_corpus(corpus_path)
    except ValueError as e:
        print(json.dumps(
            {"error": str(e), "exit": 2}, ensure_ascii=False,
        ))
        return 2

    samples = [_normalize(s) for s in raw_samples]
    schema_errs = _check_schema(samples)

    regex_fail_ids: list[str] = []
    if args.validate:
        kb = ErrorKB.load(args.kb)
        regex_fail_ids = _check_regex(samples, kb)

    stats = _bucket_stats(samples)
    stats["schema_errors"] = schema_errs
    stats["regex_fail_ids"] = regex_fail_ids
    stats["pass"] = (not schema_errs) and (not regex_fail_ids)

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0 if stats["pass"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
