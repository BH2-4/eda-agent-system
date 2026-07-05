"""B 自修复 skill_self_heal(契约 §2.3 §5.2 v1.2)。

闭环流程:综合 → 仿真 →(可选 STA)→ 诊断 → patch → 重验证,带版本栈回退与
三策略升级(diff → full_rewrite → diagnose_only)。失败也落 best/{rtl.v,meta.json}
+ report.md,``best_iter`` 可 -1。

契约硬规则:``contract_version`` 必须 ``from eda_agent.contracts import CONTRACT_VERSION``
引用(禁止裸 "0.1.0");``parsed`` 必须含 ``_schema`` 元字段;``artifacts`` 元素必须用
``artifact_ref`` 工厂;``ToolResult.status`` 只有 ok/error/timeout 三态(warning 归 ok)。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Any, Literal

from eda_agent.contracts import (
    CONTRACT_VERSION,
    LLMProvider,
    Message,
    Skill,
    SkillResult,
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
    ErrorItem,
)
from eda_agent.settings import Settings

# ─────────────────────────────────────────────────────────────────────
# 内部 dataclass
# ─────────────────────────────────────────────────────────────────────


@dataclass
class HealGoal:
    """解析后的修复目标。

    - ``pass_mode="all"``:pass all tests;``num_required`` 运行时填 total。
    - ``pass_mode="at_least"``:pass N tests;``num_required=N``。
    - ``sta_required``:goal 含 "timing"/"no violation" → True。
    """

    pass_mode: Literal["all", "at_least"]
    num_required: int
    sta_required: bool
    raw: str


@dataclass
class PatchOutcome:
    """单轮 patch 结果(供主循环判定栈推进 / 回退 / 策略升级)。"""

    iter_n: int
    diagnose_parsed: dict
    patch_source: Literal["llm_diff", "llm_full_rewrite", "rule_based", "none"]
    patch_text: str
    target_lines: tuple[int, int] | None
    applied: bool
    rolled_back: bool
    regression: bool
    llm_tokens_in: int
    llm_tokens_out: int
    rationale: str


@dataclass
class IterSnapshot:
    """单轮快照(版本栈元素 / report 渲染用)。"""

    iter_n: int
    rtl_text: str
    num_passed: int
    num_failed: int
    patch_source: str
    convergence: str


# ─────────────────────────────────────────────────────────────────────
# _parse_goal —— 自然语言 goal → HealGoal(无法解析 → None)
# ─────────────────────────────────────────────────────────────────────

_AT_LEAST_RE = re.compile(r"pass\s+(\d+)\s+test", re.IGNORECASE)


def _parse_goal(goal_str: str) -> HealGoal | None:
    """解析 goal 字符串;无法识别返回 None(调用方报 heal.unsupported_goal)。"""
    if not goal_str:
        return None
    raw = goal_str.strip()
    low = raw.lower()

    sta_required = ("timing" in low) or ("no violation" in low)

    # "pass all tests" / "pass all";排除含否定词的反向语义 goal(如 "fail all tests")。
    if ("pass all" in low or "all tests" in low) and not any(
        w in low for w in ("fail ", "skip ", "not pass")
    ):
        return HealGoal(pass_mode="all", num_required=0, sta_required=sta_required, raw=raw)

    # "pass N tests"
    m = _AT_LEAST_RE.search(raw)
    if m is not None:
        n = int(m.group(1))
        return HealGoal(
            pass_mode="at_least",
            num_required=n,
            sta_required=sta_required,
            raw=raw,
        )

    return None


# ─────────────────────────────────────────────────────────────────────
# _locate_lines —— 从 root_causes(dict 列表)抽行号区间
# ─────────────────────────────────────────────────────────────────────

_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r":(\d+):\s*\d+"),     # iverilog: file.v:42: 误差列号
    re.compile(r":(\d+):"),            # 通用 file:line:
    re.compile(r"\((\d+)\)"),          # (line)
)


def _locate_lines(
    root_causes: list[dict],
    rtl_text: str,
) -> tuple[int, int] | None:
    """从 root_causes 的 message/evidence 抽首个行号,返回 (start, end) 区间。

    命中行号 → (line, line + 5) 片段区间(±5 行语义);未命中 → None。
    """
    for rc in root_causes:
        if not isinstance(rc, dict):
            continue
        texts: list[str] = []
        msg = rc.get("message")
        if isinstance(msg, str):
            texts.append(msg)
        ev = rc.get("evidence")
        if isinstance(ev, list):
            texts.extend(str(x) for x in ev if x)
        for text in texts:
            for pat in _LINE_PATTERNS:
                m = pat.search(text)
                if m is not None:
                    try:
                        ln = int(m.group(1))
                    except (ValueError, IndexError):
                        continue
                    if ln >= 1:
                        return (max(1, ln - 5), ln + 5)
    return None


# ─────────────────────────────────────────────────────────────────────
# _build_prompt / _extract_patch / _apply_patch —— 三策略 LLM patch
# ─────────────────────────────────────────────────────────────────────

_SYSTEM_BASE = "你是 Verilog 修复专家。根据诊断根因修复 RTL,保持模块接口不变。"


def _build_prompt(
    strategy: str,
    rtl_text: str,
    root_causes: list[dict],
    target_lines: tuple[int, int] | None,
) -> list[Message] | None:
    """三策略 prompt;diagnose_only 返回 None(不调 LLM)。"""
    if strategy == "diagnose_only":
        return None

    causes_json = json.dumps(root_causes, ensure_ascii=False)

    if strategy == "diff":
        if target_lines is not None:
            start, end = target_lines
            lines = rtl_text.splitlines()
            # target_lines 是 1-based 闭区间;切片转 0-based,夹边界。
            s = max(0, start - 1)
            e = min(len(lines), end)
            snippet = "\n".join(lines[s:e])
        else:
            snippet = rtl_text
        user = (
            "[DIFF 策略] 出错片段:\n"
            + snippet
            + "\n\n根因(JSON):" + causes_json
            + "\n\n只改出错行±5 行,别动其他。"
            "输出 unified diff 补丁(以 --- /+++ /@@ /+- 开头的行)。"
        )
    else:  # full_rewrite
        user = (
            "[FULL_REWRITE 策略] 完整 RTL:\n"
            + rtl_text
            + "\n\n根因(JSON):" + causes_json
            + "\n\n整文件重写,保持模块接口不变。"
            "输出完整 .v 文件全文(纯 Verilog,不要 markdown 包裹)。"
        )

    return [Message("system", _SYSTEM_BASE), Message("user", user)]


_FENCE_RE = re.compile(r"```(?:verilog|v|Verilog)?\s*\n(.*?)```", re.DOTALL)


def _extract_patch(resp_text: str, strategy: str) -> tuple[str, bool]:
    """从 LLM 响应抽 patch 文本。

    返回 (patch_text, is_full_rewrite)。diff 策略抽 unified diff 行;
    full_rewrite 抽 Verilog 全文(剥 markdown 围栏)。
    """
    if strategy == "diff":
        out: list[str] = []
        in_diff = False
        for line in (resp_text or "").splitlines():
            stripped = line.strip()
            if not in_diff:
                if stripped.startswith("--- "):
                    in_diff = True
                    out.append(line)
                elif stripped.startswith("@@"):
                    in_diff = True
                    out.append(line)
            else:
                if stripped.startswith(("---", "+++", "@@", "+", "-")) or line == "":
                    out.append(line)
                else:
                    # diff 块结束(遇到普通文本行)。
                    break
        return ("\n".join(out).strip(), False)

    # full_rewrite:剥 markdown 围栏(若有)。
    text = resp_text or ""
    m = _FENCE_RE.search(text)
    if m is not None:
        text = m.group(1)
    return (text.strip(), True)


def _apply_patch(
    rtl_text: str,
    patch_text: str,
    is_full_rewrite: bool,
    target_lines: tuple[int, int] | None,
) -> tuple[str, bool]:
    """应用 patch 返回 (rtl_new, applied_ok)。

    full_rewrite:patch_text 替换全文(非空且含 module 关键字视为成功)。
    diff:对 target_lines 区间做整体替换(MVP:不解析完整 unified diff,
    若 patch_text 含 Verilog 代码片段则替换区间,否则失败)。
    """
    if not patch_text or not patch_text.strip():
        return (rtl_text, False)

    if is_full_rewrite:
        if "module" in patch_text and "endmodule" in patch_text:
            return (patch_text, True)
        return (rtl_text, False)

    # diff 策略 MVP:取 patch 中所有 +/- 之后的纯 Verilog 行,替换 target_lines 区间。
    add_lines: list[str] = []
    has_add = False
    for line in patch_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            add_lines.append(line[1:])
            has_add = True
    if not has_add:
        # 没有有效 + 行 → 视为未应用。
        return (rtl_text, False)

    if target_lines is None:
        # 无定位:append 到末尾(MVP 容错,实际很少走到)。
        return (rtl_text.rstrip() + "\n" + "\n".join(add_lines) + "\n", True)

    lines = rtl_text.splitlines()
    start, end = target_lines
    s = max(0, start - 1)
    e = min(len(lines), end)
    new_lines = lines[:s] + add_lines + lines[e:]
    return ("\n".join(new_lines), True)


# ─────────────────────────────────────────────────────────────────────
# WSL 路径转换(参考 tools/iverilog_sim.py)
# ─────────────────────────────────────────────────────────────────────


def _to_wsl_path(win_path: str) -> str:
    """Windows 路径 → WSL 路径:D:\\x\\y → /mnt/d/x/y。"""
    p = win_path.replace("\\", "/")
    if len(p) >= 2 and p[1] == ":" and p[0].isalpha():
        drive = p[0].lower()
        return "/mnt/" + drive + p[2:]
    return p


# ─────────────────────────────────────────────────────────────────────
# SelfHealSkill
# ─────────────────────────────────────────────────────────────────────


class SelfHealSkill:
    """B 自修复 Skill(契约 §2.3 §5.2)。``name="skill_self_heal"``。"""

    name = "skill_self_heal"
    description = (
        "自修复 RTL:综合→仿真→(STA)→诊断→patch→重验证,版本栈回退"
    )

    def __init__(
        self,
        registry: Any,
        llm: LLMProvider,
        runner: Any,
        max_iterations: int = 5,
        budget_s: float = 480,
        settings: Settings | None = None,
    ) -> None:
        self._registry = registry
        self._llm = llm
        self._runner = runner
        self.max_iterations = max_iterations
        self.budget_s = float(budget_s)
        self._settings = settings or Settings()
        self.schema: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "rtl": {"type": "string"},
                    "tb": {"type": "string"},
                    "diagnose": {"type": ["object", "null"]},
                    "max_iter": {"type": "integer"},
                    "goal": {"type": "string"},
                    "lib": {"type": ["string", "null"]},
                    "clock": {"type": ["string", "null"]},
                    "top_module": {"type": ["string", "null"]},
                    "_remaining_budget_s": {"type": "number"},
                },
                "required": ["rtl", "tb", "max_iter", "goal"],
            },
        }

    # ── Skill Protocol ───────────────────────────────────────────────
    def run(
        self,
        run_id: str | None,
        inputs: dict[str, Any],
        remaining_budget_s: float | None = None,
    ) -> SkillResult:
        t0 = monotonic()
        budget = min(self.budget_s, remaining_budget_s or self.budget_s)
        run_id_s = str(run_id) if run_id is not None else ""

        rtl_path = inputs.get("rtl")
        tb_path = inputs.get("tb")
        goal_str = str(inputs.get("goal", "") or "")
        max_iter = int(inputs.get("max_iter") or self.max_iterations)
        seed = inputs.get("diagnose")
        lib = inputs.get("lib")
        clock = inputs.get("clock")
        # top_module 当前未直接消费(L1 Tool 自带 top_module 字段);保留入参契约。
        _top = inputs.get("top_module")

        # ── 入参校验 ──────────────────────────────────────────────────
        if not rtl_path or not Path(str(rtl_path)).exists():
            return self._fail(run_id_s, HEAL_RTL_NOT_FOUND,
                              f"rtl not found: {rtl_path}", t0)
        if not tb_path or not Path(str(tb_path)).exists():
            return self._fail(run_id_s, HEAL_TB_NOT_FOUND,
                              f"tb not found: {tb_path}", t0)

        goal = _parse_goal(goal_str)
        if goal is None:
            return self._fail(
                run_id_s, HEAL_UNSUPPORTED_GOAL,
                f"unparseable goal: {goal_str}", t0,
            )

        rtl_text = Path(str(rtl_path)).read_text(encoding="utf-8")

        base_dir = Path("runs") / run_id_s / "self_heal"
        snap0_dir = base_dir / "iter_0"
        snap0_dir.mkdir(parents=True, exist_ok=True)
        (snap0_dir / "rtl_snapshot.v").write_text(rtl_text, encoding="utf-8")

        version_stack: list[str] = [rtl_text]
        best_iter = -1
        best_score = -1
        candidates_at_best = 0
        best_rtl_text = rtl_text
        strategy = "diff"
        regression_streak = 0
        num_passed_dropped_streak = 0
        convergence = "none"
        trajectory: list[dict] = [
            {"step": "start", "iter": 0, "detail": f"goal={goal_str}"},
        ]
        last_patch_source: str = "none"
        final_diag_parsed: dict = {}
        iter_done = 0

        for iter_n in range(max_iter):
            if monotonic() - t0 > budget:
                convergence = "budget"
                break

            iter_done = iter_n + 1
            cur_rtl = version_stack[-1]
            snap_dir = base_dir / f"iter_{iter_done}"
            snap_dir.mkdir(parents=True, exist_ok=True)
            rtl_snap_path = snap_dir / "rtl_snapshot.v"
            rtl_snap_path.write_text(cur_rtl, encoding="utf-8")

            # ── 三 stage ──────────────────────────────────────────────
            synth_res = self._stage_synth(
                str(rtl_snap_path), run_id_s, iter_n,
            )
            synth_ok = (
                synth_res.is_ok()
                and synth_res.parsed.get("success") is True
            )

            sim_res = self._stage_sim(
                str(rtl_snap_path), str(tb_path), run_id_s, iter_n,
            )
            num_passed = int(sim_res.parsed.get("num_passed") or 0)
            # iverilog_sim 的 parsed 不直接含 total(契约 §2.2:只有 num_passed
            # /num_failed);优先读 total,缺失时用 num_passed+num_failed 兜底。
            total_raw = sim_res.parsed.get("total")
            if total_raw is not None:
                total = int(total_raw)
            else:
                nf = sim_res.parsed.get("num_failed")
                total = num_passed + (int(nf) if nf is not None else 0)
            required = (
                total if goal.pass_mode == "all" else goal.num_required
            )
            # total=0(TB 未打印协议行/编译失败)时,required=0 会让 0>=0 误判通过;
            # 此时只信 parsed["passed"] is True(契约 §2.2:无 TEST_PASS 行 → passed=None)。
            if total == 0 and goal.pass_mode == "all":
                sim_ok = sim_res.parsed.get("passed") is True
            else:
                sim_ok = (
                    sim_res.parsed.get("passed") is True
                    or num_passed >= required
                )

            sta_res: ToolResult | None = None
            if goal.sta_required and lib and clock and synth_ok:
                netlist = self._extract_netlist(synth_res, run_id_s)
                if netlist is not None:
                    sta_res = self._stage_sta(
                        netlist, str(lib), str(clock), run_id_s, iter_n,
                    )
            sta_ok = (
                sta_res is None
                or (
                    sta_res.parsed.get("wns") is not None
                    and float(sta_res.parsed["wns"]) >= 0
                )
            )

            trajectory.append({
                "step": "stage",
                "iter": iter_n,
                "detail": (
                    f"synth={synth_ok} sim={sim_ok}/"
                    f"{num_passed}/{required} sta={sta_ok}"
                ),
            })

            # ── 全通过:收敛成功 ─────────────────────────────────────
            if synth_ok and sim_ok and sta_ok:
                convergence = "all_pass"
                best_iter = iter_n
                best_rtl_text = cur_rtl
                if num_passed > best_score:
                    best_score = num_passed
                    candidates_at_best = 1
                break

            # ── best 追踪(score = num_passed) ─────────────────────
            if best_score > 0 and num_passed < best_score:
                num_passed_dropped_streak += 1
            else:
                num_passed_dropped_streak = 0
            if num_passed > best_score and num_passed > 0:
                best_score = num_passed
                best_iter = iter_n
                candidates_at_best = 1
                best_rtl_text = cur_rtl
            elif num_passed == best_score and num_passed > 0:
                candidates_at_best += 1

            # ── 诊断 + patch ─────────────────────────────────────────
            stage_results = [synth_res, sim_res]
            if sta_res is not None:
                stage_results.append(sta_res)
            outcome = self._diagnose_and_patch(
                stage_results,
                cur_rtl,
                run_id_s,
                iter_n,
                seed if iter_n == 0 else None,
                strategy,
                snap_dir,
                budget_remaining=max(0.0, budget - (monotonic() - t0)),
            )
            final_diag_parsed = outcome.diagnose_parsed or final_diag_parsed
            if outcome.patch_source != "none":
                last_patch_source = outcome.patch_source

            trajectory.append({
                "step": "patch",
                "iter": iter_n,
                "detail": (
                    f"strategy={strategy} applied={outcome.applied} "
                    f"source={outcome.patch_source}"
                ),
            })

            if outcome.applied:
                # _diagnose_and_patch 已写 snap_dir/rtl_snapshot.v(rtl_new)。
                try:
                    rtl_new = rtl_snap_path.read_text(encoding="utf-8")
                except OSError:
                    rtl_new = cur_rtl
                    trajectory.append({
                        "step": "patch_read_error",
                        "iter": iter_n,
                        "detail": "rtl_snapshot.v read failed, fallback to cur_rtl",
                    })
                version_stack.append(rtl_new)
                regression_streak = 0
            else:
                regression_streak += 1
                next_strategy = {
                    "diff": "full_rewrite",
                    "full_rewrite": "diagnose_only",
                }.get(strategy, "diagnose_only")
                if next_strategy == "diagnose_only":
                    trajectory.append({
                        "step": "reduced_to_diagnose",
                        "iter": iter_n,
                    })
                    convergence = "regression"
                    break
                strategy = next_strategy

            if regression_streak >= 3 or num_passed_dropped_streak >= 3:
                convergence = "regression"
                break
        else:
            # for-else:循环自然结束(未 break),收敛保持已设值或 max_iter。
            if convergence == "none":
                convergence = "max_iter"

        if convergence == "none":
            convergence = "max_iter" if best_iter >= 0 else "none"

        # ── 收尾:落 best/{rtl.v,meta.json} + report.md ───────────────
        best_dir = base_dir / "best"
        best_dir.mkdir(parents=True, exist_ok=True)
        (best_dir / "rtl.v").write_text(best_rtl_text, encoding="utf-8")
        meta = {
            "best_iter": best_iter,
            "num_passed": best_score if best_iter >= 0 else -1,
            "convergence_cause": convergence,
            "candidates_at_best_score": candidates_at_best,
        }
        (best_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (base_dir / "report.md").write_text(
            self._render_report(
                goal_str, convergence, best_iter, best_score,
                last_patch_source, trajectory,
            ),
            encoding="utf-8",
        )

        passed = convergence == "all_pass"
        status: Literal["ok", "error", "budget_exhausted"] = (
            "ok" if passed
            else ("budget_exhausted" if convergence == "budget" else "error")
        )
        error_code: str | None = (
            None if passed
            else (EDA_BUDGET_EXHAUSTED if convergence == "budget"
                  else HEAL_REGRESSION_DEADLOCK)
        )
        fixed_ref = (
            artifact_ref(run_id_s, "self_heal/best/rtl.v") if passed else None
        )
        report_ref = artifact_ref(run_id_s, "self_heal/report.md")
        summary = (
            f"convergence={convergence} best_iter={best_iter} "
            f"num_passed={best_score}"
        )
        final_parsed: dict[str, Any] = {
            "_schema": {
                "name": "skill_self_heal",
                "version": "0.1.0",
                "contract_version": CONTRACT_VERSION,
            },
            "skill": "self_heal",
            "passed": passed,
            "iterations": iter_done,
            "best_iter": best_iter,
            "convergence_cause": convergence,
            "patch_source": last_patch_source,
            "fixed_rtl_ref": fixed_ref,
            "final_report_ref": report_ref,
            "summary": summary,
        }
        artifacts = [
            report_ref,
            artifact_ref(run_id_s, "self_heal/best/rtl.v"),
            artifact_ref(run_id_s, "self_heal/best/meta.json"),
        ]
        return SkillResult(
            status=status,
            iterations=iter_done,
            final_parsed=final_parsed,
            trajectory=trajectory,
            artifacts=artifacts,
            summary=summary,
            patch_source=last_patch_source,  # type: ignore[arg-type]
            convergence_cause=convergence,    # type: ignore[arg-type]
            best_iter=best_iter,
            error_code=error_code,
            budget_used_s=float(monotonic() - t0),
        )

    def as_tool(self) -> Tool:
        """委托 ``skills.base.as_tool``。"""
        from eda_agent.skills.base import as_tool
        return as_tool(self)

    # ── stage helpers(只构造 ToolCall→registry.get→append_step) ────
    def _stage_synth(
        self, rtl_path: str, run_id: str, iter_n: int,
    ) -> ToolResult:
        call = ToolCall(
            name="yosys_synth",
            args={"rtl": rtl_path, "run_id": run_id},
            caller="self_heal",
            llm_tool_call_id=None,
        )
        res = self._registry.get("yosys_synth")(call)
        self._runner.append_step(
            run_id, call, res, skill_name="self_heal", iter=iter_n,
        )
        return res

    def _stage_sim(
        self, rtl_path: str, tb_path: str, run_id: str, iter_n: int,
    ) -> ToolResult:
        call = ToolCall(
            name="iverilog_sim",
            args={"rtl": rtl_path, "tb": tb_path, "run_id": run_id},
            caller="self_heal",
        )
        res = self._registry.get("iverilog_sim")(call)
        self._runner.append_step(
            run_id, call, res, skill_name="self_heal", iter=iter_n,
        )
        return res

    def _stage_sta(
        self, netlist_path: str, lib: str, clock: str,
        run_id: str, iter_n: int,
    ) -> ToolResult:
        call = ToolCall(
            name="opensta_timing",
            args={
                "netlist": netlist_path, "lib": lib,
                "clock": clock, "run_id": run_id,
            },
            caller="self_heal",
        )
        res = self._registry.get("opensta_timing")(call)
        self._runner.append_step(
            run_id, call, res, skill_name="self_heal", iter=iter_n,
        )
        return res

    def _extract_netlist(
        self, synth_res: ToolResult, run_id: str,
    ) -> str | None:
        """从 synth_res.artifacts 找 netlist.v 引用,返回实际路径。"""
        for art in synth_res.artifacts:
            rel = art.get("rel_path", "") if isinstance(art, dict) else ""
            if rel.endswith("synth/netlist.v"):
                return str(Path("runs") / run_id / rel)
        return None

    # ── _diagnose_and_patch(B3 核心) ───────────────────────────────
    def _diagnose_and_patch(
        self,
        stage_results: list[ToolResult],
        rtl_text: str,
        run_id: str,
        iter_n: int,
        seed: dict | None,
        strategy: str,
        snap_dir: Path,
        budget_remaining: float,
    ) -> PatchOutcome:
        # 1. 调 skill_diagnose(首轮若有 seed 直接用)。
        if seed is not None:
            diag_parsed = dict(seed)
        else:
            tool_results = []
            for i, r in enumerate(stage_results):
                tool_results.append({
                    "tool": r.tool,
                    "status": r.status,
                    "parsed": r.parsed,
                    "stdout": r.stdout,
                    "run_id": run_id,
                    "step_idx": i,
                })
            diag_call = ToolCall(
                name="skill_diagnose",
                args={
                    "tool_results": tool_results,
                    "run_id": run_id,
                    "_remaining_budget_s": budget_remaining,
                },
                caller="self_heal",
            )
            diag_res = self._registry.get("skill_diagnose")(diag_call)
            self._runner.append_step(
                run_id, diag_call, diag_res,
                skill_name="self_heal", iter=iter_n,
            )
            diag_parsed = (
                diag_res.parsed if diag_res.is_ok() else {}
            )

        # 2. root_causes;空则 fallback 包装 fail_signals(ErrorItem)。
        root_causes = list(diag_parsed.get("root_causes") or [])
        if not root_causes:
            wrapped: list[dict] = []
            for r in stage_results:
                if r.tool == "iverilog_sim":
                    for sig in (r.parsed.get("fail_signals") or []):
                        ei = ErrorItem.make(
                            "sim.fail_signal",
                            tool="iverilog_sim",
                            message=str(sig),
                            severity="error",
                        )
                        wrapped.append({
                            "code": ei.code, "namespace": ei.namespace,
                            "tool": ei.tool, "severity": ei.severity,
                            "message": ei.message, "evidence": ei.evidence,
                            "fix_suggestion": ei.fix_suggestion,
                        })
            root_causes = wrapped

        # 3. 定位行号。
        target_lines = _locate_lines(root_causes, rtl_text)

        # 4. prompt;diagnose_only → 不调 LLM,直接降级。
        messages = _build_prompt(strategy, rtl_text, root_causes, target_lines)
        if messages is None:
            return PatchOutcome(
                iter_n=iter_n, diagnose_parsed=diag_parsed,
                patch_source="none", patch_text="",
                target_lines=target_lines, applied=False,
                rolled_back=False, regression=False,
                llm_tokens_in=0, llm_tokens_out=0,
                rationale="diagnose_only: no patch",
            )

        # 5. 调 LLM。
        try:
            resp = self._llm.chat(
                messages, temperature=0.0, max_tokens=4096,
            )
        except Exception as e:
            return PatchOutcome(
                iter_n=iter_n, diagnose_parsed=diag_parsed,
                patch_source="none", patch_text="",
                target_lines=target_lines, applied=False,
                rolled_back=False, regression=False,
                llm_tokens_in=0, llm_tokens_out=0,
                rationale=f"llm failed: {e}",
            )

        # 6. 抽 patch。
        patch_text, is_full_rewrite = _extract_patch(resp.text, strategy)

        # 7. 应用 + 语法预检。
        rtl_new, applied_ok = _apply_patch(
            rtl_text, patch_text, is_full_rewrite, target_lines,
        )
        syntax_ok = self._syntax_check(rtl_new) if applied_ok else False

        if applied_ok and syntax_ok:
            (snap_dir / "rtl_snapshot.v").write_text(
                rtl_new, encoding="utf-8",
            )
            (snap_dir / "rtl_patch.diff").write_text(
                patch_text, encoding="utf-8",
            )
            return PatchOutcome(
                iter_n=iter_n, diagnose_parsed=diag_parsed,
                patch_source=(
                    "llm_full_rewrite" if is_full_rewrite else "llm_diff"
                ),
                patch_text=patch_text,
                target_lines=target_lines, applied=True,
                rolled_back=False, regression=False,
                llm_tokens_in=int(resp.tokens_in),
                llm_tokens_out=int(resp.tokens_out),
                rationale="applied",
            )
        return PatchOutcome(
            iter_n=iter_n, diagnose_parsed=diag_parsed,
            patch_source=(
                "llm_full_rewrite" if is_full_rewrite else "llm_diff"
            ),
            patch_text=patch_text,
            target_lines=target_lines, applied=False,
            rolled_back=False, regression=False,
            llm_tokens_in=int(resp.tokens_in),
            llm_tokens_out=int(resp.tokens_out),
            rationale="apply/syntax failed",
        )

    # ── _syntax_check —— wsl iverilog -t null(参考 iverilog_sim.py) ─
    def _syntax_check(self, rtl_text: str) -> bool:
        if not rtl_text or not rtl_text.strip():
            return False
        wsl = bool(self._settings.eda.wsl_enabled)
        timeout = self._settings.eda.tool_timeout_s
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".v", delete=False, encoding="utf-8",
            ) as f:
                f.write(rtl_text)
                tmp_path = f.name
        except OSError:
            return False
        try:
            if wsl:
                wsl_path = _to_wsl_path(tmp_path)
                cmd = [
                    "wsl.exe", "-d", "Ubuntu-24.04", "-e",
                    self._settings.eda.iverilog_cmd, "-t", "null",
                    "-o", "/dev/null", wsl_path,
                ]
            else:
                cmd = [
                    self._settings.eda.iverilog_cmd, "-t", "null",
                    "-o", os.devnull, tmp_path,
                ]
            try:
                r = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=timeout,
                    encoding="utf-8", errors="replace",
                )
                return r.returncode == 0
            except (subprocess.TimeoutExpired, OSError):
                return False
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ── _render_report —— 每轮一节 markdown ─────────────────────────
    def _render_report(
        self,
        goal_str: str,
        convergence: str,
        best_iter: int,
        best_score: int,
        patch_source: str,
        trajectory: list[dict],
    ) -> str:
        lines: list[str] = []
        lines.append("# Self-Heal Report")
        lines.append("")
        lines.append(f"- goal: `{goal_str}`")
        lines.append(f"- convergence_cause: `{convergence}`")
        lines.append(f"- best_iter: `{best_iter}`")
        lines.append(f"- num_passed (best): `{best_score}`")
        lines.append(f"- patch_source: `{patch_source}`")
        lines.append("")
        lines.append("## Trajectory")
        lines.append("")
        cur_iter = -1
        for ev in trajectory:
            it = ev.get("iter", -1)
            if it != cur_iter:
                cur_iter = it
                lines.append(f"### iter {it}")
                lines.append("")
            step = ev.get("step", "?")
            detail = ev.get("detail", "")
            lines.append(f"- **{step}**: {detail}")
        lines.append("")
        return "\n".join(lines)

    # ── _fail —— 输入校验失败,落 namespace=heal ─────────────────────
    def _fail(
        self, run_id: str, code: str, hint: str, t0: float,
    ) -> SkillResult:
        summary = hint
        final_parsed: dict[str, Any] = {
            "_schema": {
                "name": "skill_self_heal",
                "version": "0.1.0",
                "contract_version": CONTRACT_VERSION,
            },
            "skill": "self_heal",
            "passed": False,
            "iterations": 0,
            "best_iter": -1,
            "convergence_cause": "none",
            "patch_source": "none",
            "fixed_rtl_ref": None,
            "final_report_ref": None,
            "summary": summary,
        }
        return SkillResult(
            status="error",
            iterations=0,
            final_parsed=final_parsed,
            trajectory=[{"step": "fail", "iter": 0, "detail": hint}],
            artifacts=[],
            summary=summary,
            patch_source="none",
            convergence_cause="none",
            best_iter=-1,
            error_code=code,
            budget_used_s=float(monotonic() - t0),
        )


# PatchOutcome dataclass 字段名补丁:patch_source 字面量与契约对齐(运行时不重定义)。
# 注:patch_text 字段对应 dataclass 字段名,保持一致。
