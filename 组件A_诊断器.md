# 组件 A:Agentic EDA 诊断器设计文档

> 文档版本:A_diagnoser doc v1.2(对齐 CONTRACTS.md,CONTRACT_VERSION=0.1.0)
> 角色:日志 → 归因 + 修复建议 的诊断 skill,注册为 Tool(Registry name = `skill_diagnose`),位于 L2 Skills 层。
> 最高约束:本文件所有 dataclass 字段、错误码、artifact 路径、parsed schema 严格对齐 `CONTRACTS.md` v1.2。冲突以契约为准(契约 §13)。
> 代码约定:Python dataclass / type hint,4 空格缩进,禁用反引号代码块。所有 `contract_version` 引用必须 `from eda_agent.contracts import CONTRACT_VERSION`,禁止裸字符串。

---

## 1. 定位与职责边界

### 1.1 做什么(职责)

A 诊断器是一个"日志归因 skill",消费上游 EDA 工具的 `ToolResult`(Yosys 综合 / iverilog 仿真 / OpenSTA 时序,以及它们的组合),产出结构化的 `DiagnosisReport`:

- 把工具日志里散落的错误(语法错行号、未定义信号、多驱动、端口未连、综合 warning、仿真 mismatch、时序 negative slack 等)抽取成 `ErrorItem`(契约 §2.6)。
- 给出 root_cause(根因)、severity、fix_hints(修复建议)、confidence(置信度)。
- 落 `runs/<run_id>/diagnose/report.md` 作为可被 B 自修复闭环消费的诊断工件(artifact_ref 协议,契约 §2.4)。

A 的"智能"分两层,缺一不可:

- 第 1 层(规则/正则层):`ErrorKB` 错误模式库做结构化抽取。常见错误秒出结果,零 LLM 调用,可复现。
- 第 2 层(LLM 归因层):规则抽不出 / 需跨条推理时,把规则层结构化错误 + 上下文喂 LLM,产 root_cause + fix_hints。

### 1.2 不做什么(边界)

- **不修改 RTL**:A 只输出修复建议(fix_hints),真正改 RTL 是 B 自修复闭环的职责。A 里的 fix_hints 是给 B 的 patch 提示,不是 patch 本身。
- **不直接跑 EDA 工具**:A 不调用 yosys/iverilog/opensta,只消费它们的 `ToolResult`。它没有重新跑综合/仿真的权限,避免与 B/C 的工具编排职责重叠。
- **不做迭代**:A 是一次性归因(吃一次 tool_results → 出一份 report)。是否要带着 report 重新跑工具、迭代收敛,是 B/C 的事。A 的 SkillResult.iterations 在 MVP 恒为 1(契约 §2.3 字段保留,语义不滥用)。
- **不做多模态**:MVP 不读波形 VCD 截图,只读文本日志(契约 §2.5 边界声明)。

### 1.3 与其它组件的边界

    组件   给 A 的输入             A 给它的输出               边界约定
    ─── ────────────────── ──────────────────────── ─────────────────────────────────
    C    tool_results 列表       DiagnosisReport(ToolResult 形态)  C 决定何时调 A;A 不主动请求工具
    B    A 的 report(经 artifact_ref)  fix_hints 指导 patch        B 调 A 是其迭代内一步,见契约 §5
    Yosys/iverilog/OpenSTA   它们的 ToolResult  无                        A 只读,不写它们的 artifact

---

## 2. 在整体系统中的位置

### 2.1 调用关系

A 注册为 Tool(`skill_diagnose`),既可被 C(L4 Planner)直接调用,也可被 B(L2 自修复 skill)在迭代内调用。两种入口走同一个 `Skill.run` 实现。

- C → A:plan-execute 循环中,若综合/仿真/时序任一非 ok,C 调 `skill_diagnose` 拿归因,再决定下一步(典型:调 `skill_self_heal`)。见契约 §5 第 4 步。
- B → A:B 的每轮迭代内,综合+仿真后调 `skill_diagnose` 拿到本轮 fix_hints,再让 LLM 产 patch。A 是 B 迭代闭环里的"眼睛"。

### 2.2 调用关系图

    ┌──────────────────────────────────────────────────────────────────┐
    │ L5  CLI  eda self-heal / eda report                              │
    └──────────────────────────────┬───────────────────────────────────┘
                                   │ RunRequest
    ┌──────────────────────────────▼───────────────────────────────────┐
    │ L4  C  CPlanner(plan-execute)                                    │
    │      ┌─────────────────────────────────────────────────────┐     │
    │      │ Registry.get("yosys_synth")    ──┐                   │     │
    │      │ Registry.get("iverilog_sim")    ──┤  tool_results    │     │
    │      │ Registry.get("opensta_timing")  ──┤                  │     │
    │      │                                  ▼                   │     │
    │      │                Registry.get("skill_diagnose") ◀──┐   │     │
    │      │                          │ A                     │   │     │
    │      │                          ▼ DiagnosisReport       │   │     │
    │      │                Registry.get("skill_self_heal")    │   │     │
    │      │                          │ B                      │   │     │
    │      │             ┌────────────┴───────────┐            │   │     │
    │      │             │ B 内部每轮迭代调 A ◀────┼────────────┘   │     │
    │      │             │ (synth→sim→diagnose→patch)             │     │
    │      │             └────────────────────────┘               │     │
    │      └─────────────────────────────────────────────────────┘     │
    └──────────────────────────────────────────────────────────────────┘
                 │ L0 落盘 runs/<run_id>/{steps/,skills/diagnose/report.md}

### 2.3 在契约分层里的坐标

- 层级:L2 Skills(契约 §1)。
- Registry name:`skill_diagnose`(契约 §1 对照表)。
- 实现文件:`src/eda_agent/skills/diagnose.py`(契约 §4 目录树)。
- skill 文档落点:`docs/A_diagnoser.md`(本文件副本,交付时拷一份到 docs/)。

---

## 3. 内部架构(子模块拆分 + 数据流图)

### 3.1 子模块

`diagnose.py` 内部按"两层架构 + 知识库 + 编排"拆 4 个协作类:

    class DiagnoseSkill(Skill)            # 对外交互:实现 Skill.run / as_tool,持有 self._llm + self._runner
        ├── class RuleLayer               # 第 1 层:正则/规则抽取,零 LLM
        │     └── method match(log_text, tool) -> list[ErrorItem]
        ├── class LLMAttributor           # 第 2 层:喂规则结果 + 上下文给 LLM 产 root_cause/fix_hints
        │     └── method attribute(errors, context) -> (root_cause, fix_hints, needs_patch)
        ├── class ErrorKB                 # 错误模式知识库(可积累,持久化 error_kb.json)
        │     ├── method load(path) / save(path)
        │     ├── method lookup(tool, log_text) -> list[tuple[ErrorPattern, Match]]
        │     └── method add_case(pattern: ErrorPattern)  # 增长入口
        └── class ReportWriter            # 落 diagnose/report.md + 装配 DiagnosisReport
    # 注:DiagnoseSkill 不用 @dataclass(它持有 kb/llm/runner/settings,是普通类);
    #     max_iterations / budget_s 作为实例属性在 __init__ 设(非 ClassVar 非 dataclass field)。

### 3.2 数据流图

    tool_results: list[ToolResult]
        │
        ▼
    ┌─────────────────────────────────────────────────────────────┐
    │ DiagnoseSkill.run()                                         │
    │   1. 抽取日志文本(优先读 .full.log,契约 §2.1)            │
    │   2. RuleLayer.match(log, tool) per tool_result             │
    │        └─ ErrorKB.lookup() 命中模式 → 直接产 ErrorItem      │
    │   3. 聚合 errors: list[ErrorItem]                           │
    │   4. 若 errors 为空 或 需跨条推理 → LLMAttributor.attribute │
    │        └─ LLM 出 root_cause + fix_hints(needs_patch)        │
    │   5. 计算 confidence(契约 §2.2 公式,写死)                 │
    │   6. ReportWriter 产 report.md + DiagnosisReport            │
    └──────────────────────────┬──────────────────────────────────┘
                               ▼
    SkillResult(
        status="ok",
        iterations=1,                       # A 不迭代,恒为 1
        final_parsed=<DiagnosisReport.parsed>,   # 含 _schema + 契约 §2.2 skill_diagnose schema
        trajectory=[{"step":"rule","iter":0},{"step":"llm","iter":0}],
        artifacts=[artifact_ref("diagnose/report.md")],
        summary="...",
    )
        │
        ▼ as_tool(契约 §2.3 映射表)
    ToolResult(status="ok", parsed=<final_parsed + _skill_*>, artifacts=[...])

### 3.3 两层触发规则(决定走规则层还是 LLM 层)

- 默认先跑规则层 RuleLayer.match,产出 errors。
- 满足任一条件触发 LLM 层:
  (a) 规则层 errors 为空,但 tool_result.status != "ok"(说明真有错,规则没覆盖)。
  (b) 规则层命中错误,但来自 >1 个不同 tool_result(跨工具归因,需 LLM 推理因果)。
  (c) tool_result.parsed 含 `_needs_attribution` 标记(由 EDA Tool 在无法结构化时主动置位)。
- LLM 层输出会被回灌 ErrorKB(见 §4.4 增长机制):若 LLM 找到一个新模式,提示人类确认后入库。

---

## 4. 核心数据结构(Python dataclass 伪代码)

所有 dataclass 放 `src/eda_agent/skills/diagnose.py`,复用契约 §2 的 `ErrorItem / ToolResult / SkillResult / artifact_ref`,**禁止重新定义同名结构**(契约 §2 顶部声明)。

### 4.1 ErrorPattern(ErrorKB 单条规则)

    @dataclass
    class ErrorPattern:
        pid: str                              # 模式 id,如 "synth.multidrive_001"
        tool: str                             # 适用 Tool name,如 "yosys_synth";"*" 表示通配
        error_code: str                       # 二段式 namespace.code(契约 §2.6),如 "synth.multi_driver"
        severity: Literal["info","warn","error","fatal"]   # 契约 §2.6 severity 四态
        regex: str                            # 匹配日志的正则(命名分组抽取行号/信号名)
        extractor: str                        # python 表达式或字段映射说明,描述如何从 match.groupdict() 填 ErrorItem
        fix_hint_template: str                # 修复建议模板,支持 {placeholder}
        example_log: str                      # 一行真实日志样例(回放测试 + 人工对照)
        source: Literal["seed","llm_curated","human"]   # 模式来源:种子/LLM 提议后人工确认/纯人工
        hit_count: int = 0                    # 命中次数(用于评测 ErrorKB 价值,可读不强制)
        added_at: str = ""                    # ISO8601,入库时间

### 4.2 ErrorKB(知识库)

    @dataclass
    class ErrorKB:
        patterns: list[ErrorPattern]
        version: str = "0.1.0"
        path: str = "data/error_kb.json"      # 持久化位置(相对项目根)

        def load(path: str) -> "ErrorKB": ...                # 从 json 反序列化
        def save(path: str | None = None) -> None: ...       # 写回 json(原子写:tmp+rename)
        def lookup(self, tool: str, log_text: str) -> list[tuple[ErrorPattern, re.Match]]: ...
            # 对 tool 匹配的 pattern 逐条 re.search,返回 (pattern, match) 列表
        def add_case(self, pattern: ErrorPattern, dedupe: bool = True) -> bool: ...
            # 入库;dedupe=True 时按 (tool, error_code, regex) 去重,重复返回 False
        def suggest_from_llm(self, llm_output: dict, tool: str) -> ErrorPattern | None: ...
            # 把 LLM 自由文本里抽出的"新错误描述+建议正则"装成 ErrorPattern(source="llm_curated")
            # 仅构造对象,不自动入库;需 propose_pending 里人工 confirm 才 add_case(见 §4.4)

### 4.3 DiagnosisReport(A 对外的主产物)

注意:契约 §2.2 的 `skill_diagnose.parsed` 是 DiagnosisReport 序列化后的 dict 形态。DiagnosisReport 是强类型包装,`to_parsed()` 产出契约要求的 dict。

    @dataclass
    class DiagnosisReport:
        tool: str                             # 主诊断对象(yosys_synth / iverilog_sim / opensta_timing / "multi")
        stage: Literal["synth","sim","sta","multi"]   # 失败阶段;multi=跨阶段
        errors: list[ErrorItem]               # 结构化错误列表(契约 §2.6 ErrorItem 形状)
        root_cause: str                       # 一句话根因(规则层抽不出时由 LLM 给)
        severity: Literal["info","warn","error","fatal"]   # 取 errors 中最严重的;空则 info
        fix_hints: list[str]                  # 修复建议列表(规则模板填充或 LLM 产出)
        confidence: float                     # 0..1,契约 §2.2 公式校准,见 §4.5
        evidence: list[str]                   # 证据片段(日志原文行,优先 .full.log)
        used_layers: Literal["rule","llm","rule+llm"]   # 走了哪些层(用于 §10 度量规则覆盖率)
        kb_hits: list[str]                    # 命中的错误码列表(ErrorItem.code,可追溯到错误分类;非 ErrorPattern.pid)

        def to_parsed(self) -> dict[str, Any]:
            # 序列化为契约 §2.2 skill_diagnose.parsed 形状(含强制 _schema 元字段);
            # contract_version 必须 from eda_agent.contracts import CONTRACT_VERSION,禁止裸字符串:
            from eda_agent.contracts import CONTRACT_VERSION
            return {
                "_schema": {"name": "skill_diagnose", "version": "0.1.0",
                            "contract_version": CONTRACT_VERSION},
                "skill": "diagnose",
                "tool": self.tool,
                "stage": self.stage,
                "root_causes": [asdict(e) for e in self.errors],   # 契约字段名 root_causes,装 ErrorItem.asdict(7 字段齐全)
                "root_cause_summary": self.root_cause,             # 辅助字段;权威结构化是 root_causes
                "severity": self.severity,
                "fix_hints": self.fix_hints,
                "confidence": self.confidence,
                "used_layers": self.used_layers,
                "kb_hits": self.kb_hits,
                "needs_rtl_patch": any(_needs_patch_for(e) for e in self.errors),   # 见 §4.5,按 severity 判
                "summary": self.root_cause,
            }

### 4.4 ErrorKB 增长机制(失败案例沉淀为系统能力)

机制分三档,保证"可积累但不会被 LLM 噪声污染":

    档位 1  seed         初始人工编写的种子模式(契约 §2.6 MVP 7 类对应种子)
    档位 2  llm_curated  LLM 在归因时发现新模式 → 写入 propose_pending/(json)
                         每天 review 一次,人工 confirm 后 source 改 human 并 add_case 入主库
    档位 3  human        从 runs/ 真实失败案例里手写模式(标注规范见 §5)

伪代码(在 DiagnoseSkill.run 末尾):

    if used_layers in ("llm","rule+llm") and llm_attributor.found_new_pattern:
        candidate = error_kb.suggest_from_llm(llm_raw_output, tool=tool)
        if candidate is not None:
            pending_dir = f"runs/{run_id}/diagnose/propose_pending/"
            write_json(pending_dir / f"{uuid4().hex[:8]}.json", asdict(candidate))
            report.note += " [proposed new ErrorKB pattern, pending review]"

人工 confirm 入库流程(文档化到 §9 步骤):

    1. 列出 propose_pending/*.json
    2. 对照 example_log 判断正则是否过宽/过窄
    3. 通过 → error_kb.add_case(pattern, dedupe=True);source 改 "human"
    4. 不通过 → 删该 json
    5. error_kb.save() 落 data/error_kb.json(进 git,作为系统沉淀)

### 4.5 needs_rtl_patch 派生与 confidence 计算口径(契约 §2.2 v1.2 写死,不接受 LLM 自填)

needs_rtl_patch 派生规则(v1.2 锁死,消除"按 namespace 前缀判恒 False"断路器):

    def _needs_patch_for(e: ErrorItem) -> bool:
        # A 消费的 error_code 实际是契约 §2.6 的 eda.* 别名(如 eda.rtl_syntax / eda.sim_assert_failed),
        # 不是 synth.*/sim.*/sta.* 前缀;故**按 severity 判**,不按 namespace 前缀判。
        return e.severity in ("error", "fatal")

    # needs_rtl_patch = any(_needs_patch_for(e) for e in errors)   # errors 为空时为 False
    # 单测必含:ErrorItem(code="eda.rtl_syntax", severity="error") → _needs_patch_for == True
    #         ErrorItem(code="eda.budget_exhausted", severity="warn") → _needs_patch_for == False

confidence 计算口径(v1.2 加饱和项 min(len(evidence),5),避免 6 条以上证据恒 1.0):

    def compute_confidence(evidence: list[str], has_contradiction: bool) -> float:
        # 契约 §2.2 v1.2 公式:clamp(0.5 + 0.1*min(len(evidence),5) - 0.2*has_contradiction, 0, 1)
        val = 0.5 + 0.1 * min(len(evidence), 5) - 0.2 * (1 if has_contradiction else 0)
        return max(0.0, min(1.0, val))

    # has_contradiction 触发条件(v1.2 锁死):LLM 层被触发且其 root_cause 与规则层
    # errors[0].message 的关键词 Jaccard < 0.3 时置 True(在 DiagnoseSkill.run 写死;
    # LLMAttributor 即使在 prompt 里要求给 confidence 也忽略其值)。
    # 单测必含:构造 evidence 长度 0/3/5/10 四组,断言长度>=5 时 confidence 不再单调增(饱和)。

---

## 5. 对外接口契约

### 5.1 函数签名(对齐契约 §2.3 Skill 基类 + §2.1 Tool)

A 实现契约 §2.3 的 `Skill` Protocol,经 `as_tool()` 暴露为 §2.1 的 `Tool`。三组件统一通过 Registry 取用,签名如下:

    class DiagnoseSkill:                       # implements Skill(契约 §2.3);普通类,非 @dataclass
        name: str = "skill_diagnose"
        description: str = "EDA 日志归因诊断:tool_results → root_cause + fix_hints"
        max_iterations: int = 1                # A 不迭代,硬约束恒 1(实例属性,__init__ 设)
        budget_s: float = 60.0                 # 单次诊断预算秒(从 settings.skill_diagnose_budget_s 读)

        def __init__(
            self,
            kb: ErrorKB,
            llm: LLMProvider,
            runner: "Runner",                  # v1.2:Skill 构造统一注入 runner(契约 §2.3)
            settings: Settings,
        ) -> None:
            self.kb = kb
            self._llm = llm                    # v1.2:命名统一为 self._llm(下划线表内部持有)
            self._runner = runner
            self.max_iterations = 1
            self.budget_s = settings.skill_diagnose_budget_s   # 默认 60

        def run(
            self,
            run_id: str,
            inputs: dict[str, Any],
            remaining_budget_s: float | None = None,
        ) -> SkillResult: ...
            # inputs 形状(对齐契约 §2.3 as_tool args schema):
            #   {"tool_results": list[dict]}
            #   每个 dict = 上游 ToolResult 的 asdict + step_idx:int + run_id:str(契约 §2.3 v1.2 强制)

        def as_tool(self) -> Tool: ...         # 契约 §2.3 映射表 + reserved 拆包,SkillResult → ToolResult

A 的 as_tool 恒定语义(v1.2 显式,消除 as_tool 字段语义空洞):A 不修复、不迭代,故
SkillResult.patch_source = "none"、convergence_cause = "none"、best_iter = -1(与契约 §2.3 dataclass
默认值一致,语义"无任何轮通过仿真",避免 C 误判 A 找到最佳轮)。as_tool 后这六个
`_skill_*` 字段(_patch_source/_convergence_cause/_best_iter/_skill_status/_iterations/_trajectory)
仍按契约 §2.3 映射表补齐,值符合 A 的恒定语义。

### 5.2 输入 schema(args,供 Registry/LLM 校验)

    skill_diagnose.as_tool args schema(契约 §2.3 v1.2):
    {
        "tool_results": [
            {
                "status": "ok|error|timeout",
                "exit_code": int | null,
                "stdout": str, "stderr": str,
                "parsed": {"_schema": {...}, ...},
                "artifacts": [{"run_id": str, "rel_path": str}],
                "tool": str, "error_code": str | null,
                "step_idx": int,            # v1.2 强制:由 B/C 调 A 时填,用于 _collect_logs 定位 .full.log
                "run_id": str               # v1.2 强制:同上
            }
        ]
    }
    # 校验规则(C 调用前 + A 内部入口双重校):
    #   - tool_results 非空 list
    #   - 每个元素必须有 tool(str)、parsed(dict,含 _schema)、step_idx(int)、run_id(str)
    #   - 不符 → 构造 SkillResult(status="ok", final_parsed={"root_causes":[]}, error_code=None)
    #     返回空 root_causes 让 B 拿到合法结构降级(契约 §2.6 A→B 消费契约第 4 条);
    #     或明确 error_code="diagnose.args_invalid" 时 B 走包装 fail_signals 降级路径

### 5.3 输出 schema(契约 §2.2 v1.2 skill_diagnose.parsed)

见 §4.3 `DiagnosisReport.to_parsed()`。要点(v1.2 字段集为强制完整集):

- 必含 `_schema: {name:"skill_diagnose", version:"0.1.0", contract_version: CONTRACT_VERSION}`(常量引用,禁裸字符串)。
- 必含 13 字段(含 _schema 元字段,12 业务字段):skill / tool / stage / root_causes(list[ErrorItem.asdict],7 字段齐全)/ root_cause_summary(str,辅助)/ severity / fix_hints / confidence / needs_rtl_patch(强制,派生见 §4.5)/ used_layers / kb_hits / summary。
- `root_causes` 是结构化权威(装 ErrorItem dict 列表);`root_cause_summary` / `summary` 仅人类可读辅助字段。B/C 读 root_causes 做判定,读 summary 仅入 report.md。
- `confidence` 走 §4.5 公式(含饱和项 + contradiction),不接受 LLM 自填。
- `needs_rtl_patch` 由 errors 派生:任一 ErrorItem.severity in ("error","fatal") 时为 True(§4.5)。

### 5.4 错误码(A 产出的错误码,v1.2 已正式登记于契约 §2.6 namespace 表)

A 既有 namespace = `diagnose`(v1.2 已正式登记于契约 §2.6 namespace 表,不再"沿用开放约定"):

    diagnose.no_error_found      # 工具失败但规则层+LLM 都没找出根因(severity=warn)
    diagnose.llm_call_failed     # LLM 层调用失败(规范码;eda.llm_call_failed 为兼容别名)
    diagnose.kb_corrupted        # error_kb.json 解析失败(降级为空 KB 继续跑,severity=warn)
    diagnose.args_invalid        # 输入不符 §5.2 schema(规范码;eda.tool_args_invalid 为兼容别名)

A 消费的错误码(产 ErrorItem 时填的 code)来自上游 Tool:**实际取契约 §2.6 的 eda.* 别名**(如 eda.rtl_syntax / eda.sim_assert_failed / eda.timing_violation),A 不发明上游工具的错误码,只归类。这也是 §4.5 needs_rtl_patch 改按 severity 判的原因。

### 5.5 副作用与产出工件

副作用(全部走 L0 工件存储,契约 §2.4):

    runs/<run_id>/diagnose/
        report.md                          # ReportWriter 产的人类可读诊断报告
        report.json                        # DiagnosisReport.to_parsed() 的 json 落盘(B 用)
        propose_pending/                   # LLM 提议的新 ErrorKB pattern(待人工 review)
            <uuid8>.json
        error_kb_snapshot.json             # 本次诊断用的 ErrorKB 版本快照(可复现)

artifact_ref(写入 ToolResult.artifacts):

    [
        {"run_id": <run_id>, "rel_path": "diagnose/report.md"},
        {"run_id": <run_id>, "rel_path": "diagnose/report.json"}
    ]

无内存外副作用:不修改上游 Tool 的任何 artifact;不修改 RTL/TB。

---

## 6. 关键流程伪代码

4 空格缩进,无反引号。`DiagnoseSkill.run` 主流程:

    def run(self, run_id, inputs, remaining_budget_s=None):
        budget = min(self.budget_s, remaining_budget_s or self.budget_s)
        deadline = monotonic() + budget
        trajectory = []

        # 1. 入参校验(契约 §5.2)
        tool_results = inputs.get("tool_results")
        if not isinstance(tool_results, list) or len(tool_results) == 0:
            return self._fail(run_id, "diagnose.args_invalid",
                              "tool_results missing or empty", trajectory)
        for tr in tool_results:
            if not isinstance(tr, dict) or "tool" not in tr or "parsed" not in tr:
                return self._fail(run_id, "diagnose.args_invalid",
                                  f"malformed tool_result: {tr}", trajectory)

        # 2. 抽日志文本(优先 .full.log,契约 §2.1)
        logs = self._collect_logs(run_id, tool_results)
        trajectory.append({"step": "collect_logs", "iter": 0,
                           "detail": f"{len(logs)} tool_results"})

        # 3. 规则层
        errors = []
        for tool_name, log_text in logs.items():
            for pattern, match in self.kb.lookup(tool_name, log_text):
                item = self._build_error_item(pattern, match, tool_name)
                errors.append(item)
        trajectory.append({"step": "rule", "iter": 0,
                           "detail": f"{len(errors)} errors from {len(self.kb.patterns)} patterns"})

        # 4. 决定是否触发 LLM 层(§3.3 三条件)
        any_failed = any(tr.get("status") != "ok" for tr in tool_results)
        cross_tool = len({tr["tool"] for tr in tool_results
                          if tr.get("status") != "ok"}) > 1
        needs_llm = (len(errors) == 0 and any_failed) or cross_tool

        root_cause = ""
        fix_hints = []
        used_layers = "rule"
        llm_found_new = False
        if needs_llm and monotonic() < deadline:
            used_layers = "llm" if not errors else "rule+llm"
            attrib = LLMAttributor(self._llm)
            root_cause, fix_hints, llm_found_new, raw = attrib.attribute(
                errors=[asdict(e) for e in errors],
                context={"tool_results": tool_results, "logs": logs},
                deadline=deadline,
            )
            trajectory.append({"step": "llm", "iter": 0,
                               "detail": f"root_cause len={len(root_cause)}"})
            if not root_cause and not errors:
                errors.append(self._no_error_found_item(tool_results))

        # 5. root_cause 回填:规则层命中但 LLM 未触发时,从 errors 推一句话
        if not root_cause and errors:
            root_cause = f"{len(errors)} 个 {errors[0].namespace} 类错误,首要: {errors[0].message}"
        if not fix_hints and errors:
            fix_hints = [e.fix_suggestion or "(no hint)" for e in errors]

        # 6. evidence + confidence(§4.5 写死)
        evidence = self._collect_evidence(errors, logs)
        has_contradiction = self._check_contradiction(errors, root_cause)
        confidence = compute_confidence(evidence, has_contradiction)

        # 7. LLM 提议新 pattern → propose_pending(不自动入库)
        if llm_found_new:
            cand = self.kb.suggest_from_llm(raw or {}, tool=self._primary_tool(tool_results))
            if cand:
                self._dump_propose_pending(run_id, cand)

        # 8. 装配 report + 落盘
        report = DiagnosisReport(
            tool=self._primary_tool(tool_results),
            stage=self._infer_stage(tool_results),
            errors=errors,
            root_cause=root_cause or "(no root cause identified)",
            severity=self._max_severity(errors),
            fix_hints=fix_hints,
            confidence=confidence,
            evidence=evidence,
            used_layers=used_layers,
            kb_hits=[e.code for e in errors if e.code],
        )
        ReportWriter().write(run_id, report, self.kb)
        self.kb.snapshot_to(run_id)

        # 9. 返回 SkillResult(对齐契约 §2.3)
        return SkillResult(
            status="ok",
            iterations=1,
            final_parsed=report.to_parsed(),
            trajectory=trajectory,
            artifacts=[
                artifact_ref(run_id, "diagnose/report.md"),
                artifact_ref(run_id, "diagnose/report.json"),
            ],
            summary=report.root_cause,
            error_code=None,
            budget_used_s=budget - max(0.0, deadline - monotonic()),
        )

规则层单条匹配构造 ErrorItem:

    def _build_error_item(self, pattern, match, tool_name):
        g = match.groupdict()
        message = pattern.fix_hint_template.format(**g) if g else pattern.fix_hint_template
        # 填 ErrorItem(契约 §2.6 字段)
        return ErrorItem(
            code=pattern.error_code,
            namespace=pattern.error_code.split(".")[0],
            tool=tool_name,
            severity=pattern.severity,
            message=message,
            evidence=[match.group(0)],
            fix_suggestion=pattern.fix_hint_template.format(**g) if g else pattern.fix_hint_template,
        )

LLMAttributor.prompt 构造要点(prompt 写在 `skills/prompts_diagnose.py`):

    system: "你是 EDA 诊断专家。给定结构化错误列表与工具日志,产出 JSON:
             {root_cause: str, fix_hints: [str], needs_patch: bool,
              new_pattern: {regex, error_code, fix_hint_template} | null}"
    user:   json.dumps({"errors": [...], "context": {"logs_tail_per_tool": ...}})
    强制 JSON 输出(temperature=0),解析失败回退 root_cause="(LLM parse failed)"

---

## 7. 与共享契约的对接

用到契约的哪些部分(逐条对照):

- §2.1 ToolResult / Tool Protocol:A 消费 tool_results(每个是 ToolResult 的 asdict),经 as_tool 暴露为 Tool。
- §2.2 skill_diagnose.parsed:A 的 `DiagnosisReport.to_parsed()` 严格产出该 schema(含 _schema 元字段、root_causes、confidence、needs_rtl_patch、summary)。
- §2.2 confidence 公式:A 在 §4.5 写死实现,覆盖 LLM 自填。
- §2.3 Skill Protocol + as_tool 映射表:A 实现 Skill.run,as_tool 按契约 §2.3 映射表把 SkillResult → ToolResult(_skill_status/_iterations/_trajectory 等补齐)。
- §2.4 工件存储 + artifact_ref:A 产物全落 runs/<run_id>/diagnose/,artifact_ref 协议(§5.5)。
- §2.5 LLMProvider:A 持有 provider(由 build_registry 注入,见 §12.minor4 约定),只调 provider.chat,不直接 import anthropic。CountingProvider 自动计数进 RunRecord。
- §2.6 ErrorItem + namespace.code:A 产出 ErrorItem(code 取上游 namespace 或新增 diagnose.* namespace);severity↔code 对应表沿用契约 §2.6。
- §2.6 namespace 登记:A 新增 `diagnose` namespace(开放约定,不改核心表)。
- §3 ToolRegistry:A 由 `eda_agent.tools.bootstrap`(文件 `tools/bootstrap.py`)的 `build_registry` 显式注册:registry.register(ToolEntry(tool=DiagnoseSkill(kb, provider, runner, settings).as_tool(), name="skill_diagnose", category="skill", ...))。
- §2.4 双层预算:A.run 接收 remaining_budget_s,内部 min(self.budget_s, remaining)。
- §2.1 stdout/stderr 裁剪:A 读证据优先 .full.log(契约 §2.1 明文)。

不依赖的部分:B 自修复逻辑(C 之外的迭代)、C 的 plan-execute、OpenAICompat provider(MVP 不做)。

---

## 8. 依赖

### 8.1 EDA 工具(A 不直接跑,但其产物形态依赖这些工具的输出格式)

A 不调用 EDA 工具,但 ErrorKB 的 regex 要对齐这些工具的日志格式。版本以契约 §6 settings 为准:

    yosys     建议版本 >=0.40(WSL2 apt 装;日志格式近年稳定,ERROR/WARNING 前缀一致)
    iverilog  建议版本 >=12.0(同上;TEST_PASS/TEST_FAIL 协议行由 TB 保证,见契约 §2.2)
    opensta   建议版本 >=2.3(negative slack 输出格式;violations 表格正则)

### 8.2 Python 库

    python>=3.10(契约 §6 pyproject)
    标准库:re, json, dataclasses, time.monotonic, uuid, pathlib
    第三方:无新增。复用契约 §6 dependencies(anthropic / jsonschema 已在)。
    注:A 不引 jinja2 等模板库,fix_hint_template 用 str.format 即可。

### 8.3 LLM

    provider: ClaudeProvider(MVP 唯一,契约 §2.5)
    model: claude-sonnet-4(契约 §6 settings.toml)
    temperature: 0.0(诊断要确定性)
    max_tokens: 2048(诊断输出短,省 token)

---

## 9. 实现步骤拆解

按依赖顺序拆为 10 步,每步带验证方法,完成即勾。

### 步骤 1:搭骨架 dataclass
任务:在 `src/eda_agent/skills/diagnose.py` 写 §4 全部 dataclass(ErrorPattern / ErrorKB / DiagnosisReport)+ compute_confidence。
依赖:`src/eda_agent/contracts.py`(契约 §2)必须先落地(属 Phase1 基座)。
验证方法:

    python -c "from eda_agent.skills.diagnose import DiagnosisReport; \
        r = DiagnosisReport(tool='yosys_synth', stage='synth', errors=[], \
        root_cause='x', severity='error', fix_hints=[], confidence=0.5, \
        evidence=[], used_layers='rule', kb_hits=[]); \
        p = r.to_parsed(); assert p['_schema']['name']=='skill_diagnose'; \
        assert p['confidence']==0.5; print('ok')"

### 步骤 2:写 ErrorKB + 持久化
任务:实现 ErrorKB.load/save/lookup/add_case/suggest_from_llm。落 `data/error_kb.json` 种子文件(空 patterns 数组起步)。
验证方法:

    kb = ErrorKB.load("data/error_kb.json")
    kb.add_case(ErrorPattern(pid="t1", tool="yosys_synth", error_code="synth.multi_driver",
                  severity="error", regex=r"ERROR: multiple drivers for (\w+)",
                  extractor="signal=group1", fix_hint_template="signal {signal} 多驱动,检查是否多处赋值",
                  example_log="ERROR: multiple drivers for sum_out", source="seed"))
    kb.save()
    kb2 = ErrorKB.load("data/error_kb.json")
    assert len(kb2.patterns) == 1
    print("ok")

### 步骤 3:写种子 ErrorPattern 库
任务:对照契约 §2.6 MVP 7 类错误码,写至少 10 条种子 ErrorPattern(覆盖 synth/sim/sta 三 namespace)。每条 example_log 取自真实日志或人工标注语料(§10 验收基线要求 20 条语料)。
覆盖范围(必含):

    synth.rtl_syntax        语法错(行号抽取)
    synth.multi_driver      多驱动(信号名抽取)— 见步骤 2 示例
    synth.undefined_signal  未定义信号
    synth.port_unconnected  端口未连
    sim.compile_failed      编译失败(iverilog)
    sim.assert_failed       断言失败(TEST_FAIL 协议行)
    sim.mismatch            仿真 mismatch(TEST_FAIL 信号行)
    sta.timing_violation    negative slack(WNS<0)
    eda.subprocess_timeout  子进程超时

验证方法:

    kb = ErrorKB.load("data/error_kb.json")
    assert len([p for p in kb.patterns if p.source=="seed"]) >= 10
    # 每条 example_log 必须能被自己的 regex 命中
    for p in kb.patterns:
        assert re.search(p.regex, p.example_log), f"regex miss: {p.pid}"
    print("ok")

### 步骤 4:写 RuleLayer
任务:实现 `RuleLayer.match(log_text, tool) -> list[ErrorItem]`,内部调 ErrorKB.lookup + 步骤 6 的 _build_error_item。
验证方法:用 5 条手工构造的日志喂 RuleLayer,断言产出的 ErrorItem 数量与 code 正确。写 `tests/test_diagnose_rule.py`(不需 needs_eda marker,纯字符串)。

### 步骤 5:写 LLMAttributor
任务:实现 §6 末尾 prompt 构造 + JSON 解析 + 失败回退。prompt 放 `skills/prompts_diagnose.py`。强制 JSON schema 校验(jsonschema)。
验证方法:用 mock LLMProvider(返回固定 JSON)跑通 attribute(),断言 root_cause/fix_hints 解析正确;再喂一个非法 JSON,断言回退到 "(LLM parse failed)"。

### 步骤 6:写 DiagnoseSkill.run 主流程
任务:拼装步骤 2-5 成 §6 主流程 + _collect_logs/_collect_evidence/_check_contradiction/_max_severity/_primary_tool/_infer_stage/_no_error_found_item/_fail/_dump_propose_pending 辅助方法。
验证方法:构造 2 个 mock tool_result(一个 yosys 错、一个 iverilog 错),跑 DiagnoseSkill.run,断言:

    SkillResult.status == "ok"
    SkillResult.iterations == 1
    "diagnose" in SkillResult.final_parsed["_schema"]["name"]
    len(SkillResult.artifacts) >= 2
    runs/<run_id>/diagnose/report.md 文件存在

### 步骤 7:写 ReportWriter
任务:把 DiagnosisReport 渲染成人类可读 markdown(根因表 + 错误表 + fix_hints + evidence 引用)。模板用纯字符串拼接,不引 jinja2。
验证方法:`cat runs/<run_id>/diagnose/report.md`,人眼可读,含 root_cause / 每个 ErrorItem / fix_hints 三段。

### 步骤 8:注册进 Registry + as_tool 适配
任务:在 `tools/bootstrap.py` 的 `build_registry(provider, runner, settings)`(import 路径 `eda_agent.tools.bootstrap`)加一行 `registry.register(ToolEntry(tool=DiagnoseSkill(kb, provider, runner, settings).as_tool(), name="skill_diagnose", category="skill", parsed_schema_ref={"name":"skill_diagnose","version":"0.1.0"}))`。验证 as_tool 后 ToolResult.parsed 含 _skill_status/_iterations/_trajectory。
验证方法:

    from eda_agent.tools.bootstrap import build_registry
    reg = build_registry(provider=mock_provider, runner=mock_runner, settings=...)
    t = reg.get("skill_diagnose")
    assert t is not None
    tr = t(ToolCall("skill_diagnose", {"tool_results":[<mock>]}))
    assert tr.parsed["_skill_status"] == "ok"
    assert tr.parsed["_iterations"] == 1
    print("ok")

### 步骤 9:语料 schema + 标注工具
任务:定义 §10.1 语料 schema,写 `scripts/build_corpus.py` 把 data/logs_corpus/ 散日志组装成 corpus.jsonl + 标注。附标注 SOP(§10.2)。
验证方法:跑 build_corpus,产出 data/logs_corpus/corpus.jsonl,行数 >= 20。

### 步骤 10:验收脚本 + 单测
任务:写 §10 的验收脚本 `scripts/eval_diagnose.py` + `tests/test_diagnose_skill.py`(覆盖接口单测 + ErrorKB 增长用例 + 语料 Top-1 命中率)。
验证方法:照 §10 门槛逐条跑,全绿。

以上步骤对应 Phase3 实现 + Phase4 联调。

---

## 10. 验收标准(量化指标 + 门槛 + 测试方法)

> 全部可被直接照搬执行。命令里的 <...> 占位由评审方填实际路径。

### 10.1 语料 schema 与标注规范

语料目录:

    data/logs_corpus/
        raw/                           # 原始日志(来源:runs/ 真实失败 + 公开 EDA issue)
            yosys_001.log
            iverilog_001.log
            ...
        corpus.jsonl                   # 标注后的语料,一行一条

corpus.jsonl 每行 schema(JSON;**禁止 // 注释**,枚举值在 schema 外用文字说明):

    {
        "cid": "yosys_001",
        "tool": "yosys_synth",
        "stage": "synth",
        "log_text": "...",
        "ground_truth": {
            "expected_errors": [
                {
                    "error_code": "synth.multi_driver",
                    "namespace": "synth",
                    "severity": "error",
                    "key_signal": "sum_out"
                }
            ],
            "expected_root_cause": "sum_out 被 always 块 1 和 2 同时赋值",
            "expected_fix_hint_contains": "多驱动|multi"
        },
        "source": "real_run",
        "annotator": "non-tech-member-name",
        "difficulty": "medium"
    }

枚举值说明(标注 SOP 一并下发,不进 JSON):
    - stage 取值:synth | sim | sta
    - source 取值:real_run | public_issue | synthetic
    - difficulty 取值:easy | medium | hard
    - 字段含义:
        - cid:语料 id,与 raw/ 文件名对应
        - tool:产生日志的 Tool(契约 §2.1 name)
        - log_text:日志全文(或 raw 文件引用)
        - expected_errors:期望 A 抽出的错误列表(error_code 取契约 §2.6 二段式码;key_signal 关键信号/行号,匹配用,可选)
        - expected_root_cause:一句话人类判断的根因
        - expected_fix_hint_contains:A 的 fix_hints 任一条包含此子串算 hint 命中
        - annotator:标注人(溯源)
        - difficulty:难度(影响分桶度量)

标注 SOP:

    1. 收集日志:从本项目 runs/ 失败案例复制 stdout.full.log;或从 yosys/iverilog GitHub issue 抄代表性片段。每条 50-500 行为宜。
    2. 标 expected_errors:看日志里 ERROR/WARNING 行,对照 ErrorKB 的 example_log 找匹配模式,抄 error_code。若日志反映的错误 ErrorKB 没覆盖,标 error_code="diagnose.no_error_found" 并标 difficulty=hard(留给规则层/LLM 层成长)。
    3. 标 expected_root_cause:用一句话写"为什么会这样"(人类判断)。
    4. 标 expected_fix_hint_contains:写一个子串,A 的 fix_hints 任一条命中即算对。
    5. 跑 `python scripts/build_corpus.py --in data/logs_corpus/raw --out data/logs_corpus/corpus.jsonl` 自动校验 schema。
    6. 目标:>= 30 条语料,synth/sim/sta 三类至少各 5 条,hard 类至少 3 条(保证失败案例度量有样本)。
       easy:medium:hard ≈ 9:15:6。
    7. 抽检一致性:抽检 20%,一致性 >= 0.8 才算语料合格(不一致条目剔除并补足)。

### 10.2 量化指标与门槛(v1.2,消除"指标全绿但诊断无用"的验收空洞)

    指标                              门槛(MVP)              测试方法
    ─────────────────────────────── ──────────────────────── ──────────────────────────────────────
    Top-1 根因命中率(加权总分)      加权总分 >= 0.60        scripts/eval_diagnose.py 跑 corpus.jsonl
    规则层覆盖率                     >= 40%                   分母=corpus 全样本,分子=used_layers∈{rule,rule+llm} 且 Top-1 命中
    confidence(LLM 层样本均值)       >= 0.60                  仅统计 used_layers∈{llm,rule+llm} 的样本
    confidence(规则层样本均值)       >= 0.70                  仅统计 used_layers∈{rule,rule+llm} 的样本
    接口契约单测全绿                 100%                     pytest tests/test_diagnose_skill.py(无 needs_eda)
    ErrorKB 增长用例                 >= 1 条                  手动跑 propose_pending→confirm,assert kb.patterns +1
    LLM 调用次数(单次诊断)         <= 1                     trajectory 中 step=="llm" 最多出现 1 次
    单次诊断墙钟时间                 p50 <= 20s 且 p95 <= 60s eval 脚本记录每条 wall_time(含 LLM 等待;
                                                             R4 回退样本计入 p50 不计入 p95,单独报 fallback_rate)

Top-1 根因命中定义(v1.2 加权三支,消除"任一支单独兜底刷分"):

    每条样本得分 = 0.4 * code_match + 0.4 * root_cause_match + 0.2 * hint_match,总分 >= 0.6 算命中。
    - code_match(权重 0.4):report.errors 中存在 e,e.error_code == 任一 expected_errors[].error_code
                              且 (expected_errors[].key_signal 为空 或 e.evidence 含该 signal)
    - root_cause_match(权重 0.4):report.root_cause_summary 与 expected_root_cause 关键词 Jaccard >= 0.3
                                   或 expected_root_cause 子串在 report.root_cause_summary
    - hint_match(权重 0.2):任一 fix_hint 包含 expected_fix_hint_contains
    严格命中率(code_match==1 且总分>=0.6)与宽松命中率(总分>=0.6)同时报,供读者自行判断。
    分母=corpus 全部样本(避免与规则层覆盖率循环验证)。脚本输出:命中数/总数 + 分 stage/分 difficulty 桶。

### 10.3 测试方法(可直接执行)

A. 接口单测(无 EDA 依赖,CI 必跑):

    pytest tests/test_diagnose_skill.py -v
    # 覆盖:
    #   - args 校验(空/缺字段 → diagnose.args_invalid)
    #   - 规则层命中 3 条种子 pattern
    #   - LLM 层 mock 触发 + JSON 解析 + 回退
    #   - confidence 公式(构造 evidence 长度 0/3/10 + contradiction True/False 四组断言)
    #   - as_tool 后 parsed._skill_status/_iterations 正确
    #   - ErrorKB add_case 去重 + save/load 往返

B. 语料 Top-1 验收(需 data/logs_corpus/corpus.jsonl):

    python scripts/eval_diagnose.py --corpus data/logs_corpus/corpus.jsonl \
                                    --report runs/<eval_run_id>/diagnose_eval.md
    # 输出:总体 Top-1 命中率 / 规则层覆盖率 / 分 stage 桶 / 分 difficulty 桶 / p95 墙钟
    # 退出码:总体命中率 >= 0.60 且 规则覆盖率 >= 0.40 则 0,否则 1

C. ErrorKB 增长用例(手动):

    # 1. 跑一次诊断触发 LLM 层,产 propose_pending/<id>.json
    # 2. 人工 confirm:
    python scripts/confirm_kb_pattern.py --in runs/<run_id>/diagnose/propose_pending/<id>.json
    # 3. 断言 data/error_kb.json 的 patterns 数 +1,且新条 source="human"
    python -c "import json; kb=json.load(open('data/error_kb.json')); \
               print(len(kb['patterns']))"

D. 契约对齐自检(发布前执行):

    对照契约 §2.2 v1.2 skill_diagnose.parsed 字段(13 字段强制集,含 _schema),逐字段核 DiagnosisReport.to_parsed() 输出。
    对照契约 §2.6,核 A 产出的 ErrorItem 字段完整性(code/namespace/tool/severity/message/evidence/fix_suggestion)。
    对照契约 §2.3 as_tool 映射表,核 ToolResult.parsed 含 _skill_status/_iterations/_trajectory/_patch_source/_convergence_cause/_best_iter,
        且 A 的 _patch_source=="none"、_convergence_cause=="none"、_best_iter==-1(符合 A 恒定语义)。
    对照契约 §4.5,核 needs_rtl_patch 按 severity 判(构造 eda.rtl_syntax+severity=error 样本,断言为 True)。

---

## 11. 风险与对策

    风险                                    影响    对策
    ──────────────────────────────────── ────── ──────────────────────────────────────────
    R1 LLM 输出 JSON 不规范                高      强制 temperature=0 + jsonschema 校验 + 解析失败回退
                                                  标准文本(契约 §6 LLMResponse.text 兜底)
    R2 ErrorKB 正则过宽误命中              中      add_case 时强制带 example_log 自检(步骤 3);
                                                  eval 脚本统计 false_positive,门槛设 <= 10%
    R3 语料不足 20 条                      高      优先采集真实语料;不够则用 synthetic
                                                  (按 ErrorKB example_log 反推日志),但标注 source
    R4 LLM 调用慢/超预算                   中      budget_s 硬约束;LLMAttributor 设 deadline 检查;
                                                  超时回退纯规则层(used_layers="rule")
    R5 规则层与 LLM 层 root_cause 矛盾     低      has_contradiction 触发 confidence 下调(§4.5);
                                                  report.md 标注 [CONTRADICTION] 供人工 review
    R6 propose_pending 堆积无人 review     中      Phase4 任务:集中 review;CI 加断言
                                                  propose_pending/ 文件数 <= 阈值(如 10),超了告警
    R7 日志裁剪丢证据(.full.log 未生成)  中      A 读证据时先 assert os.path.exists(.full.log);
                                                  不存在则 fallback 到 ToolResult.stdout + 标 evidence_incomplete
    R8 错误码漂移(上游 Tool 改日志格式)   中      ErrorKB.version + parsed_schema_ref 做版本锚点;
                                                  CI 跑 RuleLayer 回放测试(每条 example_log 必命中)
    R9 多 provider 切换后正则失效     低      ErrorKB 与 provider 解耦(规则层零 LLM);
                                                  LLM 层失败回退规则层,不依赖具体 provider

---

## 12. 待确认决策(开放问题,列给用户)

1. **ErrorKB 持久化路径**:当前定 `data/error_kb.json`(进 git)。是否要拆成 `data/error_kb.seed.json`(人工写,进 git)+ `data/error_kb.grown.json`(LLM 提议+人工确认后积累,进 git 但可频繁改)?分离能避免种子与增长混淆。倾向:拆,但增加一次 save/load 逻辑,需用户拍板。

2. **Top-1 命中率的"语义近似"判据**:MVP 用关键词 Jaccard,粗糙。是否引入一个小 LLM-as-judge(同一个 ClaudeProvider)做语义打分?代价:eval 脚本变慢、引入评判方偏差。倾向:MVP 用关键词,后续若需语义近似再升级。

3. **A 是否要产"patch 草稿"**:当前 fix_hints 是文字建议,B 自己拿去让 LLM 出 patch。是否让 A 直接产 patch 草稿(diff 片段)给 B?风险:边界模糊,A 越界到 B 的职责。倾向:不产,A 只给 fix_hints,B 决定 patch 形态(契约 §2.3 patch_source 由 B 填)。

4. **corpus.jsonl 的 ground_truth 标注质量门槛**:人工标注,可能不准。是否要抽检 20%?抽检不一致的条目如何处理(剔除/重标/降权)?倾向:抽检 20%,不一致剔除,语料数从 20 起步逐步补到 30。

5. **diagnose namespace 是否进契约正文**:[v1.2 已关闭] 契约 §2.6 namespace 表已正式登记 `diagnose`,A §5.4 措辞改为"已登记于契约 §2.6"。

6. **A 被 B 调用时的预算切分**:B 每轮迭代都要调 A。max_iter=5 意味着最多 5 次 A 调用。run_budget_s=600(契约 §6)下,A 每次预算 60s 是否够?是否要 A 在被 B 调用时降到 30s?倾向:A.budget_s 固定 60s,由 B 通过 remaining_budget_s 仲裁(契约 §2.3 双层预算),A 不感知调用方。

7. **propose_pending 的 review 责任人**:review LLM 提议的正则,但正则质量偏技术。是否要二次复核?倾向:第一道看 example_log 是否合理,第二道看正则是否过宽,两道都过才 add_case。

---

(自检见下方 StructuredOutput.self_check)
