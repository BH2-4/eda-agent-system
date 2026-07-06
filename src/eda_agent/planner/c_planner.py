"""CPlanner —— L4 编排大脑(T38-T43,契约 §6.1 §6.2 v1.2)。

5 相状态机:PLANNING → EXECUTING → REFLECTING → REPORTING → DONE。
- rule 模式:初始 plan(synth + sim + 可选 sta),_rule_after_reflect 动态注入
  skill_diagnose / skill_self_heal;B 报 all_pass 后强制独立验证步。
- llm 模式:每步经 ``_llm_next_action`` 取 LLM tool_call;LLM 异常降级到 REPORTING。

边界(硬规则):
- C 不调子进程(``scripts/check_no_subprocess.py`` 用 ast.walk 扫 planner/);
- C 不直接 import anthropic(经 LLMProvider);
- C 不亲自碰 RTL / stdout(经 Tool/Skill);
- ``contract_version`` 一律 ``from eda_agent.contracts import CONTRACT_VERSION``;
- ``ToolResult.status`` 只有 ok/error/timeout 三态;``artifacts`` 元素用 artifact_ref。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from time import monotonic
from typing import Any

import jsonschema  # type: ignore[import-not-found]

from eda_agent.contracts import (
    ARTIFACT_FROM_STATE,
    CONTRACT_VERSION,
    LLMProvider,
    LLMResponse,
    Message,
    RunRecord,
    RunReport,
    RunRequest,
    ToolCall,
    ToolResult,
)
from eda_agent.errors import (
    EDA_INTERNAL,
    EDA_TOOL_ARGS_INVALID,
    EDA_TOOL_NOT_FOUND,
)
from eda_agent.planner.budget import Budget
from eda_agent.planner.prompts import SYSTEM_PROMPT, _render_task
from eda_agent.planner.rule_planner import (
    Action,
    Plan,
    PlannerPhase,
    PlannerState,
)
from eda_agent.registry import ToolRegistry
from eda_agent.runner import Runner, make_config_snapshot
from eda_agent.settings import Settings


def _first_present(d: dict[str, Any], *keys: str) -> Any:
    """返回 ``d`` 中第一个 ``key`` 存在且非 None 的值(允许 0 / -1 等 falsy 值)。

    修 ``or`` 短路陷阱:``best_iter=0`` 是合法值,``heal_parsed.get("best_iter") or ...``
    会错误跳到下一个 key。本函数显式判 None。
    """
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


class CPlanner:
    """L4 编排大脑(per-process,契约行 22)。

    ``registry`` / ``llm`` / ``runner`` / ``settings`` 在构造时注入;``run_id`` 与
    ``budget`` 在 ``execute`` 内部生成,不走构造参数(每次 RunRequest 独立)。
    """

    def __init__(
        self,
        registry: ToolRegistry,
        llm: LLMProvider,
        runner: Runner,
        settings: Settings,
    ) -> None:
        self._registry = registry
        self._llm = llm
        self._runner = runner
        self._settings = settings

    # ── execute:5 相状态机主流程(T39,组件C §6.1 伪代码) ──────────────
    def execute(self, request: RunRequest) -> RunReport:
        record = self._runner.create(
            request,
            provider_used=self._llm.provider_name,
            config_snapshot=make_config_snapshot(
                self._settings,
                model=self._settings.llm.claude_model,
            ),
        )
        state = self._init_state(request)
        budget = Budget(self._settings.run_budget_s, monotonic())
        phase: PlannerPhase = "PLANNING"
        plan: Plan = Plan(actions=[], mode="rule")
        report: RunReport | None = None
        try:
            while phase != "DONE":
                if budget.exhausted() and phase != "REPORTING":
                    record.status = "budget_exhausted"
                    phase = "REPORTING"
                elif phase == "PLANNING":
                    plan = self._make_plan(request, state)
                    self._save_plan(record.run_id, plan)
                    phase = "EXECUTING" if plan.mode == "rule" else "REFLECTING"
                elif phase == "EXECUTING":
                    # rule 模式迭代上限保护(与 llm 模式对齐)
                    if state.iteration >= self._settings.planner_max_iterations:
                        phase = "REPORTING"
                        continue
                    action = self._next_rule_action(plan, state)
                    if action is None and state.pending_self_heal_all_pass:
                        action = self._verify_after_self_heal(state, request)
                    if action is None:
                        phase = "REPORTING"
                    else:
                        self._execute_action(action, state, record)
                        self._reflect(state)
                        phase = (
                            "REPORTING"
                            if (state.fatal or state.goal_achieved)
                            else "REFLECTING"
                        )
                elif phase == "REFLECTING":
                    # T50:baseline_only run 强制走 rule 分支(_rule_after_reflect 短路),
                    # 不走 LLM(实验脚本场景,固定 2 步 plan,无 LLM 决策)。
                    if (
                        self._settings.planner_mode == "llm"
                        and not request.extra.get("baseline_only")
                    ):
                        action = self._llm_next_action(state, record, budget)
                        if action is None:
                            phase = "REPORTING"
                        else:
                            self._execute_action(action, state, record)
                            self._reflect(state)
                            phase = (
                                "REPORTING"
                                if (state.fatal or state.goal_achieved)
                                else "REFLECTING"
                            )
                    else:
                        action = self._rule_after_reflect(state, request, budget)
                        if action is None:
                            phase = "REPORTING"
                        else:
                            plan.actions = [action]
                            phase = "EXECUTING"
                elif phase == "REPORTING":
                    report = self._finalize(record, state, request, budget)
                    phase = "DONE"
            return report  # type: ignore[return-value]
        except Exception as e:  # noqa: BLE001 —— 兜底:不抛给调用方
            record.status = "failed"
            import traceback as _tb
            return self._emergency_report(
                record, f"{type(e).__name__}: {e}\n{_tb.format_exc()}"
            )

    # ── _init_state —— 从 RunRequest 构造可变上下文 ──────────────────────
    def _init_state(self, request: RunRequest) -> PlannerState:
        return PlannerState(
            rtl_path=request.rtl_path,
            tb_path=request.tb_path,
            top_module=request.top_module,
            lib_path=request.lib_path,
            clock_name=request.clock_name,
            goal=request.goal,
            max_iter=(
                request.max_iter
                if request.max_iter is not None
                else self._settings.skill_max_iterations
            ),
        )

    # ── _make_plan(T40,rule 模式初始 plan;T50 baseline_only 分支) ──────
    def _make_plan(self, request: RunRequest, state: PlannerState) -> Plan:
        # T50 baseline-only plan:基线实验只跑 synth+sim,不修复。
        # 优先级最高(覆盖 llm/rule 模式);_rule_after_reflect 在 sim 跑完后短路收尾。
        if request.extra.get("baseline_only"):
            return Plan(
                actions=[
                    Action(
                        "yosys_synth",
                        {"rtl": state.rtl_path, "top_module": state.top_module or ""},
                        "基线综合",
                    ),
                    Action(
                        "iverilog_sim",
                        {"rtl": state.rtl_path, "tb": state.tb_path},
                        "基线仿真",
                    ),
                ],
                mode="rule",
            )
        if self._settings.planner_mode == "llm":
            return Plan(actions=[], mode="llm")
        # rule 模式:初始 plan 只放第一步 synth;sim / sta / diagnose / self_heal
        # 全部由 _rule_after_reflect 在 REFLECTING 相位动态注入(plan.actions 每次被
        # 重置为单元素 list,由 _next_rule_action 取 [0])。这样 rule 模式语义统一:
        # 每跑一步 reflect 一次,reflect 决定下一步。
        actions: list[Action] = [
            Action(
                "yosys_synth",
                {"rtl": state.rtl_path, "top_module": state.top_module or ""},
                "综合拿 netlist",
            ),
        ]
        return Plan(actions=actions, mode="rule")

    # ── _next_rule_action(T40,rule 模式取 plan.actions[0]) ─────────────
    def _next_rule_action(
        self, plan: Plan, state: PlannerState
    ) -> Action | None:
        # rule 模式语义:plan.actions 在 PLANNING(初始第一步)与 REFLECTING
        # (动态注入下一步)两处生成,均为单元素 list;EXECUTING 总取 [0]。
        # 迭代计数由 _execute_action 末尾的 state.iteration += 1 与
        # _llm_next_action 的 planner_max_iterations 上限控制(不走 plan 索引)。
        if plan.actions:
            return plan.actions[0]
        return None

    # ── _rule_after_reflect(T40,动态注入 diagnose / self_heal) ────────
    def _rule_after_reflect(
        self,
        state: PlannerState,
        request: RunRequest,
        budget: Budget,
    ) -> Action | None:
        # 0a. B 报 all_pass 后强制独立验证(优先级最高,避免被分支 1 误判 goal_achieved)
        if state.pending_self_heal_all_pass:
            return self._verify_after_self_heal(state, request)
        # T50 baseline_only 短路:sim 跑完后直接 goal_achieved,不注入 diagnose/heal/sta。
        # 放在 pending_self_heal_all_pass 之后、其他 stage 注入之前;baseline 只 2 步。
        if request.extra.get("baseline_only") and state.last_sim is not None:
            state.goal_achieved = True
            return None
        # 0b. 初始 plan 的后续 stage(synth 跑完但 sim 未跑;sim 跑完但 sta 未跑)
        #     rule 模式下每跑一步就 REFLECTING,plan.actions 会被覆盖,故此处补齐
        #     初始 plan 的剩余 stage。
        if state.last_synth is not None and state.last_sim is None:
            return Action(
                "iverilog_sim",
                {"rtl": state.rtl_path, "tb": state.tb_path},
                "基线仿真",
            )
        if (
            state.last_synth is not None
            and state.last_sim is not None
            and state.last_sta is None
            and state.lib_path
            and state.clock_name
        ):
            return Action(
                "opensta_timing",
                {
                    "netlist": ARTIFACT_FROM_STATE,
                    "lib": state.lib_path,
                    "clock": state.clock_name,
                },
                "时序评估",
            )

        sim = state.last_sim
        sta = state.last_sta
        sim_passed = sim is not None and sim.get("passed") is True
        sta_ok = sta is None or (
            sta.get("wns") is not None and float(sta["wns"]) >= 0
        )
        # 1. 仿真 passed 且 sta 通过(或未跑)→ goal_achieved
        if sim_passed and sta_ok and not state.pending_self_heal_all_pass:
            state.goal_achieved = True
            return None
        # 2. 仿真未过且未诊断 → skill_diagnose
        if (
            state.last_diagnose is None
            and sim is not None
            and not sim_passed
        ):
            tool_results = self._collect_upstream_results(state)
            return Action(
                "skill_diagnose",
                {"tool_results": tool_results},
                "诊断根因",
            )
        # 3. 已诊断且未 goal 且未自修复 → skill_self_heal(预算下传)
        #    仅 kind=self_heal 推进自修复;kind=diagnose 诊断产出后即收尾(见分支 4),
        #    避免 diagnose 子命令被错误推进到 self_heal(双 verify 指出的 minor)。
        if (
            request.kind == "self_heal"
            and state.last_diagnose is not None
            and not state.goal_achieved
            and state.best_rtl_ref is None
        ):
            return Action(
                "skill_self_heal",
                {
                    "rtl": state.rtl_path,
                    "tb": state.tb_path,
                    "diagnose": state.last_diagnose,
                    "max_iter": state.max_iter,
                    "goal": state.goal,
                    "lib": state.lib_path,
                    "clock": state.clock_name,
                    "top_module": state.top_module,
                    "_remaining_budget_s": budget.remaining_s(),
                },
                "自修复",
            )
        # 4. kind=diagnose:诊断报告产出后即目标达成(不自修复;status=ok)。
        #    diagnose 子命令的语义是"产出诊断报告",而非"通过测试"。
        if request.kind == "diagnose" and state.last_diagnose is not None:
            state.goal_achieved = True
            return None
        return None

    # ── _execute_action(T41,核心;含 iteration 单一计数出口) ───────────
    def _execute_action(
        self,
        action: Action,
        state: PlannerState,
        record: RunRecord,
    ) -> ToolResult:
        tool = self._registry.get(action.tool_name)
        resolved_args = self._resolve_args(action.args, state, record)
        # 注入父 run_id:skill 需要它 append_step 到父 RunRecord;L1 Tool 用它工件落盘。
        # setdefault 不覆盖上游(LLM/rule)已传的 run_id。
        resolved_args.setdefault("run_id", record.run_id)
        call = ToolCall(
            name=action.tool_name,
            args=resolved_args,
            caller="planner",
            llm_tool_call_id=action.llm_tool_call_id,
        )
        if tool is None:
            result = self._tool_not_found_result(call)
        elif not self._args_match_schema(resolved_args, tool.schema):
            result = self._args_invalid_result(call, tool)
        else:
            result = tool(call)
        self._runner.append_step(
            record.run_id, call, result, skill_name=None, iter=None
        )
        state.history.append({"call": call, "result": result})
        state.iteration += 1  # 单一计数出口(_reflect 不碰)
        return result

    # ── _resolve_args(T41,ARTIFACT_FROM_STATE + _artifact_ref 替换) ────
    def _resolve_args(
        self,
        args: dict[str, Any],
        state: PlannerState,
        record: RunRecord,
    ) -> dict[str, Any]:
        resolved = dict(args)
        # netlist 占位:用 yosys_synth 的 netlist_ref 解析成实际文件路径
        if (
            resolved.get("netlist") == ARTIFACT_FROM_STATE
            and state.netlist_ref is not None
        ):
            resolved["netlist"] = self._resolve_artifact_path(
                state.netlist_ref, record
            )
        # 独立验证步:_artifact_ref 替换 rtl(B 报 all_pass 后强制验证)
        if (
            resolved.get("rtl") == ARTIFACT_FROM_STATE
            and "_artifact_ref" in resolved
        ):
            art = resolved.pop("_artifact_ref")
            if isinstance(art, dict):
                resolved["rtl"] = self._resolve_artifact_path(art, record)
            else:
                resolved["rtl"] = art
        return resolved

    @staticmethod
    def _resolve_artifact_path(
        art: dict[str, str], record: RunRecord
    ) -> str:
        """把 artifact_ref 形状的 dict 解析成实际文件路径。

        实际路径 = ``runs/<run_id>/<rel_path>``(契约 artifact_ref 工厂注释)。
        """
        rel = art.get("rel_path", "")
        return str(Path("runs") / record.run_id / rel)

    # ── _args_match_schema(T41,下划线前缀豁免) ─────────────────────────
    def _args_match_schema(
        self, args: dict[str, Any], schema: dict[str, Any]
    ) -> bool:
        visible = {k: v for k, v in args.items() if not k.startswith("_")}
        input_schema = (
            schema.get("input_schema") if isinstance(schema, dict) else None
        )
        if not input_schema:
            return True
        try:
            jsonschema.validate(visible, input_schema)
            return True
        except jsonschema.ValidationError:
            return False

    # ── _tool_not_found_result / _args_invalid_result(T41,回灌不中断) ─
    def _tool_not_found_result(self, call: ToolCall) -> ToolResult:
        return ToolResult(
            status="error",
            exit_code=None,
            stdout="",
            stderr="",
            parsed={
                "_schema": {
                    "name": call.name,
                    "version": "0.1.0",
                    "contract_version": CONTRACT_VERSION,
                },
                "error": "tool not found",
            },
            artifacts=[],
            duration_s=0.0,
            tool=call.name,
            error_code=EDA_TOOL_NOT_FOUND,
            error_hint=f"tool {call.name} not in registry",
        )

    def _args_invalid_result(
        self, call: ToolCall, tool: Any
    ) -> ToolResult:
        return ToolResult(
            status="error",
            exit_code=None,
            stdout="",
            stderr="",
            parsed={
                "_schema": {
                    "name": call.name,
                    "version": "0.1.0",
                    "contract_version": CONTRACT_VERSION,
                },
                "error": "args invalid",
            },
            artifacts=[],
            duration_s=0.0,
            tool=call.name,
            error_code=EDA_TOOL_ARGS_INVALID,
            error_hint=f"args for {call.name} fail schema",
        )

    # ── _reflect(T42,只更新业务字段,不碰 iteration;含独立验证判定) ──
    def _reflect(self, state: PlannerState) -> None:
        if not state.history:
            return
        result: ToolResult = state.history[-1]["result"]
        p = result.parsed or {}
        name = result.tool
        if name == "yosys_synth":
            state.last_synth = p
            # netlist_ref 从 artifacts 找(契约 rel_path 后缀匹配)
            for art in result.artifacts:
                rel = art.get("rel_path", "") if isinstance(art, dict) else ""
                if rel.endswith("synth/netlist.v"):
                    state.netlist_ref = art  # type: ignore[assignment]
                    break
        elif name == "iverilog_sim":
            state.last_sim = p
            # 独立验证步判定:best_rtl_ref 已设 且 independent_verify_passed is None
            if (
                state.best_rtl_ref is not None
                and state.independent_verify_passed is None
            ):
                state.independent_verify_passed = p.get("passed") is True
                if state.independent_verify_passed:
                    state.goal_achieved = True
        elif name == "opensta_timing":
            state.last_sta = p
        elif name == "skill_diagnose":
            state.last_diagnose = p
        elif name == "skill_self_heal":
            fixed = p.get("fixed_rtl_ref")
            if fixed and isinstance(fixed, dict):
                state.best_rtl_ref = fixed
            conv = p.get("convergence_cause") or p.get("_skill_convergence_cause")
            if conv == "all_pass":
                # 等独立验证步;不立即 goal_achieved
                state.pending_self_heal_all_pass = True
        # fatal 判定:eda.internal 是 fatal
        if result.error_code == EDA_INTERNAL:
            state.fatal = True

    # ── _verify_after_self_heal(T42,B all_pass 后强制独立验证) ─────────
    def _verify_after_self_heal(
        self, state: PlannerState, request: RunRequest
    ) -> Action | None:
        if state.independent_verify_passed is not None:
            return None  # 已验证
        if state.best_rtl_ref is None:
            return None
        return Action(
            "iverilog_sim",
            {
                "rtl": ARTIFACT_FROM_STATE,
                "_artifact_ref": state.best_rtl_ref,
                "tb": state.tb_path,
            },
            "B 报 all_pass 后的第三方独立验证",
        )

    # ── _collect_upstream_results(喂 skill_diagnose) ───────────────────
    def _collect_upstream_results(self, state: PlannerState) -> list[dict]:
        out: list[dict] = []
        for i, h in enumerate(state.history):
            r: ToolResult = h["result"]
            c: ToolCall = h["call"]
            run_id = ""
            # 从 args 里反查 run_id(skill 调用时下传);不存在则空串
            rid = c.args.get("run_id") if isinstance(c.args, dict) else None
            if isinstance(rid, str):
                run_id = rid
            out.append(
                {
                    "tool": r.tool,
                    "status": r.status,
                    "parsed": r.parsed,
                    "stdout": r.stdout,
                    "step_idx": i,
                    "run_id": run_id,
                }
            )
        return out

    # ── LLM ReAct 回环(T43) ──────────────────────────────────────────
    def _llm_next_action(
        self,
        state: PlannerState,
        record: RunRecord,
        budget: Budget,
    ) -> Action | None:
        if state.iteration >= self._settings.planner_max_iterations:
            return None
        if budget.exhausted():
            return None
        messages = self._build_messages(state, record)
        tools = self._registry.to_llm_tools()
        resp = self._call_llm_safe(messages, tools, record)
        if not resp.tool_calls:
            return None
        tc = resp.tool_calls[0]
        return Action(
            tool_name=tc["name"],
            args=dict(tc.get("args", {})),
            rationale=resp.text[:200],
            llm_tool_call_id=tc.get("id"),
        )

    def _call_llm_safe(
        self,
        messages: list[Message],
        tools: list[dict],
        record: RunRecord,
    ) -> LLMResponse:
        try:
            return self._llm.chat(
                messages, tools=tools, temperature=0.0, max_tokens=4096
            )
        except Exception:  # noqa: BLE001 —— 降级:返回空 tool_calls,主循环转 REPORTING
            return LLMResponse(
                text="",
                tool_calls=[],
                tokens_in=0,
                tokens_out=0,
                provider=self._llm.provider_name,
                model="",
            )

    def _build_messages(
        self, state: PlannerState, record: RunRecord
    ) -> list[Message]:
        msgs: list[Message] = [
            Message("system", SYSTEM_PROMPT),
            Message("user", _render_task(state, record)),
        ]
        for h in state.history:
            r: ToolResult = h["result"]
            c: ToolCall = h["call"]
            msgs.append(
                Message(
                    role="tool",
                    content=json.dumps(
                        {
                            "status": r.status,
                            "parsed": r.parsed,
                            "error_hint": r.error_hint,
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                    tool_call_id=c.llm_tool_call_id,
                )
            )
        return msgs

    # ── _finalize(T39,REPORTING;落 report.md + RunReport;T50/T51 baseline+manifest) ─
    def _finalize(
        self,
        record: RunRecord,
        state: PlannerState,
        request: RunRequest,
        budget: Budget,
    ) -> RunReport:
        # 从 CountingProvider 读 llm 计数;非 CountingProvider 时回退 0
        stats_getter = getattr(self._llm, "get_stats", None)
        stats = stats_getter() if callable(stats_getter) else None
        llm_calls = stats.llm_calls if stats else 0
        tokens_in = stats.tokens_in if stats else 0
        tokens_out = stats.tokens_out if stats else 0

        is_baseline = bool(request.extra.get("baseline_only"))
        # status 判定(T50:baseline_only 跑完即 ok,不管 sim pass 与否)
        if is_baseline:
            status = "ok"
        elif state.goal_achieved:
            status = "ok"
        elif record.status == "budget_exhausted" or budget.exhausted():
            status = "budget_exhausted"
        else:
            status = "failed"

        metrics = self._build_metrics(
            state, request, budget, llm_calls, tokens_in, tokens_out
        )
        report_path = self._write_report_md(record.run_id, state, status, metrics)
        self._runner.finalize(
            record.run_id,
            status,
            final_report_path=report_path,
            llm_calls=llm_calls,
            llm_tokens_in=tokens_in,
            llm_tokens_out=tokens_out,
            total_duration_s=budget.elapsed_s(),
        )
        # T51:self_heal run(非 baseline_only)落 experiment_manifest.json(14 字段)
        if request.kind == "self_heal" and not is_baseline:
            self._write_manifest(
                record.run_id, request, metrics, llm_calls, tokens_in, tokens_out
            )
        return RunReport(
            run_id=record.run_id,
            status=status,  # type: ignore[arg-type]
            report_path=report_path,
            summary=self._summary(state, status),
            metrics=metrics,
        )

    # ── _write_manifest(T51,落 runs/<run_id>/experiment_manifest.json,14 字段) ──
    def _write_manifest(
        self,
        run_id: str,
        request: RunRequest,
        metrics: dict[str, Any],
        llm_calls: int,
        tokens_in: int,
        tokens_out: int,
    ) -> None:
        """契约 §2.4 self_heal 终态实验 manifest(14 字段)。原子写 tmp+os.replace。"""
        # candidates_at_best_score:从 runs/<run_id>/self_heal/best/meta.json 读(B 落盘)
        candidates_at_best: int | None = None
        best_meta_path = Path("runs") / run_id / "self_heal" / "best" / "meta.json"
        if best_meta_path.exists():
            try:
                best_meta = json.loads(
                    best_meta_path.read_text(encoding="utf-8")
                )
                v = best_meta.get("candidates_at_best_score")
                if isinstance(v, int):
                    candidates_at_best = v
            except (json.JSONDecodeError, OSError):
                candidates_at_best = None

        manifest = {
            "run_id": run_id,
            "baseline_run_id": request.extra.get("baseline_run_id"),
            "design_id": request.extra.get("design_id", "unknown"),
            "fault_type": request.extra.get("fault_type", "unknown"),
            "baseline_pass_rate": request.extra.get("baseline_pass_rate"),
            "self_heal_pass_rate": metrics.get("self_heal_pass_rate"),
            "self_heal_convergence": metrics.get("self_heal_convergence"),
            "self_heal_best_iter": metrics.get("self_heal_best_iter"),
            "planner_iterations": metrics.get("planner_iterations"),
            "llm_calls": llm_calls,
            "tokens_total": tokens_in + tokens_out,
            "wall_time_s": metrics.get("wall_time_s"),
            "candidates_at_best_score": candidates_at_best,
            "contract_version": CONTRACT_VERSION,
        }
        out_path = Path("runs") / run_id / "experiment_manifest.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp_path, out_path)

    def _build_metrics(
        self,
        state: PlannerState,
        request: RunRequest,
        budget: Budget,
        llm_calls: int,
        tokens_in: int,
        tokens_out: int,
    ) -> dict[str, Any]:
        """15 字段 metrics(契约 §2.4)+ T50 baseline_pass_rate 派生。"""
        sim = state.last_sim or {}
        sta = state.last_sta or {}
        heal_parsed_list = [
            h["result"].parsed
            for h in state.history
            if h["result"].tool == "skill_self_heal"
        ]
        heal_parsed: dict[str, Any] = heal_parsed_list[-1] if heal_parsed_list else {}
        conv = heal_parsed.get("convergence_cause") or heal_parsed.get(
            "_skill_convergence_cause"
        )
        # T50 baseline_pass_rate 派生:
        # - baseline_only run:从 sim.passed 派生(True→1.0,否则 0.0)。
        # - self_heal run:从 request.extra["baseline_pass_rate"] 透传(未跑 baseline→None)。
        if request.extra.get("baseline_only"):
            baseline_pass_rate = 1.0 if sim.get("passed") is True else 0.0
        else:
            bpr = request.extra.get("baseline_pass_rate")
            baseline_pass_rate = bpr if isinstance(bpr, (int, float)) else None
        return {
            "planner_iterations": state.iteration,
            "tool_calls_total": len(state.history),
            "llm_calls": llm_calls,
            "llm_tokens_in": tokens_in,
            "llm_tokens_out": tokens_out,
            "sim_passed": sim.get("passed"),
            "num_passed": sim.get("num_passed"),
            "num_failed": sim.get("num_failed"),
            "wns_ns": sta.get("wns"),
            "tns_ns": sta.get("tns"),
            "self_heal_convergence": conv,
            "self_heal_best_iter": _first_present(
                heal_parsed, "best_iter", "_skill_best_iter"
            ),
            # 契约 §2.4:self_heal_pass_rate 是 float(非 Optional)。
            # conv=="all_pass" → 1.0;其余(含 None/regression/budget/max_iter)→ 0.0。
            "self_heal_pass_rate": 1.0 if conv == "all_pass" else 0.0,
            "baseline_pass_rate": baseline_pass_rate,
            "wall_time_s": budget.elapsed_s(),
        }

    def _summary(self, state: PlannerState, status: str) -> str:
        sim = state.last_sim or {}
        if status == "ok":
            return (
                f"goal achieved: sim passed={sim.get('passed')} "
                f"after {state.iteration} steps"
            )
        if status == "budget_exhausted":
            return f"budget exhausted after {state.iteration} steps"
        return f"failed after {state.iteration} steps (sim passed={sim.get('passed')})"

    # ── _write_report_md(落 runs/<run_id>/report.md) ──────────────────
    def _write_report_md(
        self,
        run_id: str,
        state: PlannerState,
        status: str,
        metrics: dict[str, Any],
    ) -> str:
        lines: list[str] = []
        lines.append(f"# Run Report — {run_id}")
        lines.append("")
        lines.append(f"- status: `{status}`")
        lines.append(f"- goal: `{state.goal}`")
        lines.append(f"- goal_achieved: `{state.goal_achieved}`")
        lines.append(f"- planner_iterations: `{metrics['planner_iterations']}`")
        lines.append(f"- tool_calls_total: `{metrics['tool_calls_total']}`")
        lines.append(f"- llm_calls: `{metrics['llm_calls']}`")
        lines.append(f"- sim_passed: `{metrics['sim_passed']}`")
        lines.append(
            f"- self_heal_convergence: `{metrics['self_heal_convergence']}`"
        )
        lines.append(f"- wall_time_s: `{metrics['wall_time_s']:.2f}`")
        lines.append("")
        lines.append("## Steps")
        lines.append("")
        lines.append("| idx | tool | status | exit_code | error_code |")
        lines.append("| --- | --- | --- | --- | --- |")
        for i, h in enumerate(state.history, start=1):
            r: ToolResult = h["result"]
            ec = "" if r.exit_code is None else str(r.exit_code)
            lines.append(
                f"| {i} | `{r.tool}` | `{r.status}` | {ec} | "
                f"`{r.error_code or ''}` |"
            )
        lines.append("")
        out = "\n".join(lines)
        p = Path("runs") / run_id / "report.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(out, encoding="utf-8")
        return str(p)

    # ── _emergency_report(异常兜底,不抛给调用方) ──────────────────────
    def _emergency_report(self, record: RunRecord, err_msg: str) -> RunReport:
        self._runner.finalize(
            record.run_id,
            "failed",
            final_report_path=None,
            llm_calls=0,
            llm_tokens_in=0,
            llm_tokens_out=0,
        )
        return RunReport(
            run_id=record.run_id,
            status="failed",
            report_path="",
            summary=f"crash: {err_msg}",
            metrics={},
        )

    # ── _save_plan(落 plan.json,剥离 reserved 字段) ──────────────────
    def _save_plan(self, run_id: str, plan: Plan) -> None:
        p = Path("runs") / run_id / "plan.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "mode": plan.mode,
            "actions": [
                {
                    "tool_name": a.tool_name,
                    "args": {
                        k: v for k, v in a.args.items() if not k.startswith("_")
                    },
                    "rationale": a.rationale,
                }
                for a in plan.actions
            ],
        }
        p.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
