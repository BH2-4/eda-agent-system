# 组件 C —— Planner / Tool-Use 层(大脑)设计文档 doc v1.2

> 对齐共享契约 `CONTRACTS.md` v1.2(CONTRACT_VERSION="0.1.0")。本文任何与契约不一致处,以契约为准。
>
> 实现文件:`src/eda_agent/planner/c_planner.py`(主)、`src/eda_agent/planner/rule_planner.py`、`src/eda_agent/planner/llm_planner.py`、`src/eda_agent/planner/prompts.py`(prompt)、`src/eda_agent/cli.py`(L5 入口)、`src/eda_agent/mcp_server.py`(加分项)。
>
> 约定:所有代码用 Python type hint / dataclass 风格,4 空格缩进,**禁止使用反引号代码块**。所有 contract_version 引用必须 `from eda_agent.contracts import CONTRACT_VERSION`,禁止裸字符串。CPlanner 为 per-process 构造(run_id 在 execute 内生成)。

---

## 1. 定位与职责边界(做什么、不做什么)

C 是 L4 层的大脑。它接到一个 `RunRequest`(自然语言目标 + RTL/TB/lib 等输入)后,把它分解为对 Tool/Skill 的有序调用,执行,读每步的 `ToolResult.parsed`,据结构化结果决定下一步,直到目标达成或预算耗尽。C 对外暴露三个 CLI 子命令(`eda self-heal` / `eda diagnose` / `eda report`,契约 §2.0 与 §11 冲突3 裁决)+ Python SDK + 可选 MCP server。

### 1.1 C 做什么(职责)

- 把 `RunRequest.goal` 翻译成 plan(初始任务分解)。
- 用自研轻量 plan-execute / ReAct 循环组织 Tool/Skill 调用(不依赖 LangGraph)。
- 维护 run 状态机:`running → ok | failed | budget_exhausted`。
- 控制流:最大迭代 / 超时 / 失败回退 / 工件传递(综合产物 netlist 喂给仿真)/ 早停 / 降级。
- 把 LLM 发起的 `tool_calls` 经 `registry.get(name)` 校验(防幻觉),构造 `ToolCall`、执行、把 `ToolResult` 回灌 LLM(tool-use 回环闭合)。
- 双层预算仲裁:C 计算 `remaining = run_budget_s - elapsed`,下传给 Skill 的 `remaining_budget_s`。
- 每步 `ToolCall`/`ToolResult` 落 `runs/<run_id>/steps/<idx>_<tool>/`;run 结束产 `report.md`。
- 进程启动扫描僵尸 run(run.json.status=="running" 且超时),标记 failed 并写 status.json。

### 1.2 C 不做什么(边界,严格对齐契约 §11 裁决)

- **不直接懂 EDA**:C 不解析 yosys/iverilog/OpenSTA 的裸 stdout/stderr 做决策。它只读 `ToolResult.parsed` 的结构化字段(如 `parsed["passed"]`、`parsed["wns"]`)。理解错误语义由 A 诊断器(`skill_diagnose`)负责,把错误文本翻译成 `ErrorItem`。
- **不直接调子进程**:C 不 `subprocess.run("yosys ...")`,一切子进程经 Tool(Yosys/iverilog/OpenSTA Tool)。C 只认 `Tool` Protocol。
- **不做 RTL patch**:改 RTL 由 B(`skill_self_heal`)负责。C 只在 plan 里"调一次 self_heal"。
- **不重新定义契约类型**:C import `eda_agent.contracts` 的全部 dataclass,不自创 `ToolResult`/`SkillResult`/`ErrorItem` 等同名结构。
- **不直接 import anthropic/openai**:LLM 调用走 `LLMProvider`(由 `make_provider()` 工厂返回 `CountingProvider` 包装)。
- **不嵌套 run_id**:Skill 内部迭代不生成独立 run_id,其每步以 `StepRecord` 形式追加到父 `RunRecord.steps`(标 skill_name/iter)。C 只负责父 RunRecord 的生命周期。

### 1.3 与其它组件的边界一句话

| 边界 | C 侧 | 对方侧 |
|---|---|---|
| C ↔ Tool Registry | C 只 `registry.get(name)(ToolCall)` | Registry 注册发现,返回 None 时 C 构造 `eda.tool_not_found` 回灌 |
| C ↔ EDA Tool(L1) | 不直接接触 | 经 Registry 调,拿 ToolResult.parsed |
| C ↔ A(skill_diagnose) | 把上游 ToolResult.asdict 列表传给 A | A 返回 root_causes(ErrorItem 列表) |
| C ↔ B(skill_self_heal) | 传 rtl/tb/diagnose/max_iter + remaining_budget_s | B 内部迭代,返回 convergence_cause/best_iter |
| C ↔ LLM provider | 发 Message 列表 + tools(Registry.to_llm_tools) | 返回 LLMResponse(含 tool_calls) |
| C ↔ L0 工件存储 | 经 `runner` 落 RunRecord/steps/report | runner 负责 run_id 生成、僵尸自愈 |

---

## 2. 在整体系统中的位置(调用关系图)

C 位于 L4,夹在 L5(对外接口层)与 L3(Tool Registry)之间。L5 把人类需求翻译成 `RunRequest` 喂给 C;C 从 L3 取 Tool,Tool 内部可能再调 L2 的 Skill(如 self_heal 自己内部又调 diagnose)。

### 2.1 调用关系图(端到端一次 self_heal)

    ┌─────────────────────────────────────────────────────────────────┐
    │  L5 用户/评审/MCP client                                        │
    │     eda self-heal --rtl ... --tb ... --goal "pass all tests"   │
    └──────────────────────────────┬──────────────────────────────────┘
                                   │ RunRequest(kind="self_heal",...)
                                   ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │  L4  CPlanner.execute(request) → RunReport                      │
    │     ┌─────────────────────────────────────────────────────┐    │
    │     │  状态机:PLANNING → EXECUTING ⇄ REFLECTING          │    │
    │     │            ↓ (目标达成/超预算/超迭代)               │    │
    │     │            REPORTING → DONE                         │    │
    │     └─────────────────────────────────────────────────────┘    │
    │           │  registry.get(name)(ToolCall)        ▲ ToolResult  │
    └───────────┼──────────────────────────────────────┼─────────────┘
                ▼                                      │
    ┌─────────────────────────────────────────────────────────────────┐
    │  L3  ToolRegistry                                               │
    │     yosys_synth | iverilog_sim | opensta_timing                 │
    │     skill_diagnose(A) | skill_self_heal(B)  ← as_tool 注册      │
    └───────────┬──────────────────────────────────────┬─────────────┘
                ▼                                      │
    ┌──────────────────────┐         ┌─────────────────────────────────┐
    │  L1  EDA Tool 子进程 │         │  L2  Skill(自身也迭代)         │
    │  yosys/iverilog/sta  │         │   B 内部:综合→仿真→A→patch   │
    └──────────────────────┘         └────────────┬────────────────────┘
                                                  │ ToolResult(子步骤)
                                                  ▼ 追加为父 RunRecord.steps
    ┌─────────────────────────────────────────────────────────────────┐
    │  L0  runs/<run_id>/  RunRecord + steps/ + report.md             │
    │       C 经 runner 写入;B 子步骤也写同一 steps/(标 skill_name) │
    └─────────────────────────────────────────────────────────────────┘

    C 与 LLM provider 的旁路(每轮 REFLECTING 可能调一次):
        C → provider.chat(messages, tools=registry.to_llm_tools())
          → LLMResponse.tool_calls = [{"id","name","args"}]
          → registry.get(name) 校验(防幻觉)→ ToolCall(llm_tool_call_id=id)
          → 执行 → ToolResult → Message(role="tool", tool_call_id=id) 回灌

### 2.2 三种调用关系强度

- **C → Registry.get**:每次执行必经(强耦合,契约 §2.5 规定)。
- **C → Skill(A/B)**:plan 决定调不调。self_heal 流程必调 B;诊断 step 调 A。
- **C → LLM**:主路径。MVP 两种 planner 模式:(a) **LLM planner**(ReAct,LLM 发 tool_calls,**v1.2 默认**,契合 Track 01 agentic 充分性);(b) **规则 planner**(确定性 plan,无需 LLM 即可跑 synth→sim→sta→diagnose→self_heal,作为 LLM 不可用时的降级路径)。

> 取舍说明(v1.2 调整契约 §11 冲突7):默认 `planner_mode=llm`,让 C 的 plan-execute 走 ReAct 调 LLM 组织工具(避免被评委会判定"会循环的 wrapper");`rule` 模式仅作 LLM 不可用时的降级(feasibility 保底)。两者走同一 `PlanExecutor` 抽象,模式由 `settings.toml [planner] mode` 开关切换,默认 `llm`。演示主路径必须用 llm 模式 run 作为主 demo。

---

## 3. 内部架构(子模块拆分 + 数据流图)

### 3.1 子模块拆分(`src/eda_agent/planner/`)

    planner/
        c_planner.py        # CPlanner 主类 + 状态机 + 双模 planner
        prompts.py          # SYSTEM_PROMPT / planner/reflect prompt 模板
        rule_planner.py     # 规则 planner:确定性的 self_heal plan 生成
        llm_planner.py      # LLM planner:ReAct,调 provider + tool-use 回环
        budget.py           # 预算仲裁器:elapsed/remaining 计算 + 双层下传
        report.py           # report.md 渲染 + RunReport 构造

### 3.2 子模块职责

| 子模块 | 职责 | 对外接口 |
|---|---|---|
| `CPlanner` | 顶层入口,持有 registry/llm/runner/run_id/budget;驱动状态机 | `execute(request: RunRequest) -> RunReport` |
| `RulePlanner` | 给定 RunRequest.kind 产出确定性 `Plan`(step 序列) | `make_plan(request) -> Plan` |
| `LLMPlanner` | ReAct:每轮问 LLM 拿 tool_calls,校验执行,回灌 | `next_action(state) -> Action` |
| `Budget` | wall-clock 计时 + remaining 计算 + Skill 下传 | `remaining_s() -> float` |
| `report` | 渲染 `runs/<run_id>/report.md` + 构造 `RunReport` | `render(record) -> RunReport` |
| `prompts` | system/planner/reflect 的 prompt 骨架 | 常量字符串 + `format` 函数 |

### 3.3 CPlanner 内部数据流图(单轮)

    RunRequest
        │
        ▼
    ┌───────────────┐
    │ Runner.create │  生成 run_id, 落 run.json(status=running), config_snapshot
    └───────┬───────┘
            ▼
    ┌────────────────────────────────────────────────────────┐
    │  CPlanner.execute 循环                                  │
    │                                                        │
    │   ┌─────────────┐    Plan/Action     ┌──────────────┐  │
    │   │ RulePlanner │ ─────────────────▶ │ PlanExecutor │  │
    │   │ 或 LLMPlanner│                   │              │  │
    │   └─────────────┘                   │  对每个 Action:│  │
    │           ▲                          │   1. registry │  │
    │           │ ToolResult.parsed        │      .get(name)│  │
    │           │ (回灌决策)              │   2. 校验 args │  │
    │           │                          │   3. 构 ToolCall│  │
    │           │                          │   4. 执行      │  │
    │           │                          │   5. 落 step   │  │
    │           │                          │   6. 更新 state│  │
    │           │                          └──────┬───────┘  │
    │           │                                 │ ToolResult│
    │           │                                 ▼          │
    │   ┌───────────────┐                  ┌──────────────┐   │
    │   │ State(上下文)│ ◀────────────────│ 工件传递     │   │
    │   │ - netlist ref │                  │ netlist→sim  │   │
    │   │ - sim passed  │                  │ sim→diagnose │   │
    │   │ - wns         │                  └──────────────┘   │
    │   │ - iter count  │                                     │
    │   └───────────────┘                                     │
    │                                                        │
    │   终止条件: goal达成 / max_iter / budget / 致命错     │
    └────────────────────────────┬───────────────────────────┘
                                 ▼
                         report.render(record)
                                 ▼
                         RunReport + report.md
                                 ▼
                     Runner.finalize(status=终态, status.json)

---

## 4. 核心数据结构(Python dataclass 伪代码,字段级注释)

> 所有 dataclass 来自 `eda_agent.contracts`(契约 §2)。C **不重新定义**这些类型,只 import。下面是 C 视角对它们的字段级注释 + C 自身的少量扩展结构(Plan/Action/State)。

### 4.1 C 直接复用的契约类型(只列 C 用到的字段视角)

    # from eda_agent.contracts
    RunRequest          # §2.0:C 的唯一输入。C 读 kind 决定 plan 模板。
    RunReport           # §2.0:C 的唯一输出。C 填 status/report_path/summary/metrics。
    ToolCall            # §2.1:C 构造,填 name/args/caller="planner"/llm_tool_call_id。
    ToolResult          # §2.1:C 消费,读 status/parsed(尤其 passed/wns/num_failed)/error_code/artifacts。
    SkillResult         # §2.3:C 不直接见(被 as_tool 包成 ToolResult),但读 parsed._skill_status/_convergence_cause/_best_iter。
    RunRecord           # §2.4:C 经 runner 写入 status/steps/llm_calls/tokens/final_report_path。
    StepRecord          # §2.4:C 经 runner 追加;B 子步骤也追加(skill_name="self_heal", iter=n)。
    Message / LLMResponse  # §2.5:LLM planner 用。C 拼 messages,读 response.tool_calls。
    ErrorItem           # §2.6:C 不构造(A 构造),但读 root_causes[0].code.severity 做"致命错早停"判断。
    artifact_ref        # §2.4:dict {"run_id","rel_path"}。C 在工件传递时直接透传 artifacts 列表元素。

### 4.2 C 自身的扩展结构(仅 planner 内部使用,不进 contracts.py)

    # src/eda_agent/planner/c_planner.py
    from dataclasses import dataclass, field
    from typing import Literal, Any

    @dataclass(frozen=True)
    class Action:
        # 一次要执行的 Tool 调用意图(由 planner 产出,PlanExecutor 执行)
        tool_name: str                          # 必须是 Registry 已注册名,如 "yosys_synth"
        args: dict[str, Any]                    # 与 Tool.schema 对齐;PlanExecutor 执行前用 jsonschema 校验
        rationale: str                          # 为什么要这一步(规则模板填 / LLM 的 thinking 填)
        llm_tool_call_id: str | None = None     # LLM 发起则填 tool_calls.id;规则发起为 None
        # 不含 timeout/budget;由 Budget 在执行时统一注入 settings.eda.tool_timeout_s

    @dataclass
    class Plan:
        # 一个有序 Action 列表(规则 planner 的产物)。LLM planner 每轮只产 1 个 Action(ReAct 单步)。
        actions: list[Action] = field(default_factory=list)
        mode: Literal["rule", "llm"] = "rule"
        # rule 模式:PlanExecutor 顺序执行直到某步失败或全部 ok。
        # llm 模式:Plan 为空,每轮问 LLM 拿单步 Action,直到 LLM 不再发 tool_calls 或达上限。

    @dataclass
    class PlannerState:
        # C 循环内的可变上下文(贯穿所有步),用于工件传递与早停判断
        rtl_path: str                           # 当前 RTL(self_heal 流程中可能被 B 改写,但 C 视角 RTL 路径不变,B 自己快照)
        tb_path: str | None
        top_module: str | None
        lib_path: str | None
        clock_name: str | None
        netlist_ref: dict | None = None         # 上游 yosys_synth 的 artifact_ref,喂给 sim/sta
        last_synth: dict | None = None          # yosys_synth.parsed(快照,num_cells/area)
        last_sim: dict | None = None            # iverilog_sim.parsed(passed/num_passed/num_failed/fail_signals)
        last_sta: dict | None = None            # opensta_timing.parsed(wns/tns/violations)
        last_diagnose: dict | None = None       # skill_diagnose.parsed(root_causes/needs_rtl_patch)
        iteration: int = 0                      # C 层迭代计数(区分于 B 内部 iter);统一在 _execute_action 后 +1
        goal_achieved: bool = False             # 早停主信号:B all_pass 后独立 iverilog_sim 验证 passed=True
        fatal: bool = False                     # 早停致命错(如 eda.internal / rtl_syntax 且 A 判 fatal)
        history: list[dict] = field(default_factory=list)   # v1.2:_build_messages 回灌用,每步 append {"call":call,"result":result}
        independent_verify_passed: bool | None = None       # v1.2:B all_pass 后独立 iverilog_sim 验证结果
        best_rtl_ref: dict | None = None        # v1.2:B 的 fixed_rtl_ref,独立验证步读它
        pending_self_heal_all_pass: bool = False  # v1.2:B 报 all_pass 但独立验证未跑过

    # 工件传递占位常量(contracts.py 导出,三文档统一引用,不用裸字符串):
    ARTIFACT_FROM_STATE = "<from_state>"   # RulePlanner 产 Action 时用它标记需工件注入的字段

    @dataclass
    class CPlannerConfig:
        # 从 settings.toml [budget]/[planner] 段映射,C 持有
        planner_max_iterations: int = 8         # settings.budget.planner_max_iterations
        skill_max_iterations: int = 5           # 透传给 B 的 max_iter 默认值
        run_budget_s: int = 600                 # C 与所有 Skill 共享上限(双层预算仲裁顶层)
        tool_timeout_s: int = 120               # settings.eda.tool_timeout_s
        planner_mode: Literal["rule", "llm"] = "llm"  # v1.2:默认 llm(rule 仅作降级)

### 4.3 状态机枚举(C 内部)

    PlannerPhase = Literal["PLANNING", "EXECUTING", "REFLECTING", "REPORTING", "DONE"]
    # PLANNING:  生成初始 Plan(rule:全量;llm:占位空 Plan,首步在 REFLECTING 取)
    # EXECUTING: 取下一个 Action,经 Registry 执行,落 step
    # REFLECTING: 读 ToolResult.parsed,更新 state,判断 goal_achieved/fatal/继续
    #            (llm 模式此阶段还问 LLM 拿下一个 Action)
    # REPORTING: 终态判定 + 渲染 report.md + 构造 RunReport
    # DONE:      runner 写 status.json,返回 RunReport

---

## 5. 对外接口契约(函数签名 + 输入/输出 schema + 错误码 + 副作用/产出工件)

三处对外接口签名严格一致(CLI / SDK / MCP 调同一函数)。MVP 实现 CLI + SDK;MCP 为加分项。

### 5.1 Python SDK(权威实现)

    # src/eda_agent/planner/c_planner.py
    class CPlanner:
        def __init__(
            self,
            registry: ToolRegistry,             # 已注册所有 Tool + A/B(as_tool)
            llm: LLMProvider,                    # make_provider() 返回的 CountingProvider
            runner: "Runner",                    # 落盘器,负责 run_id/steps/report/僵尸自愈
            settings: "Settings",                # 含 budget/eda/planner_mode
        ) -> None: ...

        def execute(self, request: RunRequest) -> RunReport:
            """C 的唯一入口。
            输入:RunRequest(契约 §2.0)。
            输出:RunReport(契约 §2.0)。
            副作用:
              - 创建 runs/<run_id>/ 目录(run_id = generate_run_id())
              - 写 run.json(request/config_snapshot/status=running)、plan.json
              - 每步写 steps/<idx>_<tool>/{tool_call.json,tool_result.json,stdout.log,stderr.log[,.full.log]}
              - B 子步骤以 StepRecord 追加到同一 steps/(标 skill_name="self_heal", iter=n)
              - 终态写 status.json + 更新 run.json.status
              - 写 report.md(人类可读)
              - (可选)写 experiment_manifest.json(self_heal 流程)
            错误处理:不抛业务异常给调用方。所有错误经 ToolResult.error_code 流转;
                     未预期异常捕获后置 RunReport.status="failed" + summary 记异常信息。
            """

    # 便利函数(L5 CLI 与 MCP 共用)
    def run_pipeline(request: RunRequest, settings: "Settings") -> RunReport:
        """构造 registry/llm/runner → CPlanner.execute。一行调用,CLI/MCP 都走这里。"""

### 5.2 CLI 子命令(L5,契约 §2.0 映射)

    # src/eda_agent/cli.py
    # 用 argparse 或 typer 均可,签名如下。MVP 实现 3 个子命令(契约 §2.0 + §11 冲突3 裁决)。

    eda self-heal \
        --rtl <path> --tb <path> \
        --goal "pass all tests" \
        [--top-module <name>] \
        [--lib <path>] [--clock <name>] \
        [--max-iter <int>]      # 覆盖 settings.budget.skill_max_iterations
        [--mode rule|llm]       # 覆盖 settings.planner_mode
        [--run-budget <int>s]
        # → 内部构造 RunRequest(kind="self_heal", ...) → run_pipeline → 打印 report_path
        # 退出码:0=ok, 1=failed, 2=budget_exhausted, 64=report 不存在

    eda diagnose --rtl <path> --tb <path>
        # → 内部构造 RunRequest(kind="diagnose", ...) → run_pipeline(内部 plan kind=diagnose)
        # 单点演示 A 诊断器能力(契约 §2.0 v1.2 新增子命令,工程量 0.3 人天)
        # 退出码同 self-heal

    eda report <run_id>
        # → 读 runs/<run_id>/report.md 渲染到 stdout;不重跑 pipeline
        # → 若 run_id 不存在,stderr 报错 + 退出码 64

### 5.3 MCP server(加分项,签名与 SDK 一致)

    # src/eda_agent/mcp_server.py(FastMCP 实现,契约 §3 末尾适配约定)
    # 暴露两类 MCP tool:
    #   eda_agent.self_heal(inputSchema = RunRequest 字段子集) → RunReport dict
    #   eda_agent.report(inputSchema = {run_id: str})          → report.md 文本
    # 另:registry.to_mcp_tools() 把每个 EDA Tool 也暴露为 eda_agent.<tool_name>(加分项)。
    # 适配约定:
    #   - 同步 Tool 用 asyncio.to_thread 包成 async
    #   - ToolCall.args 直接作为 MCP inputSchema 入参
    #   - ToolResult.status="error" → MCP error response,error_code 放 data 字段
    # 降级策略:集训评估 4 天做不完则 to_mcp_tools 仅留签名存根,不阻塞 MVP。

### 5.4 输入 schema(RunRequest,契约 §2.0 原样)

    RunRequest 字段(详见契约):kind/goal/rtl_path/tb_path/top_module/lib_path/clock_name/max_iter/extra。
    CLI 参数 → RunRequest 映射见 5.2;MCP inputSchema 与 RunRequest 字段同名同义。

### 5.5 输出 schema(RunReport + 副作用清单)

    RunReport 字段(契约 §2.0):
        run_id / status("ok"|"failed"|"budget_exhausted") / report_path /
        summary / metrics(dict, 契约 §2.4 v1.2 锁定 15 字段标准集)
    C 填的 metrics(v1.2 锁定 15 字段,与契约 §2.4 + experiment_manifest.json 一一对应):
        {
          "planner_iterations": int,
          "tool_calls_total": int,
          "llm_calls": int,                    # 经 CountingProvider 读
          "llm_tokens_in": int, "llm_tokens_out": int,
          "sim_passed": bool | None,
          "num_passed": int | None, "num_failed": int | None,
          "wns_ns": float | None, "tns_ns": float | None,
          "self_heal_convergence": str | None, # parsed._convergence_cause
          "self_heal_best_iter": int | None,   # parsed._best_iter
          "self_heal_pass_rate": float | None, # (convergence=="all_pass") ? 1.0 : 0.0
          "baseline_pass_rate": float | None,  # 来自 baseline run(契约 §2.4 baseline run 定义)
          "wall_time_s": float,
        }

### 5.6 错误码(C 视角产生的,契约 §2.6 二段式)

C 自身不发明新 namespace,复用契约 MVP 13 码:

| 触发场景 | error_code | severity(契约表) | C 行为 |
|---|---|---|---|
| LLM 发 tool_calls 但 Registry.get(name)==None | `eda.tool_not_found` | error | 构造 error ToolResult 回灌 LLM,继续 ReAct(不中断) |
| LLM 发 tool_calls 但 args 不符 schema | `eda.tool_args_invalid` | error | 同上,回灌后继续 |
| 子进程退出码非 0(由 Tool 返回) | `eda.subprocess_failed` | error | C 读 ToolResult.error_code,转 A 诊断 |
| 子进程超时 | `eda.subprocess_timeout` | error | 同上 |
| LLM 调用失败(provider 抛) | `eda.llm_call_failed` | error | C 捕获,规则 planner 降级 / LLM planner 重试 1 次后置 failed |
| 预算耗尽 | `eda.budget_exhausted` | warn | status="budget_exhausted",走 REPORTING |
| Skill 经 as_tool 的 budget_exhausted | `eda.budget_exhausted`(parsed._skill_status 保留原值) | warn | C 据 _skill_status 区分"真崩 vs 没收敛"写进 report |
| 未分类内部错(如 runner 落盘失败) | `eda.internal` | fatal | 立即 REPORTING,status="failed" |
| parsed._schema 不匹配 | `eda.schema_mismatch` | error | 立即 REPORTING(status=failed),记录到 report |

C 消费 A/B 规范码(契约 §2.6 要求本表含 diagnose.*/heal.* 行):经 errors.namespace_of(code) 派生 namespace,按 {"diagnose","heal"} vs "eda" 分支聚类(契约 §2.6),不做双向字符串相等比较。

| 触发场景 | error_code(规范码) | severity | C 行为 |
|---|---|---|---|
| A 工具失败但规则+LLM 无根因 | `diagnose.no_error_found` | warn | 入 report.errors,不中断 ReAct |
| A LLM 失败 / args 不符 / KB 损坏 | `diagnose.llm_call_failed`(别名 eda.*)/ `diagnose.args_invalid`(别名 eda.*)/ `diagnose.kb_corrupted` | error / error / warn | 同上 |
| B goal 不可解析 / rtl·tb 不存在 / diagnose 失败 | `heal.unsupported_goal` / `heal.rtl_not_found` / `heal.tb_not_found` / `heal.diagnose_failed` | error | 立即 REPORTING(status=failed) |
| B patch 语法错 / 死锁 / 防退化降级 | `heal.patch_syntax_invalid` / `heal.regression_deadlock` / `heal.reduced_to_diagnose` | warn / warn / info | 据 _skill_status 区分,入 report |

C 的退出码与 status 映射:`ok→0`,`failed→1`,`budget_exhausted→2`,report 不存在 `→64`(仅 `eda report <run_id>` 子命令,run_id 目录无 report.md)。

### 5.7 副作用 / 产出工件清单(契约 §2.4 路径)

    runs/<run_id>/
        run.json                 # RunRecord(C 经 runner 写,实时更新 status)
        request.json             # 原 RunRequest
        plan.json                # C 产出的 Plan(rule 模式有 actions,llm 模式记 mode)
        steps/<idx>_<tool>/      # 每个 Action 一份 + B 子步骤(skill_name/iter)
        skills/{diagnose,self_heal}/  # A/B skill 自身产物(C 不直接写,但目录在 C 的 run 下)
        experiment_manifest.json # self_heal 流程产出(对比实验度量)
        report.md                # C 终态渲染
        status.json              # 仅终态写(防僵尸)

---

## 6. 关键流程伪代码(Python 风格,4 空格缩进,禁用反引号)

### 6.1 CPlanner.execute 主循环(状态机)

    def execute(self, request: RunRequest) -> RunReport:
        record = self.runner.create(request)        # 生成 run_id, 落 run.json(running)+ config_snapshot
        state = self._init_state(request)
        phase: PlannerPhase = "PLANNING"
        try:
            while phase != "DONE":
                if self._budget_exhausted():
                    record.status = "budget_exhausted"
                    phase = "REPORTING"

                if phase == "PLANNING":
                    plan = self._make_plan(request, state)
                    self.runner.save_plan(plan)
                    phase = "EXECUTING" if plan.mode == "rule" else "REFLECTING"

                elif phase == "EXECUTING":
                    # 规则模式:顺序吃 plan.actions;任一步失败转诊断/自修复分支
                    action = self._next_rule_action(plan, state)
                    # v1.2:B 报 all_pass 后,优先插独立验证步
                    if action is None and state.pending_self_heal_all_pass:
                        action = self._verify_after_self_heal(state, request)
                    if action is None:
                        phase = "REPORTING"          # plan 跑完
                    else:
                        result = self._execute_action(action, state, record)
                        self._reflect(result, state)
                        if state.fatal:
                            phase = "REPORTING"
                        elif state.goal_achieved:
                            phase = "REPORTING"
                        else:
                            phase = "REFLECTING"     # 判断是否要插诊断/自修复

                elif phase == "REFLECTING":
                    # llm 模式:问 LLM 拿下一 Action;规则模式:决定下一步或转入 B
                    if self.config.planner_mode == "llm":
                        action = self._llm_next_action(state, record)
                        if action is None:           # LLM 不再发 tool_calls → 收尾
                            phase = "REPORTING"
                            continue
                        result = self._execute_action(action, state, record)
                        self._reflect(result, state)
                        if state.fatal or state.goal_achieved:
                            phase = "REPORTING"
                    else:
                        # 规则模式 REFLECTING:若 sim 失败且未调过 B,触发 self_heal
                        action = self._rule_after_reflect(state, request)
                        if action is None:
                            phase = "REPORTING"
                        else:
                            phase = "EXECUTING"
                            plan.actions = [action]  # 注入单步继续 EXECUTING

                elif phase == "REPORTING":
                    report = self._finalize(record, state, request)
                    self.runner.finalize(record, report)
                    phase = "DONE"

            return report
        except Exception as e:
            # 未预期异常:不向调用方抛,落 failed
            record.status = "failed"
            self.runner.mark_crash(record, str(e))
            return self._emergency_report(record, str(e))

### 6.2 规则 planner:确定性 self_heal plan 生成

    def _make_plan(self, request: RunRequest, state: PlannerState) -> Plan:
        if request.kind == "self_heal":
            actions = [
                Action("yosys_synth", {"rtl": state.rtl_path, "top": state.top_module},
                       rationale="综合以拿到 netlist + num_cells"),
                Action("iverilog_sim", {"rtl": state.rtl_path, "tb": state.tb_path},
                       rationale="先跑一次基线仿真,确认是否已通过"),
            ]
            if state.lib_path and state.clock_name:
                actions.append(
                    Action("opensta_timing",
                           {"netlist": ARTIFACT_FROM_STATE, "lib": state.lib_path, "clock": state.clock_name},
                           rationale="时序评估(若有 liberty + clock)"))
            # diagnose 与 self_heal 由 _rule_after_reflect 按结果动态注入,不写死进初始 plan
            return Plan(actions=actions, mode="rule")
        elif request.kind == "diagnose":
            # v1.2:CLI 子命令 eda diagnose 独立暴露(契约 §2.0 + §11 冲突3 裁决为 3 子命令)
            # plan = [yosys_synth, iverilog_sim, (opensta_timing), skill_diagnose]
            # skill_diagnose 的 tool_results 由 _rule_after_reflect 用 _collect_upstream_results(state) 动态注入(同 self_heal 分支)
            return Plan(actions=actions, mode="rule")
        # kind=="run" 由 self_heal 覆盖(契约 §2.0)
        return Plan(actions=[], mode="rule")

    def _rule_after_reflect(self, state: PlannerState, request: RunRequest) -> Action | None:
        # 仿真未过 → 诊断 → 自修复;仿真已过且(若跑 STA)wns>=0 → 收尾
        sim = state.last_sim or {}
        sta = state.last_sta
        if sim.get("passed") is True and (sta is None or (sta.get("wns") or 0) >= 0):
            state.goal_achieved = True
            return None
        if state.last_diagnose is None and not sim.get("passed", False):
            return Action("skill_diagnose",
                          {"tool_results": self._collect_upstream_results(state)},
                          rationale="仿真未过,先归因")
        if state.last_diagnose is not None and not state.goal_achieved:
            # v1.2:扩展 args 含 goal/lib/clock/top_module(契约 §2.3 args schema)+ reserved _remaining_budget_s
            remaining = self.budget.remaining_s()
            return Action("skill_self_heal",
                          {"rtl": state.rtl_path, "tb": state.tb_path,
                           "diagnose": state.last_diagnose,
                           "max_iter": request.max_iter or self.config.skill_max_iterations,
                           "goal": request.goal,                 # v1.2 新增
                           "lib": request.lib_path,               # v1.2 新增
                           "clock": request.clock_name,           # v1.2 新增
                           "top_module": request.top_module,      # v1.2 新增
                           "_remaining_budget_s": remaining},     # reserved,as_tool 拆包
                          rationale="调 B 自修复闭环")
        return None

    # v1.2:B 报 all_pass 后,C 强制追加独立 iverilog_sim 验证步(非 open question,契约 §10)
    def _verify_after_self_heal(self, state: PlannerState, request: RunRequest) -> Action | None:
        if state.independent_verify_passed is not None:
            return None   # 已验证过,不重复
        best_ref = state.best_rtl_ref
        if best_ref is None:
            return None
        # 用 B 的 best/rtl.v 跑一次独立 iverilog_sim(不接 STA,只验仿真)
        return Action("iverilog_sim",
                      {"rtl": ARTIFACT_FROM_STATE, "_artifact_ref": best_ref, "tb": state.tb_path},
                      rationale="B 报 all_pass 后的第三方独立验证")
    # _resolve_args 对 _artifact_ref 字段做特殊处理:用 best_ref 替换 rtl(见 §6.3)

### 6.3 Action 执行 + 工件传递 + Registry 校验 + 单一 iteration 计数出口

    def _execute_action(self, action: Action, state: PlannerState, record: RunRecord) -> ToolResult:
        tool = self.registry.get(action.tool_name)
        call = ToolCall(
            name=action.tool_name,
            args=self._resolve_args(action.args, state),  # 工件传递:替换 ARTIFACT_FROM_STATE 占位
            caller="planner",
            llm_tool_call_id=action.llm_tool_call_id,
        )
        if tool is None:                                   # 契约 §2.5 幻觉校验
            result = self._tool_not_found_result(call)
        elif not self._args_match_schema(call.args, tool.schema):
            result = self._args_invalid_result(call, tool)
        else:
            result = tool(call)                            # 真执行
        self.runner.append_step(record, call, result, skill_name=None, iter=None)
        state.history.append({"call": call, "result": result})   # v1.2:_build_messages 回灌用
        state.iteration += 1                               # v1.2:统一在此 +1(_reflect 不再碰计数器)
        return result

    def _args_match_schema(self, args: dict, schema: dict) -> bool:
        # v1.2:下划线前缀字段(reserved,如 _remaining_budget_s / _artifact_ref)豁免 jsonschema 校验
        visible = {k: v for k, v in args.items() if not k.startswith("_")}
        return jsonschema_valid(visible, schema)

    def _resolve_args(self, args: dict, state: PlannerState) -> dict:
        # v1.2:用常量 ARTIFACT_FROM_STATE 替代裸字符串 "<from_state>"
        resolved = dict(args)
        for k, v in list(resolved.items()):
            if v == ARTIFACT_FROM_STATE and k == "netlist":
                resolved[k] = state.netlist_ref            # 上游 yosys_synth.artifacts[0]
        # 独立验证步:_artifact_ref 替换 rtl
        if "_artifact_ref" in resolved and resolved.get("rtl") == ARTIFACT_FROM_STATE:
            resolved["rtl"] = resolved.pop("_artifact_ref")   # 用 best_ref 的实际路径解析
        return resolved

    def _reflect(self, result: ToolResult, state: PlannerState) -> None:
        # v1.2:不再碰 state.iteration(统一在 _execute_action 出口 +1);只更新业务字段
        p = result.parsed
        name = result.tool
        if name == "yosys_synth":
            state.last_synth = p
            if result.artifacts:
                state.netlist_ref = result.artifacts[0]    # 喂给 sim/sta
            if p.get("success") is False:
                state.fatal = self._is_fatal(p)            # rtl_syntax 致命 → 早停
        elif name == "iverilog_sim":
            state.last_sim = p
            # v1.2:独立验证步(若 best_rtl_ref 已设)用本次结果更新 independent_verify_passed
            if state.best_rtl_ref is not None and state.independent_verify_passed is None:
                state.independent_verify_passed = (p.get("passed") is True)
                if state.independent_verify_passed:
                    state.goal_achieved = True   # 第三方验证通过才最终置 goal_achieved
        elif name == "opensta_timing":
            state.last_sta = p
        elif name == "skill_diagnose":
            state.last_diagnose = p
            # 若 root_causes 含 fatal → 早停(v1.2:diagnose namespace 也按 severity 聚类)
            for rc in p.get("root_causes", []):
                if rc.get("severity") == "fatal":
                    state.fatal = True
        elif name == "skill_self_heal":
            cause = p.get("_convergence_cause")
            best = p.get("_best_iter")
            state.best_rtl_ref = p.get("fixed_rtl_ref")    # v1.2:存 best_ref 给独立验证步
            if cause == "all_pass":
                # v1.2:不立即置 goal_achieved,等独立验证步通过后再置(见 _verify_after_self_heal)
                state.pending_self_heal_all_pass = True
            # iteration 计数已在 _execute_action 出口统一 +1,_reflect 不再重复

### 6.4 LLM planner(ReAct 单步,tool-use 回环闭合)

    def _llm_next_action(self, state: PlannerState, record: RunRecord) -> Action | None:
        messages = self._build_messages(state)            # system + 历史 user/assistant/tool
        tools = self.registry.to_llm_tools()
        resp: LLMResponse = self._call_llm_safe(messages, tools)   # 含重试与降级
        if not resp.tool_calls:
            return None                                    # LLM 认为目标达成,收尾
        tc = resp.tool_calls[0]                            # MVP 每轮取首个 tool_call
        return Action(
            tool_name=tc["name"],
            args=tc["args"],
            rationale=resp.text[:200],
            llm_tool_call_id=tc.get("id"),
        )

    def _call_llm_safe(self, messages, tools) -> LLMResponse:
        try:
            return self.llm.chat(messages, tools=tools, temperature=0.0, max_tokens=4096)
        except Exception as e:
            # 降级:LLM 不可用时回退到规则 planner 的下一步(契约 feasibility)
            self.runner.log_event("llm_failed_fallback_to_rule", str(e))
            return LLMResponse(text="", tool_calls=[], tokens_in=0, tokens_out=0,
                               provider=self.llm.provider_name, model="")

    def _build_messages(self, state) -> list[Message]:
        msgs = [Message(role="system", content=SYSTEM_PROMPT)]
        msgs.append(Message(role="user", content=self._render_task(state)))
        # 回灌历史 ToolResult 为 role="tool"(契约 §2.5 回环序列化)
        for step in state.history:
            r = step["result"]
            msgs.append(Message(
                role="tool",
                content=json.dumps({"status": r.status, "parsed": r.parsed, "error_hint": r.error_hint}),
                tool_call_id=step["call"].llm_tool_call_id,
            ))
        return msgs

### 6.5 预算仲裁(双层,契约 §2.3)

    class Budget:
        def __init__(self, run_budget_s: int, started_at: float):
            self.run_budget_s = run_budget_s
            self.started_at = started_at

        def elapsed_s(self) -> float:
            return time.monotonic() - self.started_at

        def remaining_s(self) -> float:
            return max(0.0, self.run_budget_s - self.elapsed_s())

        def exhausted(self) -> bool:
            return self.remaining_s() <= 0

    # C 调 Skill 前:
    #   remaining = budget.remaining_s()
    #   下传:Action.args["_remaining_budget_s"] = remaining
    #   Skill.run 内部:self.budget_s = min(self.budget_s, remaining_budget_s)
    # 工具子进程超时:settings.eda.tool_timeout_s(由 Tool 自己 subprocess timeout,C 不再包一层)

### 6.6 僵尸 run 自愈(契约 §2.4)

    # runner 启动时调用(CPlanner 构造前由 cli.py 触发一次)
    def runner.scavenge_zombies(run_budget_s: int) -> None:
        for run_dir in glob("runs/*"):
            run_json = run_dir / "run.json"
            if run_json.status == "running" and now - run_json.created_at > run_budget_s * 2:
                run_json.status = "failed"
                write run_json
                write status.json with {"crash": "scavenged_zombie"}

---

## 7. 与共享契约的对接(用到哪些 Tool / Skill / 工件 / LLM provider)

### 7.1 C 用到的 Tool / Skill(经 Registry)

| Registry name | 类别 | C 何时调 | C 读哪些 parsed 字段 |
|---|---|---|---|
| `yosys_synth` | synth | self_heal 初始 plan 第 1 步 | success / num_cells / cell_area / module_name / errors |
| `iverilog_sim` | sim | 第 2 步 | passed / num_passed / num_failed / fail_signals |
| `opensta_timing` | sta | 第 3 步(若有 lib+clock) | wns / tns / num_violating_endpoints |
| `skill_diagnose`(A) | skill | sim/sta 失败后 | root_causes[].severity / needs_rtl_patch / summary |
| `skill_self_heal`(B) | skill | A 判需 patch 后 | _skill_status / _convergence_cause / _best_iter / _iterations |

### 7.2 C 用到的工件协议

- **artifact_ref**(§2.4):`yosys_synth.artifacts[0]` 即 netlist 的 artifact_ref,C 存进 `state.netlist_ref`,在 `opensta_timing` 的 args 里透传(`_resolve_args`)。B 的子步骤产物也走同一协议,落同一 `runs/<run_id>/` 下。
- **RunRecord / StepRecord**(§2.4):C 经 runner 写父 RunRecord;B 子步骤由 B 自己经 runner 追加(skill_name="self_heal", iter=n)。C 不写 B 的子步骤,但读取它们以渲染 report。
- **experiment_manifest.json**(§2.4):self_heal 流程终态时,C 调 `report.render` 顺手产出(对比实验度量)。

### 7.3 C 用到的 LLM provider

- `make_provider(settings.llm_provider)` → `CountingProvider(ClaudeProvider)`(契约 §2.5)。
- C 通过 `self.llm.chat(messages, tools=registry.to_llm_tools(), temperature=0.0, max_tokens=4096)` 调用。
- tool_calls 形状:`{"id": str|None, "name": str, "args": dict}`,C 取首项构造 `Action(llm_tool_call_id=id)`。
- 回灌:`Message(role="tool", content=json.dumps({status,parsed,error_hint}), tool_call_id=id)`。
- 计数:run 结束从 CountingProvider 读 `llm_calls/tokens_in/tokens_out` 填 RunRecord。
- MVP 只 Claude;Qwen/DeepSeek 走 OpenAICompat 为加分项(C 代码零改,只换 provider)。

### 7.4 C 用到的错误码(§2.6)

见 §5.6 表。C 不发明新 namespace,全部复用 MVP 13 码(`eda.tool_not_found / tool_args_invalid / subprocess_failed / subprocess_timeout / llm_call_failed / budget_exhausted / internal / schema_mismatch` 等)。

---

## 8. 依赖(EDA 工具 / Python 库 / LLM,带建议版本)

### 8.1 EDA 工具(WSL2 Ubuntu,契约 §6)

| 工具 | 安装 | 版本建议 | C 是否直接依赖 |
|---|---|---|---|
| yosys | `apt install yosys` | ≥0.40(或源码最新) | 否(经 yosys_synth Tool) |
| iverilog | `apt install iverilog` | ≥12.0 | 否(经 iverilog_sim Tool) |
| vvp | 随 iverilog | — | 否 |
| OpenSTA | `apt install opensta` 或源码 | ≥2.3.4 | 否(经 opensta_timing Tool) |

> Day0.5 前置 gate:`yosys -p "synth -top counter; stat -json"` 跑通 hello-world,工具没装好前不写 C 的 e2e 测试。

### 8.2 Python 库(对齐契约 §6 pyproject.toml)

    eda-agent 依赖(C 视角用到的):
        anthropic>=0.40        # ClaudeProvider(LLM provider 加分项才需 openai>=1.30)
        jsonschema>=4          # C 校验 LLM tool_calls.args 是否符 Tool.schema
        tomli; python_version<'3.11'   # settings.toml 解析
        # CLI:argparse(标准库)或 typer>=0.12(可选,带 --help 友好)
        # MCP 加分项:mcp>=1.0(FastMCP),列为 optional dependency

> C 不直接依赖 subprocess(由 Tool 封装);不依赖 LangGraph/LangChain(契约硬要求自研轻量循环)。

### 8.3 LLM

| provider | MVP | 模型 | 何时用 |
|---|---|---|---|
| ClaudeProvider | 是 | claude-sonnet-4 | settings.planner_mode="llm" 时;A/B 也用它 |
| OpenAICompatProvider | 加分项 | qwen-plus / deepseek-chat | 切 provider 对比实验,C 代码零改 |

API key 走环境变量 `ANTHROPIC_API_KEY` / `DASHSCOPE_API_KEY` / `DEEPSEEK_API_KEY`,不进 settings.toml(契约 §6)。

### 8.4 运行环境

- Win11 + RTX3060 Laptop,agent 层 Python(本地,无需 GPU)。
- EDA 工具跑 WSL2 Ubuntu,经 `wsl -e <cmd>` 或 WSL 内直接调。
- Python 3.10+(契约 pyproject requires-python=">=3.10")。

---

## 9. 实现步骤拆解(给新终端的有序子任务,每步带验证方法)

> 假设 Day1-2 的基座(contracts/registry/runner/settings)已就绪。以下聚焦 C 组件自身的实现顺序。每个子任务给"新终端照着能直接领"的指令 + 量化验证方法。

### Step C1 —— CPlanner 骨架 + 状态机(不接 Tool,空转)

终端指令:

    cd D:/eda\ agent\ system
    # 创建文件
    touch src/eda_agent/planner/__init__.py
    touch src/eda_agent/planner/c_planner.py
    touch src/eda_agent/planner/prompts.py
    touch src/eda_agent/planner/budget.py
    # 写 CPlanner.execute 的状态机骨架(§6.1),各分支先 pass / 占位

验证方法:

    pytest tests/test_planner_smoke.py -q
    # 测试内容:用 stub registry(只注册一个 echo tool)+ RunRequest(kind="self_heal")
    # 断言:execute 返回 RunReport,status in {"ok","failed","budget_exhausted"},
    #       run_id 形如 \d{8}_\d{6}_[0-9a-f]{4},runs/<run_id>/run.json 存在且 status != "running"。

通过门槛:状态机能从 PLANNING 走到 DONE,不抛异常。

### Step C2 —— Budget 预算仲裁器

终端指令:

    # 写 src/eda_agent/planner/budget.py,实现 §6.5 的 Budget 类
    # 写测试

验证方法:

    pytest tests/test_budget.py -q
    # 用例:
    #   1. started_at=now, run_budget_s=10 → remaining_s() 在 [9.9,10]
    #   2. time.sleep(0.2) → remaining_s() 减少 0.2(±0.05)
    #   3. run_budget_s=0 → exhausted() 立即 True
    #   4. remaining_s() 永不 < 0(max(0, ...))

通过门槛:4 用例全过。

### Step C3 —— RulePlanner(确定性 self_heal plan)

终端指令:

    # 写 src/eda_agent/planner/rule_planner.py,实现 §6.2 _make_plan
    # 用 stub EDA Tool(yosys_synth/iverilog_sim/opensta_timing 都返回固定 ToolResult)

验证方法:

    pytest tests/test_rule_planner.py -q
    # 用例:
    #   1. RunRequest(kind="self_heal", rtl, tb, lib, clock) → Plan.actions 含 3 步(yosys/iverilog/opensta)
    #   2. 无 lib_path → 只 2 步(无 opensta)
    #   3. action.tool_name 全部在 {"yosys_synth","iverilog_sim","opensta_timing"}

通过门槛:3 用例全过,action.rationale 非空。

### Step C4 —— Action 执行 + 工件传递 + Registry 校验

终端指令:

    # 在 c_planner.py 实现 _execute_action / _resolve_args / _args_match_schema
    # 写 stub tool 测试

验证方法:

    pytest tests/test_action_exec.py -q
    # 用例:
    #   1. registry 有 yosys_synth → 执行,steps/001_yosys_synth/{tool_call.json,tool_result.json} 存在
    #   2. action.args["netlist"]="<from_state>",state.netlist_ref={"run_id":"x","rel_path":"synth/netlist.v"}
    #      → _resolve_args 后 args["netlist"]=={"run_id":"x","rel_path":"synth/netlist.v"}
    #   3. registry.get("not_exist") → ToolResult.status="error",error_code="eda.tool_not_found"
    #   4. args 缺必填字段 → error_code="eda.tool_args_invalid"

通过门槛:4 用例全过,tool_call.json 含 llm_tool_call_id(可为 null)。

### Step C5 —— 端到端规则 pipeline(stub 全栈,不真跑 EDA)

终端指令:

    # 串联 C1-C4 + stub A/B skill
    # 实现 _rule_after_reflect(§6.2 下半)

验证方法:

    pytest tests/test_e2e_stub.py -q
    # 场景:stub yosys ok / stub iverilog passed=False / stub diagnose 给 needs_rtl_patch=True
    #       / stub self_heal 返回 _convergence_cause="all_pass"
    # 断言:
    #   1. RunReport.status="ok"
    #   2. report.md 含 "self_heal" "convergence" "all_pass" 字样
    #   3. steps/ 下有 5 个子目录(001~005: yosys/iverilog/sta?/diagnose/self_heal)
    #   4. metrics.sim_passed==True(因为 B 报 all_pass 后 C 把 state.goal_achieved=True,report 反映)

通过门槛:全过,这是"无 EDA 工具/无 LLM 也能 e2e 跑通"的保底测试(feasibility 兜底)。

### Step C6 —— LLMPlanner(ReAct + tool-use 回环)

终端指令:

    # 写 src/eda_agent/planner/llm_planner.py(§6.4)
    # 用 mock provider(返回预设 tool_calls)

验证方法:

    pytest tests/test_llm_planner.py -q
    # 用例:
    #   1. mock provider 首轮发 tool_calls=[{name:"yosys_synth",args:{rtl:"x"}}] → C 构造 Action 执行
    #   2. tool_calls 含不存在的 tool 名 → C 构造 eda.tool_not_found 回灌 Message(role="tool")
    #   3. tool_calls 为空 → C 返回 None,phase 转 REPORTING
    #   4. provider.chat 抛异常 → C 降级(_call_llm_safe 返回空 tool_calls)

通过门槛:4 用例全过,回灌 Message 的 tool_call_id 与上游一致。

### Step C7 —— CLI 三子命令(self-heal / diagnose / report)

终端指令:

    # 写 src/eda_agent/cli.py
    # 装包:在 pyproject.toml [project.scripts] eda = "eda_agent.cli:main"

验证方法:

    pip install -e .
    eda --help                      # 应列出 self-heal / diagnose / report 三个子命令
    eda self-heal --rtl data/examples/counter/rtl.v --tb data/examples/counter/tb.v \
        --goal "pass all tests" --mode rule
    # 断言:退出码 0 或 1 或 2(非 64);stdout 打印 runs/<run_id>/report.md 路径;该路径文件存在
    eda diagnose --rtl data/examples/counter/rtl_bitwidth_bug.v --tb data/examples/counter/tb.v
    # 断言:退出码非 64;steps/ 下含 skill_diagnose 子目录;diagnose/report.md 存在
    eda report <上一步的 run_id>     # 应把 report.md 打到 stdout
    eda report not_exist            # 退出码 64,stderr 报错

通过门槛:6 个交互断言全过。

### Step C8 —— 预算/迭代上限单测

终端指令:

    # 写 tests/test_planner_limits.py

验证方法:

    pytest tests/test_planner_limits.py -q
    # 用例:
    #   1. settings.budget.run_budget_s=1, stub tool 每步 sleep 0.6 → 第 2 步后 budget_exhausted,
    #      RunReport.status="budget_exhausted"
    #   2. planner_max_iterations=2, 每步都失败(不死循环)→ iteration 到 2 后转 REPORTING
    #   3. 致命错(stub diagnose 返回 severity="fatal")→ state.fatal=True,立即 REPORTING

通过门槛:3 用例全过。

### Step C9 —— 真跑 EDA 端到端(needs_eda marker)

终端指令:

    # 前置:WSL2 yosys/iverilog/opensta 已装(Day0.5 gate)
    # 用 data/examples/counter + 预 inject bug
    pytest tests/test_e2e_pipeline.py -m needs_eda -q

验证方法:

    # 场景:rtl_bitwidth_bug.v + tb.v, goal="pass all tests", mode=rule
    # 断言:
    #   1. RunReport.status in {"ok","failed","budget_exhausted"}
    #   2. steps/001_yosys_synth/tool_result.json parsed.success==True(yosys 综合 bitwidth 错也能过)
    #   3. steps/002_iverilog_sim/tool_result.json parsed.passed==False(基线失败,符合预期)
    #   4. steps/ 下含 skill_self_heal 子目录
    #   5. experiment_manifest.json 含 design_id / fault_type / baseline_pass_rate / self_heal_pass_rate 字段

通过门槛:5 断言全过(成功修复或失败案例都算交付,契约 §7 验收基线)。

### Step C10 —— MCP server(加分项,有时间再做)

终端指令:

    # 写 src/eda_agent/mcp_server.py(FastMCP)
    # 实现 self_heal / report 两个 MCP tool,签名与 SDK 一致
    # to_mcp_tools() 把 registry 每个 Tool 暴露

验证方法:

    # 用 Claude Code 或 Cursor 接 MCP server,调 eda_agent.self_heal
    # 断言:返回 RunReport dict,status 字段在三种取值内
    # 降级:做不完则 to_mcp_tools 仅留签名存根,不影响 MVP 验收

---

## 10. 验收标准(量化指标 + 通过门槛 + 测试方法,可直接照着验收)

> 全部测试命令在 `D:/eda agent system` 下执行。`needs_eda` marker 需 WSL2 工具就绪。

### 10.1 接口契约对齐(硬指标,必过)

| 指标 | 通过门槛 | 测试方法 |
|---|---|---|
| C 不重新定义契约类型 | `contracts.py` 外 C 文件无 `class ToolResult\|class RunReport\|class SkillResult` 等同名定义 | `grep -rE "^class (ToolResult\|RunReport\|SkillResult\|RunRequest\|ErrorItem)" src/eda_agent/planner/ src/eda_agent/cli.py` 应无输出 |
| Tool/Skill 调用全经 Registry | C 源码无 `subprocess.run` / `subprocess.Popen` 调用 | v1.2:改用 Python ast.walk 跨平台检查(替代 grep),脚本 `scripts/check_no_subprocess.py` 遍历 src/eda_agent/planner/ 的 AST,断言无 ast.Call(func=ast.Attribute(value=ast.Name('subprocess'))) |
| RunRequest/RunReport schema 一致 | CLI/MCP 入参出参与 dataclass 字段同名同义 | 人工核对 §5.4/5.5 与 contracts §2.0,或写 `test_schema_roundtrip`:RunRequest→json→RunRequest 字段不变 |

### 10.2 功能验收(量化)

| 验收项 | 量化门槛 | 测试方法 |
|---|---|---|
| 状态机能跑完 | 任意 RunRequest 不抛异常,RunReport.status ∈ {ok,failed,budget_exhausted} | `pytest tests/test_planner_smoke.py` 全过 |
| 规则 pipeline e2e(stub) | steps/ 下 ≥4 个子目录,report.md 存在,RunReport.metrics 含 sim_passed 字段 | `pytest tests/test_e2e_stub.py` 全过 |
| LLM planner tool-use 回环 | 回灌 Message 含 tool_call_id 且与上游 tool_calls.id 一致;幻觉 tool 名返回 eda.tool_not_found | `pytest tests/test_llm_planner.py` 4 用例全过 |
| 预算耗尽控制 | run_budget_s=1s 时,RunReport.status="budget_exhausted",无步数爆炸(≤ planner_max_iterations+1) | `pytest tests/test_planner_limits.py::test_budget` |
| 最大迭代控制 | planner_max_iterations=2 时,iteration 计数到 2 即转 REPORTING | `pytest tests/test_planner_limits.py::test_max_iter` |
| 致命错早停 | A 返回 severity="fatal" → state.fatal=True,后续不执行新 Action | `pytest tests/test_planner_limits.py::test_fatal` |
| v1.2 退出码一致性 | status=budget_exhausted→退出码 2;failed→1;report 不存在→64 | `pytest tests/test_cli_exit_codes.py`(3 用例:stub B 返回 budget_exhausted / fatal / report not found) |
| 工件传递 | opensta 的 args.netlist 等于 yosys_synth.artifacts[0] 的 artifact_ref | `pytest tests/test_action_exec.py::test_artifact_pass` |
| Tool 注册可扩展 | 注册一个 stub tool "stub_ping",C 能发现并调用,parsed._schema.name="stub_ping" | `pytest tests/test_registry_extensibility.py`(新写:注册 stub → C plan 含它 → 执行成功) |

### 10.3 端到端真跑(EDA 工具就绪时,needs_eda;v1.2 拆分为"可执行性"与"修复效力"两个独立必过项)

| 验收项 | 量化门槛 | 测试方法 |
|---|---|---|
| (必过A) e2e 可执行性 | RunReport.status ∈ {ok,failed,budget_exhausted};至少触发 B 一次;experiment_manifest.json 含 10 个必填字段(含 baseline_run_id/candidates_at_best_score) | `pytest tests/test_e2e_pipeline.py -m needs_eda` |
| (必过B) 修复效力 | 至少 1 个 inject bug 的 RunReport.status=ok 且 parsed._convergence_cause="all_pass" | 跑 8 个 inject bug,统计 experiment_summary.json.by_fault_type |
| (必过C) 独立验证步存在 | B all_pass 的 run,父 RunRecord.steps 含一个非 self_heal 的 iverilog_sim 步(独立验证),且 passed=True | `pytest tests/test_independent_verify.py -m needs_eda` |
| 失败案例可追溯(加分) | 失败 run 的 trajectory 含至少 1 轮 patch diff + sim_result + 回退事件(若发生) | 人工查 `runs/<run_id>/skills/self_heal/iter_*/rtl_patch.diff` 存在 |
| LLM 计数准确 | RunRecord.llm_calls 与实际 provider 调用次数一致(±0) | CountingProvider 单测 + e2e 后比对 run.json.llm_calls |

> v1.2 删除 v1.1 "成功或失败案例皆可"的并集表述:可执行性(必过A)与修复效力(必过B)是两个独立必过项;失败案例可追溯单独作为加分项,不进必过。评审无工具时,提交 runs/eval_snapshot/ 下准备期预跑的真实 run 目录 + experiment_manifest.json 截图作为替代证据。

### 10.4 文档/可集成性(完赛奖第 2 条)

| 验收项 | 量化门槛 | 测试方法 |
|---|---|---|
| CLI 可被新终端直接调用 | 新终端 `pip install -e . && eda --help` 列出 self-heal/diagnose/report | 人工执行 |
| SDK 一行可调 | `from eda_agent import run_pipeline; run_pipeline(req, settings)` 返回 RunReport | 人工执行 |
| MCP(加分项) | Claude Code/Cursor 能调 eda_agent.self_heal 拿到 RunReport dict | 人工接 server 验证;做不完降级签名存根 |
| 三处接口签名一致 | CLI/SDK/MCP 入参字段名与 RunRequest dataclass 完全一致 | 非技术成员执行字段级互查(Day8 任务) |

### 10.5 通过门槛汇总(给验收人的一句话;v1.2 拆分可执行性与修复效力)

> C 组件验收通过的充要条件:**10.1 三项全过 + 10.2 九项全过(含退出码一致性)+ 10.3 必过A(可执行性)+ 必过B(修复效力,至少 1 个 all_pass)+ 必过C(独立验证步)+ 10.4 前两项可人工执行**。MCP 与 OpenAICompat 为加分项,不阻塞。评审无工具时,10.3 用准备期预跑快照 runs/eval_snapshot/ 作为替代证据(契约 §12 #15)。

---

## 11. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| WSL2 工具 Day0.5 没装好 | e2e 测试无法跑,完赛奖第 1 条"真实可跑"悬 | Day0.5 前置 gate(契约 §7):先 `wsl yosys -p "synth"` 跑通再写 Tool;C 的 stub e2e(Step C5)保底不依赖真工具 |
| LLM tool_calls 幻觉(调不存在 tool) | 循环空转 / 抛异常 | 契约 §2.5 强制 registry.get 校验 + 构造 eda.tool_not_found 回灌,不中断;Step C6 用例 2 覆盖 |
| LLM API 限流/不可用 | e2e 卡死 | `_call_llm_safe` 降级到规则 planner(feasibility 保底);规则 planner 不依赖 LLM |
| 预算被 B 独占(双层仲裁失效) | C 后续步骤没预算,实验不可复现 | 契约 §2.3:C 调 B 前算 remaining 并下传 remaining_budget_s;B 内部取 min(self.budget_s, remaining);Step C2 单测 |
| B 内部 patch 把 RTL 改坏无回退 | 失败案例无结构,评委质疑 agentic 真实性 | 契约 §2.3 patch 回退契约:num_passed 回退则恢复上一版 RTL,trajectory 记 best_iter;C 在 report 里显式渲染回退事件 |
| step 目录爆炸(B 迭代多轮) | run 目录巨大,diff 困难 | ToolResult.stdout/stderr 头 32KB+尾 32KB 裁剪 + .full.log 落盘(契约 §2.1);VCD 二进制原样但不进 git |
| Python 3.14(本机)与契约 ≥3.10 兼容 | tomli 在 3.11+ 是 stdlib tomllib | pyproject 条件依赖 `tomli; python_version<'3.11'`;3.14 用 tomllib |
| iverilog 无结构化输出 | num_passed/num_failed 解析失败 | 契约 §2.2 TB 打印协议(TEST_PASS/TEST_FAIL 固定标记行);未打印则返回 None,不崩 |
| 僵尸 run(进程崩没写终态) | 对比实验 diff 误判 | 契约 §2.4 runner.scavenge_zombies:CPlanner 构造前 cli.py 触发一次扫描 |
| 2 人工期紧(OpenSTA 上调 MVP) | Day3 工程量翻倍 | 契约 §11 冲突2:OpenSTA stat 走 -json,与 yosys 同量级;C 侧只多一个 Action,无新代码模式 |

---

## 12. 待确认决策(列给用户的开放问题)

1. **planner_mode 默认值**:[v1.2 已裁决] 默认 `llm`(契约 §11 冲突7),契合 Track 01 agentic 充分性,避免被评委会判定"会循环的 wrapper";`rule` 仅作 LLM 不可用时的降级路径。演示主路径必须用 llm 模式 run 作为主 demo。本决策关闭。
2. **LLM planner 每轮取首个 tool_call 还是支持并行多 tool_calls**:MVP 取首个(简单,可调试)。若评审更看重"agent 一次规划多步",可扩展。请确认 MVP 取首。
3. **CLI 是否要 `--dry-run`(只 plan 不执行)**:便于演示任务分解能力,但契约 §2.0 没列。是否加?(加 = 0.5 人天)
4. **report.md 的渲染深度**:MVP 一页(状态+steps 表+metrics);是否要嵌入 trajectory 可视化(mermaid)?非技术成员可做,但集训 4 天工期紧。
5. **MCP server 是否进 MVP**:契约列为加分项。如果评审现场会用 Claude Code 调,MCP 是大加分;如果只看 report.md,可不做。请定夺优先级。
6. **experiment_manifest.json 的 fault_type 分类粒度**:契约 §2.4 列 4 类(syntax/comb_logic/timing_reset/bitwidth)。非技术成员准备 inject bug 时,3 类够不够?是否需第 4 类凑齐?
7. **RunReport.metrics 是否标准化字段集**:[v1.2 已裁决] 契约 §2.4 已锁定 15 字段标准集(含 self_heal_pass_rate / baseline_pass_rate),本文 §5.5 已对齐。原 minor#13(v1.1 允许 MVP 自由填)已升级处理。本决策关闭。
8. **C 是否要在 self_heal 收敛后自动追加一次 final iverilog_sim 验证 B 的 patch 真的过**:[v1.2 已裁决] 必加,作为 MVP 主流程非 open question。B 报 all_pass 后 C 强制追加独立 iverilog_sim 验证步(读 B.best/rtl.v),通过后才置 goal_achieved(契约 §10 agentic 自检 + §10.3 必过C)。多 1 步 tool call,演示可信度显著提升。本决策关闭。

---

## 自检(交付前核对)

- [x] 与契约对齐:import contracts dataclass,不自创同名类型;Tool/Skill 调用全经 Registry;artifact_ref 协议;双层预算仲裁;tool-use 回环;RunRequest/RunReport/RunRecord/StepRecord 字段视角正确。
- [x] 12 节齐全:定位边界 / 系统位置(含调用关系图)/ 内部架构(含数据流图)/ 核心数据结构 / 对外接口契约 / 关键流程伪代码 / 契约对接 / 依赖 / 实现步骤(10 个有序子任务)/ 验收标准(量化)/ 风险对策 / 待确认决策。
- [x] 验收量化:§10 全部门槛用 `pytest` 命令或人工可执行断言,含退出码、字段名、计数。
- [x] 实现步骤细到新终端可领:每个 Step 给文件路径 + 终端指令 + 验证 pytest 用例 + 通过门槛。
- [x] 伪代码 4 空格缩进,无反引号代码块。
- [x] 2 人可实现:Step C1-C9 是 MVP(C10 加分),规则 planner 保底无需 LLM 即可 e2e;LLM planner 与 MCP 列加分。
- [x] agentic 体现:C 用 plan-execute/ReAct 组织工具迭代;调 B 触发"综合→仿真→诊断→patch→重试"闭环;读 _convergence_cause/_best_iter 作智能证据。
