# CONTRACTS.md — Agentic EDA 系统共享契约(宪法) v1.2

> 本文件是 Agentic EDA Agent System 的**最高约束文档**。A(诊断器)/ B(RTL 自修复闭环)/ C(Planner / Tool-Use 层)三个组件,以及 Tool Registry、工件存储、LLM provider 抽象,都必须**严格遵守本文定义的数据结构与接口签名**。任何组件若与本文件冲突,以本文件为准。
>
> 设计原则:开闭原则(加新 EDA 工具 / 加新 skill 不改核心)、最小可用(MVP 聚焦四条核心目标)、可追溯(每次 run 一个目录,满足实验记录要求)、可集成(对外接口有版本锚点,schema 漂移可被检测)。
>
> 约定:所有抽象用 Python dataclass / Protocol / type hint 写死字段。代码 4 空格缩进,**禁止使用反引号代码块**(本文件正文用 4 空格缩进代码段呈现签名)。
>
> 版本治理(消除 v1.1 的版本号歧义):
> - **CONTRACT_VERSION**(常量)= `"0.1.0"`,是 `parsed._schema.contract_version` 与 `run.json.contract_version` 的唯一锚点。任何组件引用必须 `from eda_agent.contracts import CONTRACT_VERSION`,禁止裸字符串。
> - **文档版本**(`doc v1.2`):本文档的人类可读修订号,与 CONTRACT_VERSION 解耦。doc v1.2 = 在 CONTRACT_VERSION=0.1.0 基础上的第 5 次修订(集成断点修复)。
>
> v1.2 相对 v1.1 变更摘要(全部为 blocker/major 集成断点修复,见 ARCHITECTURE.md §风险与对策 与各组件文档):
> - `artifact_ref` 显式定义为**工厂函数** `def artifact_ref(run_id: str, rel_path: str) -> dict[str,str]`,contracts.py 必须导出(消除"函数 vs dict"三处矛盾)。
> - §2.6 namespace 登记表正式加入 `diagnose`(A)与 `heal`(B)两个 namespace 及错误码。
> - `skill_self_heal.as_tool` args schema 扩展为 `{rtl, tb, diagnose, max_iter, goal, lib, clock, top_module}`(消除 B 内部 goal/lib/clock 永不触发的死路)。
> - `remaining_budget_s` 传递通道明确:由 as_tool 适配层从 `ToolCall.args.pop("_remaining_budget_s", None)` 取出,作为 `Skill.run` 的位置参数下传;`_remaining_budget_s` 为 reserved 字段,registry.to_llm_tools 时剥离,不进 LLM 可见 schema,C 的 `_args_match_schema` 对下划线前缀字段豁免。
> - Skill 构造统一注入 `runner`(StepRecorder 回调),消除 B 子步无法落 StepRecord 的断点;B/A 持有 provider 命名统一为 `self._llm`。
> - 新增 baseline run 定义 + `fault_manifest.json` schema + 聚合层 `experiment_summary.json`,实验指标对比可复现。
> - `skill_diagnose.parsed.needs_rtl_patch` 升级为强制字段,派生口径锁死为**按 severity 判**(`error/fatal → True`),消除"按 namespace 前缀判"导致恒 False 的隐性断路器。
> - 统一 inject bug RTL 目录为 `data/examples/`(契约 §4 目录树为唯一权威,B §8.4 / C §9 全部引用此路径)。
> - CPlanner 构造签名锁为 per-process:`CPlanner(registry, llm, runner, settings)`,run_id/budget 在 `execute(request)` 内部生成。
> - `planner_mode` 默认改为 `llm`(保证 planner 真正组织工具迭代),`rule` 作为 LLM 不可用时的降级路径。
> - B 的自修复通过率门槛锁死为**单一硬门槛**:按 fault_type 分组取最小值 >= 0.50 且 bitwidth 类至少 1 个 `all_pass`。
> - `confidence` 公式加饱和项;A 的 Top-1 命中改为加权三支总分 >= 0.6。
> - C 在 B 报 all_pass 后**强制追加独立 iverilog_sim 验证步**(非 open question)。
> - 文档登记位(docs/A_diagnoser.md / docs/B_self_heal.md / docs/C_planner.md)对齐状态见 §13。
>
> 文末附 §11 裁决说明(代表意见冲突时的取舍依据)与 §12 已知次要问题(minor 列表,留待后续迭代处理)。

---

## 1. 系统总览与分层

系统分为 5 层,自上而下,每层只依赖下层暴露的接口,不反向依赖。

    ┌─────────────────────────────────────────────────────────────┐
    │  L5  对外接口层  CLI 子命令 / 可选 MCP server                │
    │      eda self-heal / eda diagnose / eda report               │
    ├─────────────────────────────────────────────────────────────┤
    │  L4  C  Planner / Tool-Use 层(大脑)                        │
    │      ReAct / plan-execute 循环 + 任务分解 + 工具编排 + 迭代 │
    ├─────────────────────────────────────────────────────────────┤
    │  L3  Tool Registry(手脚)                                   │
    │      统一 Tool 接口 + 注册发现 + schema 声明               │
    ├─────────────────────────────────────────────────────────────┤
    │  L2  Skills  A(诊断器) / B(RTL 自修复闭环)              │
    │      本身也是 Tool,注册进 Registry 供 C 调用              │
    ├─────────────────────────────────────────────────────────────┤
    │  L1  EDA 工具封装  Yosys / iverilog / OpenSTA / KLayout...  │
    │  +   LLM Provider 抽象  Claude / Qwen / DeepSeek           │
    ├─────────────────────────────────────────────────────────────┤
    │  L0  工件存储  runs/<run_id>/  RunRecord + Artifacts        │
    └─────────────────────────────────────────────────────────────┘

分层职责一句话:

- L5 对外接口层:把人类一句话需求翻译成对 C 的调用,把 C 的产物翻译成报告。
- L4 C:接到任务后分解成 plan,从 Registry 取 Tool 执行,迭代直到目标达成或预算耗尽。
- L3 Tool Registry:所有可被 C 调用的能力(EDA 工具 + skill)统一注册在此,C 只认 Tool 接口,不认具体实现。
- L2 Skills:A 和 B 是高级 skill,内部可能调多个 Tool 并自己迭代,但对外仍是一个 Tool。
- L1 基座:具体 EDA 工具的子进程封装 + LLM 调用的 provider 抽象。
- L0 工件存储:每次 run 一个目录,记录完整轨迹,满足"实验记录 / 失败案例"的可追溯要求。

组件代号 ↔ Tool name ↔ 文件名对照表(消除 A/B/C 代号与 Registry name 的混淆):

    代号  角色              Registry name       skill 文件             实现要点
    ─── ─────────────── ──────────────── ─────────────── ──────────────────────────
    A    EDA 诊断器       skill_diagnose     skills/diagnose.py   吃 ToolResult 出 ErrorItem
    B    RTL 自修复闭环   skill_self_heal    skills/self_heal.py  综合→仿真→诊断→patch 迭代
    C    Planner/Tool-Use (非 Tool)          planner/c_planner.py plan-execute + 对外接口

数据流(端到端一次):

    用户需求(str)
      → L5 CLI 解析为 RunRequest
      → L4 C 创建 RunRecord(L0 落目录),进入 plan-execute 循环
      → C 通过 Registry 取 Tool(A / B / Yosys / iverilog / OpenSTA...),拿到 ToolResult
      → 每步 ToolResult.parsed + artifacts 写回 RunRecord
      → 循环结束,C 产出 RunReport,落 runs/<run_id>/report.md
      → L5 把 report.md 渲染给用户

---

## 2. 核心抽象契约

以下所有定义位于 Python 包 `eda_agent.contracts`。三组件 import 时一律 from 该模块取,禁止各自重新定义同名结构。

模块顶部常量(契约版本锚点,集成两端据此做 schema 漂移检测):

    CONTRACT_VERSION = "0.1.0"   # 语义化版本,parsed._schema.contract_version 与之一致演进

工件引用工厂函数(v1.2 显式定义,消除"函数 vs dict"歧义;contracts.py 必须导出):

    def artifact_ref(run_id: str, rel_path: str) -> dict[str, str]:
        """构造跨组件工件引用 dict。所有 ToolResult.artifacts 与 SkillResult.artifacts
        的元素必须由本函数构造(禁止裸 str 路径,禁止裸 dict 字面量)。
        实际路径 = runs/<run_id>/<rel_path>。"""
        return {"run_id": run_id, "rel_path": rel_path}

### 2.0 RunRequest / RunReport(对外入口契约)

CLI 与 MCP 的入参/出参就是这两个 dataclass 的 schema。任何外部调用方(集成方、外部 CPlanner、MCP client)据此构造合法请求。

    from typing import Literal, Any

    @dataclass(frozen=True)
    class RunRequest:
        kind: Literal["run", "diagnose", "self_heal"]
        goal: str                                # 自然语言目标,如 "pass all tests"
        rtl_path: str                            # RTL 源(绝对或相对项目根)
        tb_path: str | None = None               # TB 源;diagnose-only 可缺
        top_module: str | None = None            # 顶层模块名;None 由 Yosys 推断
        lib_path: str | None = None              # liberty(.lib);None 则 STA 跳过面积/时序
        clock_name: str | None = None            # 时钟信号名;None 则 STA 跳过
        max_iter: int | None = None              # 覆盖 settings 的 skill_max_iterations
        extra: dict[str, Any] = field(default_factory=dict)  # 扩展位

    @dataclass
    class RunReport:
        run_id: str
        status: Literal["ok", "failed", "budget_exhausted"]
        report_path: str                         # runs/<run_id>/report.md 绝对路径
        summary: str                             # 一句话结论
        metrics: dict[str, Any]                  # 关键指标(见 §2.4 标准字段集)

CLI 子命令映射(MVP 实现 3 个,self_heal 为流水线主入口,diagnose 为单点演示):

    eda self-heal --rtl ... --tb ... --goal ... [--top-module <name>] [--max-iter N] [--lib ..] [--clock ..] [--mode rule|llm] [--run-budget <int>s]
                  → kind="self_heal"
    eda diagnose  --rtl ... --tb ...             → kind="diagnose"(单点 demo,内部 plan kind=diagnose)
    eda report    <run_id>                        → 读取已有 run 目录渲染报告

> CLI 第三个子命令 `diagnose` 由 v1.2 新增(演示 A 诊断器的单点能力,工程量小),不阻塞 MVP 主路径;`run` 内部由 `self_heal` 覆盖。

CLI 退出码(三子命令统一,src/eda_agent/cli.py):

    0  = ok                  # RunReport.status="ok"
    1  = failed              # RunReport.status="failed"
    2  = budget_exhausted    # RunReport.status="budget_exhausted"
    64 = report 不存在       # 仅 `eda report <run_id>`,run_id 目录无 report.md

### 2.1 Tool 基类与 ToolResult

Tool 是 L3 Registry 中**一切可被 C 调用能力**的统一抽象。无论是 Yosys 这种子进程工具,还是 A/B 这种内部迭代的 skill,对外都是一个 Tool。

    from __future__ import annotations
    from dataclasses import dataclass, field
    from typing import Any, Literal, Protocol, runtime_checkable

    @dataclass(frozen=True)
    class ToolCall:
        name: str                       # Tool 名,如 "yosys_synth"
        args: dict[str, Any]            # 调用参数,与 Tool.schema 对齐(含 reserved 字段,见 §2.3)
        caller: str = "planner"         # 调用方标识,便于追溯
        llm_tool_call_id: str | None = None  # 关联 LLM tool_calls.id;非 LLM 发起则为 None

    @dataclass
    class ToolResult:
        status: Literal["ok", "error", "timeout"]   # 三态对外;skill 的 budget_exhausted 经 as_tool 映射为 error(见 §2.3)
        exit_code: int | None                        # 子进程退出码;skill 无子进程则 None
        stdout: str                                  # 头 32KB + 尾 32KB 拼接;超过则中段标记并落盘(见下)
        stderr: str                                  # 同上裁剪策略
        parsed: dict[str, Any]                       # 结构化解析;必须含 _schema 元字段(见下)
        artifacts: list[dict[str, str]]              # 产物路径,元素必须是 artifact_ref(...) 的返回
        duration_s: float                            # 耗时秒
        tool: str                                    # 产生该结果的 Tool 名
        error_code: str | None = None                # 二段式 namespace.code,取值见 §2.6
        error_hint: str | None = None                # 失败时一行人类可读提示

        def is_ok(self) -> bool:
            return self.status == "ok"

parsed 的 `_schema` 元字段(契约版本锚点,强制):

    parsed = {
        "_schema": {"name": "yosys_synth", "version": "0.1.0", "contract_version": CONTRACT_VERSION},
        ...其余字段...
    }
    # 集成方读 parsed 必须先校验 _schema.name 与 _schema.contract_version,不匹配抛 eda.schema_mismatch。
    # 所有组件引用 contract_version 必须 from eda_agent.contracts import CONTRACT_VERSION,禁止裸字符串。

stdout / stderr 裁剪策略(避免丢失诊断证据):

    - 总长 ≤ 64KB:原样保留。
    - 总长 > 64KB:保留"头 32KB + 尾 32KB",中段替换为标记
      "[...N lines truncated, see stdout.full.log]"
      并把完整内容落 steps/<idx>/stdout.full.log(stderr.full.log)。
    - A 诊断器读取 evidence 时优先读 .full.log 文件,不依赖内存里的裁剪串。

    @runtime_checkable
    class Tool(Protocol):
        name: str
        description: str
        schema: dict[str, Any]          # JSON Schema 描述 args(供 LLM)+ parsed_schema_ref(见 §3)

        def __call__(self, call: ToolCall) -> ToolResult: ...

设计要点:

- status 只有 ok / error / timeout 三态,杜绝 "warning" 这种模糊态。warning 一律归 ok 并写进 parsed。
- skill 的 budget_exhausted 经 as_tool 后对外是 error + error_code=eda.budget_exhausted,但 parsed 保留 `_skill_status` 原值(见 §2.3)。
- stdout / stderr 保留裁剪原文 + 完整版落盘,保证 A 诊断器拿得到尾部错误行。
- parsed 必须是结构化 dict 且带 `_schema` 元字段,不是裸字符串。每个 Tool 在 schema 里声明 parsed 形状(见 §2.2)。
- artifacts 路径必须以 `runs/<run_id>/` 为根,跨组件引用走 artifact_ref(...) 协议(见 §2.4)。
- error_code 采用二段式 `namespace.code`,见 §2.6。

### 2.2 各 EDA 工具的 parsed schema 示例

parsed 的 schema 由 Tool 自身在 `Tool.schema["parsed"]` 声明,并在 `parsed_schema_ref` 指向命名版本。以下是 MVP 必须支持的四个工具的 parsed 形状,严格对齐。**所有 parsed 都隐含携带 §2.1 的 `_schema` 元字段(其中 contract_version = CONTRACT_VERSION),下述示例省略不写。**

Yosys 综合(yosys_synth):

    parsed = {
        "success": bool,                       # 综合是否成功
        "num_cells": int,                      # 总 cell 数(强制走 stat -json 解析,见下)
        "cell_area": float | None,             # 面积(若有 liberty)
        "num_wires": int,
        "num_ports": int,
        "module_name": str,
        "script_used": str,                    # 实际执行的 yosys tcl/命令
        "warnings": list[str],                 # best-effort 提取(WARNING/ERROR 前缀正则,容忍格式)
        "errors": list[str],                   # best-effort 提取(success=False 时非空)
    }
    artifacts = [artifact_ref(run_id, "synth/netlist.v"), artifact_ref(run_id, "synth/synth.json")]

硬规则:numeric 字段(num_cells / cell_area / num_wires / num_ports)必须从 `yosys -p "stat -json"` 的 JSON 输出解析,**禁止**从 stdout 文本正则抓取;文本字段(warnings / errors)允许 best-effort。

iverilog 仿真(iverilog_sim):

    parsed = {
        "compiled": bool,                      # iverilog 编译是否通过
        "passed": bool | None,                 # 仿真是否通过(None=编译失败未跑)
        "num_passed": int,                     # 通过的 test case 数(依赖 TB 遵守打印协议)
        "num_failed": int,                     # 失败的 test case 数(同上)
        "fail_signals": list[str],             # 失败信号名(从 TB 协议标记行解析)
        "vvp_stdout_tail": str,                # vvp 输出最后 2KB(快速失败定位)
    }
    artifacts = [artifact_ref(run_id, "sim/wave.vcd")]

已知限制:iverilog 无任何原生 JSON/结构化输出能力(无 --dump-json / --ast),只能解析裸 stdout/stderr。故约定 **TB 打印协议**,把"解析裸文本"收敛为"解析自己定义的固定标记行":

    TB 必须在结束时打印以下固定标记行(大小写敏感,行首无空格):
        TEST_PASS <n>/<total>      例如 TEST_PASS 3/5
        TEST_FAIL <signal>         每个失败信号一行,例如 TEST_FAIL sum_out
    iverilog_sim Tool 只解析这两类标记行;未打印标记的 TB 视为协议违反,num_passed/num_failed 返回 None。
    data/examples 下所有 TB 必须遵守此协议(作为强制示例)。

OpenSTA 时序(opensta_timing)—— v1.1 上调为 MVP 必做(理由见 §11):

    parsed = {
        "wns": float | None,                   # worst negative slack(ns)
        "tns": float | None,                   # total negative slack(ns)
        "num_violating_endpoints": int,
        "critical_path_delay_ns": float | None,
        "clock_name": str | None,
        "violations": list[dict],              # [{endpoint, slack_ns, path}],取 top 10
    }
    artifacts = [artifact_ref(run_id, "sta/timing.rpt")]

A 诊断器(skill_diagnose)—— v1.2 parsed 字段集升级为强制完整集:

    parsed = {
        "_schema": {"name": "skill_diagnose", "version": "0.1.0", "contract_version": CONTRACT_VERSION},
        "skill": "diagnose",
        "tool": str,                           # 主诊断对象(yosys_synth / iverilog_sim / opensta_timing / "multi")
        "stage": Literal["synth","sim","sta","multi"],
        "root_causes": list[dict],             # ErrorItem 的 asdict 列表(契约 §2.6,7 字段齐全)
        "root_cause_summary": str,             # 一句话人类可读根因(辅助字段;权威结构化是 root_causes)
        "severity": Literal["info","warn","error","fatal"],   # 取 root_causes 中最严重的;空则 info
        "fix_hints": list[str],                # 修复建议列表(规则模板填充或 LLM 产出)
        "confidence": float,                   # 0..1,公式见下
        "needs_rtl_patch": bool,               # 强制字段;派生规则见下
        "used_layers": Literal["rule","llm","rule+llm"],
        "kb_hits": list[str],                  # 命中的错误码列表(ErrorItem.code 二段式,可追溯到错误分类;多条 ErrorPattern 可共享一码)
        "summary": str,                        # 与 root_cause_summary 同值,满足契约最小字段集
    }
    artifacts = [artifact_ref(run_id, "diagnose/report.md"), artifact_ref(run_id, "diagnose/report.json")]

needs_rtl_patch 派生规则(v1.2 锁死,消除 v1.1 "按 namespace 前缀判恒 False" 断路器):

    def _needs_patch_for(e: ErrorItem) -> bool:
        # A 消费的 error_code 实际是契约 §2.6 的 eda.* 别名(如 eda.rtl_syntax),
        # 不能按 namespace 前缀判;统一按 severity 判。
        return e.severity in ("error", "fatal")

    needs_rtl_patch = any(_needs_patch_for(e) for e in errors)   # errors 为空时为 False

confidence 计算口径(v1.2 加饱和项,降低 LLM 自由度,保证跨 run 可比):

    confidence = clamp(0.5 + 0.1 * min(len(evidence), 5) - 0.2 * has_contradiction, 0, 1)

    # has_contradiction 触发条件(v1.2 锁死):LLM 层被触发且其 root_cause 与规则层
    # errors[0].message 的关键词 Jaccard < 0.3 时置 True。在 DiagnoseSkill.run 里写死,
    # LLMAttributor 即使在 prompt 里要求给 confidence 也忽略其值。
    # 饱和项 min(len(evidence),5) 避免 6 条以上证据恒 1.0。

parsed 字段命名使用 snake_case,布尔字段用 is_/passed/success 等明确语义词。

### 2.3 Skill 基类、SkillResult 与 as_tool 适配契约

Skill 是 L2 概念:A 和 B 这类内部会迭代、会组合多个 Tool 的高级能力。Skill 对外**注册为 Tool**(走 §2.1 的 Tool 接口),但对内继承 Skill 基类以复用迭代控制、预算管理、子 run 落盘逻辑。

    @dataclass
    class SkillResult:
        status: Literal["ok", "error", "budget_exhausted"]
        iterations: int                      # 实际迭代轮数(A 不迭代,恒 1)
        final_parsed: dict[str, Any]         # 最终的 parsed(会作为 ToolResult.parsed 返回,需自带 _schema)
        trajectory: list[dict]               # 每一步:{"step": tool_name, "status": ..., "iter": n, "detail": ...}
        artifacts: list[dict[str, str]]      # 所有产物路径(元素必须是 artifact_ref(...) 返回)
        summary: str                         # 一句话总结
        # B 自修复专用字段(scoring 代表要求,把"智能"写进契约):
        patch_source: Literal["llm_full_rewrite", "llm_diff", "rule_based", "none"] = "none"
        convergence_cause: Literal["all_pass", "max_iter", "regression", "budget", "none"] = "none"
        best_iter: int = -1                  # 历史最佳轮(用于回退语义);-1=无任何轮通过仿真
        error_code: str | None = None        # 二段式,见 §2.6
        budget_used_s: float = 0.0           # 实际消耗预算(双层预算仲裁用,见下)

A(诊断器)恒定语义(v1.2 显式,消除 as_tool 字段语义空洞):A 不修复、不迭代,故
`patch_source = "none"`、`convergence_cause = "none"`、`best_iter = -1`(与 §2.3 dataclass 默认值一致,
语义"无任何轮通过仿真",避免 C 误判 A 找到最佳轮)。as_tool 后这六个 `_skill_*`
字段仍按下方映射表补齐,值符合 A 的恒定语义。

    @runtime_checkable
    class Skill(Protocol):
        name: str
        description: str
        max_iterations: int                  # 迭代上限,硬约束(A=1,B 由 settings 注入)
        budget_s: float                      # 时间预算秒,硬约束(C 调用时下传 remaining_budget_s)

        def run(
            self,
            run_id: str,
            inputs: dict[str, Any],
            remaining_budget_s: float | None = None,   # C 传入剩余 run 预算,Skill 不得独占
        ) -> SkillResult: ...

        def as_tool(self) -> Tool: ...       # 包装成 Tool 注册进 Registry

as_tool() 适配契约(SkillResult → ToolResult 明确映射表 + reserved 字段拆包):

    def as_tool(self) -> Tool:
        def wrapped(call: ToolCall) -> ToolResult:
            # 1. 拆包 reserved 字段:_remaining_budget_s 不走 Skill.run 的 inputs,
            #    由 as_tool 适配层取出,作为 remaining_budget_s 位置参数下传。
            remaining = call.args.pop("_remaining_budget_s", None)
            sr = self.run(call.name if False else <self.run_id_from_context>,
                          inputs=call.args, remaining_budget_s=remaining)
            # 2. 映射
            status = "ok" if sr.status == "ok" else "error"
            error_code = ("eda.budget_exhausted" if sr.status == "budget_exhausted"
                          else sr.error_code)
            error_hint = (f"skill {self.name} status={sr.status}"
                          if (error_code is not None or sr.status != "ok") else None)
            parsed = dict(sr.final_parsed)
            parsed.update({
                "_skill_status": sr.status,
                "_skill_iterations": sr.iterations,
                "_skill_trajectory": sr.trajectory,
                "_skill_patch_source": sr.patch_source,
                "_skill_convergence_cause": sr.convergence_cause,
                "_skill_best_iter": sr.best_iter,
            })
            return ToolResult(status=status, exit_code=None, stdout="", stderr="",
                              parsed=parsed, artifacts=sr.artifacts, duration_s=sr.budget_used_s,
                              tool=self.name, error_code=error_code, error_hint=error_hint)
        return wrapped   # 包装为 Tool(命名/描述/schema 沿用 self)

> 说明:as_tool 包装层持有 Skill 实例与构造期注入的 `run_id`(由调用方在 ToolCall 上下文或 Skill 构造时确定),实际实现细节见各组件文档。reserved 字段拆包是 as_tool 的特权,不受 LLM 可见 schema 约束。

每个 Skill 经 as_tool 后的 args schema(供 LLM/Registry 校验;**不含 reserved 字段**,registry.to_llm_tools 时剥离下划线前缀字段):

    skill_diagnose.as_tool args schema:
        {"tool_results": list[dict]}
        # 每个 dict = 上游 ToolResult 的 asdict,**必须额外含** step_idx:int 与 run_id:str
        # 两字段(由 B/C 调 A 时填),用于 A._collect_logs 定位 .full.log 路径。
    skill_self_heal.as_tool args schema(v1.2 扩展,消除 goal/lib/clock 死路):
        {
            "rtl": str,                  # RTL 源绝对路径(必须存在)
            "tb": str,                   # TB 源绝对路径(必须存在,遵守 TB 打印协议 契约 §2.2)
            "diagnose": dict | None,     # 可选:外部(C)预先调过 skill_diagnose 的 parsed;None 则 B 内部自调
            "max_iter": int,             # 覆盖 settings.skill_max_iterations
            "goal": str,                 # RunRequest.goal 原样下传(MVP 模板:"pass all tests" / "pass N tests" / 含 "timing")
            "lib": str | None,           # RunRequest.lib_path;None 则 B 跳过 STA
            "clock": str | None,         # RunRequest.clock_name;None 则 B 跳过 STA
            "top_module": str | None     # RunRequest.top_module;None 由 Yosys 推断
        }

reserved 字段登记表(v1.2,下划线前缀字段不进 LLM 可见 schema;**统一由调用方处理**,as_tool 适配层仅负责 `_remaining_budget_s` 拆包):

    _remaining_budget_s  float | None   # 双层预算仲裁:C 调 Skill 前算 remaining 下传;as_tool 适配层从
                                         #   ToolCall.args.pop("_remaining_budget_s", None) 取出,
                                         #   作为 Skill.run 的 remaining_budget_s 位置参数下传(见上方 as_tool 映射)。
    _artifact_ref        dict[str,str]  # 工件注入占位:由 C._resolve_args 处理(见 C §6.3),用于把上游
                                         #   artifact_ref 注入到本 ToolCall 的某个参数(如独立验证步把
                                         #   B.best/rtl.v 注入到 iverilog_sim 的 rtl 参数)。as_tool 适配层
                                         #   不处理此字段(C 在调 tool(call) 前已 _resolve_args 解析掉)。

> args schema 校验规则(v1.2,C 的 `_args_match_schema` 实现):下划线前缀字段(含上表 reserved 字段 `_remaining_budget_s` 与 `_artifact_ref`)**豁免** jsonschema 校验,由调用方处理;非下划线字段按 schema 严格校验。

Skill 构造统一注入 runner(v1.2,消除 B 子步无法落 StepRecord 的断点):

    class DiagnoseSkill:
        def __init__(self, kb: ErrorKB, llm: LLMProvider, runner: "Runner",
                     settings: Settings) -> None:
            self._llm = llm          # 命名统一为 self._llm(下划线表内部持有)
            self._runner = runner
            ...

    class SelfHealSkill:
        def __init__(self, registry: ToolRegistry, llm: LLMProvider, runner: "Runner",
                     max_iterations: int = 5, budget_s: float | None = None,
                     settings: Settings | None = None) -> None:
            self._llm = llm
            self._runner = runner    # B 内部 _stage_* 经 runner.append_step 落父 RunRecord 的 StepRecord
            ...

> runner 由 `build_registry(provider, runner, settings)` 在构造 Skill 时注入。Skill 不生成独立 run_id;子步通过 `self._runner.append_step(parent_run_id, call, result, skill_name=..., iter=...)` 追加到父 RunRecord.steps。

设计要点:

- Skill 区分 error 与 budget_exhausted:前者是真坏了,后者是没收敛但没崩。经 as_tool 后 budget_exhausted 在 ToolResult 三态里塌缩为 error,但 error_code=eda.budget_exhausted + parsed._skill_status 保留原值,C 可据此区分"真崩"与"没收敛"。"失败案例"的可追溯主要靠此状态。
- max_iterations 与 budget_s 是硬约束,Skill 内部必须检查,超了立即停。
- patch_source / convergence_cause / best_iter 是 B 智能性的契约级证据:trajectory + 这些字段本身即判定"迭代是真的、智能也是真的"的依据(scoring 代表 blocker 的解药)。
- patch 回退契约:若新 patch 的 num_passed < 上一轮,则回退到上一版 RTL,trajectory 必须记录回退事件与 best_iter。
- patch 施加方式优先用 diff/语义化编辑(llm_diff),整文件重写(llm_full_rewrite)仅在 diff 失败时降级使用。
- 双层预算仲裁:C 调 Skill 前计算 `remaining = run_budget_s - elapsed`,经 `Action.args["_remaining_budget_s"] = remaining` 下传(as_tool 拆包);Skill 自身 budget_s 取 min(self.budget_s, remaining_budget_s)。run_budget_s 是 C 与所有 Skill 共享的上限,任一 Skill 不得独占。

Skill 内部迭代落盘规则(见 §2.4 RunRecord↔Skill iter↔StepRecord 隶属关系图)。

### 2.4 工件存储 RunRecord / Artifact 路径约定

每次 C 接到一个 RunRequest 就生成一个 run_id,落一个目录。run_id 格式:`YYYYmmdd_HHMMSS_<short_uuid>`,如 `20260715_103022_a3f1`。

目录结构(单次 run,完整;**v1.2 唯一权威目录树**,三份组件文档引用此路径):

    runs/20260715_103022_a3f1/
        run.json                 # RunRecord 序列化(含 contract_version + config_snapshot)
        request.json             # 原始 RunRequest
        plan.json                # C 产出的 plan(任务分解)
        steps/
            001_yosys_synth/
                tool_call.json   # ToolCall(含 llm_tool_call_id)
                tool_result.json # ToolResult(含 parsed._schema)
                stdout.log       # 裁剪版
                stderr.log
                stdout.full.log  # 完整版(超过 64KB 时才出现)
                stderr.full.log
            002_iverilog_sim/
                ...
            003_opensta_timing/
                ...
            004_skill_diagnose/
                ...
            005_skill_self_heal/  # B 入口步(skill_name="self_heal", iter=None)
                ...
            006_yosys_synth/      # B 内部第 0 轮子步(skill_name="self_heal", iter=0)
            007_iverilog_sim/
            008_skill_diagnose/
            009_yosys_synth/      # B 内部第 1 轮
            ...
            NNN_iverilog_sim/     # B 报 all_pass 后 C 的独立验证步(skill_name=None)
        skills/
            diagnose/
                report.md         # A 产出的人类可读诊断报告
                report.json       # DiagnosisReport.to_parsed() 落盘(B 用)
                propose_pending/ # LLM 提议的新 ErrorKB pattern(待人工 review)
                    <uuid8>.json
                error_kb_snapshot.json
            self_heal/
                iter_0/
                    rtl_snapshot.v   # 该轮 RTL 快照(回退基准)
                    rtl_patch.diff   # 该轮 patch(iter_0 为 None)
                    sim_result.json
                iter_1/ ...
                best/
                    rtl.v
                    meta.json     # {best_iter, num_passed, convergence_cause, candidates_at_best_score}
                report.md
        experiment_manifest.json  # 单 run 对比实验度量(见下)
        report.md                 # C 最终产出的人类可读报告
        status.json               # 仅终态写入(见僵尸 run 自愈规则)

RunRecord 数据结构:

    @dataclass
    class RunRecord:
        run_id: str
        request: dict[str, Any]           # 原始 RunRequest 的 asdict
        created_at: str                   # ISO8601
        status: Literal["running", "ok", "failed", "budget_exhausted"]
        steps: list[StepRecord]           # 每步执行记录(含 Skill 内部每一步,见隶属关系图)
        final_report_path: str | None
        total_duration_s: float
        llm_calls: int                    # 经 CountingProvider 统一计数
        llm_tokens_in: int
        llm_tokens_out: int
        provider_used: str                # "claude" / "qwen" / "deepseek"
        contract_version: str             # = CONTRACT_VERSION
        config_snapshot: dict[str, Any]   # 见下,实验可复现性

config_snapshot 字段("指标对比"与 provider 切换实验复现依据):

    config_snapshot = {
        "llm": {"provider": "claude", "model": "claude-sonnet-4",
                "temperature": 0.0, "max_tokens": 4096},
        "eda": {"yosys_version": "...", "iverilog_version": "...",
                "opensta_version": "..."},
        "contract_version": CONTRACT_VERSION,
        "settings_hash": "<settings.toml 的 sha256 前 12 位>",
        "planner_mode": "llm",      # v1.2 新增:记录本次 run 用的 planner 模式
    }

    @dataclass
    class StepRecord:
        index: int                        # 从 1 开始,父 RunRecord 全局序号
        tool_name: str
        tool_call_path: str               # tool_call.json 绝对路径
        tool_result_path: str             # tool_result.json 绝对路径
        started_at: str
        duration_s: float
        status: Literal["ok", "error", "timeout"]
        # 若该步属于某 Skill 的内部迭代,补记:
        skill_name: str | None = None     # 如 "self_heal"
        iter: int | None = None           # 该步属于 Skill 的第几轮(从 0 起)

RunRecord ↔ Skill iter ↔ StepRecord 隶属关系图(blocker 解药):

    RunRecord (每 run 一个,run_id 唯一)
      ├─ StepRecord[001] yosys_synth        (C 直接调,skill_name=None)
      ├─ StepRecord[002] iverilog_sim       (C 直接调)
      ├─ StepRecord[003] opensta_timing     (C 直接调)
      ├─ StepRecord[004] skill_diagnose     (skill_name="diagnose", iter=0)
      └─ StepRecord[005] skill_self_heal    (skill_name="self_heal", iter=None — 外层入口)
           │
           └─ Skill 内部迭代【不生成独立 run_id】,但每轮的子 Tool 调用
              以 StepRecord 形式追加到父 RunRecord.steps,序号继续递增:
              ├─ StepRecord[006] yosys_synth   skill_name="self_heal" iter=0
              ├─ StepRecord[007] iverilog_sim  skill_name="self_heal" iter=0
              ├─ StepRecord[008] skill_diagnose skill_name="self_heal" iter=0
              ├─ StepRecord[009] yosys_synth   skill_name="self_heal" iter=1
              └─ ...
              每轮 RTL 快照落 skills/self_heal/iter_<n>/rtl_snapshot.v
              best 快照落 skills/self_heal/best/
      └─ StepRecord[NNN] iverilog_sim   skill_name=None  # v1.2:C 在 B 报 all_pass 后的独立验证步

规则:Skill 内部迭代**不生成独立 run_id**(避免 RunRecord 嵌套爆炸),但每一步都经 `self._runner.append_step(parent_run_id, call, result, skill_name="self_heal", iter=iter_n)` 进父 RunRecord.steps 并标 skill_name/iter。一条 trajectory 是否完整 = 父 RunRecord.status 为终态且所有 Skill 入口 StepRecord 都有对应收敛记录。

artifact 跨组件引用协议(v1.2 工厂函数,见 §2 顶部 `artifact_ref` 定义):

    artifact_ref(run_id, rel_path) 返回 {"run_id": str, "rel_path": str}
    含义:artifact 实际路径 = runs/<run_id>/<rel_path>。
    所有 ToolResult.artifacts 与 SkillResult.artifacts 元素必须由 artifact_ref(...) 构造(禁止裸 str 路径,禁止裸 dict 字面量)。
    示例:B 引用 A 的 diagnose report:
        artifact_ref(run_id, "diagnose/report.md") = {"run_id": "20260715_103022_a3f1", "rel_path": "diagnose/report.md"}

约定:

- 所有 artifact 必须落在 `runs/<run_id>/` 下,禁止写 /tmp 或项目根。
- artifact_ref(...) 必须出现在产生它的 ToolResult.artifacts 列表里。
- 二进制产物(VCD / GDS)原样存;文本产物(netlist / report)同时存原文 + 必要时存 json。
- run.json 实时更新 status 字段(running → 终态);status.json 仅在终态写入(见下)。

status.json 僵尸 run 自愈规则(minor:extensibility 解药):

    - running 态不落 status.json,只更新 run.json 的 status 字段。
    - 进程启动时扫描所有 run 目录:凡 run.json.status=="running" 且 created_at 距今
      超过 run_budget_s*2 的,标记为 "failed" 并写 status.json + crash 标记字段。
    - 保证对比实验 diff 两个 run 目录时不会因僵尸 run 误判。

experiment_manifest.json(单 run 对比实验度量,scoring 代表 major 解药;MVP 必须有):

    {
      "run_id": "20260715_103022_a3f1",
      "baseline_run_id": "20260715_103100_b1e2",   // v1.2:基线 run 的 run_id;缺失时 null
      "design_id": "counter_injected_bitwidth",
      "fault_type": "bitwidth",            // syntax | comb_logic | timing_reset | bitwidth
      "baseline_pass_rate": 0.0,            // baseline run 的 parsed.passed ? 1.0 : 0.0(见下"baseline run 定义")
      "self_heal_pass_rate": 1.0,           // (self_heal_convergence=="all_pass") ? 1.0 : 0.0
      "self_heal_convergence": "all_pass",  // all_pass|max_iter|regression|budget|none(对应 parsed._convergence_cause)
      "self_heal_best_iter": 3,             // 对应 parsed._best_iter;-1 表无成功轮
      "planner_iterations": 3,              // C 状态机相数
      "llm_calls": 12,
      "tokens_total": 8420,                 // llm_tokens_in + llm_tokens_out 之和
      "wall_time_s": 187.4,
      "candidates_at_best_score": 1,        // v1.2:达到 best_score 的最早轮,用于 tie-break 复核
      "contract_version": "0.1.0"           // 与 contracts.py CONTRACT_VERSION 一致
    }

baseline run 定义(v1.2,指标对比的复现依据):

    baseline run = 对同一 inject bug RTL,C 的 plan 只跑 [yosys_synth, iverilog_sim]
                   (不调 diagnose、不调 self_heal、不调 STA),baseline_pass_rate =
                   iverilog_sim.parsed.passed ? 1.0 : 0.0。
    self_heal run = 完整 self-heal 流程(self_heal_pass_rate = (self_heal_convergence=="all_pass") ? 1.0 : 0.0)。
    生成时机:C 在 self_heal 流程终态时,若 experiment_manifest.json 的 baseline_run_id 缺失,
              先跑一次 baseline-only plan 拿 baseline_pass_rate,再写 manifest。
              (或:在 fault_manifest.json 预存每个 inject bug 的已知 baseline=0,免跑一次。)

experiment_summary.json(聚合层,v1.2 新增,消除"分故障类型修复率靠人工算"):

    {
      "overall_pass_rate": 0.875,
      "min_group_pass_rate": 0.5,           // min(by_fault_type.*.passed/total)
      "by_fault_type": {
          "bitwidth":      {"total": 2, "passed": 2, "pass_rate": 1.0, "mean_iters": 2.5, "mean_best_iter": 2.0},
          "comb_logic":    {"total": 2, "passed": 1, "pass_rate": 0.5, "mean_iters": 4.0, "mean_best_iter": 3.0},
          "timing_reset":  {"total": 2, "passed": 2, "pass_rate": 1.0, "mean_iters": 3.5, "mean_best_iter": 2.5},
          "syntax":        {"total": 2, "passed": 2, "pass_rate": 1.0, "mean_iters": 1.5, "mean_best_iter": 1.5}
      },
      "total_runs": 8,
      "total_passed": 7
    }
    # 生成:scripts/summarize_eval.py 扫描所有 experiment_manifest.json 聚合。

RunReport.metrics 标准字段集(v1.2 锁定,与 experiment_manifest.json 一一对应):

    metrics = {
        "planner_iterations": int,
        "tool_calls_total": int,
        "llm_calls": int,
        "llm_tokens_in": int, "llm_tokens_out": int,
        "sim_passed": bool | None,
        "num_passed": int | None, "num_failed": int | None,
        "wns_ns": float | None, "tns_ns": float | None,
        "self_heal_convergence": str | None,   # parsed._convergence_cause
        "self_heal_best_iter": int | None,      # parsed._best_iter
        "self_heal_pass_rate": float | None,    # (convergence=="all_pass") ? 1.0 : 0.0
        "baseline_pass_rate": float | None,     # 来自 baseline run
        "wall_time_s": float,
    }

这套存储直接满足"实验证据 / 指标对比":每次实验一个目录,对比实验就是 diff 两个 run 目录 + 聚合 experiment_manifest.json 到 experiment_summary.json。

### 2.5 LLM provider 抽象

LLM 调用统一走 provider 抽象,C 和 skill 都不直接 import anthropic / openai。这样能从 Claude 切到 Qwen / DeepSeek 而不改业务代码,满足"预留多 provider provider"要求。

    @dataclass(frozen=True)
    class Message:
        role: Literal["system", "user", "assistant", "tool"]
        content: str                        # 仅承载文本;多模态/tool_use 结构走 tool_calls(见下边界声明)
        tool_call_id: str | None = None     # role="tool" 时必填,关联 assistant 的 tool_calls.id

    @dataclass
    class LLMResponse:
        text: str                           # 模型回复文本
        tool_calls: list[dict]              # 模型发起的工具调用,无则为空
        tokens_in: int
        tokens_out: int
        provider: str                       # "claude" / "qwen" / "deepseek"
        model: str                          # 具体模型名
        raw: dict[str, Any] | None = None   # 原始响应,debug 用

    # tool_calls 元素形状(统一):
    # {"id": str | None, "name": str, "args": dict}
    # id 由 provider 层保证唯一:Claude 用原生 tool_use.id;OpenAI 兼容若无则 provider 生成 uuid。

    @runtime_checkable
    class LLMProvider(Protocol):
        provider_name: str

        def chat(
            self,
            messages: list[Message],
            tools: list[dict] | None = None,    # JSON Schema 工具描述(来自 Registry.to_llm_tools)
            temperature: float = 0.0,
            max_tokens: int = 4096,
        ) -> LLMResponse: ...

边界声明(防止 provider 抽象泄漏):

- Message.content 仅承载文本;多模态(图像)与 assistant 的 tool_use 结构**只走 tool_calls 字段,不进 content**。MVP 不做多模态。
- role="tool" 的 Message 必须带 tool_call_id(provider 层负责翻译:OpenAI 用原生 tool_call_id 字段,Claude 用 tool_result block)。
- tool_calls.id 为 Optional:provider 层保证唯一即可,Claude 用原生 id,OpenAI 兼容若无则 provider 生成 uuid。

LLM tool-use 回环闭合(integration 代表 major 解药):

- ToolResult → Message(回灌 LLM)的标准序列化:
      msg = Message(
          role="tool",
          content=json.dumps({"status": r.status, "parsed": r.parsed, "error_hint": r.error_hint}),
          tool_call_id=<对应 ToolCall.llm_tool_call_id>,
      )
- C planner 收到 LLMResponse.tool_calls 后,**必须先经 registry.get(name) 验证**:
      - Tool 不存在 → 构造 status="error"/error_code="eda.tool_not_found" 的 ToolResult 回灌,不抛异常中断。
      - args 不符 schema → error_code="eda.tool_args_invalid" 回灌。
- ToolCall.llm_tool_call_id 必须填入对应 LLM 的 tool_calls.id,保证回环映射。

provider 实现口径(major:feasibility 解药):

- MVP 只实现 **ClaudeProvider**,provider_name="claude"。
- OpenAICompatProvider 封装 Qwen/DeepSeek 走 OpenAI 兼容接口,**列为可选扩展**,不进 MVP 测试。§6 settings.toml 的 qwen/deepseek 配置仅作注释示例。
- tool_calls 归一只需覆盖 Claude 一家(Anthropic tool_use block → 统一 {id,name,args})。

计数器统一(minor:feasibility 解药):

    所有 LLM 调用必须经过统一计数器,实现方式:在 make_provider() 工厂里返回
    CountingProvider(真实 provider) 装饰器,RunRecord 在 run 结束时从计数器读取
    llm_calls / tokens_in / tokens_out。一处实现,不靠每个 provider 自觉。

### 2.6 错误码与结构化 ErrorItem(v1.2 namespace 登记表含 diagnose/heal)

错误码改二段式 `namespace.code`:前缀 = 工具/领域 namespace(每加一个工具/skill 声明自己的 namespace),后缀 = 具体错误。契约保留 namespace 登记表,新工具/skill 自带错误码不改核心表。

namespace 登记约定(v1.2 正式登记 diagnose(A)与 heal(B)):

    eda        框架级 + 现有 13 个 MVP 码(作为别名保留,逐步迁移)
    synth      Yosys 综合领域
    sim        iverilog 仿真领域
    sta        OpenSTA 时序领域
    drc        KLayout DRC(可选)
    pnr        nextpnr 布局布线(可选)
    llm        LLM 调用领域
    diagnose   A 诊断器领域(v1.2 正式登记)
    heal       B 自修复领域(v1.2 正式登记)

diagnose namespace 错误码(A §5.4,severity 注明):

    diagnose.no_error_found      severity=warn    # 工具失败但规则层+LLM 都没找出根因
    diagnose.llm_call_failed     severity=error   # 别名 eda.llm_call_failed
    diagnose.kb_corrupted        severity=warn    # error_kb.json 解析失败,降级空 KB
    diagnose.args_invalid        severity=error   # 别名 eda.tool_args_invalid

heal namespace 错误码(B §5.2,severity 注明):

    heal.unsupported_goal        severity=error   # goal 字符串无法解析成 HealGoal
    heal.rtl_not_found           severity=error
    heal.tb_not_found            severity=error
    heal.diagnose_failed         severity=error   # 内部 skill_diagnose 返回 error 且无 root_causes
    heal.patch_syntax_invalid    severity=warn    # LLM patch 语法校验失败,策略已耗尽
    heal.regression_deadlock     severity=warn    # 连续 N 轮 num_passed 下降触发死锁保护
    heal.reduced_to_diagnose     severity=info    # 防退化降级:本 run 只产出诊断报告,不自动改

别名解析方向(v1.2 锁定,消除 C 早停/重试漏判):

- 规范码是 `diagnose.*` 与 `heal.*`(以及 `synth/sim/sta/...` 等领域码);`eda.*` 是兼容别名。
- C 读 error_code 时**按 namespace 聚类**:
  - namespace in {"diagnose","heal"} → 走各自语义
  - namespace == "eda" → 走 13 码语义
  - 不做双向字符串相等比较。
- C §5.6 错误码表必须含 `diagnose.*` 与 `heal.*` 行。

MVP 13 个核心码(保留为 `eda.*` 别名,向后兼容):

    eda.tool_not_found       = "eda.tool_not_found"      # Registry 找不到该 Tool
    eda.tool_args_invalid    = "eda.tool_args_invalid"   # args 不符合 schema
    eda.subprocess_failed    = "eda.subprocess_failed"   # 子进程退出码非 0
    eda.subprocess_timeout   = "eda.subprocess_timeout"  # 子进程超时
    eda.parse_failed         = "eda.parse_failed"        # 工具输出无法解析成 parsed
    eda.rtl_syntax           = "eda.rtl_syntax"          # RTL 语法错
    eda.sim_compile_failed   = "eda.sim_compile_failed"  # 仿真编译失败
    eda.sim_assert_failed    = "eda.sim_assert_failed"   # 仿真断言失败
    eda.timing_violation     = "eda.timing_violation"    # 时序违例
    eda.llm_call_failed      = "eda.llm_call_failed"     # LLM 调用失败
    eda.budget_exhausted     = "eda.budget_exhausted"    # 迭代/时间预算耗尽
    eda.internal             = "eda.internal"            # 未分类内部错
    eda.schema_mismatch      = "eda.schema_mismatch"     # parsed._schema 不匹配时抛

新工具示例(无需改核心表):

    drc.violation            # KLayout DRC 违例(可选)
    drc.lvs_mismatch         # LVS 不匹配
    pnr.routing_congested    # 布线拥塞

    @dataclass
    class ErrorItem:
        code: str                           # 二段式 namespace.code
        namespace: str                      # 派生字段 = code.split(".")[0],便于过滤/聚类
        tool: str                           # 哪个 Tool/skill 产生
        severity: Literal["info", "warn", "error", "fatal"]
        message: str                        # 一句话
        evidence: list[str]                 # 日志原文片段(优先读 .full.log)
        fix_suggestion: str | None = None   # 修复建议(A 诊断器填)

severity 与 code 的对应关系表(降低 LLM 自由度,提升跨 run 可比):

    eda.timing_violation      → error
    eda.rtl_syntax            → error
    eda.sim_compile_failed    → error
    eda.sim_assert_failed     → error
    eda.budget_exhausted      → warn
    eda.tool_not_found        → error
    eda.tool_args_invalid     → error
    eda.subprocess_timeout    → error
    eda.internal              → fatal
    eda.schema_mismatch       → error
    diagnose.no_error_found   → warn
    diagnose.llm_call_failed  → error
    diagnose.kb_corrupted     → warn
    diagnose.args_invalid     → error
    heal.unsupported_goal     → error
    heal.rtl_not_found        → error
    heal.tb_not_found         → error
    heal.diagnose_failed      → error
    heal.patch_syntax_invalid → warn
    heal.regression_deadlock  → warn
    heal.reduced_to_diagnose  → info

ErrorItem 是 A 诊断器的核心产物之一(skill_diagnose 的 root_causes 元素就是 ErrorItem 形状)。ToolResult.error_code 也取同一命名空间体系。这样 A 既能消费 Tool 的错误,也能产出新错误,链路一致。

A→B 消费契约(v1.2 锁定,消除 root_causes 字段结构在 A/B 间摇摆):

    1. skill_diagnose 经 as_tool 后,parsed.root_causes 永远是 list[ErrorItem.asdict](7 字段齐全)。
       空列表合法(代表规则层+LLM 都没归因),不视为 error。
    2. B 消费 root_causes 时,每个元素按 ErrorItem.asdict 读(.code/.namespace/.severity/.evidence/.fix_suggestion 等)。
    3. B fallback 时若用 sim_res.parsed.fail_signals(iverilog 的 list[str])喂 LLM,
       **必须先包装成 ErrorItem** 再喂:
           wrapped = ErrorItem(
               code="sim.fail_signal", namespace="sim", tool="iverilog_sim",
               severity="error", message=<signal>, evidence=[], fix_suggestion=None)
       禁止把裸字符串列表当 ErrorItem 喂 LLM。
    4. A §5.2 校验失败时返回 status="ok" + 空 root_causes(让 B 拿到合法结构),
       或明确 error_code="diagnose.args_invalid" 时 B 的降级路径走包装 fail_signals。

MVP 落地建议(minor:feasibility):先实现 7 个核心码(subprocess_failed/timeout/parse_failed/rtl_syntax/sim_compile_failed/sim_assert_failed/budget_exhausted),severity 先用 error/fatal 两态,info/warn 与其余码作为预留常量占位,不阻塞交付。

---

## 3. Tool Registry 注册与发现机制

Registry 是 L3 的单例。所有 Tool(EDA 工具封装 + A/B skill 包装)在进程启动时注册进去,C 只通过 Registry 取 Tool,不直接 import 具体实现。

    @dataclass
    class ToolEntry:
        tool: Tool
        name: str                           # 唯一,如 "yosys_synth"
        category: str                       # 开放 str,推荐前缀见下;不再用 Literal 枚举
        schema: dict[str, Any]              # args 的 JSON Schema,给 LLM 用(不含 reserved 字段)
        parsed_schema_ref: dict[str, str]   # {"name": "yosys_synth", "version": "0.1.0"},指向命名版本
        display_category: str | None = None # 可选,UI 分组用

category 开放 str 推荐前缀(blocker:extensibility 解药,兑现开闭原则):

    synth / sim / sta / pnr / layout / drc / skill / util
    # 这些是文档推荐值,非类型约束。加 'pnr'/'layout' 只需在新 Tool 文件里写 category="pnr",
    # 核心 contracts.py 零改动。Literal 仅作文档示例,不再出现在 dataclass 类型。

    class ToolRegistry:
        def register(self, entry: ToolEntry) -> None: ...
        def get(self, name: str) -> Tool | None: ...
            # 找不到返回 None,由调用方(C)构造 eda.tool_not_found 的 ToolResult 回灌 LLM
        def list(self, category: str | None = None) -> list[ToolEntry]: ...
        def to_llm_tools(self) -> list[dict]:
            """把所有 Tool 的 schema 转成 LLM provider 接受的工具描述列表。
            输出的每个 tool 名与 Registry name 一致(LLM 幻觉调用由 C 校验拦截)。
            v1.2:剥离下划线前缀 reserved 字段(如 _remaining_budget_s),不进 LLM 可见 schema。"""

注册方式二选一:

1. 显式注册(MVP 默认):在 `eda_agent.tools.bootstrap`(文件 `tools/bootstrap.py`)里集中 `registry.register(...)`,一目了然。
2. 装饰器注册(可选):`@tool(category="synth")` 装饰 Tool 实现类,启动时自动扫描。

MVP 选显式注册,避免装饰器扫描的隐式性带来调试负担。

build_registry 工厂(v1.2,统一注入 runner):

    def build_registry(provider: LLMProvider, runner: "Runner", settings: Settings) -> ToolRegistry:
        registry = ToolRegistry()
        # L1 子进程 Tool(不持有 provider)
        registry.register(ToolEntry(tool=YosysSynthTool(settings), name="yosys_synth", ...))
        registry.register(ToolEntry(tool=IverilogSimTool(settings), name="iverilog_sim", ...))
        registry.register(ToolEntry(tool=OpenSTATimingTool(settings), name="opensta_timing", ...))
        # L2 Skill(持有 provider + runner)
        kb = ErrorKB.load(settings.error_kb_path)
        diag = DiagnoseSkill(kb=kb, llm=provider, runner=runner, settings=settings)
        registry.register(ToolEntry(tool=diag.as_tool(), name="skill_diagnose", category="skill", ...))
        heal = SelfHealSkill(registry=registry, llm=provider, runner=runner,
                             max_iterations=settings.skill_max_iterations,
                             budget_s=settings.skill_self_heal_budget_s, settings=settings)
        registry.register(ToolEntry(tool=heal.as_tool(), name="skill_self_heal", category="skill", ...))
        return registry

MCP 暴露(可选,非 MVP):

    Registry 额外提供 to_mcp_tools(),把每个 Tool 包成 MCP tool 暴露。MCP 适配约定(实施前必读):
    - 同步 Tool 用 asyncio.to_thread 包成 async。
    - ToolCall.args 直接作为 MCP tool 的 inputSchema 对应入参。
    - ToolResult.status="error" 时映射为 MCP error response,error_code 放进 data 字段。
    - MCP tool 命名加 namespace 前缀避免冲突:"eda_agent.yosys_synth"。
    若评估做不完,降级为"仅 to_mcp_tools 签名存根,不实现",不要让扩展项反噬 MVP。

---

## 4. 目录结构(完整树,v1.2 唯一权威)

    eda-agent-system/
        pyproject.toml                      # 包元数据 + 依赖 + CLI 入口
        settings.toml                       # provider / 工具路径 / 预算配置
        README.md
        ARCHITECTURE.md                     # 项目总览(吸收本契约精华 + 系统视图)
        CONTRACTS.md                        # 本文件(契约)
        验收标准.md                          # 验收项汇总(可勾选表)
        组件A_诊断器.md                      # A 组件设计文档
        组件B_自修复闭环.md                  # B 组件设计文档
        组件C_Planner_ToolUse.md            # C 组件设计文档
        docs/
            api.md                          # 接口速查(待补)
        src/eda_agent/
            __init__.py
            contracts.py                    # §2 全部 dataclass / Protocol + CONTRACT_VERSION + artifact_ref
            errors.py                       # §2.6 错误码 namespace 登记 + ErrorItem
            registry.py                     # §3 ToolRegistry
            settings.py                     # 读 settings.toml → Settings dataclass
            llm/
                base.py                     # LLMProvider Protocol + Message/LLMResponse
                counting.py                 # CountingProvider 装饰器
                claude_provider.py
                openai_compat_provider.py   # Qwen / DeepSeek(可选)
                factory.py                  # make_provider() → CountingProvider
            tools/
                base.py                     # Tool Protocol + ToolResult + ToolCall
                yosys_synth.py
                iverilog_sim.py
                opensta_timing.py           # v1.1 上调为 MVP 必做
                bootstrap.py                # build_registry(provider, runner, settings)
            skills/
                base.py                     # Skill Protocol + SkillResult(as_tool 适配在此)
                diagnose.py                 # A
                self_heal.py                # B
            planner/
                c_planner.py                # C: plan-execute 循环
                rule_planner.py             # 规则 planner(降级路径)
                llm_planner.py              # LLM planner(ReAct 主路径)
                budget.py
                report.py
                prompts.py
            runner.py                       # RunRecord 落盘 + run_id 生成 + 僵尸 run 自愈 + append_step
            cli.py                          # L5 CLI 子命令(self-heal + diagnose + report)
            mcp_server.py                   # 可选:MCP 暴露
        data/
            examples/                       # 示例 RTL + TB(含预 inject bug 版本)★唯一权威路径★
                counter/
                    rtl.v tb.v
                    rtl_bitwidth_bug.v      # inject: 位宽错(fault_type=bitwidth)
                    rtl_reset_bug.v         # inject: reset 极性错(fault_type=timing_reset)
                    rtl_offbyone_bug.v      # inject: off-by-one(fault_type=comb_logic)
                    rtl_syntax_bug.v        # inject: 语法错(fault_type=syntax)
                adder/
                    rtl.v tb.v
                    rtl_carry_break_bug.v   # inject: 进位链断(fault_type=comb_logic)
                mux2/
                    rtl.v tb.v
                    rtl_port_bug.v          # inject: 端口错连(fault_type=syntax)
                shift_reg/
                    rtl.v tb.v
                    rtl_reset_missing_bug.v # inject: 异步 reset 缺(fault_type=timing_reset)
                tiny_fsm/
                    rtl.v tb.v
                    rtl_state_bug.v         # inject: 状态转移错(失败案例演示,healable=false)
                adder_pipe/
                    rtl.v tb.v
                    rtl_timing_bug.v        # inject: wns<0(fault_type=timing_reset,STA 触发)
                LICENSE                     # 全部 MIT(项目自带,无版权风险)
            lib/                            # 示例 liberty(.lib),STA 必备
                sky130_xx.lib               # 需自行放入开源小 liberty 文件
            fault_manifest.json             # inject bug 清单(schema 见下)
            error_kb.json                   # A 的 ErrorKB 种子 + 增长(进 git)
            logs_corpus/                    # A 的诊断语料
                raw/                        # 原始日志(从 runs/ 真实失败 + 公开 issue)
                    yosys_001.log
                    iverilog_001.log
                    ...
                corpus.jsonl                # 标注后的语料,一行一条(合法 JSON,无 // 注释)
        runs/                               # 运行产物,gitignore
            <run_id>/ ...
            eval_snapshot/                  # v1.2:预跑快照(无 EDA 工具环境时的可复现证据)
        scripts/
            build_corpus.py                 # 语料组装 + schema 校验
            eval_diagnose.py                # A 的 Top-1 命中率/规则覆盖率评测
            confirm_kb_pattern.py           # ErrorKB propose_pending 人工入库
            summarize_eval.py               # v1.2:聚合 experiment_manifest.json → experiment_summary.json
            run_baseline.py                 # v1.2:对 inject bug 跑 baseline-only plan
        tests/
            test_contracts.py               # dataclass 字段完整性 + _schema 元字段 + artifact_ref 工厂
                                             # 含 test_namespace_registry(namespace 登记 diagnose/heal 校验,并入本文件)
            test_registry.py
            test_registry_extensibility.py  # Tool 注册可扩展:注册 stub_ping,C 能发现并调用(C §10.2 / 验收 C11)
            test_yosys_tool.py              # 真跑 yosys,需 WSL 工具就绪
            test_iverilog_tool.py           # 含 TB 打印协议解析
            test_opensta_tool.py
            test_diagnose_skill.py          # A 接口契约单测(args 校验/规则层/LLM mock/confidence/as_tool/ErrorKB)
            test_diagnose_rule.py           # RuleLayer 纯字符串单测(不需 needs_eda,A §9 步骤4)
            test_self_heal_skill.py
            test_e2e_pipeline.py            # 端到端:需求 → report
            test_independent_verify.py      # C 在 B all_pass 后的独立 iverilog_sim 验证步(C §10.3 必过C / 验收 C13)

fault_manifest.json schema(v1.2 新增,复现依据):

    {
      "version": "1.0",
      "entries": [
        {
          "design_id": "counter_injected_bitwidth",
          "fault_type": "bitwidth",                 // syntax | comb_logic | timing_reset | bitwidth
          "rtl_path": "data/examples/counter/rtl_bitwidth_bug.v",
          "tb_path": "data/examples/counter/tb.v",
          "top_module": "counter",
          "baseline_expected": 0,                   // 已知 baseline 仿真失败
          "ground_truth_patch": "< 5 行 diff>",
          "healable": true,                         // 人能 < 5 行 diff 修;tiny_fsm 状态错标 false
          "annotator": "ai-member-name"
        },
        ...
      ]
    }
    # 至少 8 条,fault_type 分布:bitwidth>=2、comb_logic>=2、timing_reset>=2、syntax>=2。
    # 50% 通过率门槛只在 healable=true 子集上算(B §10.1)。

tests 目录的真跑测试用 pytest marker 区分 `@pytest.mark.needs_eda`,CI 无工具时跳过。
无 EDA 工具环境时,用 runs/eval_snapshot/ 下预跑的真实 run 目录作为可复现证据(见 验收标准.md)。

---

## 5. 端到端 pipeline 调用示例

一句话需求到报告的完整链路(伪代码,展示契约如何串起来;v1.2 CPlanner 为 per-process):

    # L5 CLI: eda self-heal --rtl data/examples/counter/rtl_bitwidth_bug.v \
    #                       --tb data/examples/counter/tb.v --goal "pass all tests"
    request = RunRequest(
        kind="self_heal",
        goal="pass all tests",
        rtl_path="data/examples/counter/rtl_bitwidth_bug.v",
        tb_path="data/examples/counter/tb.v",
        max_iter=5,
    )

    # L4 C 入口(per-process CPlanner,run_id 在 execute 内生成)
    provider = make_provider(settings.llm_provider)               # claude(→ CountingProvider)
    runner = Runner(runs_dir="runs", settings=settings)
    runner.scavenge_zombies(settings.run_budget_s)                # 僵尸自愈
    registry = build_registry(provider=provider, runner=runner, settings=settings)
    planner = CPlanner(registry=registry, llm=provider, runner=runner, settings=settings)
    report = planner.execute(request)                             # 内部 ReAct:综合→仿真→STA→诊断→patch→重试→独立验证

    # report 已落 runs/<run_id>/report.md
    print(f"done, see {report.report_path}")

CPlanner.execute 内部一次典型迭代(以 self_heal 为例,含 v1.2 独立验证步):

    # 1. 综合工具(LLM 若发起,先 registry.get 校验)
    r1 = registry.get("yosys_synth")(ToolCall("yosys_synth", {"rtl": rtl_path, "top": top_module}))
    # 2. 仿真工具
    r2 = registry.get("iverilog_sim")(ToolCall("iverilog_sim", {"rtl": rtl_path, "tb": tb_path}))
    # 3. STA(若 lib_path + clock_name 提供)
    if request.lib_path and request.clock_name:
        r3 = registry.get("opensta_timing")(ToolCall("opensta_timing",
            {"netlist": r1.artifacts[0], "lib": request.lib_path, "clock": request.clock_name}))
    # 4. 若仿真/时序失败,调 A 诊断
    if not r2.is_ok() or (r3 and r3.parsed.get("wns", 0) < 0):
        r4 = registry.get("skill_diagnose")(ToolCall("skill_diagnose", {
            "tool_results": [asdict(r1), asdict(r2)] + ([asdict(r3)] if r3 else []),
        }))
        # 5. 调 B 自修复(内部迭代 综合→仿真→诊断→patch;预算下传)
        remaining = budget_s - elapsed_s
        r5 = registry.get("skill_self_heal")(ToolCall("skill_self_heal", {
            "rtl": rtl_path, "tb": tb_path, "diagnose": r4.parsed, "max_iter": 5,
            "goal": request.goal, "lib": request.lib_path, "clock": request.clock_name,
            "top_module": request.top_module,
            "_remaining_budget_s": remaining,
        }))
        # r5.parsed._skill_status / _convergence_cause / _best_iter 即可审计的"智能证据"
        # 6. v1.2:B 报 all_pass 后,C 追加独立 iverilog_sim 验证步(读 B.best/rtl.v)
        if r5.parsed.get("_convergence_cause") == "all_pass":
            best_ref = r5.parsed["fixed_rtl_ref"]   # artifact_ref 指向 best/rtl.v
            r6 = registry.get("iverilog_sim")(ToolCall("iverilog_sim",
                {"rtl": <resolve best_ref path>, "tb": tb_path}))
            if not r6.is_ok():
                # B 自报通过但第三方验证失败 → 记录但不静默置 goal_achieved
                state.independent_verify_passed = False

每一步的 ToolCall / ToolResult 都被 runner 落到 steps/<idx>_<tool>/,LLM 每次调用经 CountingProvider 自动计入 record.llm_calls。B 内部迭代的每一步也经 `self._runner.append_step` 追加为父 RunRecord 的 StepRecord(标 skill_name/iter)。

---

## 6. 命名约定与配置管理

命名约定:

- Tool 名:动词_工具,snake_case。如 yosys_synth / iverilog_sim / opensta_timing / skill_diagnose / skill_self_heal。
- Skill 名:skill_<动词>。注册进 Registry 时 name 与 Tool 名一致(见 §1 对照表)。
- 文件名:snake_case.py,一个 Tool 一个文件。
- run_id:`YYYYmmdd_HHMMSS_<4位hex>`。
- error_code:二段式 `namespace.code`(如 `eda.budget_exhausted`)。
- LLM provider 持有命名:Skill 内统一为 `self._llm`(下划线表内部持有)。
- MCP tool 名(可选):`eda_agent.<tool_name>` 加 namespace 前缀。
- 工件传递占位常量:`ARTIFACT_FROM_STATE = "<from_state>"`(contracts.py 导出),RulePlanner 用此标记需工件注入的字段。

配置管理(settings.toml,v1.2 补 planner_mode + skill 子预算 + liberty 默认):

    [llm]
    provider = "claude"            # claude(MVP 唯一)| qwen | deepseek(可选)
    claude_model = "claude-sonnet-4"
    # qwen_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"   # 可选,注释
    # qwen_model = "qwen-plus"
    temperature = 0.0
    max_tokens = 4096

    [eda]
    wsl_enabled = true             # 工具是否跑在 WSL2
    yosys_cmd = "yosys"
    iverilog_cmd = "iverilog"
    vvp_cmd = "vvp"
    opensta_cmd = "sta"
    tool_timeout_s = 120
    default_lib_path = "data/lib/sky130_xx.lib"   # v1.2:STA 验收默认 liberty

    [planner]                      # v1.2 新增段
    mode = "llm"                   # rule | llm;v1.2 默认 llm(保证 planner 真正组织工具迭代)
    max_iterations = 8

    [budget]
    planner_max_iterations = 8
    skill_max_iterations = 5
    run_budget_s = 600             # C 与所有 Skill 共享的上限,任一 Skill 不得独占
    skill_diagnose_budget_s = 60   # v1.2:A 子预算
    skill_self_heal_budget_s = 480 # v1.2:B 子预算(留 120 给 C 的综合/仿真/STA 步骤)

    [data]
    error_kb_path = "data/error_kb.json"
    fault_manifest_path = "data/fault_manifest.json"
    logs_corpus_path = "data/logs_corpus/corpus.jsonl"

API key 走环境变量(ANTHROPIC_API_KEY / DASHSCOPE_API_KEY / DEEPSEEK_API_KEY),不进 settings.toml,不进 git。

pyproject.toml 关键项:

    [project]
    name = "eda-agent"
    version = "0.1.0"
    requires-python = ">=3.10"
    dependencies = ["anthropic>=0.40", "openai>=1.30", "tomli; python_version<'3.11'", "jsonschema>=4"]

    [project.scripts]
    eda = "eda_agent.cli:main"

    [tool.pytest.ini_options]
    markers = ["needs_eda: tests requiring yosys/iverilog/opensta installed"]

---

## 7. MVP 边界(v1.2 调整)

MVP 核心目标 → 必须做:

- [x] 真实可跑组件:Yosys 综合 + iverilog 仿真 + OpenSTA 时序 三个 Tool 真跑通(WSL2 apt 装),A 诊断器真调 LLM 产出结构化归因,B 自修复真迭代至少 1 轮。
- [x] 清晰接口:§2 全部 dataclass(含 §2.0 RunRequest/RunReport)+ CLI `eda self-heal / diagnose / report` 子命令可调。
- [x] 实验证据:runs/ 落轨迹 + experiment_manifest.json + experiment_summary.json,跑通至少 4 个示例设计 × 8 个 inject bug,对比 baseline vs self_heal 分故障类型的修复成功率(单一硬门槛见下)。
- [x] agentic 充分:C 默认 LLM planner 用 ReAct 组织工具迭代,B 内部迭代+自修复(含 patch 回退),C 在 B 报 all_pass 后追加独立验证,非一次性脚本。

B 自修复验收基线(v1.2 单一硬门槛,消除 v1.1 三套口径分叉):

- **单一硬门槛**:在 fault_manifest.json 登记的 N>=8 个 inject bug 中(summarize_eval.py 聚合时含 healable=false 的不可修案例作分母以反映真实难度,不按 healable 字段过滤),按 fault_type 分组取最小通过率 >= 0.50,且 bitwidth 类至少 1 个 convergence_cause=all_pass。
- **失败案例**:至少 1 个 inject bug 演示失败案例,convergence_cause ∈ {budget, regression, max_iter}(MVP 失败态全部算合规失败案例)。
- 两个都算交付。失败案例"真实且有结构",不会出现"全部失败 = 没 demo"。

MVP 必做清单(优先级从高到低):

1. contracts.py 全部 dataclass + artifact_ref 工厂 + ARTIFACT_FROM_STATE 常量 + 单测(2.0-2.6,含 _schema 元字段)。
2. ToolRegistry + 显式注册 + build_registry(provider, runner, settings)(category 开放 str + parsed_schema_ref)。
3. **前置 gate**:WSL2 装 yosys+iverilog+opensta + 跑 hello-world 综合(工具没装好前不写 Tool 封装);确认 data/lib/ 有可用 liberty。
4. Yosys / iverilog / OpenSTA Tool + parsed 解析(§2.2 schema,numeric 走 stat -json / TB 打印协议)+ 真跑单测。
5. RunRecord 落盘 + runner.append_step(run_id, call, result, skill_name, iter)+ run_id 生成 + status.json / config_snapshot / 僵尸 run 自愈。
6. LLMProvider 抽象 + ClaudeProvider + CountingProvider + make_provider() 工厂。
7. CPlanner per-process + 最简 plan-execute(默认 LLM 模式;能调 Tool、能读 parsed、能迭代、能落 report、tool_calls 幻觉校验、B all_pass 后独立验证)。
8. A 诊断器 skill:吃 ToolResult,LLM 产出 ErrorItem 列表(confidence 走规则校准 + 饱和项 + contradiction)。
9. B 自修复 skill:综合→仿真→STA→诊断→LLM 出 patch→重试,max_iter=5,含 patch 回退与 best_iter(tie-break 最早)。
10. **预 inject bug 准备**:先把 8 个 inject bug + 对应 TB 全部写好,记 ground-truth patch 进 data/fault_manifest.json(限定 healable=true);fault_manifest.json 标注 + 实验记录整理由人工完成。
11. CLI 三个子命令(self-heal + diagnose + report)。
12. 8 个 inject bug + 端到端 e2e 测试 + baseline vs self_heal 对比实验记录(experiment_summary.json 汇总)。

可选扩展(有余力再做,不阻塞 MVP):

- OpenAICompatProvider 接 Qwen / DeepSeek,做 provider 切换的对比实验(多 provider 扩展)。
- MCP server 暴露 Registry(to_mcp_tools + FastMCP),让外部集成方能调我们的 Tool。
- 装饰器式 Tool 注册。
- 更多示例设计(状态机 / FIFO)+ 更难故障注入(组合逻辑错、时序错)。
- KLayout 版图 / DRC Tool(category="layout"/"drc")、nextpnr(category="pnr")。

开发阶段建议(v1.2 调整):

- Phase0(前置):WSL2 装 yosys+iverilog+opensta + 跑 hello-world 综合前置 gate + 确认 data/lib/。
- Phase1:contracts(含 artifact_ref 工厂 / ARTIFACT_FROM_STATE / §2.3 as_tool 映射 + reserved 拆包 / §2.4 隶属关系)+ registry + build_registry + settings + 落盘框架(属基座共建)。
- Phase2:写 8 个 inject bug + 对应 TB + fault_manifest.json;做 Yosys/iverilog/OpenSTA Tool(numeric 走 stat -json)。
- Phase2.5:ClaudeProvider + CountingProvider + CPlanner 骨架(默认 LLM 模式,降级 rule)/ Tool 真跑单测 + TB 打印协议。
- Phase3:A 诊断器(confidence 规则校准 + 饱和项 + contradiction + Top-1 加权)。
- Phase3.5:B 自修复(含 patch 回退、best_iter、convergence_cause、fail_signals 包装)。
- Phase4:e2e 串联(含 B all_pass 后独立验证)+ 示例设计 + 跑通(单点能跑通)。
- Phase4.5:experiment_manifest + experiment_summary 对比实验(baseline vs self_heal 多 run 轨迹)+ 文档对齐(A/B/C 三份文档与契约字段级互查)。
- 收尾:整理实验记录 + eval_snapshot 预跑。
- 稳定化阶段:稳定性优化 + 按需补可选扩展(OpenAICompat/MCP)。

---

## 8. 自检(发布前逐条核对,v1.2 更新)

- [x] 大纲齐全:总览分层 / 核心抽象(RunRequest/Tool/Skill/RunRecord/LLM/ErrorItem + artifact_ref 工厂)/ Registry / 目录树 / e2e / 命名配置 / MVP 边界,全部覆盖。
- [x] dataclass 字段完整 + artifact_ref 工厂函数 + ARTIFACT_FROM_STATE 常量。
- [x] schema 版本锚点统一:CONTRACT_VERSION 常量 + parsed._schema.contract_version + run.json.contract_version 全用常量引用。
- [x] Tool↔Skill 适配闭合 + reserved 字段拆包(_remaining_budget_s 不进 LLM 可见 schema)。
- [x] 工件存储三模型打通 + B 子步经 runner.append_step 落父 RunRecord(Skill 构造注入 runner)。
- [x] LLM tool-use 回环闭合 + 命名统一 self._llm。
- [x] 实验可复现 + baseline run 定义 + fault_manifest.json schema + experiment_summary.json 聚合层。
- [x] parsed 结构化:四个工具 + A 诊断器都给了 schema 示例 + 强制 _schema 元字段。
- [x] 开闭原则 + namespace 表含 diagnose/heal。
- [x] B 智能性入契约 + 单一硬门槛(分组最小值 >= 0.50 且 bitwidth >= 1 all_pass)。
- [x] 时序闭环 + C 独立验证步。
- [x] needs_rtl_patch 按 severity 判(消除 namespace 前缀断路器)。
- [x] confidence 加饱和项 + Top-1 加权三支。
- [x] planner_mode 默认 llm(rule 降级)。
- [x] 目录树统一 data/examples/ 为唯一权威路径。

---

## 9. 接口稳定性与版本治理

parsed schema 演进规则:

- 字段改名/删字段必须 bump parsed_schema_ref.version(语义化:破坏性改 major,加字段 minor)。
- 集成方读 parsed 先校验 _schema.name + _schema.contract_version(常量);不匹配抛 eda.schema_mismatch,不静默读 None。
- MCP 暴露、跨组调用、provider 切换回归对比都依赖此版本锚点。

contract_version 与 parsed_schema_ref.version 关系:

- contract_version(CONTRACT_VERSION 常量)是契约整体版本,任何 §2 数据结构破坏性改动都要 bump。
- parsed_schema_ref.version 是单个 Tool parsed 的局部版本,可独立演进。
- run.json 落盘时同时记 contract_version 与各 step 的 parsed._schema.version。

---

## 10. Agentic 自检清单

> 以下清单保证系统是真正的 agentic 编排(组织工具、迭代、自修复),而非"会循环的 wrapper"。每条都有对应契约字段或验证步骤。

agentic 自检:
- C 默认 LLM planner 用 ReAct 组织工具(非硬编码脚本);rule 模式仅作 LLM 不可用时的降级。
- B 的 patch_source 必须有非 none 值(收敛成功的 run;llm_diff 优先),convergence_cause 必须真实记录。
- 失败 run 的 patch_source 记录"最后一次尝试的 source"(即使 applied=False);只有"全程一次 LLM 都没调"(convergence=budget 且 iterations=0)才允许 none。
- trajectory 必须可追溯每一轮的 patch diff、sim 结果、回退事件。
- best_iter 字段证明"系统知道哪一轮最好",而非盲目取最后一轮。
- C 在 B 报 all_pass 后追加独立 iverilog_sim 验证步(第三方可信度)。

---

## 11. 裁决说明(代表意见冲突时的取舍依据)

冲突 1:Skill 基类去留 → **保留**(理由同 v1.1)。

冲突 2:OpenSTA 是否 MVP 必做 → **上调 MVP**(理由同 v1.1)。

冲突 3:CLI 子命令数量 → v1.2 调整为 **3 个**(self-heal + diagnose + report)。理由:diagnose 单点 demo 对展示 A 诊断器能力有显著价值,工程量小;run 仍由 self_heal 覆盖。

冲突 4:错误码封闭表 vs 开放二段式 → **二段式 namespace.code + MVP 13 码降级为 eda.* 别名**(理由同 v1.1,v1.2 扩为 13 含 schema_mismatch)。v1.2 补:namespace 表正式含 diagnose/heal。

冲突 5:provider 多实现是否 MVP → **MVP 只 ClaudeProvider,OpenAICompat 明确可选扩展**(理由同 v1.1)。

冲突 6:stdout/stderr 裁剪策略 → **头 32KB + 尾 32KB + 中段标记 + 完整版落盘**(理由同 v1.1)。

冲突 7(v1.2 新增):planner_mode 默认值 → **默认 llm**。理由:保证"agent 组织工具、迭代、自修复"的主路径,MVP 必须用 LLM planner 才能避免被误判为"会循环的 wrapper";rule 模式仅作 LLM 不可用时的降级(feasibility 保底)。

冲突 8(v1.2 新增):B 自修复通过率门槛 → **单一硬门槛(分组最小值 >= 0.50 且 bitwidth 类至少 1 all_pass)**。理由:v1.1 的"均值 50%"与"至少 1 个 bitwidth"双硬门槛并存使评审方无所适从;分组最小值避免"只修简单类刷均值",bitwidth 兜底保证"至少 1 个真修通"。

冲突 9(v1.2 新增):needs_rtl_patch 派生口径 → **按 severity 判(error/fatal → True)**。理由:A 消费的 error_code 是 eda.* 别名(非 synth.*/sim.*/),按 namespace 前缀判会恒 False 导致 B 跳过 patch,端到端闭环断路。

---

## 12. 已知次要问题(minor,留待后续迭代处理)

以下 minor 问题不阻塞 v1.2 交付,记录在此供后续迭代按需处理:

12. [minor]decorator 注册(可选)与显式注册并存时的命名冲突检测未规定 —— 后续若做装饰器再补。
13. [minor]RunReport.metrics 字段集 —— v1.2 已在 §2.4 锁定标准字段集(原 minor 升级处理)。
14. [minor]data/examples 的 liberty 库来源 —— v1.2 已在 §6 settings 加 default_lib_path,前置 gate 确认(原 minor 升级处理)。
15. [minor]tests 目录的 needs_eda marker 在 WSL 工具未就绪时的 fallback 策略 —— v1.2 明确:无 EDA 工具环境时用 runs/eval_snapshot/ 预跑快照作为可复现证据(原 minor 升级处理)。
16. [minor]LLM planner 每轮取首个 tool_call —— MVP 取首,后续若需"一次规划多步"再扩展(见 C 文档 open_question #2)。
17. [minor]RunReport 是否标准化字段集 —— v1.2 已锁定(见 §2.4)。

---

## 13. 文档间一致性约束

本契约是最高约束。组件A_诊断器.md / 组件B_自修复闭环.md / 组件C_Planner_ToolUse.md 三份组件文档的接口签名、dataclass 字段、错误码、artifact 路径必须与本文严格对齐,任何不一致以本契约为准。

    - [x] 组件A_诊断器.md —— v1.2 已对齐 §2.2 skill_diagnose parsed(含 tool/stage/severity/fix_hints/used_layers/kb_hits/needs_rtl_patch 全字段)/ §2.6 ErrorItem + diagnose namespace / §2.3 as_tool + self._llm / needs_rtl_patch 按 severity 判 / confidence 饱和项 / Top-1 加权
    - [x] 组件B_自修复闭环.md —— v1.2 已对齐 §2.3 SkillResult(patch_source/convergence_cause/best_iter=-1)/ patch 回退 / §2.4 iter 落盘 + runner 注入 / fail_signals 包装 / inputs.goal / data/examples/ 路径 / 单一硬门槛
    - [x] 组件C_Planner_ToolUse.md —— v1.2 已对齐 §2.0 RunRequest/RunReport + per-process CPlanner / §2.5 tool-use 回环 / §5 e2e + 独立验证步 / 双层预算仲裁 + reserved 拆包 / planner_mode 默认 llm / PlannerState.history

ARCHITECTURE.md 吸收本契约精华(系统视图 + 端到端数据流 + Phase 排期),不重复完整契约正文,需引用时回指本文件。验收标准.md 把 A/B/C/系统集成的验收项汇总成可勾选表。发布前执行三份组件文档与本契约的字段级互查。
