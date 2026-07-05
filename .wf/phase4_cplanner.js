export const meta = {
  name: 'eda-phase4-cplanner',
  description: 'Phase 4: 实现 CPlanner(C 组件 T38-T43+T47: 状态机+Rule/LLM planner+独立验证+CLI)',
  phases: [
    { title: '实现 CPlanner' },
    { title: '对抗验证' },
    { title: '单测' },
  ],
}

// ─────────────────────────────────────────────────────────────────────
// 共享上下文
// ─────────────────────────────────────────────────────────────────────
const CONTEXT = `
项目:EDA Agent System(Agentic4Systems 比赛,提交 2026-07-15)。
代码根:D:\\eda agent system\\(Windows,4 空格缩进,from __future__ import annotations,中文注释,每行≤100 字符)。
工作目录:bash 命令先 cd '/d/eda agent system'。

== 当前进度(Phase 0/1/2/3 已完成,148 单测全绿)==
- 契约基座 contracts.py/errors.py/registry.py/runner.py/settings.py 已就位
- LLM provider 链:eda_agent.llm.factory.make_provider(settings) -> LLMProvider(CountingProvider 包装,计数 llm_calls/tokens)
- 3 个 L1 EDA Tool 已注册:yosys_synth / iverilog_sim / opensta_timing(sta 未装时 sta.sta_unavailable 降级)
- A 诊断器 eda_agent.skills.diagnose.DiagnoseSkill(已注册为 skill_diagnose)
- B 自修复 eda_agent.skills.self_heal.SelfHealSkill(已注册为 skill_self_heal)
- eda_agent.tools.bootstrap.build_registry(provider, runner, settings) -> ToolRegistry(已含 5 个 tool:3 L1 + 2 skill)

== 编码硬规则(违反=blocker) ==
- contract_version 必须 from eda_agent.contracts import CONTRACT_VERSION(禁止裸 "0.1.0")
- artifacts 元素必须用 artifact_ref(run_id, rel_path) 工厂(禁止裸 str/dict)
- 禁止重定义 contracts/errors 已有类型,一律 import
- C 不直接调子进程(验收 scripts/check_no_subprocess.py 用 ast.walk 检查 planner/ 目录);不直接 import anthropic(经 LLMProvider);不亲自碰 RTL/stdout(经 Tool/Skill)
- ToolResult.status 只有 ok/error/timeout 三态

== 基础设施 API(先 Read 核对)==
eda_agent.contracts:
  CONTRACT_VERSION, artifact_ref(run_id, rel_path), ARTIFACT_FROM_STATE="<from_state>"
  RunRequest(kind, goal, rtl_path, tb_path=None, top_module=None, lib_path=None, clock_name=None, max_iter=None, extra={})  [frozen]
  RunReport(run_id, status, report_path, summary, metrics)
  ToolCall(name, args, caller="planner", llm_tool_call_id=None), ToolResult(status, exit_code, stdout, stderr, parsed, artifacts, duration_s, tool, error_code=None, error_hint=None)
  Message(role, content, tool_call_id=None), LLMResponse(text, tool_calls, tokens_in, tokens_out, provider, model, raw=None)
  LLMProvider.chat(messages, tools=None, temperature=0.0, max_tokens=4096) -> LLMResponse
  StepRecord(index, tool_name, tool_call_path, tool_result_path, started_at, duration_s, status, skill_name=None, iter=None)
eda_agent.errors:
  ErrorItem, namespace_of(code), severity_of(code), SEVERITY_BY_CODE
  EDA_TOOL_NOT_FOUND="eda.tool_not_found", EDA_TOOL_ARGS_INVALID="eda.tool_args_invalid", EDA_BUDGET_EXHAUSTED="eda.budget_exhausted", EDA_INTERNAL="eda.internal"
eda_agent.registry:
  ToolRegistry.register(entry), .get(name) -> Tool | None(不存在返回 None,不抛), .list(category=None), .to_llm_tools() -> list[dict](剥离下划线前缀 reserved 字段)
  ToolEntry(tool, name, category, schema, parsed_schema_ref)
eda_agent.runner.Runner:
  create(request, *, provider_used, config_snapshot) -> RunRecord(生成 run_id, 落 run.json+request.json)
  finalize(run_id, status, *, final_report_path, llm_calls, llm_tokens_in, llm_tokens_out, total_duration_s)
  append_step(run_id, call, result, *, skill_name=None, iter=None) -> StepRecord
  get_record(run_id), load_record(run_id), scavenge_zombies(run_budget_s) -> int
  make_config_snapshot(settings, *, settings_path, eda_versions, model) -> dict
eda_agent.settings.Settings:
  settings.planner_mode(="llm"), settings.planner_max_iterations(=8), settings.run_budget_s(=600)
  settings.skill_max_iterations(=5), settings.skill_self_heal_budget_s(=480), settings.skill_diagnose_budget_s(=60)
  settings.eda.wsl_enabled/tool_timeout_s, settings.llm.claude_model
eda_agent.llm.factory.make_provider(settings) -> LLMProvider(CountingProvider 包装;有 .get_stats() -> LLMStats(llm_calls, tokens_in, tokens_out) 若是 CountingProvider)
eda_agent.tools.bootstrap.build_registry(provider, runner, settings) -> ToolRegistry
`

// ─────────────────────────────────────────────────────────────────────
// CPlanner 契约 + 设计
// ─────────────────────────────────────────────────────────────────────
const C_CONTRACT = `
== CPlanner 总体(C 组件,planner/c_planner.py 主)==
L4 大脑:把 RunRequest 分解为对 Tool/Skill 的有序调用,执行,读 ToolResult.parsed 据结构化字段决策,直到 goal 达成/预算耗尽。
边界:C 不碰 EDA/stdout/子进程/RTL patch,全经 Tool/Skill/A/B;不重定义契约类型;self._llm 命名统一持有 provider。

== 构造签名(契约行 22 锁定,per-process)==
class CPlanner:
    def __init__(self, registry: ToolRegistry, llm: LLMProvider, runner: Runner, settings: Settings): ...
    def execute(self, request: RunRequest) -> RunReport: ...
run_id 与 budget 在 execute 内部生成,不走构造参数。

== 文件分布(契约行 918-921)==
- planner/__init__.py(空)
- planner/budget.py — Budget 预算仲裁器
- planner/c_planner.py — CPlanner 主类(状态机 + execute + _execute_action + _resolve_args + _args_match_schema + _reflect + _finalize)
- planner/rule_planner.py — RulePlanner(make_plan + _rule_after_reflect) + Plan/Action dataclass
- planner/llm_planner.py — _llm_next_action + _call_llm_safe + _build_messages(可合并进 c_planner.py 或独立)
- planner/prompts.py — SYSTEM_PROMPT + _render_task
- planner/report.py — render(record, state, metrics) -> RunReport(渲染 report.md)
- cli.py — main + run_pipeline(request, settings) -> RunReport

== Budget(T38,planner/budget.py)==
class Budget:
    def __init__(self, run_budget_s: int, started_at: float): self._run_budget_s=run_budget_s; self._started_at=started_at
    def elapsed_s(self) -> float: return monotonic() - self._started_at
    def remaining_s(self) -> float: return max(0.0, self._run_budget_s - self.elapsed_s())
    def exhausted(self) -> bool: return self.remaining_s() <= 0

== Plan / Action / PlannerState / PlannerPhase(T39-T40,planner/rule_planner.py 或 c_planner.py)==
@dataclass Action:
    tool_name: str; args: dict; rationale: str = ""; llm_tool_call_id: str | None = None
@dataclass Plan:
    actions: list[Action] = field(default_factory=list); mode: Literal["rule","llm"] = "rule"
PlannerPhase = Literal["PLANNING","EXECUTING","REFLECTING","REPORTING","DONE"]
@dataclass PlannerState:  # 可变上下文
    iteration: int = 0
    history: list[dict] = field(default_factory=list)  # [{"call":ToolCall,"result":ToolResult}]
    netlist_ref: dict | None = None       # yosys_synth.artifacts[0]
    last_synth: dict | None = None; last_sim: dict | None = None; last_sta: dict | None = None
    last_diagnose: dict | None = None
    best_rtl_ref: dict | None = None       # skill_self_heal 的 fixed_rtl_ref
    pending_self_heal_all_pass: bool = False
    independent_verify_passed: bool | None = None
    goal_achieved: bool = False; fatal: bool = False
    rtl_path: str; tb_path: str | None; top_module: str | None; lib_path: str | None; clock_name: str | None; goal: str; max_iter: int

== execute 主流程(T39,5 相状态机,组件C §6.1 伪代码)==
def execute(self, request):
    record = self._runner.create(request, provider_used=self._llm.provider_name, config_snapshot=make_config_snapshot(self._settings, model=self._settings.llm.claude_model))
    state = self._init_state(request)
    budget = Budget(self._settings.run_budget_s, monotonic())
    phase = "PLANNING"
    report = None
    try:
        while phase != "DONE":
            if budget.exhausted():
                record.status = "budget_exhausted"; phase = "REPORTING"
            elif phase == "PLANNING":
                plan = self._make_plan(request, state)  # rule 模式产 actions;llm 模式产空 Plan(mode="llm")
                self._save_plan(record.run_id, plan)
                phase = "EXECUTING" if plan.mode == "rule" else "REFLECTING"
            elif phase == "EXECUTING":
                action = self._next_rule_action(plan, state)
                if action is None and state.pending_self_heal_all_pass:
                    action = self._verify_after_self_heal(state, request)
                if action is None:
                    phase = "REPORTING"
                else:
                    result = self._execute_action(action, state, record)  # 含 state.iteration += 1
                    self._reflect(result, state)
                    phase = "REPORTING" if (state.fatal or state.goal_achieved) else "REFLECTING"
            elif phase == "REFLECTING":
                if self._settings.planner_mode == "llm":
                    action = self._llm_next_action(state, record, budget)
                    if action is None:
                        phase = "REPORTING"
                    else:
                        result = self._execute_action(action, state, record)
                        self._reflect(result, state)
                        phase = "REPORTING" if (state.fatal or state.goal_achieved) else "REFLECTING"
                else:
                    action = self._rule_after_reflect(state, request, budget)
                    if action is None:
                        phase = "REPORTING"
                    else:
                        plan.actions = [action]; phase = "EXECUTING"
            elif phase == "REPORTING":
                report = self._finalize(record, state, request, budget)
                phase = "DONE"
        return report
    except Exception as e:
        record.status = "failed"
        # 紧急 report:不抛异常给调用方
        return self._emergency_report(record, str(e))

== _make_plan(T40,rule 模式 self_heal 初始 plan)==
def _make_plan(self, request, state) -> Plan:
    if self._settings.planner_mode == "llm":
        return Plan(actions=[], mode="llm")
    # rule 模式:self_heal / run 初始 plan = synth + sim (+ sta)
    actions = [
        Action("yosys_synth", {"rtl": state.rtl_path, "top_module": state.top_module or ""}, "综合拿 netlist"),
        Action("iverilog_sim", {"rtl": state.rtl_path, "tb": state.tb_path}, "基线仿真"),
    ]
    if state.lib_path and state.clock_name:
        actions.append(Action("opensta_timing", {"netlist": ARTIFACT_FROM_STATE, "lib": state.lib_path, "clock": state.clock_name}, "时序评估"))
    return Plan(actions=actions, mode="rule")
注意:diagnose / self_heal 不写死进初始 plan,由 _rule_after_reflect 动态注入。
kind=="diagnose" 时 plan 同上 synth+sim(+sta),末尾由 _rule_after_reflect 注入 skill_diagnose。

== _next_rule_action(T40)==
def _next_rule_action(self, plan, state):
    # 取 plan.actions 中第 state.iteration 个(rule 模式顺序执行)
    if state.iteration < len(plan.actions):
        return plan.actions[state.iteration]
    return None

== _rule_after_reflect(T40,动态注入 diagnose / self_heal)==
def _rule_after_reflect(self, state, request, budget) -> Action | None:
    # 1. 仿真 passed 且 sta 通过(或未跑)→ goal_achieved
    sim = state.last_sim; sta = state.last_sta
    sim_passed = sim is not None and sim.get("passed") is True
    sta_ok = sta is None or (sta.get("wns") is not None and sta["wns"] >= 0)
    if sim_passed and sta_ok and not state.pending_self_heal_all_pass:
        state.goal_achieved = True; return None
    # 2. 仿真未过且未诊断 → skill_diagnose
    if state.last_diagnose is None and sim is not None and not sim_passed:
        tool_results = self._collect_upstream_results(state)
        return Action("skill_diagnose", {"tool_results": tool_results}, "诊断根因")
    # 3. 已诊断且未 goal → skill_self_heal(预算下传)
    if state.last_diagnose is not None and not state.goal_achieved and state.best_rtl_ref is None:
        return Action("skill_self_heal", {
            "rtl": state.rtl_path, "tb": state.tb_path,
            "diagnose": state.last_diagnose, "max_iter": state.max_iter,
            "goal": state.goal, "lib": state.lib_path, "clock": state.clock_name,
            "top_module": state.top_module, "_remaining_budget_s": budget.remaining_s(),
        }, "自修复")
    return None

== _execute_action(T41,核心,含 iteration 单一计数出口)==
def _execute_action(self, action, state, record) -> ToolResult:
    tool = self._registry.get(action.tool_name)
    resolved_args = self._resolve_args(action.args, state)
    call = ToolCall(name=action.tool_name, args=resolved_args, caller="planner", llm_tool_call_id=action.llm_tool_call_id)
    if tool is None:
        result = self._tool_not_found_result(call)            # error_code=eda.tool_not_found,回灌不中断
    elif not self._args_match_schema(resolved_args, tool.schema):
        result = self._args_invalid_result(call, tool)        # error_code=eda.tool_args_invalid,回灌不中断
    else:
        result = tool(call)                                    # 真执行
    self._runner.append_step(record.run_id, call, result, skill_name=None, iter=None)
    state.history.append({"call": call, "result": result})
    state.iteration += 1                                       # 单一计数出口(_reflect 不碰)
    return result

== _resolve_args(T41,ARTIFACT_FROM_STATE + _artifact_ref 替换)==
def _resolve_args(self, args, state) -> dict:
    resolved = dict(args)
    # netlist 占位:用 yosys_synth 的 netlist_ref
    if resolved.get("netlist") == ARTIFACT_FROM_STATE and state.netlist_ref is not None:
        resolved["netlist"] = state.netlist_ref
    # 独立验证步:_artifact_ref 替换 rtl
    if resolved.get("rtl") == ARTIFACT_FROM_STATE and "_artifact_ref" in resolved:
        resolved["rtl"] = resolved.pop("_artifact_ref")
    return resolved

== _args_match_schema(T41,下划线前缀豁免)==
def _args_match_schema(self, args, schema) -> bool:
    import jsonschema
    visible = {k: v for k, v in args.items() if not k.startswith("_")}
    input_schema = schema.get("input_schema") if isinstance(schema, dict) else None
    if not input_schema:
        return True
    try:
        jsonschema.validate(visible, input_schema); return True
    except jsonschema.ValidationError:
        return False

== _tool_not_found_result / _args_invalid_result(T41,回灌不中断)==
def _tool_not_found_result(self, call) -> ToolResult:
    return ToolResult(status="error", exit_code=None, stdout="", stderr="",
        parsed={"_schema": {"name": call.name, "version": "0.1.0", "contract_version": CONTRACT_VERSION}, "error": "tool not found"},
        artifacts=[], duration_s=0.0, tool=call.name, error_code=EDA_TOOL_NOT_FOUND, error_hint=f"tool {call.name} not in registry")
def _args_invalid_result(self, call, tool) -> ToolResult:
    return ToolResult(status="error", exit_code=None, stdout="", stderr="",
        parsed={"_schema": {"name": call.name, "version": "0.1.0", "contract_version": CONTRACT_VERSION}, "error": "args invalid"},
        artifacts=[], duration_s=0.0, tool=call.name, error_code=EDA_TOOL_ARGS_INVALID, error_hint=f"args for {call.name} fail schema")

== _reflect(T42,只更新业务字段,不碰 iteration;含独立验证判定)==
def _reflect(self, result, state):
    p = result.parsed or {}; name = result.tool
    if name == "yosys_synth":
        state.last_synth = p
        if p.get("success") is not True and result.status != "ok":
            pass  # synth 失败不一定是 fatal(LLM 可能修复);仅记录
        # netlist_ref 从 artifacts 找
        for art in result.artifacts:
            if art.get("rel_path", "").endswith("synth/netlist.v"):
                state.netlist_ref = art; break
    elif name == "iverilog_sim":
        state.last_sim = p
        # 独立验证步判定:best_rtl_ref 已设 且 independent_verify_passed is None
        if state.best_rtl_ref is not None and state.independent_verify_passed is None:
            state.independent_verify_passed = (p.get("passed") is True)
            if state.independent_verify_passed:
                state.goal_achieved = True
    elif name == "opensta_timing":
        state.last_sta = p
    elif name == "skill_diagnose":
        state.last_diagnose = p
    elif name == "skill_self_heal":
        # best_rtl_ref 来自 fixed_rtl_ref;pending all_pass 不立即 goal_achieved
        fixed = p.get("fixed_rtl_ref")
        if fixed: state.best_rtl_ref = fixed
        conv = p.get("convergence_cause") or p.get("_skill_convergence_cause")
        if conv == "all_pass":
            state.pending_self_heal_all_pass = True   # 等独立验证步
    # fatal 判定:severity=fatal 的 ErrorItem 或 eda.internal
    if result.error_code == EDA_INTERNAL:
        state.fatal = True

== _verify_after_self_heal(T42,B all_pass 后强制独立验证)==
def _verify_after_self_heal(self, state, request) -> Action | None:
    if state.independent_verify_passed is not None: return None   # 已验证
    if state.best_rtl_ref is None: return None
    return Action("iverilog_sim",
        {"rtl": ARTIFACT_FROM_STATE, "_artifact_ref": state.best_rtl_ref, "tb": state.tb_path},
        "B 报 all_pass 后的第三方独立验证")

== _collect_upstream_results(喂 skill_diagnose)==
def _collect_upstream_results(self, state) -> list[dict]:
    out = []
    for i, h in enumerate(state.history):
        r = h["result"]
        out.append({"tool": r.tool, "status": r.status, "parsed": r.parsed, "stdout": r.stdout, "step_idx": i, "run_id": ""})
    return out

== LLM ReAct 回环(T43,planner/llm_planner.py 或 c_planner.py 内)==
def _llm_next_action(self, state, record, budget) -> Action | None:
    if state.iteration >= self._settings.planner_max_iterations: return None
    if budget.exhausted(): return None
    messages = self._build_messages(state, record)
    tools = self._registry.to_llm_tools()
    resp = self._call_llm_safe(messages, tools, record)
    if not resp.tool_calls: return None                       # LLM 认为目标达成或降级
    tc = resp.tool_calls[0]                                    # MVP 取首项
    return Action(tool_name=tc["name"], args=tc.get("args", {}), rationale=resp.text[:200], llm_tool_call_id=tc.get("id"))

def _call_llm_safe(self, messages, tools, record) -> LLMResponse:
    try:
        return self._llm.chat(messages, tools=tools, temperature=0.0, max_tokens=4096)
    except Exception as e:
        # 降级:返回空 tool_calls,主循环转 REPORTING
        return LLMResponse(text="", tool_calls=[], tokens_in=0, tokens_out=0, provider=self._llm.provider_name, model="")

def _build_messages(self, state, record) -> list[Message]:
    msgs = [Message("system", SYSTEM_PROMPT),
            Message("user", self._render_task(state, record))]
    for h in state.history:
        r = h["result"]; c = h["call"]
        msgs.append(Message(role="tool",
            content=json.dumps({"status": r.status, "parsed": r.parsed, "error_hint": r.error_hint}, ensure_ascii=False, default=str),
            tool_call_id=c.llm_tool_call_id))
    return msgs

== prompts.py ==
SYSTEM_PROMPT = "你是 EDA 修复编排 agent。可用工具经 tools 参数给出。根据用户目标(让 RTL 通过 TB 测试)与各工具返回的结构化结果,逐步选择下一个工具调用。读 parsed.passed(iverilog_sim)/parsed.success(yosys_synth)/parsed.wns(opensta_timing)/_skill_convergence_cause(skill_self_heal) 等字段决策。每次只发一个 tool_call。目标达成(仿真 passed=True 且已独立验证)后不再发 tool_call。"
def _render_task(state, record) -> str:
    return json.dumps({"goal": state.goal, "rtl": state.rtl_path, "tb": state.tb_path, "top_module": state.top_module, "lib": state.lib_path, "clock": state.clock_name}, ensure_ascii=False)

== _finalize(T39,REPORTING,落 report.md + RunReport)==
def _finalize(self, record, state, request, budget) -> RunReport:
    # 从 CountingProvider 读 llm 计数
    stats = getattr(self._llm, "get_stats", lambda: None)()
    llm_calls = stats.llm_calls if stats else 0
    tokens_in = stats.tokens_in if stats else 0
    tokens_out = stats.tokens_out if stats else 0
    # status 判定
    if state.goal_achieved:
        status = "ok"
    elif record.status == "budget_exhausted" or budget.exhausted():
        status = "budget_exhausted"
    else:
        status = "failed"
    # metrics 13 字段
    sim = state.last_sim or {}
    sta = state.last_sta or {}
    heal = state.history and [h["result"].parsed for h in state.history if h["result"].tool == "skill_self_heal"]
    heal_parsed = heal[-1] if heal else {}
    conv = heal_parsed.get("convergence_cause") or heal_parsed.get("_skill_convergence_cause")
    metrics = {
        "planner_iterations": state.iteration,
        "tool_calls_total": len(state.history),
        "llm_calls": llm_calls, "llm_tokens_in": tokens_in, "llm_tokens_out": tokens_out,
        "sim_passed": sim.get("passed"),
        "num_passed": sim.get("num_passed"), "num_failed": sim.get("num_failed"),
        "wns_ns": sta.get("wns"), "tns_ns": sta.get("tns"),
        "self_heal_convergence": conv,
        "self_heal_best_iter": heal_parsed.get("best_iter") or heal_parsed.get("_skill_best_iter"),
        "self_heal_pass_rate": 1.0 if conv == "all_pass" else (0.0 if conv else None),
        "baseline_pass_rate": None,    # T50 baseline run 才填;MVP None
        "wall_time_s": budget.elapsed_s(),
    }
    # 渲染 report.md
    report_path = self._write_report_md(record.run_id, state, status, metrics)
    self._runner.finalize(record.run_id, status, final_report_path=report_path, llm_calls=llm_calls, llm_tokens_in=tokens_in, llm_tokens_out=tokens_out, total_duration_s=budget.elapsed_s())
    return RunReport(run_id=record.run_id, status=status, report_path=report_path, summary=self._summary(state, status), metrics=metrics)

== _emergency_report(异常兜底,不抛给调用方)==
def _emergency_report(self, record, err_msg) -> RunReport:
    self._runner.finalize(record.run_id, "failed", final_report_path=None, llm_calls=0, llm_tokens_in=0, llm_tokens_out=0)
    return RunReport(run_id=record.run_id, status="failed", report_path="", summary=f"crash: {err_msg}", metrics={})

== CLI(T47,cli.py)==
def run_pipeline(request: RunRequest, settings: Settings) -> RunReport:
    provider = make_provider(settings)
    runner = Runner(runs_dir="runs", settings=settings)
    runner.scavenge_zombies(settings.run_budget_s)
    registry = build_registry(provider=provider, runner=runner, settings=settings)
    planner = CPlanner(registry=registry, llm=provider, runner=runner, settings=settings)
    return planner.execute(request)

def main(argv=None):
    # argparse 三子命令:eda self-heal / eda diagnose / eda report
    parser = argparse.ArgumentParser(prog="eda")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_sh = sub.add_parser("self-heal")
    p_sh.add_argument("--rtl", required=True); p_sh.add_argument("--tb", required=True); p_sh.add_argument("--goal", required=True)
    p_sh.add_argument("--top-module"); p_sh.add_argument("--lib"); p_sh.add_argument("--clock")
    p_sh.add_argument("--max-iter", type=int); p_sh.add_argument("--mode", choices=["rule","llm"]); p_sh.add_argument("--run-budget", type=int)
    p_dg = sub.add_parser("diagnose"); p_dg.add_argument("--rtl", required=True); p_dg.add_argument("--tb", required=True)
    p_rp = sub.add_parser("report"); p_rp.add_argument("run_id")
    args = parser.parse_args(argv)
    settings = load_settings()
    if args.cmd == "self-heal":
        if args.mode: settings.planner.mode = args.mode      # 覆盖
        if args.run_budget: settings.budget.run_budget_s = args.run_budget
        req = RunRequest(kind="self_heal", goal=args.goal, rtl_path=args.rtl, tb_path=args.tb, top_module=args.top_module, lib_path=args.lib, clock_name=args.clock, max_iter=args.max_iter)
        rep = run_pipeline(req, settings)
        print(rep.report_path); return {"ok":0,"failed":1,"budget_exhausted":2}[rep.status]
    elif args.cmd == "diagnose":
        req = RunRequest(kind="diagnose", goal="diagnose", rtl_path=args.rtl, tb_path=args.tb)
        rep = run_pipeline(req, settings); print(rep.report_path); return {"ok":0,"failed":1,"budget_exhausted":2}[rep.status]
    elif args.cmd == "report":
        md = Path("runs")/args.run_id/"report.md"
        if not md.exists(): print(f"run_id {args.run_id} not found", file=sys.stderr); return 64
        print(md.read_text(encoding="utf-8")); return 0
main() 返回退出码(0/1/2/64);pyproject.toml [project.scripts] eda = "eda_agent.cli:main"(MVP 用 python -m eda_agent.cli 也可)

== _init_state ==
def _init_state(self, request) -> PlannerState:
    return PlannerState(rtl_path=request.rtl_path, tb_path=request.tb_path, top_module=request.top_module,
        lib_path=request.lib_path, clock_name=request.clock_name, goal=request.goal,
        max_iter=request.max_iter or self._settings.skill_max_iterations)

== _save_plan(落 plan.json)==
def _save_plan(self, run_id, plan):
    p = Path("runs")/run_id/"plan.json"
    p.write_text(json.dumps({"mode": plan.mode, "actions": [{"tool_name":a.tool_name,"args":{k:v for k,v in a.args.items() if not k.startswith("_")},"rationale":a.rationale} for a in plan.actions]}, ensure_ascii=False, indent=2), encoding="utf-8")
`

// ─────────────────────────────────────────────────────────────────────
// Schemas
// ─────────────────────────────────────────────────────────────────────
const IMPL_SCHEMA = {
  type: "object",
  properties: {
    files_created: { type: "array", items: { type: "string" } },
    files_modified: { type: "array", items: { type: "string" } },
    key_classes: { type: "array", items: { type: "string" } },
    contract_decisions: { type: "array", items: { type: "string" } },
    pytest_target: { type: "string" },
    pytest_full: { type: "string" },
    errors: { type: ["string", "null"] },
  },
  required: ["files_created", "files_modified", "key_classes", "pytest_full"],
}
const VERDICT_SCHEMA = {
  type: "object",
  properties: {
    verdict: { type: "string", enum: ["pass", "fail"] },
    blockers: { type: "array", items: {
      type: "object",
      properties: {
        severity: { type: "string", enum: ["critical", "major", "minor"] },
        file: { type: "string" }, line: { type: ["string", "null"] },
        issue: { type: "string" }, fix: { type: "string" },
      },
      required: ["severity", "file", "issue", "fix"],
    }},
    passed_checks: { type: "array", items: { type: "string" } },
    notes: { type: "string" },
  },
  required: ["verdict", "blockers", "notes"],
}
const TEST_SCHEMA = {
  type: "object",
  properties: {
    tests_written: { type: "array", items: { type: "string" } },
    pytest_result: { type: "string" },
    passed_count: { type: "integer" },
    failed_tests: { type: "array", items: { type: "string" } },
    skipped_eda: { type: "array", items: { type: "string" } },
    notes: { type: "string" },
  },
  required: ["tests_written", "pytest_result", "passed_count"],
}

// ─────────────────────────────────────────────────────────────────────
// 主流程
// ─────────────────────────────────────────────────────────────────────
phase('实现 CPlanner')
const impl = await agent(
  CONTEXT + "\n" + C_CONTRACT + "\n\n== 你的任务(T38-T43 + T47)==\n实现 CPlanner 完整核心 + CLI。产出文件:\n1. src/eda_agent/planner/__init__.py(空)\n2. src/eda_agent/planner/budget.py — Budget(T38)\n3. src/eda_agent/planner/rule_planner.py — Plan/Action/PlannerState/PlannerPhase dataclass + make_plan/_rule_after_reflect 逻辑(可作模块级函数或 RulePlanner 类;由 c_planner 调用)\n4. src/eda_agent/planner/prompts.py — SYSTEM_PROMPT + _render_task\n5. src/eda_agent/planner/c_planner.py — CPlanner 主类(execute 状态机 + _make_plan + _next_rule_action + _rule_after_reflect + _execute_action + _resolve_args + _args_match_schema + _tool_not_found_result + _args_invalid_result + _reflect + _verify_after_self_heal + _collect_upstream_results + _llm_next_action + _call_llm_safe + _build_messages + _finalize + _emergency_report + _init_state + _save_plan + _write_report_md + _summary)\n6. src/eda_agent/planner/report.py — render helper(可选,或合入 c_planner)\n7. src/eda_agent/cli.py — run_pipeline + main(argparse 三子命令)\n8. pyproject.toml — 加 [project.scripts] eda = \"eda_agent.cli:main\"\n9. tests/test_budget.py — T38(4 用例:remaining_s/elapsed_s/exhausted/边界)\n10. tests/test_planner_smoke.py — T39(stub echo tool + RunRequest;断言 execute 返回 RunReport,status 三态,run_id 正则,run.json 存在且 status!=running)\n11. tests/test_action_exec.py — T41(execute_action 落 steps/001_yosys_synth/;ARTIFACT_FROM_STATE 解析;tool_not_found 回灌;args_invalid 回灌)\n12. tests/test_e2e_stub.py — T42(stub yosys ok / iverilog passed=False / diagnose needs_rtl_patch / self_heal all_pass;断言独立验证步触发 + goal_achieved + report.md 含 all_pass + metrics.sim_passed)\n13. tests/test_llm_planner.py — T43(FakeLLMProvider:首轮 tool_calls→Action;不存在 tool→tool_not_found 回灌;tool_calls 空→None;chat 异常→降级)\n14. tests/test_planner_limits.py — T45(budget_exhausted / max_iter / fatal / iteration 不重复)\n15. tests/test_cli_exit_codes.py — T48(stub → 退出码 0/1/2/64;report 不存在→64)\n\n== 实现顺序 ==\n先 Read 核对:src/eda_agent/contracts.py(RunRequest/RunReport/ARTIFACT_FROM_STATE), errors.py(EDA_* 常量), registry.py(to_llm_tools/get), runner.py(create/finalize/append_step/make_config_snapshot), settings.py(planner_mode/planner_max_iterations/run_budget_s), skills/base.py(as_tool), skills/diagnose.py(parsed 形状), skills/self_heal.py(final_parsed 形状), tools/bootstrap.py(build_registry)。\n再实现:budget.py → rule_planner.py(dataclass) → prompts.py → c_planner.py(状态机+全部方法) → cli.py → pyproject → 单测。\n\n== 关键注意 ==\n- _reflect 不碰 state.iteration(单一计数出口在 _execute_action 末尾 state.iteration += 1)\n- 独立验证步:best_rtl_ref 已设 且 independent_verify_passed is None 时,在 _reflect 里判定;通过才 goal_achieved;失败不静默(independent_verify_passed=False,goal_achieved 保持 False)\n- pending_self_heal_all_pass 不立即 goal_achieved(等独立验证步)\n- ARTIFACT_FROM_STATE 替换 netlist;_artifact_ref 替换 rtl(独立验证步)\n- _args_match_schema 下划线前缀字段豁免\n- tool_not_found/args_invalid 回灌不中断;eda.internal 是 fatal\n- experiment_manifest 14 字段本任务可不做(T51);metrics 13 字段在 _finalize 填\n- planner_mode 默认 llm;rule 作降级(_call_llm_safe 异常→空 tool_calls→主循环 REPORTING 或继续 rule)\n- 单测用 stub Tool(返回固定 ToolResult)+ FakeLLMProvider(返回固定 LLMResponse),不需真 EDA;e2e 真跑用 @needs_eda + skipif 标记\n- check_no_subprocess.py 检查 planner/ 目录无 subprocess.run(C 不调子进程)\n\n== 验收 ==\n- cd '/d/eda agent system' && python -m pytest tests/test_budget.py tests/test_planner_smoke.py tests/test_action_exec.py tests/test_e2e_stub.py tests/test_llm_planner.py tests/test_planner_limits.py tests/test_cli_exit_codes.py -q → 全绿\n- cd '/d/eda agent system' && python -m pytest tests/ -q → 整体绿(148 + C 新增,无回归)\n- cd '/d/eda agent system' && python scripts/check_no_subprocess.py src/eda_agent/planner → 通过(C 不调子进程)\n\n返回 schema。contract_decisions 填契约裁决(如 metrics 字段调整、stub 设计)。errors 填问题(无则 null)。",
  { schema: IMPL_SCHEMA, phase: '实现 CPlanner', label: 'C-impl' }
)

phase('对抗验证')
const verdicts = await parallel([
  () => agent(
    CONTEXT + "\n" + C_CONTRACT + "\n\n== 你的任务:契约合规对抗验证(只读)==\nRead src/eda_agent/planner/c_planner.py + rule_planner.py + budget.py + cli.py,逐项检查:\n1. CPlanner 构造签名锁定 (registry, llm, runner, settings);run_id/budget 在 execute 内生成(非构造参数)\n2. execute 返回 RunReport;异常不抛给调用方(_emergency_report 兜底)\n3. 5 相状态机 PLANNING/EXECUTING/REFLECTING/REPORTING/DONE 转移正确\n4. planner_mode 默认 llm;rule 降级(_call_llm_safe 异常→空 tool_calls)\n5. _execute_action:registry.get(name) None→tool_not_found 回灌(不中断);args 不符→tool_args_invalid 回灌\n6. _args_match_schema 下划线前缀字段豁免(_remaining_budget_s/_artifact_ref)\n7. _resolve_args:ARTIFACT_FROM_STATE 替换 netlist;_artifact_ref 替换 rtl(独立验证步)\n8. 独立验证步:B all_pass 后强制 iverilog_sim 读 best/rtl.v;通过才 goal_achieved;失败不静默(independent_verify_passed=False)\n9. iteration 单一计数出口(_execute_action 末尾;_reflect 不碰)\n10. 双层预算:Action.args[_remaining_budget_s]=budget.remaining_s() 下传给 skill_self_heal\n11. LLM 回环:ToolResult→Message(role=tool,content=json{status,parsed,error_hint},tool_call_id);tools 经 registry.to_llm_tools\n12. metrics 13 字段齐全(planner_iterations/tool_calls_total/llm_calls/tokens/sim_passed/num_passed/num_failed/wns_ns/tns_ns/self_heal_convergence/best_iter/pass_rate/baseline_pass_rate/wall_time_s)\n13. CLI 三子命令(self-heal/diagnose/report)+ 退出码(0/1/2/64);run_pipeline 构造顺序(provider→runner.scavenge_zombies→registry→CPlanner)\n14. _schema 用 CONTRACT_VERSION;artifacts 用 artifact_ref 工厂(若有)\n15. C 不调 subprocess(planner/ 目录);不直接 import anthropic\n16. kind=diagnose 走单点 demo plan;kind=self_heal 走完整流程\n返回 verdict + blockers(file:line+fix) + passed_checks。",
    { schema: VERDICT_SCHEMA, phase: '对抗验证', label: 'C-verify-contract' }
  ),
  () => agent(
    CONTEXT + "\n" + C_CONTRACT + "\n\n== 你的任务:逻辑/状态机/边界对抗验证(只读)==\nRead src/eda_agent/planner/c_planner.py,检查:\n1. 状态机转移无死锁/无无限循环(max_iter 兜底;budget 兜底;LLM 无 tool_calls 兜底)\n2. _reflect 各分支(yosys_synth/iverilog_sim/opensta_timing/skill_diagnose/skill_self_heal)正确更新 state;不碰 iteration\n3. 独立验证步时序:_verify_after_self_heal 在 pending_self_heal_all_pass 后触发;independent_verify_passed 只判定一次;best_rtl_ref None 时跳过\n4. _rule_after_reflect 三分支(仿真过→goal_achieved;未诊断→diagnose;已诊断→self_heal)正确;避免重复调 self_heal(best_rtl_ref None 守卫)\n5. netlist_ref 从 yosys_synth.artifacts 正确找(endwith synth/netlist.v)\n6. _llm_next_action:iteration>=max_iter 返回 None;budget 耗尽返回 None;取首项 tool_calls[0]\n7. _call_llm_safe 异常→空 LLMResponse(不抛)\n8. _build_messages 遍历 history 无 AttributeError(tool_call_id None 时 Message 接受)\n9. _finalize:status 判定(goal_achieved→ok;budget→budget_exhausted;其余→failed);llm 计数从 CountingProvider.get_stats() 读(若无则 0)\n10. _emergency_report:不抛,返回 failed RunReport\n11. _args_match_schema:schema 无 input_schema 时返回 True(不阻塞)\n12. CLI:run_id 不存在 report 子命令→退出码 64;参数缺失→argparse 报错\n13. 边界:空 history/空 tool_calls/synth 失败/sim passed=None/sta wns=None 不崩\n14. Budget.remaining_s 永不 < 0;exhausted 边界(==0)\n15. check_no_subprocess.py 对 planner/ 通过(C 无 subprocess.run)\n返回 verdict + blockers + passed_checks。状态机漏洞/无限循环=critical。",
    { schema: VERDICT_SCHEMA, phase: '对抗验证', label: 'C-verify-logic' }
  ),
])

phase('单测')
const test = await agent(
  CONTEXT + "\n" + C_CONTRACT + "\n\n== 你的任务:补全 C 单测并跑绿 ==\n如果 C-impl 已写 tests/test_budget.py/test_planner_smoke.py/test_action_exec.py/test_e2e_stub.py/test_llm_planner.py/test_planner_limits.py/test_cli_exit_codes.py,Read 补强;否则按 C-impl 产出补全。\n必须覆盖:\n- test_budget.py(T38):remaining_s/elapsed_s 精度;exhausted 边界;remaining_s>=0\n- test_planner_smoke.py(T39):stub echo tool + RunRequest(kind=self_heal/diagnose);execute 返回 RunReport;status 三态;run_id 正则 \\d{8}_\\d{6}_[0-9a-f]{4};run.json 存在且 status!=running\n- test_action_exec.py(T41):execute_action 落 steps/001_yosys_synth/{tool_call.json,tool_result.json};ARTIFACT_FROM_STATE netlist 解析;tool_not_found 回灌(error_code=eda.tool_not_found);args_invalid 回灌\n- test_e2e_stub.py(T42):stub yosys ok/iverilog passed=False/diagnose needs_rtl_patch/self_heal all_pass+fixed_rtl_ref;断言独立验证步触发(父 steps 含非 self_heal 的 iverilog_sim)+ goal_achieved + report.md 含 all_pass + metrics.sim_passed\n- test_llm_planner.py(T43):FakeLLMProvider 首轮 tool_calls→Action;不存在 tool→tool_not_found 回灌 Message(role=tool);tool_calls 空→None;chat 异常→降级空 tool_calls\n- test_planner_limits.py(T45):budget_exhausted(run_budget_s=1s + stub sleep→budget_exhausted);max_iter(iteration 到 planner_max_iterations 转 REPORTING);fatal(stub 返回 eda.internal→state.fatal);iteration 不重复(_reflect 不碰)\n- test_cli_exit_codes.py(T48):stub B→退出码(ok=0/failed=1/budget_exhausted=2);参数错→64;report run_id 不存在→64\n用 stub Tool + FakeLLMProvider(不需真 EDA)。\n\n跑 cd '/d/eda agent system' && python -m pytest tests/test_budget.py tests/test_planner_smoke.py tests/test_action_exec.py tests/test_e2e_stub.py tests/test_llm_planner.py tests/test_planner_limits.py tests/test_cli_exit_codes.py -v 2>&1 | tail -60。\n失败则 Read 源文件诊断修复(优先修源,其次修测试)。改完重跑直到绿。\n返回 tests_written/pytest_result/passed_count/failed_tests/skipped_eda。",
  { schema: TEST_SCHEMA, phase: '单测', label: 'C-test' }
)

return { impl, verdicts, test }
