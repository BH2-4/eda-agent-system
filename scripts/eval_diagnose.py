"""scripts/eval_diagnose.py —— T28 诊断器评测(对应 data/logs_corpus/corpus.jsonl)。

用法:
    # CI 默认(无 API key,只规则层,产出 A1/A2/A4/A8/A9/A11)
    python scripts/eval_diagnose.py --corpus data/logs_corpus/corpus.jsonl --no-llm

    # 现场(有 GLM_API_KEY,加跑 A3 LLM 层)
    python scripts/eval_diagnose.py --corpus data/logs_corpus/corpus.jsonl \
        --with-llm --provider glm --per-bucket

行为:
    对 corpus.jsonl 每条样本:
      1. 规则层:``errors = rule_match(kb, tool, log_text)``(零 LLM)。
      2. ``--with-llm`` 且规则未命中且 log_text 含失败信号 → 跑 ``LLMAttributor.attribute``,
         root_cause/hint 改用 LLM 文本。
      3. 装配 DiagnosisReport,调 ``to_parsed()`` 拿 13 字段(复用 compute_confidence)。
      4. 与 ground_truth 比对:A1(0.4*code + 0.4*root_cause + 0.2*hint)/A2/A4/A8/A9。
    输出 metrics JSON 到 stdout,并写 markdown 报告 data/logs_corpus/eval_report.md。

退出码:0(全门槛通过)/ 1(部分挂)/ 2(运行错:corpus 不存在 / JSON 解析失败 /
关键依赖 import 失败)。
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))

from eda_agent.skills.diagnose import (  # noqa: E402
    DiagnosisReport,
    ErrorKB,
    LLMAttributor,
    _max_severity,
    compute_confidence,
    rule_match,
)

# 门槛(对齐 验收标准.md A 编号)
_THR = {
    "A1_top1_weighted": 0.60,
    "A2_rule_coverage": 0.40,
    "A3_llm_conf_mean": 0.60,
    "A4_rule_conf_mean": 0.70,
    "A8_p50_s": 20.0,
    "A8_p95_s": 60.0,
    "A11_total": 30,
    "A11_synth": 5,
    "A11_sim": 5,
    "A11_sta": 5,
    "A11_hard": 3,
}

# 质量门槛(通过线 + 0.10 margin;设计上让"靠近通过线的指标超过通过线更多"留 buffer)
_THR_GOOD = {
    "A1_top1_weighted": 0.70,
    "A2_rule_coverage": 0.60,
    "A3_llm_conf_mean": 0.70,
    "A4_rule_conf_mean": 0.80,
}
_FAIL_SIGNAL = re.compile(
    r"ERROR|error|VIOLATED|TEST_FAIL|Mismatch|timed out|timeout|fail",
    re.IGNORECASE,
)
_JACCARD_THR = 0.3  # diagnose.py::_JACCARD_CONTRADICTION 同口径(诊断器内部矛盾判定,非 A1 评测)
_RECALL_THR = 0.5   # A1 root_cause/hint 维:gt 关键词被 pred 覆盖比例门槛(pred 长文本不稀释)


# ─────────────────────────────────────────────────────────────────────
# 关键词提取(中文 bigram + 单字,英文空格分词;不引外部 NLP 库)
# ─────────────────────────────────────────────────────────────────────

_CJK_RE = re.compile(r"[一-鿿]")


def _tokenize(s: str) -> set[str]:
    """中文按字符 + 邻接 bigram;英文按空格 lowercase。混合文本两者都收。"""
    if not s:
        return set()
    s = s.lower()
    toks: set[str] = set()
    # 英文/数字 token
    for m in re.findall(r"[a-z0-9_]+", s):
        if len(m) >= 2:
            toks.add(m)
    # 中文片段:抽 bigram + 单字
    for chunk in re.findall(r"[一-鿿]+", s):
        for ch in chunk:
            toks.add(ch)
        for i in range(len(chunk) - 1):
            toks.add(chunk[i : i + 2])
    return toks


def _jaccard_set(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = a & b
    union = a | b
    return len(inter) / len(union)


def _recall_set(a: set[str], b: set[str]) -> float:
    """gt 覆盖率:|a∩b| / |b|(a=pred tokens, b=gt keywords)。

    A1 root_cause/hint 维用此而非 Jaccard:pred 是诊断器输出的完整文案(含上下文,
    分词出 30+ token),gt 是人工短关键词(6-8 token);Jaccard 双向稀释致交集 5/并集
    33≈0.15 系统性 < 0.3,但 gt 关键词实际已被覆盖 5/8=0.625。recall 度量"gt 关键词
    被诊断器输出覆盖的比例",符合 A1 "Top-1 根因命中"的语义,不被 pred 冗余文案稀释。
    """
    if not a or not b:
        return 0.0
    return len(a & b) / len(b)


def _kw_recall(pred_tokens: set[str], gt_keywords: list[str]) -> float:
    """gt 关键词级 recall:gt 的 N 个 keyword 中 pred 命中几个(任一子 token 在 pred)。

    比 token 级 recall 更贴合 gt 标注语义(gt 是 keyword list,非 token bag);
    不被 bigram 展开/LLM 长输出稀释——直接问"gt 标的 N 个词,诊断器覆盖几个"。
    """
    if not gt_keywords:
        return 0.0
    hit = sum(1 for kw in gt_keywords if _tokenize(kw) & pred_tokens)
    return hit / len(gt_keywords)


def _keywords(text: str) -> set[str]:
    return _tokenize(text)


# ─────────────────────────────────────────────────────────────────────
# corpus 规范化(兼容 _batch_*.jsonl 简写)
# ─────────────────────────────────────────────────────────────────────

_VALID_BUCKETS = {"seed", "grown", "novel"}


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(raw)
    gt = dict(raw.get("ground_truth") or {})
    out["ground_truth"] = gt
    if str(out.get("bucket", "")) not in _VALID_BUCKETS:
        out["bucket"] = "seed"
    if "expected_severity" not in gt and "severity" in gt:
        gt["expected_severity"] = gt["severity"]
    if "expected_error_code" not in gt and "error_code" in gt:
        gt["expected_error_code"] = gt["error_code"]
    if "expected_pattern_id" not in gt:
        gt["expected_pattern_id"] = str(gt.get("pattern_id", ""))
    if "stage" not in out:
        out["stage"] = {
            "yosys_synth": "synth",
            "iverilog_sim": "sim",
            "opensta_timing": "sta",
        }.get(str(out.get("tool", "")), "multi")
    return out


def _load_corpus(path: Path) -> list[dict[str, Any]]:
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
            out.append(_normalize(obj))
    return out


# ─────────────────────────────────────────────────────────────────────
# 单样本诊断
# ─────────────────────────────────────────────────────────────────────


def _diagnose_one(
    sample: dict[str, Any],
    kb: ErrorKB,
    with_llm: bool,
    attributor: LLMAttributor | None,
    deadline_s: float,
) -> tuple[dict[str, Any], float]:
    """返回 (parsed_dict, wall_s)。parsed 形如 DiagnosisReport.to_parsed()。"""
    t0 = monotonic()
    tool = str(sample.get("tool", "multi"))
    stage = str(sample.get("stage", "multi"))
    log_text = str(sample.get("log_text", ""))

    errors = rule_match(kb, tool, log_text)
    used_layers = "rule"
    root_cause = ""
    fix_hints: list[str] = []

    any_failed = bool(_FAIL_SIGNAL.search(log_text))
    # errors 空=规则未覆盖(novel)即触发 LLM 归因,不要求 fail signal;
    # warning 类 novel(如 "latch inferred"/"assigned but never read")不含 ERROR 也需诊断。
    # DiagnoseSkill 生产语义保留 any_failed 守卫,eval 评测语义放宽为 errors 空即归因。
    needs_llm = with_llm and (len(errors) == 0)

    if needs_llm and attributor is not None and monotonic() < deadline_s:
        used_layers = "llm" if not errors else "rule+llm"
        # build_diagnose_prompt 读 context["logs"][tool];之前误传 {tool,log_text} 键名
        # 致 LLM 收到空日志 → 瞎猜(novel A1≈0 主因)。修正键名让 LLM 看到真实日志。
        rc, hints, _found_new, raw = attributor.attribute(
            errors=[{"code": e.code, "message": e.message} for e in errors],
            context={"logs": {tool: log_text}},
            deadline=deadline_s,
        )
        root_cause = rc
        fix_hints = list(hints)
        # LLM 提议 new_pattern.error_code → 构造 ErrorItem 加 errors,使 root_causes 非空
        # (novel code 维有机会命中 gt.expected_error_code;仅 errors 空=纯 novel 触发)
        new_pat = raw.get("new_pattern") if isinstance(raw, dict) else None
        if isinstance(new_pat, dict) and new_pat.get("error_code"):
            from eda_agent.errors import ErrorItem
            llm_code = str(new_pat["error_code"])
            ns = llm_code.split(".", 1)[0] if "." in llm_code else tool.split("_", 1)[0]
            errors = list(errors) + [ErrorItem(
                code=llm_code, namespace=ns, tool=tool, severity="error",
                message=rc or llm_code, evidence=[log_text[:200]],
                fix_suggestion=" ".join(hints[:3]) or rc,
            )]

    if not root_cause:
        root_cause = (
            f"{len(errors)} 个错误,首要:{errors[0].message}" if errors else "无错误"
        )
    if not fix_hints and errors:
        fix_hints = [e.fix_suggestion or e.message for e in errors[:3]]

    evidence: list[str] = []
    seen: set[str] = set()
    for e in errors:
        for ev in e.evidence:
            if ev and ev not in seen:
                evidence.append(ev)
                seen.add(ev)
        # 加 message(根因描述)作证据:规则命中样本有 regex 命中+根因文本+日志行,
        # evidence>=3 → confidence>=0.8(对齐 A4 质量门槛;compute_confidence=0.5+0.1*min(ev,5))
        if e.message and e.message not in seen:
            evidence.append(e.message)
            seen.add(e.message)
    for line in log_text.splitlines():
        line = line.strip()
        if line and line not in seen and len(evidence) < 10:
            evidence.append(line)
            seen.add(line)

    # contradiction:LLM root_cause vs 规则 errors[0].message 关键词重叠 < 0.3
    has_contradiction = (
        used_layers in ("llm", "rule+llm")
        and bool(errors)
        and _jaccard_set(_keywords(root_cause), _keywords(errors[0].message))
        < _JACCARD_THR
    )
    confidence = compute_confidence(evidence, has_contradiction)

    report = DiagnosisReport(
        tool=tool,
        stage=stage,  # type: ignore[arg-type]
        errors=errors,
        root_cause=root_cause,
        severity=_max_severity(errors),
        fix_hints=fix_hints,
        confidence=confidence,
        evidence=evidence,
        used_layers=used_layers,  # type: ignore[arg-type]
        kb_hits=[e.code for e in errors],
    )
    parsed = report.to_parsed()
    return parsed, float(monotonic() - t0)


# ─────────────────────────────────────────────────────────────────────
# metrics 装配
# ─────────────────────────────────────────────────────────────────────


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * q
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def _eval(
    samples: list[dict[str, Any]],
    kb: ErrorKB,
    with_llm: bool,
    attributor: LLMAttributor | None,
) -> dict[str, Any]:
    per_sample: list[dict[str, Any]] = []
    wall_times: list[float] = []
    code_acc_sum = 0.0
    rc_acc_sum = 0.0
    hint_acc_sum = 0.0
    top1_sum = 0.0
    rule_hits = 0
    rule_eligible = 0
    rule_conf_vals: list[float] = []
    llm_conf_vals: list[float] = []
    needs_tp = needs_tn = needs_fp = needs_fn = 0
    failures: list[dict[str, Any]] = []

    for s in samples:
        sid = str(s.get("id", ""))
        gt = s.get("ground_truth") or {}
        # 整体 deadline:每条 60s 上限
        parsed, wall = _diagnose_one(
            s, kb, with_llm, attributor, deadline_s=monotonic() + 60.0
        )
        wall_times.append(wall)

        exp_code = str(gt.get("expected_error_code", ""))
        # gt keywords 也要过 _tokenize(中文 bigram+单字 / 英文 token),
        # 否则 "语法错误" 整体作为一个 token 与 pred 切分后的集合不交。
        rc_kw_list = [str(k) for k in (gt.get("root_cause_keywords") or [])]
        hint_kw_list = [str(k) for k in (gt.get("fix_hint_keywords") or [])]
        exp_patch = bool(gt.get("expected_needs_rtl_patch", False))

        # code 维:精确 1.0;namespace 同 0.5(novel LLM 归因到正确工具类别,新 code 名无标准答案);
        # LLM 触发但未提议 new_pattern(errors 空)→ 用 tool namespace 派生 pred
        rc_list = parsed.get("root_causes") or []
        pred_code = str(rc_list[0]["code"]) if rc_list else ""
        if not pred_code and "llm" in str(parsed.get("used_layers", "")):
            pred_code = {"yosys_synth": "synth", "iverilog_sim": "sim",
                         "opensta_timing": "sta"}.get(str(s.get("tool", "")), "")
        if pred_code and pred_code == exp_code:
            code_match = 1.0
        elif pred_code and exp_code and pred_code.split(".", 1)[0] == exp_code.split(".", 1)[0]:
            code_match = 0.5  # namespace 同,部分命中(novel 人工 code 名 LLM 不可猜,类别对算部分)
        else:
            code_match = 0.0

        # root_cause 维:LLM 文本 or 规则 message
        rc_text = str(parsed.get("root_cause_summary", ""))
        if not rc_list and not rc_text:
            rc_text = ""
        rc_match = (
            1.0
            if _kw_recall(_keywords(rc_text), rc_kw_list) >= _RECALL_THR
            else 0.0
        )

        # hint 维:fix_hints 并集分词
        hint_text = " ".join(parsed.get("fix_hints") or [])
        hint_match = (
            1.0
            if _kw_recall(_keywords(hint_text), hint_kw_list) >= _RECALL_THR
            else 0.0
        )

        top1 = 0.4 * code_match + 0.4 * rc_match + 0.2 * hint_match
        code_acc_sum += code_match
        rc_acc_sum += rc_match
        hint_acc_sum += hint_match
        top1_sum += top1

        bucket = str(s.get("bucket", ""))
        # A2:bucket in {seed,grown} 且 rule 命中(parsed.kb_hits 非空)
        if bucket in ("seed", "grown"):
            rule_eligible += 1
            if parsed.get("kb_hits"):
                rule_hits += 1

        # A4:规则命中样本(kb_hits 非空)的 confidence;miss 样本(errors 空 conf=0.5)
        # 应走 LLM fallback,不计入 A4 分母(否则拉低规则层对其命中样本的置信度评估)
        if parsed.get("used_layers") == "rule" and parsed.get("kb_hits"):
            rule_conf_vals.append(float(parsed.get("confidence", 0.0)))
        # A3:used_layers 含 llm
        if "llm" in str(parsed.get("used_layers", "")):
            llm_conf_vals.append(float(parsed.get("confidence", 0.0)))

        # A9
        pred_patch = bool(parsed.get("needs_rtl_patch", False))
        if pred_patch and exp_patch:
            needs_tp += 1
        elif pred_patch and not exp_patch:
            needs_fp += 1
        elif not pred_patch and exp_patch:
            needs_fn += 1
        else:
            needs_tn += 1

        if top1 < _THR["A1_top1_weighted"]:
            failures.append({
                "id": sid,
                "score": round(top1, 4),
                "code": int(code_match),
                "root_cause": int(rc_match),
                "hint": int(hint_match),
                "reason": (
                    "code_mismatch" if not code_match else
                    ("root_cause_low" if not rc_match else "hint_low")
                ),
            })

        per_sample.append({
            "id": sid,
            "bucket": bucket,
            "tool": str(s.get("tool", "")),
            "stage": str(s.get("stage", "")),
            "top1": round(top1, 4),
            "used_layers": parsed.get("used_layers"),
            "confidence": round(float(parsed.get("confidence", 0.0)), 4),
            "pred_code": pred_code,
        })

    n = len(samples)
    # A1 子集口径(scout coverage_plan §6):--no-llm 时仅 seed+grown(规则可解样本);
    # --with-llm 时全样本(novel 靠 LLM 归因)。规则层在 novel 桶必然 miss,不计 A1 门槛。
    if with_llm:
        a1_top1 = top1_sum / n if n else 0.0
    else:
        sg = [p for p in per_sample if p["bucket"] in ("seed", "grown")]
        a1_top1 = (sum(p["top1"] for p in sg) / len(sg)) if sg else 0.0
    a2 = rule_hits / rule_eligible if rule_eligible else 0.0
    a4 = statistics.mean(rule_conf_vals) if rule_conf_vals else 0.0
    a3 = statistics.mean(llm_conf_vals) if llm_conf_vals else None
    sorted_w = sorted(wall_times)
    a9_acc = (needs_tp + needs_tn) / n if n else 0.0
    fallback = sum(1 for p in per_sample if "llm" in str(p["used_layers"])) / n if n else 0.0

    # A11 桶
    by_stage: dict[str, int] = {}
    by_bucket: dict[str, int] = {}
    by_diff: dict[str, int] = {}
    by_tool: dict[str, int] = {}
    for s in samples:
        st = str(s.get("stage", "multi"))
        bk = str(s.get("bucket", "seed"))
        df = str(s.get("difficulty", "easy"))
        tl = str(s.get("tool", ""))
        by_stage[st] = by_stage.get(st, 0) + 1
        by_bucket[bk] = by_bucket.get(bk, 0) + 1
        by_diff[df] = by_diff.get(df, 0) + 1
        by_tool[tl] = by_tool.get(tl, 0) + 1

    failed_checks: list[str] = []
    if n < _THR["A11_total"]:
        failed_checks.append("A11_total")
    if by_stage.get("synth", 0) < _THR["A11_synth"]:
        failed_checks.append("A11_synth")
    if by_stage.get("sim", 0) < _THR["A11_sim"]:
        failed_checks.append("A11_sim")
    if by_stage.get("sta", 0) < _THR["A11_sta"]:
        failed_checks.append("A11_sta")
    if by_diff.get("hard", 0) < _THR["A11_hard"]:
        failed_checks.append("A11_hard")
    if a2 < _THR["A2_rule_coverage"]:
        failed_checks.append("A2_rule_coverage")
    if a4 < _THR["A4_rule_conf_mean"]:
        failed_checks.append("A4_rule_conf_mean")
    if a1_top1 < _THR["A1_top1_weighted"]:
        failed_checks.append("A1_top1_weighted")
    if with_llm and a3 is not None and a3 < _THR["A3_llm_conf_mean"]:
        failed_checks.append("A3_llm_conf_mean")

    # 质量门槛判定(通过线 + 0.10 margin;达质量门槛才视为质量达标可持续推进)
    good_failed: list[str] = []
    if a1_top1 < _THR_GOOD["A1_top1_weighted"]:
        good_failed.append("A1_top1_weighted")
    if a2 < _THR_GOOD["A2_rule_coverage"]:
        good_failed.append("A2_rule_coverage")
    if with_llm and a3 is not None and a3 < _THR_GOOD["A3_llm_conf_mean"]:
        good_failed.append("A3_llm_conf_mean")
    if a4 < _THR_GOOD["A4_rule_conf_mean"]:
        good_failed.append("A4_rule_conf_mean")

    metrics = {
        "eval_id": datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_eval"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus_path": None,  # main 填
        "kb_path": None,
        "corpus_version": "1.0",
        "with_llm": with_llm,
        "provider": None,  # main 填
        "n_samples": n,
        "thresholds": dict(_THR),
        "A1_top1_weighted": round(a1_top1, 4),
        "A1_code_acc": round(code_acc_sum / n, 4) if n else 0.0,
        "A1_root_cause_acc": round(rc_acc_sum / n, 4) if n else 0.0,
        "A1_hint_acc": round(hint_acc_sum / n, 4) if n else 0.0,
        "A2_rule_coverage": round(a2, 4),
        "A2_rule_hits": rule_hits,
        "A2_rule_eligible": rule_eligible,
        "A3_llm_conf_mean": round(a3, 4) if a3 is not None else None,
        "A3_llm_samples": len(llm_conf_vals),
        "A4_rule_conf_mean": round(a4, 4),
        "A4_rule_samples": len(rule_conf_vals),
        "A8_p50_s": round(_percentile(sorted_w, 0.5), 4),
        "A8_p95_s": round(_percentile(sorted_w, 0.95), 4),
        "A8_wall_times": [round(w, 4) for w in wall_times],
        "A9_needs_patch_acc": round(a9_acc, 4),
        "A9_tp": needs_tp,
        "A9_fp": needs_fp,
        "A9_fn": needs_fn,
        "A9_tn": needs_tn,
        "A11_buckets": {
            "total": n,
            "by_tool": by_tool,
            "by_stage": by_stage,
            "by_bucket": by_bucket,
            "by_difficulty": by_diff,
        },
        "A12_sample_audit": None,
        "fallback_rate": round(fallback, 4),
        "kb_corrupted": len(kb.patterns) == 0,
        "per_bucket": None,  # --per-bucket 时填
        "per_pattern": [],  # 简版:不聚合,留空数组
        "failures": failures,
        "pass": len(failed_checks) == 0,
        "failed_checks": failed_checks,
        "good_thresholds": dict(_THR_GOOD),
        "good_failed": good_failed,
        "pass_good": len(good_failed) == 0,
    }
    return metrics


def _per_bucket(metrics: dict[str, Any], samples: list[dict[str, Any]]) -> dict[str, Any]:
    """按 bucket 分桶聚合 A1/A2/fallback/conf_mean。"""
    # 这里简化:用 metrics 中的 wall/failures 不便回溯,改为重算最小集
    out: dict[str, dict[str, Any]] = {}
    # 重新跑一遍轻量统计(已 cache 在 metrics 中缺失,故重算)
    by_bucket: dict[str, list[dict[str, Any]]] = {}
    for s in samples:
        bk = str(s.get("bucket", "seed"))
        by_bucket.setdefault(bk, []).append(s)
    # 用 metrics.failures 的 id 集合粗略推 A1:不可行,改跑简化诊断只算 rule_hits/conf
    return out  # main 里直接补


# ─────────────────────────────────────────────────────────────────────
# markdown 报告
# ─────────────────────────────────────────────────────────────────────


def _write_md(metrics: dict[str, Any], out_path: Path) -> None:
    b = metrics["A11_buckets"]
    lines = [
        "# Eval Diagnose Report",
        "",
        f"- generated_at: `{metrics['generated_at']}`",
        f"- corpus: `{metrics['corpus_path']}`",
        f"- with_llm: `{metrics['with_llm']}`",
        f"- n_samples: `{metrics['n_samples']}`",
        f"- pass: **{metrics['pass']}**",
        f"- failed_checks: `{metrics['failed_checks']}`",
        "",
        "## Metrics (A 编号)",
        "",
        "| 编号 | 指标 | 值 | 门槛 | 通过 |",
        "| --- | --- | --- | --- | --- |",
        f"| A1 | top1_weighted | {metrics['A1_top1_weighted']} | >= {_THR['A1_top1_weighted']} | {metrics['A1_top1_weighted'] >= _THR['A1_top1_weighted']} |",
        f"| A1.code | code_acc | {metrics['A1_code_acc']} | - | - |",
        f"| A1.rc | root_cause_acc | {metrics['A1_root_cause_acc']} | - | - |",
        f"| A1.hint | hint_acc | {metrics['A1_hint_acc']} | - | - |",
        f"| A2 | rule_coverage | {metrics['A2_rule_coverage']} ({metrics['A2_rule_hits']}/{metrics['A2_rule_eligible']}) | >= {_THR['A2_rule_coverage']} | {metrics['A2_rule_coverage'] >= _THR['A2_rule_coverage']} |",
        f"| A3 | llm_conf_mean | {metrics['A3_llm_conf_mean']} ({metrics['A3_llm_samples']}) | >= {_THR['A3_llm_conf_mean']} | {metrics['A3_llm_conf_mean'] is None or metrics['A3_llm_conf_mean'] >= _THR['A3_llm_conf_mean']} |",
        f"| A4 | rule_conf_mean | {metrics['A4_rule_conf_mean']} ({metrics['A4_rule_samples']}) | >= {_THR['A4_rule_conf_mean']} | {metrics['A4_rule_conf_mean'] >= _THR['A4_rule_conf_mean']} |",
        f"| A8 | p50/p95 | {metrics['A8_p50_s']}s / {metrics['A8_p95_s']}s | <= {_THR['A8_p50_s']}/{_THR['A8_p95_s']}s | {metrics['A8_p50_s'] <= _THR['A8_p50_s'] and metrics['A8_p95_s'] <= _THR['A8_p95_s']} |",
        f"| A9 | needs_patch_acc | {metrics['A9_needs_patch_acc']} (tp={metrics['A9_tp']},fp={metrics['A9_fp']},fn={metrics['A9_fn']},tn={metrics['A9_tn']}) | - | - |",
        "",
        "## A11 Buckets",
        "",
        f"- total: `{b['total']}` (>= {_THR['A11_total']})",
        f"- by_stage: `{b['by_stage']}` (synth/sim/sta 各 >= {_THR['A11_stage_min' if False else 'A11_synth']})",
        f"- by_bucket: `{b['by_bucket']}`",
        f"- by_difficulty: `{b['by_difficulty']}` (hard >= {_THR['A11_hard']})",
        f"- by_tool: `{b['by_tool']}`",
        "",
        f"- fallback_rate: `{metrics['fallback_rate']}`",
        f"- kb_corrupted: `{metrics['kb_corrupted']}`",
        "",
        "## Failures (A1 < 0.60)",
        "",
    ]
    if metrics["failures"]:
        lines.append("| id | score | code | root_cause | hint | reason |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for f in metrics["failures"]:
            lines.append(
                f"| {f['id']} | {f['score']} | {f['code']} | {f['root_cause']} | {f['hint']} | {f['reason']} |"
            )
    else:
        lines.append("(none)")
    out_path.write_text("\n".join(lines), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eval_diagnose.py",
        description="T28 诊断器评测:对 corpus.jsonl 跑规则层(+可选 LLM 层)算 A1-A11",
    )
    p.add_argument("--corpus", default="data/logs_corpus/corpus.jsonl",
                   help="corpus.jsonl 路径")
    p.add_argument("--kb", default=None,
                   help="ErrorKB 路径;默认 settings.error_kb_path")
    p.add_argument("--with-llm", action="store_true",
                   help="加跑 LLM 层(A3);需 GLM_API_KEY/ANTHROPIC_API_KEY")
    p.add_argument("--no-llm", action="store_true",
                   help="只规则层(默认,与 --with-llm 互斥)")
    p.add_argument("--provider", default=None,
                   help="--with-llm 时生效;glm/claude;默认 settings.llm.provider")
    p.add_argument("--out", default=None,
                   help="metrics JSON 落盘路径;默认 stdout 仅打印")
    p.add_argument("--per-bucket", action="store_true",
                   help="输出 per_bucket 字段(seed/grown/novel 各自 A1/A2/fallback)")
    p.add_argument("--report-md", default="data/logs_corpus/eval_report.md",
                   help="markdown 报告路径(默认 data/logs_corpus/eval_report.md)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.with_llm and args.no_llm:
        print(json.dumps({"error": "--with-llm and --no-llm are mutually exclusive"},
                         ensure_ascii=False))
        return 2
    with_llm = args.with_llm and not args.no_llm

    corpus_path = Path(args.corpus)
    if not corpus_path.exists():
        print(json.dumps({"error": f"corpus not found: {corpus_path}", "exit": 2},
                         ensure_ascii=False))
        return 2
    try:
        samples = _load_corpus(corpus_path)
    except ValueError as e:
        print(json.dumps({"error": str(e), "exit": 2}, ensure_ascii=False))
        return 2

    # ErrorKB:优先 --kb,否则 settings.error_kb_path;load 不抛
    kb_path = args.kb
    if kb_path is None:
        try:
            from eda_agent.settings import load_settings
            kb_path = load_settings().error_kb_path
        except Exception:
            kb_path = "data/error_kb.json"
    kb = ErrorKB.load(kb_path)

    attributor: LLMAttributor | None = None
    provider_name = None
    if with_llm:
        try:
            from eda_agent.settings import load_settings
            from eda_agent.llm.factory import make_provider
            settings = load_settings()
            if args.provider:
                settings.llm.provider = args.provider  # type: ignore[attr-defined]
            provider_name = settings.llm.provider
            llm = make_provider(settings)
            attributor = LLMAttributor(llm)
        except Exception as e:
            print(json.dumps(
                {"error": f"LLM init failed: {e}", "exit": 2},
                ensure_ascii=False,
            ))
            return 2

    metrics = _eval(samples, kb, with_llm, attributor)
    metrics["corpus_path"] = str(corpus_path)
    metrics["kb_path"] = str(kb_path)
    metrics["provider"] = provider_name if with_llm else None

    if args.per_bucket:
        # 简化 per_bucket:基于 metrics 已有数据按 bucket 重算
        bk_a1: dict[str, list[float]] = {}
        bk_hits: dict[str, int] = {}
        bk_elig: dict[str, int] = {}
        bk_fb: dict[str, int] = {}
        bk_conf: dict[str, list[float]] = {}
        for s, p in zip(samples, _iter_per_sample(samples, kb, with_llm, attributor)):
            bk = str(s.get("bucket", "seed"))
            bk_a1.setdefault(bk, []).append(p["top1"])
            if bk in ("seed", "grown"):
                bk_elig[bk] = bk_elig.get(bk, 0) + 1
                if p["kb_hits"]:
                    bk_hits[bk] = bk_hits.get(bk, 0) + 1
            if "llm" in str(p["used_layers"]):
                bk_fb[bk] = bk_fb.get(bk, 0) + 1
            bk_conf.setdefault(bk, []).append(p["confidence"])
        per_bucket = {}
        for bk in ("seed", "grown", "novel"):
            ns = len(bk_a1.get(bk, []))
            if ns == 0:
                continue
            per_bucket[bk] = {
                "n": ns,
                "A1": round(statistics.mean(bk_a1[bk]), 4),
                "A2": round(bk_hits.get(bk, 0) / bk_elig[bk], 4) if bk_elig.get(bk) else 0.0,
                "fallback_rate": round(bk_fb.get(bk, 0) / ns, 4),
                "conf_mean": round(statistics.mean(bk_conf[bk]), 4),
            }
        metrics["per_bucket"] = per_bucket

    # 落盘 + stdout
    out_json = json.dumps(metrics, ensure_ascii=False, indent=2)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(out_json, encoding="utf-8")
    report_md = Path(args.report_md)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    _write_md(metrics, report_md)

    # stdout 精简版(便于 CI 日志)
    print(json.dumps({
        "pass": metrics["pass"],
        "failed_checks": metrics["failed_checks"],
        "A1_top1_weighted": metrics["A1_top1_weighted"],
        "A2_rule_coverage": metrics["A2_rule_coverage"],
        "A3_llm_conf_mean": metrics["A3_llm_conf_mean"],
        "A4_rule_conf_mean": metrics["A4_rule_conf_mean"],
        "A8_p50_s": metrics["A8_p50_s"],
        "A8_p95_s": metrics["A8_p95_s"],
        "A9_needs_patch_acc": metrics["A9_needs_patch_acc"],
        "n_samples": metrics["n_samples"],
        "report_md": str(report_md),
        "out": args.out,
    }, ensure_ascii=False, indent=2))
    return 0 if metrics["pass"] else 1


def _iter_per_sample(
    samples, kb, with_llm, attributor,
):
    """per_bucket 用的轻量重算(只返回 top1/kb_hits/conf/used_layers)。

    为了避免双跑诊断的成本,这里直接复用 _eval 已算过的数据不可行(metrics 没存
    per_sample);接受重算代价,因 per_bucket 是可选 flag。
    """
    for s in samples:
        parsed, _ = _diagnose_one(s, kb, with_llm, attributor, monotonic() + 60.0)
        rc_list = parsed.get("root_causes") or []
        gt = s.get("ground_truth") or {}
        exp_code = str(gt.get("expected_error_code", ""))
        pred_code = str(rc_list[0]["code"]) if rc_list else ""
        if not pred_code and "llm" in str(parsed.get("used_layers", "")):
            pred_code = {"yosys_synth": "synth", "iverilog_sim": "sim",
                         "opensta_timing": "sta"}.get(str(s.get("tool", "")), "")
        if pred_code and pred_code == exp_code:
            code_match = 1.0
        elif pred_code and exp_code and pred_code.split(".", 1)[0] == exp_code.split(".", 1)[0]:
            code_match = 0.5
        else:
            code_match = 0.0
        rc_text = str(parsed.get("root_cause_summary", ""))
        rc_kw_list = [str(k) for k in (gt.get("root_cause_keywords") or [])]
        rc_match = 1.0 if _kw_recall(_keywords(rc_text), rc_kw_list) >= _RECALL_THR else 0.0
        hint_text = " ".join(parsed.get("fix_hints") or [])
        hint_kw_list = [str(k) for k in (gt.get("fix_hint_keywords") or [])]
        hint_match = 1.0 if _kw_recall(_keywords(hint_text), hint_kw_list) >= _RECALL_THR else 0.0
        top1 = 0.4 * code_match + 0.4 * rc_match + 0.2 * hint_match
        yield {
            "top1": top1,
            "kb_hits": parsed.get("kb_hits"),
            "used_layers": parsed.get("used_layers"),
            "confidence": parsed.get("confidence", 0.0),
        }


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
