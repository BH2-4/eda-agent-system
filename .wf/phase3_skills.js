export const meta = {
  name: 'eda-phase3-skills',
  description: 'Phase 3: 实现 A 诊断器(skill_diagnose) + B 自修复(skill_self_heal),T20-T37',
  phases: [
    { title: 'A: 实现诊断器' },
    { title: 'A: 对抗验证' },
    { title: 'A: 单测' },
    { title: 'B: 实现自修复' },
    { title: 'B: 对抗验证' },
    { title: 'B: 单测' },
  ],
}

// ─────────────────────────────────────────────────────────────────────
// 共享上下文(注入每个 agent)
// ─────────────────────────────────────────────────────────────────────
const CONTEXT = `
项目:EDA Agent System(Agentic4Systems 比赛项目,提交 2026-07-15)。
代码根:D:\\eda agent system\\(Windows,4 空格缩进,from __future__ import annotations,中文注释,每行≤100 字符)。
工作目录:所有 bash 命令先 cd '/d/eda agent system'。

== 编码硬规则(验收 I12/A14/C2,违反=blocker) ==
- contract_version 必须 from eda_agent.contracts import CONTRACT_VERSION 引用(禁止裸 "0.1.0")
- parsed 必须含 _schema 元字段:{"name":<tool>,"version":"0.1.0","contract_version":CONTRACT_VERSION}
- artifacts 元素必须用 artifact_ref(run_id, rel_path) 工厂构造(禁止裸 str / 裸 dict 字面量)
- 禁止重定义 contracts.py/errors.py 已有类型(ErrorItem/SkillResult/ToolCall/ToolResult/artifact_ref/CONTRACT_VERSION/Severity),一律 import
- ToolResult.status 只有 ok/error/timeout 三态(warning 归 ok 写进 parsed)

== 基础设施 API(已实现,直接用;实现前先 Read 核对签名) ==
eda_agent.contracts:
  CONTRACT_VERSION="0.1.0"
  artifact_ref(run_id, rel_path) -> {"run_id":..,"rel_path":..}
  ToolCall(name, args, caller="planner", llm_tool_call_id=None)  [frozen dataclass]
  ToolResult(status, exit_code, stdout, stderr, parsed, artifacts, duration_s, tool, error_code=None, error_hint=None)
  Tool Protocol: name, description, schema, __call__(call)->ToolResult
  SkillResult(status, iterations, final_parsed, trajectory, artifacts, summary, patch_source="none", convergence_cause="none", best_iter=-1, error_code=None, budget_used_s=0.0)
  Skill Protocol: name, description, max_iterations, budget_s, run(run_id, inputs, remaining_budget_s=None)->SkillResult, as_tool()->Tool
  Message(role, content, tool_call_id=None), LLMResponse(text, tool_calls, tokens_in, tokens_out, provider, model, raw=None), LLMProvider(provider_name, chat(messages, tools=None, temperature=0.0, max_tokens=4096)->LLMResponse)
eda_agent.errors:
  Severity = Literal["info","warn","error","fatal"]
  ErrorItem(code, namespace, tool, severity, message, evidence=[], fix_suggestion=None)
  ErrorItem.make(code, tool, message, *, evidence=None, fix_suggestion=None, severity=None) -> 自动派生 namespace+severity
  namespace_of(code), severity_of(code), SEVERITY_BY_CODE
  常量:DIAGNOSE_NO_ERROR_FOUND, DIAGNOSE_LLM_CALL_FAILED, DIAGNOSE_KB_CORRUPTED, DIAGNOSE_ARGS_INVALID, HEAL_UNSUPPORTED_GOAL, HEAL_RTL_NOT_FOUND, HEAL_TB_NOT_FOUND, HEAL_DIAGNOSE_FAILED, HEAL_PATCH_SYNTAX_INVALID, HEAL_REGRESSION_DEADLOCK, HEAL_REDUCED_TO_DIAGNOSE, EDA_BUDGET_EXHAUSTED, EDA_SUBPROCESS_TIMEOUT, EDA_TOOL_ARGS_INVALID 等
eda_agent.skills.base:
  as_tool(skill) -> Tool  (SkillAdapter:拆包 _remaining_budget_s + run_id;SkillResult→ToolResult 映射 + 6 个 _skill_* 元字段;budget_exhausted→eda.budget_exhausted 覆写)
eda_agent.runner.Runner:
  create(request, *, provider_used, config_snapshot), finalize(run_id, status, *, final_report_path, llm_calls, llm_tokens_in, llm_tokens_out, total_duration_s)
  append_step(run_id, call, result, *, skill_name=None, iter=None) -> StepRecord
  get_record(run_id), load_record(run_id)
eda_agent.settings.Settings:
  settings.skill_max_iterations(=5), settings.skill_diagnose_budget_s(=60), settings.skill_self_heal_budget_s(=480)
  settings.error_kb_path(="data/error_kb.json"), settings.data.logs_corpus_path(="data/logs_corpus/corpus.jsonl")
  settings.eda.wsl_enabled, settings.eda.tool_timeout_s
eda_agent.llm.factory.make_provider(settings) -> LLMProvider (CountingProvider 包装)
eda_agent.tools.bootstrap.build_registry(provider, runner, settings) -> ToolRegistry  (已注册 yosys_synth/iverilog_sim/opensta_timing 三 L1 Tool)
eda_agent.registry: ToolRegistry, ToolEntry(tool, name, category, schema, parsed_schema_ref)

== Tool 调用模式(B 调 L1 Tool / 调 skill_diagnose) ==
- 调 L1 Tool: result = registry.get("yosys_synth")(ToolCall(name="yosys_synth", args={...}, caller="self_heal", llm_tool_call_id=None))
  然后 runner.append_step(run_id, call, result, skill_name="self_heal", iter=N)
- 调 skill_diagnose: diag = registry.get("skill_diagnose")(ToolCall(name="skill_diagnose", args={"tool_results":[...], "run_id":run_id, "_remaining_budget_s":remaining}, caller="self_heal"))
  diag.parsed 含 13 字段 + _skill_* 元字段;diag.parsed["root_causes"] 是 list[dict]

== 现有 ToolResult.parsed 范式(参考 tools/yosys_synth.py) ==
parsed 必须含 _schema 元字段 + 业务字段;失败也按契约固定声明 parsed(见 yosys_synth.py 空 rtl 分支)。
`

// ─────────────────────────────────────────────────────────────────────
// A 诊断器契约 + 设计
// ─────────────────────────────────────────────────────────────────────
const A_CONTRACT = `
== A 诊断器契约(CONTRACTS §2.2):skill_diagnose 的 ToolResult.parsed 必须含 13 个 key ==
1. _schema: {"name":"skill_diagnose","version":"0.1.0","contract_version":CONTRACT_VERSION}
2. skill: "diagnose" (字面量)
3. tool: str  (yosys_synth / iverilog_sim / opensta_timing / "multi")
4. stage: Literal["synth","sim","sta","multi"]
5. root_causes: list[dict]  (ErrorItem.asdict 列表,7 字段齐全;空列表合法)
6. root_cause_summary: str
7. severity: Literal["info","warn","error","fatal"]  (取 root_causes 中最严重;空则 "info")
8. fix_hints: list[str]
9. confidence: float (0..1,公式见下)
10. needs_rtl_patch: bool  (派生:any(e.severity in ("error","fatal") for e in errors);按 severity 判,禁止按 namespace 前缀判)
11. used_layers: Literal["rule","llm","rule+llm"]
12. kb_hits: list[str]  (命中的 ErrorPattern.pid 列表;MVP 用 errors 的 code 作 kb_hits)
13. summary: str  (与 root_cause_summary 同值)

== confidence 公式(v1.2 写死,覆盖 LLM 自填) ==
def compute_confidence(evidence: list[str], has_contradiction: bool) -> float:
    val = 0.5 + 0.1 * min(len(evidence), 5) - 0.2 * (1 if has_contradiction else 0)
    return max(0.0, min(1.0, val))
- 饱和项 min(len(evidence),5):5 条 evidence 后不再加(避免恒 1.0)
- has_contradiction:仅 LLM 层触发时(used_layers in ("llm","rule+llm"))检查;LLM 的 root_cause 文本 vs 规则层 errors[0].message 的关键词 Jaccard < 0.3 → True
- Jaccard:两文本 split() 分词后 |A 交 B| / |A 并 B|(MVP 简单分词);errors 为空时 has_contradiction=False
- LLMAttributor 即使 prompt 要求给 confidence 也忽略,用此公式自算

== needs_rtl_patch 派生(v1.2 按 severity,禁止按 namespace 前缀) ==
needs_rtl_patch = any(e.severity in ("error","fatal") for e in errors)  # errors 空时 False
原因:A 消费 eda.* 别名(如 eda.rtl_syntax),按前缀判会恒 False(v1.1 断路器 bug)。

== A 恒定语义(SkillResult 字段) ==
patch_source="none", convergence_cause="none", best_iter=-1, iterations=1  (A 不修复不迭代)

== 内部 dataclass(定义在 skills/diagnose.py) ==
@dataclass ErrorPattern:
  pid: str  (如 "synth.multidrive_001")
  tool: str  ("yosys_synth"/"iverilog_sim"/"opensta_timing"/"*" 通配)
  error_code: str  (二段式规范码,如 "synth.multi_driver")
  severity: Severity
  regex: str  (命名分组抽行号/信号名)
  fix_hint_template: str  (支持 {placeholder} .format)
  example_log: str  (自检:re.search(regex, example_log) 必须命中)
  source: Literal["seed","llm_curated","human"]
  hit_count: int = 0
  added_at: str = ""

class ErrorKB:
  patterns: list[ErrorPattern], version: str = "0.1.0"
  @classmethod load(path) -> ErrorKB  (json→对象;解析失败返回空 KB,记 diagnose.kb_corrupted 不抛)
  save(path=None) -> None  (原子写:tmp 文件 + os.replace)
  lookup(tool, log_text) -> list[tuple[ErrorPattern, re.Match]]  (过滤 p.tool in (tool,"*"),re.search 命中)
  add_case(pattern, dedupe=True) -> bool  (按 (tool,error_code,regex) 去重;重复返回 False)
  suggest_from_llm(llm_output: dict, tool: str) -> ErrorPattern | None  (构造 source="llm_curated" pattern,不入库)
  snapshot_to(run_id) -> None  (落 runs/<run_id>/diagnose/error_kb_snapshot.json)

@dataclass DiagnosisReport:
  tool: str, stage: str, errors: list[ErrorItem], root_cause: str, severity: Severity,
  fix_hints: list[str], confidence: float, evidence: list[str],
  used_layers: Literal["rule","llm","rule+llm"], kb_hits: list[str]
  def to_parsed(self) -> dict:
    # errors → root_causes = [asdict(e) for e in self.errors]
    # root_cause_summary = self.root_cause; summary = self.root_cause
    # 映射到 13 字段(含 _schema)
  注意:内部字段叫 errors(list[ErrorItem]),parsed 里叫 root_causes(list[dict])。这是 m1 命名裁决。

== DiagnoseSkill.run 9 步主流程 ==
预算:budget = min(self.budget_s, remaining_budget_s or self.budget_s);deadline = monotonic()+budget
0. 入参校验:inputs["tool_results"] 必须非空 list,每条 dict 含 "tool"+"parsed"。不符 → 返回 status="ok"+空 root_causes SkillResult(不抛,让 B 降级,error_code=None)。
   trajectory 初始 [{"step":"start","iter":0,"detail":f"{n} tool_results"}]
1. _collect_logs(run_id, tool_results) -> {tool_name: log_text}
   优先读 runs/<run_id>/steps/<idx>_*/<tool>.full.log(找最新匹配 step 目录),fallback tool_result.get("stdout","")
   trajectory +{"step":"collect_logs","iter":0,"detail":..}
2. RuleLayer.match(零 LLM):对每个 (tool, log_text) 调 kb.lookup → _build_error_item 构造 ErrorItem → 聚合 errors
   trajectory +{"step":"rule","iter":0,"detail":f"{len(errors)} errors"}
3. 决定是否触发 LLM(OR 条件):
   any_failed = any(tr.get("status")!="ok" for tr in tool_results)
   cross_tool = len({tr["tool"] for tr in tool_results if tr.get("status")!="ok"}) > 1
   needs_llm = (len(errors)==0 and any_failed) or cross_tool
   满足且 monotonic()<deadline:
     used_layers = "llm" if not errors else "rule+llm"
     (root_cause, fix_hints, llm_found_new, raw) = LLMAttributor(self._llm).attribute(errors=[asdict(e) for e in errors], context={"tool_results":tool_results,"logs":logs}, deadline=deadline)
     LLM 无 root_cause 且 errors 空 → errors.append(ErrorItem.make(DIAGNOSE_NO_ERROR_FOUND, tool="multi", message="no error found", severity="warn"))
4. root_cause 回填:LLM 没触发 → root_cause = f"{len(errors)} 个错误,首要:{errors[0].message}"(errors 空时 root_cause="无错误")
   fix_hints 空且 errors 非空 → fix_hints = [e.fix_suggestion or e.message for e in errors[:3]]
5. evidence + confidence:
   evidence = 抽 errors 的 evidence 展开 + 关键日志行(去重,≤10 条)
   has_contradiction = (used_layers in ("llm","rule+llm") and errors and _jaccard(root_cause, errors[0].message) < 0.3)
   confidence = compute_confidence(evidence, has_contradiction)
6. LLM 提议新 pattern → propose_pending:
   if llm_found_new and raw: cand = kb.suggest_from_llm(raw, tool=primary_tool); 写 runs/<run_id>/diagnose/propose_pending/<uuid8>.json(asdict(cand));不自动入库
7. 装配 DiagnosisReport + 落盘:
   severity = _max_severity(errors)  (空则 "info")
   kb_hits = [e.code for e in errors]
   report = DiagnosisReport(tool, stage, errors, root_cause, severity, fix_hints, confidence, evidence, used_layers, kb_hits)
   ReportWriter().write(run_id, report, kb)  → 落 runs/<run_id>/diagnose/report.md + report.json
   kb.snapshot_to(run_id)  → runs/<run_id>/diagnose/error_kb_snapshot.json
8. 返回 SkillResult(status="ok", iterations=1, final_parsed=report.to_parsed(), trajectory=trajectory,
   artifacts=[artifact_ref(run_id,"diagnose/report.md"), artifact_ref(run_id,"diagnose/report.json")],
   summary=root_cause, patch_source="none", convergence_cause="none", best_iter=-1,
   budget_used_s=float(monotonic()-t0))

== RuleLayer(零 LLM,模块级函数) ==
def rule_match(kb, tool, log_text) -> list[ErrorItem]:
    items = []
    for pattern, match in kb.lookup(tool, log_text):
        items.append(_build_error_item(pattern, match, tool))
    return items
def _build_error_item(pattern, match, tool_name) -> ErrorItem:
    g = match.groupdict()
    msg = pattern.fix_hint_template.format(**g) if g else pattern.fix_hint_template
    return ErrorItem(code=pattern.error_code, namespace=namespace_of(pattern.error_code),
        tool=tool_name, severity=pattern.severity, message=msg, evidence=[match.group(0)], fix_suggestion=msg)

== LLMAttributor(skills/diagnose.py 内) + prompts_diagnose.py ==
class LLMAttributor:
    def __init__(self, llm): self._llm = llm
    def attribute(self, errors, context, deadline):
        # 返回 (root_cause:str, fix_hints:list, llm_found_new:bool, raw:dict)
        if monotonic() >= deadline: return ("",[],False,{})
        msgs = build_diagnose_prompt(errors, context)  # 从 prompts_diagnose.py
        try: resp = self._llm.chat(msgs, temperature=0.0, max_tokens=2048)
        except Exception: return ("(LLM call failed)",[],False,{})
        raw = _extract_json(resp.text)  # 找首个 { 用 JSONDecoder.raw_decode
        if not raw: return ("(LLM parse failed)",[],False,{})
        ok = _validate(raw)  # jsonschema 校验:必含 root_cause:str, fix_hints:list, needs_patch:bool, new_pattern:obj|null
        if not ok: return ("(LLM parse failed)",[],False,raw)
        new = raw.get("new_pattern")
        return (raw["root_cause"], list(raw.get("fix_hints",[])), bool(new), raw)

skills/prompts_diagnose.py:
  def build_diagnose_prompt(errors: list[dict], context: dict) -> list[Message]:
    system = "你是 EDA 诊断专家。给定结构化错误列表与工具日志,产出 JSON 对象,字段:root_cause(str),fix_hints(list[str]),needs_patch(bool),new_pattern(null 或 {regex,error_code,fix_hint_template})。只输出 JSON,不要解释。"
    user = json.dumps({"errors":errors, "logs_tail_per_tool": <每个 tool 取 log 末尾 2000 字符>}, ensure_ascii=False)
    return [Message("system",system), Message("user",user)]

== 种子 ErrorPattern(data/error_kb.json,T21:≥10 条 source="seed") ==
每条 example_log 必须被自己 regex re.search 命中(自检,实现时验证)。
覆盖:synth.rtl_syntax, synth.multi_driver, synth.undefined_signal, synth.port_unconnected,
sim.compile_failed, sim.assert_failed, sim.mismatch, sta.timing_violation, eda.subprocess_timeout 等(≥10 条,覆盖 synth/sim/sta 三类)。
error_code 用规范二段式(synth.*/sim.*/sta.*),severity 按 SEVERITY_BY_CODE(未登记的按语义定,如 synth.rtl_syntax=error)。
data/error_kb.json 格式:{"version":"0.1.0","patterns":[asdict(p),...]}

== ReportWriter(放 skills/diagnose.py 同模块的类,不建子包) ==
class ReportWriter:
    def write(self, run_id, report, kb):
        base = Path("runs")/run_id/"diagnose"; base.mkdir(parents=True, exist_ok=True)
        (base/"report.md").write_text(self._render_md(report), encoding="utf-8")
        (base/"report.json").write_text(json.dumps(report.to_parsed(), ensure_ascii=False, indent=2), encoding="utf-8")
    _render_md(report): markdown,含 tool/stage/severity/confidence/used_layers/contradiction 标注/root_causes 表/fix_hints

== bootstrap.py 注册 skill_diagnose(T26) ==
在 tools/bootstrap.py build_registry 末尾(L2 Skill 段)补:
    from eda_agent.skills.diagnose import DiagnoseSkill, ErrorKB
    from eda_agent.skills.base import as_tool
    diag = DiagnoseSkill(kb=ErrorKB.load(settings.error_kb_path), llm=provider, runner=runner, settings=settings)
    registry.register(ToolEntry(
        tool=as_tool(diag), name="skill_diagnose", category="skill",
        schema=diag.schema["input_schema"],
        parsed_schema_ref={"name":"skill_diagnose","version":_SCHEMA_VERSION}))

== DiagnoseSkill 构造签名 ==
class DiagnoseSkill:
    name="skill_diagnose", description="诊断 EDA 工具日志错误,输出根因+修复建议"
    def __init__(self, kb, llm, runner, settings):
        self._kb=kb; self._llm=llm; self._runner=runner; self._settings=settings
        self.max_iterations=1; self.budget_s=float(settings.skill_diagnose_budget_s)
        self.schema={"name":"skill_diagnose","description":..., "input_schema":{"type":"object","properties":{"tool_results":{"type":"array"}},"required":["tool_results"]}}
    def run(self, run_id, inputs, remaining_budget_s=None) -> SkillResult:  # 9 步
    def as_tool(self): return as_tool(self)  # 委托 skills.base
`

// ─────────────────────────────────────────────────────────────────────
// B 自修复契约 + 设计
// ─────────────────────────────────────────────────────────────────────
const B_CONTRACT = `
== B 自修复契约(CONTRACTS §2.3):skill_self_heal ==
as_tool 后 ToolResult.parsed(final_parsed 业务字段 + _skill_* 元字段)。final_parsed 业务字段:
  _schema: {"name":"skill_self_heal","version":"0.1.0","contract_version":CONTRACT_VERSION}
  skill: "self_heal"
  passed: bool  (convergence=="all_pass")
  iterations: int
  best_iter: int
  convergence_cause: str
  patch_source: str  (llm_diff|llm_full_rewrite|rule_based|none)
  fixed_rtl_ref: dict | None  (artifact_ref(run_id,"self_heal/best/rtl.v");失败 None)
  final_report_ref: dict  (artifact_ref(run_id,"self_heal/report.md"))
  summary: str

== skill_self_heal.as_tool 的 args schema(9 字段) ==
input_schema.properties: rtl(str,必填), tb(str,必填), diagnose(dict|null), max_iter(int,必填),
  goal(str,必填), lib(str|null), clock(str|null), top_module(str|null)
reserved 字段 _remaining_budget_s / run_id 由 as_tool 拆包(不进 LLM schema)

== 内部 dataclass(skills/self_heal.py) ==
@dataclass HealGoal:
  pass_mode: Literal["all","at_least"]  # "pass all tests"→all; "pass N tests"→at_least
  num_required: int  # all 时 0(运行时填 total);at_least 时 N
  sta_required: bool  # goal 含 "timing"/"no violation" → True
  raw: str
@dataclass PatchOutcome:
  iter_n: int, diagnose_parsed: dict,
  patch_source: Literal["llm_diff","llm_full_rewrite","rule_based","none"],
  patch_diff: str, target_lines,
  applied: bool, rolled_back: bool, regression: bool,
  llm_tokens_in: int, llm_tokens_out: int, rationale: str
@dataclass IterSnapshot:
  iter_n: int, rtl_text: str, num_passed: int, num_failed: int,
  patch_source: str, convergence

== SelfHealSkill 构造签名 ==
class SelfHealSkill:
    name="skill_self_heal", description="自修复 RTL:综合→仿真→(STA)→诊断→patch→重验证,版本栈回退"
    def __init__(self, registry, llm, runner, max_iterations=5, budget_s=480, settings=None):
        self._registry=registry; self._llm=llm; self._runner=runner
        self.max_iterations=max_iterations; self.budget_s=float(budget_s); self._settings=settings or Settings()
        self.schema={"name":"skill_self_heal","description":..., "input_schema":{9 字段}}
    def run(self, run_id, inputs, remaining_budget_s=None) -> SkillResult:
    def as_tool(self): return as_tool(self)

== _parse_goal(goal_str) -> HealGoal | None ==
"pass all tests" → HealGoal("all", 0, False, goal)
"pass N tests" (正则 r"pass\\s+(\\d+)\\s+test") → HealGoal("at_least", N, False, goal)
含 "timing"/"no violation" → sta_required=True
无法解析 → None(调用方报 heal.unsupported_goal)

== _stage_synth / _stage_sim / _stage_sta(B2,只构造 ToolCall→registry.get→append_step,不做判定) ==
def _stage_synth(self, rtl_path, run_id, iter_n):
    call = ToolCall(name="yosys_synth", args={"rtl":rtl_path,"run_id":run_id}, caller="self_heal", llm_tool_call_id=None)
    res = self._registry.get("yosys_synth")(call)
    self._runner.append_step(run_id, call, res, skill_name="self_heal", iter=iter_n)
    return res
def _stage_sim(self, rtl_path, tb_path, run_id, iter_n):
    call = ToolCall(name="iverilog_sim", args={"rtl":rtl_path,"tb":tb_path,"run_id":run_id}, caller="self_heal")
    res = self._registry.get("iverilog_sim")(call); append_step(...); return res
def _stage_sta(self, netlist_path, lib, clock, run_id, iter_n):
    # 仅当 goal.sta_required and lib and clock 才跑;否则调用方不调
    call = ToolCall(name="opensta_timing", args={"netlist":netlist_path,"lib":lib,"clock":clock,"run_id":run_id}, caller="self_heal")
    res = self._registry.get("opensta_timing")(call); append_step(...); return res
判定(在 run 主循环):
  synth_ok = synth_res.is_ok() and synth_res.parsed.get("success") is True
  sim_parsed = sim_res.parsed; num_passed=sim_parsed.get("num_passed") or 0; total=sim_parsed.get("total") or 0
  required = total if goal.pass_mode=="all" else goal.num_required
  sim_ok = sim_parsed.get("passed") is True or num_passed >= required
  sta_ok = sta_res is None or (sta_res.parsed.get("wns") is not None and sta_res.parsed["wns"] >= 0)
  all_pass = synth_ok and sim_ok and sta_ok

== _diagnose_and_patch(B3 核心) ==
def _diagnose_and_patch(self, stage_results, rtl_text, run_id, iter_n, seed, strategy, snap_dir) -> PatchOutcome:
  1. 调 skill_diagnose:
     if seed is not None: diag_parsed = seed  # 首轮外部预调过
     else:
       tool_results = [挑选 status/parsed/tool/stdout 字段 + step_idx=i + run_id for i,r in enumerate(stage_results)]
       diag_call = ToolCall(name="skill_diagnose", args={"tool_results":tool_results,"run_id":run_id,"_remaining_budget_s":<剩余>}, caller="self_heal")
       diag_res = self._registry.get("skill_diagnose")(diag_call)
       diag_parsed = diag_res.parsed if diag_res.is_ok() else {}
       self._runner.append_step(run_id, diag_call, diag_res, skill_name="self_heal", iter=iter_n)
  2. root_causes = diag_parsed.get("root_causes",[])
     if not root_causes:  # fallback 包装 fail_signals(契约 §2.6 A→B 第 3 条,禁止裸字符串当 ErrorItem)
       wrapped = []
       for r in stage_results:
         if r.tool=="iverilog_sim":
           for sig in (r.parsed.get("fail_signals") or []):
             wrapped.append(asdict(ErrorItem.make("sim.fail_signal", tool="iverilog_sim", message=sig, severity="error")))
       root_causes = wrapped
  3. target_lines = _locate_lines(root_causes, rtl_text)  # 正则 r":(\\d+):" / r"\\((\\d+)\\)" / r":(\\d+):\\s*\\d+";命中 (start,end) 否则 None
  4. messages = _build_prompt(strategy, rtl_text, root_causes, target_lines)  # 三策略;diagnose_only 返回 None
  5. resp = self._llm.chat(messages, temperature=0.0, max_tokens=4096)
  6. (patch_text, is_full_rewrite) = _extract_patch(resp.text, strategy)
  7. (rtl_new, applied_ok) = _apply_patch(rtl_text, patch_text, is_full_rewrite, target_lines)
     syntax_ok = _syntax_check(rtl_new)  # iverilog -t null -o /dev/null(WSL),纯语法预检
     if applied_ok and syntax_ok:
       写 snap_dir/rtl_snapshot.v(rtl_new) + rtl_patch.diff
       return PatchOutcome(iter_n, diag_parsed, "llm_full_rewrite" if is_full_rewrite else "llm_diff", patch_text, target_lines, True, False, False, resp.tokens_in, resp.tokens_out, "applied")
     else:
       return PatchOutcome(iter_n, diag_parsed, "llm_full_rewrite" if is_full_rewrite else "llm_diff", patch_text, target_lines, False, False, False, resp.tokens_in, resp.tokens_out, "apply/syntax failed")

== 三策略 _build_prompt(strategy, rtl_text, root_causes, target_lines) ==
def _build_prompt(strategy, rtl_text, root_causes, target_lines):
  system_base = "你是 Verilog 修复专家。根据诊断根因修复 RTL,保持模块接口不变。"
  if strategy == "diff":
    snippet = rtl_text  # target_lines 命中时取 ±5 行片段,否则全文
    user = "[DIFF 策略] 出错片段:\n" + snippet + "\n根因(JSON):" + json.dumps(root_causes, ensure_ascii=False) + "\n只改出错行±5 行,别动其他。输出 unified diff 补丁(以 --- /+++ /@@ /+- 开头的行)。"
  elif strategy == "full_rewrite":
    user = "[FULL_REWRITE 策略] 完整 RTL:\n" + rtl_text + "\n根因(JSON):" + json.dumps(root_causes, ensure_ascii=False) + "\n整文件重写,保持模块接口不变。输出完整 .v 文件全文(纯 Verilog,不要 markdown 包裹)。"
  else:  # diagnose_only
    return None
  return [Message("system",system_base), Message("user",user)]

_extract_patch(resp_text, strategy):
  diff 策略:从 resp_text 抽出 unified diff 行(匹配以 --- 或 @@ 开头的块,到末尾或下个非 diff 行)
  full_rewrite 策略:抽 Verilog 文本(若被 markdown 包裹则取代码块内容,否则整段)
  返回 (patch_text, is_full_rewrite_bool)

== _syntax_check(B 语法预检) ==
def _syntax_check(self, rtl_text) -> bool:
  # 写临时 .v,跑 wsl iverilog -t null -o /dev/null(WSL 模式);returncode==0 → True
  # 失败/超时 → False(不抛)
  参考 tools/iverilog_sim.py 的 WSL 调用范式(用 _to_wsl_path + subprocess.run(encoding=utf-8,errors=replace))

== 版本栈 + 主循环 run(B4) ==
def run(self, run_id, inputs, remaining_budget_s=None) -> SkillResult:
  budget = min(self.budget_s, remaining_budget_s or self.budget_s); t0=monotonic()
  rtl_path = inputs.get("rtl"); tb_path = inputs.get("tb"); goal_str = inputs.get("goal","")
  max_iter = inputs.get("max_iter") or self.max_iterations
  seed = inputs.get("diagnose")
  lib=inputs.get("lib"); clock=inputs.get("clock"); top=inputs.get("top_module")
  if not rtl_path or not Path(rtl_path).exists(): return self._fail(run_id, HEAL_RTL_NOT_FOUND, ...)
  if not tb_path or not Path(tb_path).exists(): return self._fail(run_id, HEAL_TB_NOT_FOUND, ...)
  goal = _parse_goal(goal_str)
  if goal is None: return self._fail(run_id, HEAL_UNSUPPORTED_GOAL, f"unparseable goal: {goal_str}")
  rtl_text = Path(rtl_path).read_text(encoding="utf-8")
  base_dir = Path("runs")/run_id/"self_heal"; snap0_dir=base_dir/"iter_0"; snap0_dir.mkdir(parents=True,exist_ok=True)
  (snap0_dir/"rtl_snapshot.v").write_text(rtl_text,encoding="utf-8")
  version_stack=[rtl_text]; best_iter=-1; best_score=-1; candidates_at_best=0; best_rtl_text=rtl_text
  strategy="diff"; regression_streak=0; num_passed_dropped_streak=0; convergence="none"
  trajectory=[{"step":"start","iter":0,"detail":f"goal={goal_str}"}]; last_patch_source="none"
  final_diag_parsed={}
  for iter_n in range(max_iter):
    if monotonic()-t0 > budget: convergence="budget"; break
    cur_rtl = version_stack[-1]; snap_dir=base_dir/f"iter_{iter_n+1}"; snap_dir.mkdir(parents=True,exist_ok=True)
    (snap_dir/"rtl_snapshot.v").write_text(cur_rtl,encoding="utf-8")
    synth_res = self._stage_synth(str(snap_dir/"rtl_snapshot.v"), run_id, iter_n)
    synth_ok = synth_res.is_ok() and synth_res.parsed.get("success") is True
    sim_res = self._stage_sim(str(snap_dir/"rtl_snapshot.v"), tb_path, run_id, iter_n)
    num_passed = sim_res.parsed.get("num_passed") or 0; total = sim_res.parsed.get("total") or 0
    required = total if goal.pass_mode=="all" else goal.num_required
    sim_ok = (sim_res.parsed.get("passed") is True) or (num_passed>=required)
    sta_res=None
    if goal.sta_required and lib and clock and synth_ok:
      netlist = <从 synth_res.artifacts 解析 netlist.v 路径>
      sta_res = self._stage_sta(netlist, lib, clock, run_id, iter_n)
    sta_ok = sta_res is None or (sta_res.parsed.get("wns") is not None and sta_res.parsed["wns"]>=0)
    trajectory 添加 {"step":"stage","iter":iter_n,"detail":f"synth={synth_ok} sim={sim_ok}/{num_passed}/{required} sta={sta_ok}"}
    if synth_ok and sim_ok and sta_ok:
      convergence="all_pass"; best_iter=iter_n; best_rtl_text=cur_rtl; break
    if best_score>0 and num_passed<best_score: num_passed_dropped_streak+=1
    else: num_passed_dropped_streak=0
    if num_passed>best_score and num_passed>0:
      best_score=num_passed; best_iter=iter_n; candidates_at_best=1; best_rtl_text=cur_rtl
    elif num_passed==best_score and num_passed>0: candidates_at_best+=1
    outcome = self._diagnose_and_patch([synth_res,sim_res]+([sta_res] if sta_res else []), cur_rtl, run_id, iter_n, seed if iter_n==0 else None, strategy, snap_dir)
    final_diag_parsed = outcome.diagnose_parsed or final_diag_parsed
    if outcome.patch_source!="none": last_patch_source=outcome.patch_source
    trajectory 添加 {"step":"patch","iter":iter_n,"detail":f"strategy={strategy} applied={outcome.applied} source={outcome.patch_source}"}
    if outcome.applied:
      # _diagnose_and_patch 已写 snap_dir/rtl_snapshot.v(rtl_new);这里 push rtl_new 到栈
      version_stack.append(<读 snap_dir/rtl_snapshot.v 的内容>)
      regression_streak=0
    else:
      regression_streak+=1
      next_strategy = {"diff":"full_rewrite","full_rewrite":"diagnose_only"}.get(strategy,"diagnose_only")
      if next_strategy=="diagnose_only":
        trajectory 添加 {"step":"reduced_to_diagnose","iter":iter_n}; convergence="regression"; break
      strategy=next_strategy
    if regression_streak>=3 or num_passed_dropped_streak>=3: convergence="regression"; break
  else:
    convergence = convergence if convergence!="none" else "max_iter"
  if convergence=="none": convergence = "max_iter" if best_iter>=0 else "none"
  # 收尾
  best_dir=base_dir/"best"; best_dir.mkdir(parents=True,exist_ok=True)
  (best_dir/"rtl.v").write_text(best_rtl_text,encoding="utf-8")
  (best_dir/"meta.json").write_text(json.dumps({"best_iter":best_iter,"num_passed":best_score if best_iter>=0 else -1,"convergence_cause":convergence,"candidates_at_best_score":candidates_at_best},ensure_ascii=False,indent=2),encoding="utf-8")
  (base_dir/"report.md").write_text(<渲染每轮一节 markdown>,encoding="utf-8")
  passed = convergence=="all_pass"
  status = "ok" if passed else ("budget_exhausted" if convergence=="budget" else "error")
  error_code = None if passed else (EDA_BUDGET_EXHAUSTED if convergence=="budget" else HEAL_REGRESSION_DEADLOCK)
  patch_source = last_patch_source
  fixed_ref = artifact_ref(run_id,"self_heal/best/rtl.v") if passed else None
  final_parsed = {"_schema":{"name":"skill_self_heal","version":"0.1.0","contract_version":CONTRACT_VERSION},"skill":"self_heal","passed":passed,"iterations":<实际轮数>,"best_iter":best_iter,"convergence_cause":convergence,"patch_source":patch_source,"fixed_rtl_ref":fixed_ref,"final_report_ref":artifact_ref(run_id,"self_heal/report.md"),"summary":f"convergence={convergence} best_iter={best_iter} num_passed={best_score}"}
  return SkillResult(status=status, iterations=<实际轮数>, final_parsed=final_parsed, trajectory=trajectory, artifacts=[artifact_ref(run_id,"self_heal/report.md"),artifact_ref(run_id,"self_heal/best/rtl.v"),artifact_ref(run_id,"self_heal/best/meta.json")], summary=final_parsed["summary"], patch_source=patch_source, convergence_cause=convergence, best_iter=best_iter, error_code=error_code, budget_used_s=float(monotonic()-t0))

_fail(run_id, code, hint):  # 输入校验失败,落 namespace=heal
  返回 SkillResult(status="error",iterations=0,final_parsed={"_schema":{...},"skill":"self_heal","passed":False,"iterations":0,"best_iter":-1,"convergence_cause":"none","patch_source":"none","fixed_rtl_ref":None,"final_report_ref":None,"summary":hint},trajectory=[{"step":"fail","iter":0,"detail":hint}],artifacts=[],summary=hint,patch_source="none",convergence_cause="none",best_iter=-1,error_code=code,budget_used_s=0.0)

== bootstrap.py 注册 skill_self_heal(T36) ==
build_registry 末尾(skill_diagnose 注册之后)补:
    from eda_agent.skills.self_heal import SelfHealSkill
    heal = SelfHealSkill(registry=registry, llm=provider, runner=runner,
        max_iterations=settings.skill_max_iterations, budget_s=float(settings.skill_self_heal_budget_s), settings=settings)
    registry.register(ToolEntry(tool=as_tool(heal), name="skill_self_heal", category="skill",
        schema=heal.schema["input_schema"], parsed_schema_ref={"name":"skill_self_heal","version":_SCHEMA_VERSION}))
注意:skill_self_heal 持 registry(调 L1 Tool + skill_diagnose);注册时 registry 必须已含 skill_diagnose(A 先注册)。

== best/meta.json 格式(T34) ==
{"best_iter":int, "num_passed":int, "convergence_cause":str, "candidates_at_best_score":int}
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
    seed_pattern_count: { type: "integer" },
    contract_decisions: { type: "array", items: { type: "string" }, description: "遇到的契约歧义及裁决" },
    pytest_target: { type: "string", description: "专项单测结果" },
    pytest_full: { type: "string", description: "全量 pytest 末尾几行" },
    errors: { type: ["string", "null"] },
  },
  required: ["files_created", "files_modified", "key_classes", "pytest_full"],
}

const VERDICT_SCHEMA = {
  type: "object",
  properties: {
    verdict: { type: "string", enum: ["pass", "fail"] },
    blockers: {
      type: "array",
      items: {
        type: "object",
        properties: {
          severity: { type: "string", enum: ["critical", "major", "minor"] },
          file: { type: "string" },
          line: { type: ["string", "null"] },
          issue: { type: "string" },
          fix: { type: "string" },
        },
        required: ["severity", "file", "issue", "fix"],
      },
    },
    passed_checks: { type: "array", items: { type: "string" } },
    notes: { type: "string" },
  },
  required: ["verdict", "blockers", "notes"],
}

const TEST_SCHEMA = {
  type: "object",
  properties: {
    tests_written: { type: "array", items: { type: "string" } },
    pytest_result: { type: "string", description: "pytest 末尾 summary 行" },
    passed_count: { type: "integer" },
    failed_tests: { type: "array", items: { type: "string" } },
    skipped_eda: { type: "array", items: { type: "string" } },
    notes: { type: "string" },
  },
  required: ["tests_written", "pytest_result", "passed_count"],
}

// ─────────────────────────────────────────────────────────────────────
// A 阶段
// ─────────────────────────────────────────────────────────────────────
phase('A: 实现诊断器')
const aImpl = await agent(
  CONTEXT + "\n" + A_CONTRACT + "\n\n== 你的任务(T20-T26 全部)==\n实现 A 诊断器。产出文件:\n1. src/eda_agent/skills/diagnose.py — ErrorPattern, ErrorKB, DiagnosisReport, compute_confidence, rule_match, _build_error_item, LLMAttributor, DiagnoseSkill, ReportWriter, _jaccard, _max_severity, _extract_json, _validate\n2. src/eda_agent/skills/prompts_diagnose.py — build_diagnose_prompt(errors, context) -> list[Message]\n3. data/error_kb.json — ≥10 条种子(source=seed),覆盖 synth/sim/sta;每条 example_log 自检 re.search 命中\n4. src/eda_agent/tools/bootstrap.py — 注册 skill_diagnose(T26,在 L2 Skill 段)\n5. tests/test_build_registry.py — 更新断言:names 含 skill_diagnose,共 4 个 tool;新增 skill_diagnose 注册断言\n6. tests/test_diagnose_skill.py — T29 覆盖:args 校验(空 tool_results → ok+空 root_causes)、rule_match 命中、LLM mock(FakeLLMProvider)、compute_confidence 饱和、contradiction(Jaccard)、as_tool 暴露(isinstance Tool)、ErrorKB add_case 去重 + save/load 往返、DiagnosisReport.to_parsed 13 字段、_schema.contract_version==CONTRACT_VERSION 无裸串、A 恒定语义 best_iter==-1\n\n== 实现顺序 ==\n先 Read 核对:src/eda_agent/contracts.py, errors.py, skills/base.py, runner.py, settings.py, tools/bootstrap.py, tools/yosys_synth.py(Tool 范式), registry.py(ToolEntry/ToolRegistry)。\n再实现 diagnose.py(全部类/函数)→ prompts_diagnose.py → error_kb.json → bootstrap.py 注册 → 更新 test_build_registry.py → 写 test_diagnose_skill.py。\n\n== 验收 ==\n- cd '/d/eda agent system' && python -m pytest tests/test_diagnose_skill.py tests/test_build_registry.py -q → 全绿\n- cd '/d/eda agent system' && python -m pytest tests/ -q → 78 + 新增全绿(不破坏现有)\n- 自检 error_kb.json:python -c \"import json,re; kb=json.load(open('data/error_kb.json')); [print(p['pid'], bool(re.search(p['regex'], p['example_log']))) for p in kb['patterns']]\"(全部 True)\n\n返回 schema。errors 字段填实现中遇到的问题(无则 null)。",
  { schema: IMPL_SCHEMA, phase: 'A: 实现诊断器', label: 'A-impl' }
)

phase('A: 对抗验证')
const aVerdicts = await parallel([
  () => agent(
    CONTEXT + "\n" + A_CONTRACT + "\n\n== 你的任务:契约合规对抗验证(只读,不改代码)==\nRead src/eda_agent/skills/diagnose.py + prompts_diagnose.py + data/error_kb.json + tools/bootstrap.py,逐项检查(失败=critical/major blocker):\n1. skill_diagnose.parsed 13 个 key 齐全\n2. _schema 用 CONTRACT_VERSION 常量(禁止裸 0.1.0)\n3. confidence 公式精确:0.5 + 0.1*min(len(evidence),5) - 0.2*has_contradiction,clamp[0,1]\n4. needs_rtl_patch 按 severity 判(any error/fatal),禁止按 namespace 前缀\n5. contradiction:仅 LLM 层触发时检查,Jaccard<0.3\n6. A 恒定语义:SkillResult patch_source=none/convergence_cause=none/best_iter=-1/iterations=1\n7. artifacts 元素全是 artifact_ref(...) 返回(无裸 str/dict)\n8. ErrorItem 7 字段齐全(root_causes 里每条 dict)\n9. ErrorKB.load 解析失败降级空 KB(不抛)\n10. RuleLayer 零 LLM\n11. LLMAttributor JSON 解析失败降级((LLM parse failed))\n12. bootstrap 注册 skill_diagnose(category=skill,as_tool 包装)\n13. data/error_kb.json ≥10 条,每条 example_log 被 regex 命中\n14. 无重定义 contracts/errors 已有类型\n返回 verdict + blockers(精确 file:line + fix) + passed_checks。",
    { schema: VERDICT_SCHEMA, phase: 'A: 对抗验证', label: 'A-verify-contract' }
  ),
  () => agent(
    CONTEXT + "\n" + A_CONTRACT + "\n\n== 你的任务:逻辑/边界对抗验证(只读)==\nRead src/eda_agent/skills/diagnose.py,检查:\n1. _collect_logs:正确读 runs/<run_id>/steps/<idx>_*/<tool>.full.log(fallback stdout);tool_results 为空/缺字段时不崩\n2. 9 步主流程顺序正确,trajectory 每步追加\n3. confidence 饱和:evidence≥5 不再单调增\n4. _jaccard 实现正确(空集/单边空处理;|A 交 B|/|A 并 B|)\n5. _max_severity 正确(info<warn<error<fatal;空→info)\n6. _build_error_item:fix_hint_template.format 异常时降级(占位符缺失不崩)\n7. propose_pending 写盘路径 + uuid8 生成\n8. ReportWriter 落盘 report.md/report.json\n9. kb.snapshot_to 落盘\n10. budget 检查:超 deadline 跳过 LLM\n11. 入参校验:tool_results 非空 list,每条 dict 含 tool+parsed;不符→ok+空 root_causes(不抛)\n12. 任何边界(空 errors/空 evidence/LLM 返回非 JSON/KB 空模式)不抛异常\n返回 verdict + blockers + passed_checks。逻辑漏洞=critical/major。",
    { schema: VERDICT_SCHEMA, phase: 'A: 对抗验证', label: 'A-verify-logic' }
  ),
])

phase('A: 单测')
const aTest = await agent(
  CONTEXT + "\n" + A_CONTRACT + "\n\n== 你的任务:补全 A 单测并跑绿 ==\n如果 A-impl 已写 tests/test_diagnose_skill.py,Read 它并补强覆盖;否则新建。\n必须覆盖(T29):\n- args 校验:空 tool_results → status=ok + root_causes==[](不抛)\n- rule_match 命中:喂 synth 日志 → 命中 ErrorItem\n- LLM mock:FakeLLMProvider(chat 返回固定 JSON)触发 LLM 层 → root_cause 来自 LLM\n- compute_confidence 饱和:evidence 长度 0/3/5/10 四组,断言 len≥5 时 confidence 不再增\n- contradiction:LLM root_cause 与 errors[0].message Jaccard<0.3 → confidence 减 0.2\n- as_tool 暴露:as_tool(diag) isinstance Tool;__call__ 返回 ToolResult,parsed 含 _skill_* 元字段\n- ErrorKB:add_case 去重;save/load 往返一致\n- DiagnosisReport.to_parsed:13 key 齐全,root_causes 是 list[dict]\n- A 恒定语义:best_iter==-1, patch_source==none, convergence_cause==none, iterations==1\n- _schema 无裸串:contract_version 引用 CONTRACT_VERSION\n\n跑 cd '/d/eda agent system' && python -m pytest tests/test_diagnose_skill.py -v 2>&1 | tail -40,确认全绿。\n失败则 Read 源文件诊断,修源文件(优先)或修测试。改完重跑直到绿。\n返回 tests_written/pytest_result/passed_count/failed_tests。",
  { schema: TEST_SCHEMA, phase: 'A: 单测', label: 'A-test' }
)

// ─────────────────────────────────────────────────────────────────────
// B 阶段(A 完成后)
// ─────────────────────────────────────────────────────────────────────
phase('B: 实现自修复')
const bImpl = await agent(
  CONTEXT + "\n" + B_CONTRACT + "\n\n== 前置:A 诊断器已实现 ==\nsrc/eda_agent/skills/diagnose.py + bootstrap 已注册 skill_diagnose。Read 了解 skill_diagnose 调用方式(registry.get(skill_diagnose)(ToolCall(...)))。\n\n== 你的任务(T30-T37 全部)==\n实现 B 自修复。产出文件:\n1. src/eda_agent/skills/self_heal.py — HealGoal, PatchOutcome, IterSnapshot, SelfHealSkill(_parse_goal, _stage_synth/_sim/_sta, _diagnose_and_patch, _locate_lines, _build_prompt, _extract_patch, _apply_patch, _syntax_check, run 主循环, _fail, _render_report)\n2. src/eda_agent/tools/bootstrap.py — 在 skill_diagnose 注册之后补 skill_self_heal(T36)\n3. tests/test_build_registry.py — 再更新断言:共 5 个 tool(含 skill_self_heal)\n4. tests/test_self_heal_as_tool_schema.py — as_tool args schema 9 字段(T36)\n5. tests/test_self_heal_skill.py — T35(B6:rollback/budget_exhausted/regression_deadlock/best_iter_tiebreak/patch_source_on_failure/consume_diagnose_fix_suggestion/fail_signals_wrapping)+ T37 e2e(test_run_pass/test_trace_integrity,@needs_eda 真跑 counter_bitwidth bug)\n\n== 实现顺序 ==\n先 Read:skills/diagnose.py, skills/base.py, contracts.py, runner.py, tools/iverilog_sim.py(_to_wsl_path + WSL subprocess 范式,供 _syntax_check 参考), tools/bootstrap.py。\n再实现 self_heal.py → bootstrap 注册 → test_build_registry 更新 → test_self_heal_as_tool_schema → test_self_heal_skill。\n\n== 关键注意 ==\n- _syntax_check 用 wsl iverilog -t null -o /dev/null(参考 iverilog_sim.py 的 WSL 调用 + encoding=utf-8,errors=replace)\n- skill_self_heal 持 registry(调 L1 Tool + skill_diagnose);bootstrap 注册时 registry 必须已含 skill_diagnose\n- e2e 测试用 data/examples/counter_bitwidth/ 的 bug RTL(iverilog 真跑,@needs_eda + skipif)\n- 失败也写 best/rtl.v + best/meta.json(best_iter 可 -1) + report.md\n- fail_signals 喂 LLM 前必须包装成 ErrorItem(禁止裸字符串列表)\n\n== 验收 ==\n- cd '/d/eda agent system' && python -m pytest tests/test_self_heal_skill.py tests/test_self_heal_as_tool_schema.py tests/test_build_registry.py -q → 全绿(允许 @needs_eda 真跑项 skipif)\n- cd '/d/eda agent system' && python -m pytest tests/ -q → 整体绿\n\n返回 schema。contract_decisions 填契约裁决。",
  { schema: IMPL_SCHEMA, phase: 'B: 实现自修复', label: 'B-impl' }
)

phase('B: 对抗验证')
const bVerdicts = await parallel([
  () => agent(
    CONTEXT + "\n" + B_CONTRACT + "\n\n== 你的任务:契约合规对抗验证(只读)==\nRead src/eda_agent/skills/self_heal.py + tools/bootstrap.py,逐项检查:\n1. final_parsed 业务字段:_schema/skill/passed/iterations/best_iter/convergence_cause/patch_source/fixed_rtl_ref/final_report_ref/summary\n2. _schema 用 CONTRACT_VERSION 常量\n3. as_tool args schema 9 字段(rtl/tb/diagnose/max_iter/goal/lib/clock/top_module + reserved)\n4. artifacts 元素全是 artifact_ref(...)\n5. fail_signals 喂 LLM 前包装成 ErrorItem(code=sim.fail_signal)(禁止裸字符串列表)\n6. 三策略阶梯 diff→full_rewrite→diagnose_only\n7. 版本栈:仅 patch applied+syntax ok 时 push\n8. best_iter tie-break 取最早达到 best_score 的轮\n9. convergence_cause 5 值(all_pass/max_iter/regression/budget/none)\n10. status 三态映射:all_pass→ok,budget→budget_exhausted(error_code=eda.budget_exhausted),其余→error\n11. error_code 规范码(heal.*/eda.budget_exhausted)\n12. best/meta.json 4 字段\n13. bootstrap 注册 skill_self_heal(category=skill,registry 注入)\n14. SkillResult 字段值与 final_parsed 一致(patch_source/convergence_cause/best_iter)\n返回 verdict + blockers + passed_checks。",
    { schema: VERDICT_SCHEMA, phase: 'B: 对抗验证', label: 'B-verify-contract' }
  ),
  () => agent(
    CONTEXT + "\n" + B_CONTRACT + "\n\n== 你的任务:逻辑/边界对抗验证(只读)==\nRead src/eda_agent/skills/self_heal.py,检查:\n1. _parse_goal:三个模板(pass all/pass N/timing)+ 无法解析返回 None\n2. _stage_synth/_sim/_sta:ToolCall caller=self_heal,append_step skill_name+iter 正确\n3. _diagnose_and_patch:首轮用 seed 跳过内部调用;非首轮内部调 skill_diagnose\n4. _locate_lines:三种正则适配 yosys/iverilog 行号;都没命中→None\n5. _apply_patch:diff 应用失败→applied=False;full_rewrite 替换全文\n6. _syntax_check:iverilog -t null,WSL 调用,encoding=utf-8,失败/超时→False 不抛\n7. 主循环:预算检查(超→budget break);regression_streak/num_passed_dropped_streak 任一≥3→regression break;max_iter 自然结束\n8. best_score/best_iter 更新逻辑正确(num_passed>best_score 才更新;==只增 candidates_at_best)\n9. 收尾:best_iter<0 时 best/rtl.v 写原版;best/meta.json num_passed=-1\n10. sta 跳过:lib/clock None 或 sta_required False → sta_res=None,sta_ok=True\n11. 边界:RTL/TB 不存在→_fail;goal 无法解析→_fail;skill_diagnose error→diag_parsed={} 降级\n12. 死锁保护触发后主动停机(convergence=regression)\n返回 verdict + blockers + passed_checks。",
    { schema: VERDICT_SCHEMA, phase: 'B: 对抗验证', label: 'B-verify-logic' }
  ),
])

phase('B: 单测')
const bTest = await agent(
  CONTEXT + "\n" + B_CONTRACT + "\n\n== 你的任务:补全 B 单测并跑绿 ==\n如果 B-impl 已写 tests/test_self_heal_skill.py + test_self_heal_as_tool_schema.py,Read 补强;否则新建。\n必须覆盖:\n- as_tool schema:test_self_heal_as_tool_schema.py,args 9 字段,required 含 rtl/tb/max_iter/goal\n- test_rollback:patch 应用失败→策略升级(diff→full_rewrite),版本栈不 push\n- test_budget_exhausted:预算耗尽→convergence=budget,status=budget_exhausted,error_code=eda.budget_exhausted\n- test_regression_deadlock:连续 3 轮 num_passed 下降→convergence=regression 主动停机\n- test_best_iter_tiebreak:两轮同 num_passed→best_iter 取最早,candidates_at_best=2\n- test_patch_source_on_failure:全程失败→patch_source=最后尝试的 source(或 none)\n- test_consume_diagnose_fix_suggestion:seed diagnose 注入→首轮跳过内部 skill_diagnose 调用\n- test_fail_signals_wrapping:sim fail_signals → 包装成 ErrorItem(code=sim.fail_signal) 喂 LLM\n- _schema.contract_version==CONTRACT_VERSION 无裸串\n用 FakeLLMProvider + mock registry(假 Tool 返回固定 ToolResult)跑纯逻辑测试(不需真 EDA)。\n\nT37 e2e(@needs_eda,可 skipif):\n- test_stage_tools:真跑 yosys+iverilog,断言 ToolResult 返回\n- test_run_pass:用 data/examples/counter_bitwidth/ 的 bug RTL + tb,真跑 SelfHealSkill.run,断言 passed=True 或 best_iter≥0\n- test_trace_integrity:断言 runner RunRecord.steps 含 self_heal 子步(skill_name=self_heal,iter 递增)\n\n跑 cd '/d/eda agent system' && python -m pytest tests/test_self_heal_skill.py tests/test_self_heal_as_tool_schema.py -v 2>&1 | tail -50。\n失败则 Read 源文件诊断修复,重跑直到绿(e2e 真跑项允许 skipif)。\n返回 tests_written/pytest_result/passed_count/failed_tests/skipped_eda。",
  { schema: TEST_SCHEMA, phase: 'B: 单测', label: 'B-test' }
)

return {
  A: { impl: aImpl, verdicts: aVerdicts, test: aTest },
  B: { impl: bImpl, verdicts: bVerdicts, test: bTest },
}
