# ARCHITECTURE.md — Agentic EDA 系统项目总览(doc v1.2)

> 本文件是 Agentic4Systems 暑期学校 Hackathon 参赛系统的**项目总览**。完整契约(数据结构/接口签名/错误码/工件协议)见 `CONTRACTS.md`(权威);本文件吸收其精华做系统级呈现,引用时回指契约。验收细节见 `验收标准.md`,组件实现细节见三份组件文档。
>
> 约定:代码段 4 空格缩进,禁用反引号代码块。所有 `contract_version` 引用 `from eda_agent.contracts import CONTRACT_VERSION`,CONTRACT_VERSION = "0.1.0"。

---

## 1. 项目概述

### 1.1 比赛与目标

- 比赛:Agentic4Systems 暑期学校 + Hackathon(北大/中科院计算所/复旦/深圳河套学院联合发起,2026-07-12 至 07-15 四天集训,07-15 11:00 提交)。
- 赛道:Track 01 Agentic EDA Infra — 让智能体接入 EDA 工具链,完成设计生成 / 仿真验证 / 综合评估 / 错误分析 / 迭代优化,把芯片设计流程变成可执行闭环。
- 目标奖项:完赛奖(3000 元,最基础档)。官方标准:奖励完成扎实系统组件、为整体基座贡献关键能力的团队;评审看重能被集成、能被验证、能推动基座继续生长的真实贡献。

### 1.2 完赛奖四条硬指标 → 系统映射

    完赛奖硬指标              本系统落点                                    验收方式
    ────────────────────── ─────────────────────────────────────── ──────────────────────────────────
    (1) 真实可跑组件         A(skill_diagnose)+ B(skill_self_heal)+    tests/test_*_tool.py 真跑(needs_eda)
                             Yosys/iverilog/OpenSTA 三 Tool 真跑通
    (2) 清晰可调用接口       §5 CLI(eda self-heal/diagnose/report)+     eda self-heal --rtl ... 可直接调
                             §5 SDK(run_pipeline)+ §5 MCP(加分)
    (3) 实验证据/指标对比    runs/ 落轨迹 + experiment_manifest.json +   diff 两个 run 目录 + 聚合 summary
                             experiment_summary.json + baseline run +
                             fault_manifest.json
    (4) EDA 三赛道且 agentic C 默认 LLM planner(ReAct)+ B 内部迭代 + patch 回退 + C 独立验证步

agentic 充分性自检(防"会循环的 wrapper"判定,契约 §10):
- C 默认 LLM planner 用 ReAct 组织工具(rule 仅作降级)。
- B 的 patch_source / convergence_cause / best_iter 是契约级智能证据。
- C 在 B 报 all_pass 后追加独立 iverilog_sim 验证步(第三方可信度)。
- trajectory 可追溯每一轮的 patch diff、sim 结果、回退事件。

### 1.3 团队与时间

- 团队:2 人核心(偏 AI/Agent/系统,数字前端 RTL 不熟)+ 1 人非技术(数据收集标注 / 实验记录整理 / 接口文档 / 演示脚本)。
- 时间:9 天准备期(现在→07-11)+ 4 天集训(0712-0715,0715 11:00 提交)。
- 环境:Win11 + RTX3060 Laptop;EDA 工具跑 WSL2 Ubuntu(apt 装 yosys/iverilog,源码/conda 装 OpenSTA);agent 层 Python(本地,无需 GPU);LLM 主用 Claude API + provider 抽象预留国产模型(昇腾/壁仞/智子芯元)。

---

## 2. 系统总览

### 2.1 五层分层

    ┌─────────────────────────────────────────────────────────────┐
    │  L5  对外接口层  CLI 子命令 / 可选 MCP server                │
    │      eda self-heal / eda diagnose / eda report               │
    ├─────────────────────────────────────────────────────────────┤
    │  L4  C  Planner / Tool-Use 层(大脑,默认 LLM ReAct)        │
    │      plan-execute 循环 + 任务分解 + 工具编排 + 迭代 + 独立验证│
    ├─────────────────────────────────────────────────────────────┤
    │  L3  Tool Registry(手脚)                                   │
    │      统一 Tool 接口 + 注册发现 + schema 声明(reserved 剥离)│
    ├─────────────────────────────────────────────────────────────┤
    │  L2  Skills  A(诊断器)/ B(RTL 自修复闭环)               │
    │      本身也是 Tool(as_tool 注册),持有 self._llm + runner  │
    ├─────────────────────────────────────────────────────────────┤
    │  L1  EDA 工具封装  Yosys / iverilog / OpenSTA / KLayout...   │
    │  +   LLM Provider 抽象  Claude(MVP)/ Qwen / DeepSeek(加分)│
    ├─────────────────────────────────────────────────────────────┤
    │  L0  工件存储  runs/<run_id>/  RunRecord + Artifacts         │
    └─────────────────────────────────────────────────────────────┘

每层只依赖下层接口,不反向依赖。组件代号:A=skill_diagnose(L2)、B=skill_self_heal(L2)、C=CPlanner(L4,非 Tool)。

### 2.2 端到端数据流(一次 self_heal)

    用户需求(str)
      → L5 CLI 解析为 RunRequest(契约 §2.0)
      → L4 CPlanner.execute(request):
            1. runner.create(request) 生成 run_id,落 run.json(running)+ config_snapshot
            2. PLANNING → EXECUTING ⇄ REFLECTING → REPORTING → DONE(状态机)
            3. 默认 LLM planner:每轮问 LLM 拿 tool_calls,registry.get 校验(防幻觉),
               执行 ToolCall,落 step,ToolResult 回灌 LLM(tool-use 回环闭合)
               规则 planner(降级):确定 plan = [yosys, iverilog, (sta), diagnose, self_heal]
            4. 仿真/时序失败 → 调 A 诊断 → 调 B 自修复(双层预算仲裁下传 remaining)
            5. B 报 all_pass → C 追加独立 iverilog_sim 验证步 → 通过才置 goal_achieved
            6. 终态写 status.json + experiment_manifest.json + report.md
      → L5 把 report.md 渲染给用户

每一步的 ToolCall / ToolResult 都被 runner 落到 steps/<idx>_<tool>/;B 内部迭代的每一步也经 self._runner.append_step 追加为父 RunRecord 的 StepRecord(标 skill_name/iter)。LLM 调用经 CountingProvider 自动计入 RunRecord.llm_calls。

---

## 3. 核心契约精华(摘要,详见 CONTRACTS.md)

### 3.1 顶层常量与工厂函数

    CONTRACT_VERSION = "0.1.0"   # parsed._schema.contract_version 与 run.json.contract_version 锚点
    ARTIFACT_FROM_STATE = "<from_state>"   # RulePlanner 工件注入占位常量

    def artifact_ref(run_id: str, rel_path: str) -> dict[str, str]:
        return {"run_id": run_id, "rel_path": rel_path}   # 所有 artifacts 元素必须由此构造

### 3.2 对外入口(契约 §2.0)

    @dataclass(frozen=True)
    class RunRequest:
        kind: Literal["run", "diagnose", "self_heal"]
        goal: str
        rtl_path: str
        tb_path: str | None = None
        top_module: str | None = None
        lib_path: str | None = None
        clock_name: str | None = None
        max_iter: int | None = None
        extra: dict[str, Any] = field(default_factory=dict)

    @dataclass
    class RunReport:
        run_id: str
        status: Literal["ok", "failed", "budget_exhausted"]
        report_path: str
        summary: str
        metrics: dict[str, Any]   # 标准字段集见 CONTRACTS §2.4

### 3.3 Tool / ToolResult / Skill / SkillResult 关键点

- ToolResult.status 只有 ok/error/timeout;skill 的 budget_exhausted 经 as_tool 塌缩为 error + error_code=eda.budget_exhausted + parsed._skill_status 保留原值。
- parsed 必须含 _schema 元字段({name, version, contract_version=CONTRACT_VERSION})。
- SkillResult 含智能证据字段:patch_source / convergence_cause / best_iter / budget_used_s。
- as_tool 适配层负责:SkillResult → ToolResult 映射;**reserved 字段拆包**(_remaining_budget_s 从 ToolCall.args.pop 出,作为 Skill.run 的 remaining_budget_s 位置参数下传);剥离 reserved 字段不进 LLM 可见 schema。
- Skill 构造统一注入 runner(消除 B 子步无法落 StepRecord 断点);命名统一 self._llm。
- skill_self_heal args schema(v1.2 扩展):{rtl, tb, diagnose, max_iter, goal, lib, clock, top_module}。
- skill_diagnose args:tool_results 元素必须额外含 step_idx + run_id(定位 .full.log)。
- needs_rtl_patch 派生按 severity 判(error/fatal → True),消除 namespace 前缀断路器。

### 3.4 错误码 namespace(契约 §2.6 v1.2)

    eda / synth / sim / sta / drc / pnr / llm / diagnose(A) / heal(B)
    # MVP 13 码保留为 eda.* 别名;diagnose.* / heal.* 是规范码,C 按 namespace 聚类读

### 3.5 工件存储(契约 §2.4)

- 每次 RunRequest 一个 run_id(YYYYmmdd_HHMMSS_<4hex>),一个 runs/<run_id>/ 目录。
- RunRecord / StepRecord / Skill 内部 iter 隶属关系图(详见契约):B 内部迭代不生成独立 run_id,子步追加到父 RunRecord.steps 标 skill_name/iter。
- experiment_manifest.json(单 run)+ experiment_summary.json(聚合层,scripts/summarize_eval.py 产出)。
- baseline run 定义:对同一 inject bug,plan 只跑 [yosys_synth, iverilog_sim],baseline_pass_rate = passed?1.0:0.0。
- fault_manifest.json:schema {design_id, fault_type, rtl_path, tb_path, top_module, baseline_expected, ground_truth_patch, healable},至少 8 条。
- 僵尸 run 自愈:进程启动扫描 runs/*,超时 running 态标 failed 写 status.json。

完整字段集、as_tool 映射表、目录树、parsed schema 示例、LLM provider 协议、namespace 错误码表 — 全部见 CONTRACTS.md。

---

## 4. 完整目录结构树(与 CONTRACTS.md §4 唯一对齐)

    eda-agent-system/
        pyproject.toml                      # 包元数据 + 依赖 + CLI 入口
        settings.toml                       # provider / 工具路径 / 预算配置
        README.md
        ARCHITECTURE.md                     # 本文件
        CONTRACTS.md                        # 契约(权威)
        验收标准.md                          # 验收项汇总
        组件A_诊断器.md                      # A 组件设计文档
        组件B_自修复闭环.md                  # B 组件设计文档
        组件C_Planner_ToolUse.md            # C 组件设计文档
        docs/
            api.md                          # 接口速查(集训期补)
        src/eda_agent/
            __init__.py
            contracts.py                    # CONTRACT_VERSION + artifact_ref + ARTIFACT_FROM_STATE + §2 全部 dataclass
            errors.py                       # namespace 登记 + ErrorItem
            registry.py                     # ToolRegistry
            settings.py                     # Settings dataclass
            llm/
                base.py                     # LLMProvider Protocol + Message/LLMResponse
                counting.py                 # CountingProvider
                claude_provider.py
                openai_compat_provider.py   # 加分项
                factory.py                  # make_provider() → CountingProvider
            tools/
                base.py                     # Tool Protocol + ToolResult + ToolCall
                yosys_synth.py
                iverilog_sim.py
                opensta_timing.py
                bootstrap.py                # build_registry(provider, runner, settings)
            skills/
                base.py                     # Skill Protocol + SkillResult + as_tool 适配
                diagnose.py                 # A
                self_heal.py                # B
            planner/
                c_planner.py                # C: plan-execute + 独立验证步
                rule_planner.py             # 降级路径
                llm_planner.py              # 主路径(默认)
                budget.py
                report.py
                prompts.py
            runner.py                       # RunRecord 落盘 + run_id + 僵尸自愈 + append_step
            cli.py                          # L5 CLI(self-heal + diagnose + report)
            mcp_server.py                   # 加分项
        data/
            examples/                       # ★inject bug RTL+TB 唯一权威路径★
                counter/{rtl.v,tb.v,rtl_bitwidth_bug.v,rtl_reset_bug.v,rtl_offbyone_bug.v,rtl_syntax_bug.v}
                adder/{rtl.v,tb.v,rtl_carry_break_bug.v}
                mux2/{rtl.v,tb.v,rtl_port_bug.v}
                shift_reg/{rtl.v,tb.v,rtl_reset_missing_bug.v}
                tiny_fsm/{rtl.v,tb.v,rtl_state_bug.v}      # healable=false(失败案例)
                adder_pipe/{rtl.v,tb.v,rtl_timing_bug.v}   # STA 触发
                LICENSE                     # MIT
            lib/sky130_xx.lib               # STA liberty(Day0.5 gate 确认)
            fault_manifest.json             # inject bug 清单(>=8 条)
            error_kb.json                   # A 的 ErrorKB(种子+增长,进 git)
            logs_corpus/{raw/,corpus.jsonl} # A 的诊断语料
        runs/                               # 运行产物,gitignore
            <run_id>/ ...
            eval_snapshot/                  # 准备期预跑快照(评审无工具时替代证据)
        scripts/
            build_corpus.py
            eval_diagnose.py
            confirm_kb_pattern.py
            summarize_eval.py               # 聚合 experiment_manifest.json → experiment_summary.json
            run_baseline.py                 # 跑 baseline-only plan
            check_no_subprocess.py          # ast.walk 跨平台检查 C 无 subprocess 调用
        tests/
            test_contracts.py               # 含 test_namespace_registry(namespace 登记 diagnose/heal 校验,并入本文件)
            test_registry.py
            test_registry_extensibility.py  # Tool 注册可扩展(C §10.2 / 验收 C11)
            test_yosys_tool.py              # needs_eda
            test_iverilog_tool.py
            test_opensta_tool.py
            test_diagnose_skill.py
            test_diagnose_rule.py           # RuleLayer 纯字符串单测(A §9 步骤4)
            test_self_heal_skill.py
            test_e2e_pipeline.py            # needs_eda
            test_independent_verify.py      # C 独立验证步(C §10.3 必过C / 验收 C13)
            test_planner_smoke.py / test_budget.py / test_rule_planner.py
            test_action_exec.py / test_e2e_stub.py / test_llm_planner.py
            test_planner_limits.py / test_cli_exit_codes.py

---

## 5. 系统集成(对外接口示例)

### 5.1 CLI(Win11 PowerShell 或 WSL2 bash 均可)

    pip install -e .
    eda --help                      # 列出 self-heal / diagnose / report 三个子命令
    eda self-heal \
        --rtl data/examples/counter/rtl_bitwidth_bug.v \
        --tb  data/examples/counter/tb.v \
        --goal "pass all tests" \
        [--lib data/lib/sky130_xx.lib --clock clk] \
        [--max-iter 5] [--mode llm] [--run-budget 600]
    # 退出码:0=ok,1=failed,2=budget_exhausted,64=report 不存在
    # stdout 打印 runs/<run_id>/report.md 路径

    eda diagnose --rtl data/examples/counter/rtl_bitwidth_bug.v --tb data/examples/counter/tb.v
    # 单点演示 A 诊断器能力

    eda report <run_id>             # 渲染已有 run 的 report.md 到 stdout

### 5.2 Python SDK(一行可调)

    from eda_agent import run_pipeline
    from eda_agent.contracts import RunRequest
    request = RunRequest(kind="self_heal", goal="pass all tests",
                         rtl_path="data/examples/counter/rtl_bitwidth_bug.v",
                         tb_path="data/examples/counter/tb.v", max_iter=5)
    report = run_pipeline(request, settings)
    print(report.report_path, report.status, report.metrics)

### 5.3 MCP server(加分项)

    # src/eda_agent/mcp_server.py(FastMCP)
    # 暴露:eda_agent.self_heal / eda_agent.report / eda_agent.<tool_name>(registry.to_mcp_tools)
    # 同步 Tool 用 asyncio.to_thread 包 async;error_code 放 MCP data 字段
    # 用 Claude Code / Cursor 接 MCP server 调 eda_agent.self_heal 拿 RunReport dict

### 5.4 端到端 pipeline(伪代码,契约 §5)

    provider = make_provider(settings.llm_provider)               # CountingProvider(ClaudeProvider)
    runner = Runner(runs_dir="runs", settings=settings)
    runner.scavenge_zombies(settings.run_budget_s)                # 僵尸自愈
    registry = build_registry(provider=provider, runner=runner, settings=settings)
    planner = CPlanner(registry=registry, llm=provider, runner=runner, settings=settings)
    report = planner.execute(request)
    # 内部 ReAct:综合→仿真→STA→诊断→patch→重试→B all_pass 后独立验证步

---

## 6. 环境与依赖(带版本)

### 6.1 EDA 工具(WSL2 Ubuntu)

    yosys       >= 0.30      # apt install yosys;stat -json 支持(契约 §2.2 硬规则)
    iverilog    >= 12.0      # apt install iverilog;含 vvp
    opensta     >= 2.3       # 源码或 conda install -c openroad opensta(契约 §11 上调 MVP)

Day0.5 前置 gate:`yosys -V && iverilog -V && vvp -V && sta -version` 全部有输出,且 `yosys -p "synth -top counter; stat -json"` 跑通 hello-world;data/lib/ 有可用 liberty。

### 6.2 Python 库(pyproject.toml)

    python>=3.10
    anthropic>=0.40        # ClaudeProvider(MVP 唯一 provider)
    openai>=1.30           # 加分项:OpenAICompatProvider(Qwen/DeepSeek)
    jsonschema>=4          # args schema 校验(下划线前缀 reserved 字段豁免)
    tomli; python_version<'3.11'   # settings.toml 解析(3.11+ 用 tomllib)
    # CLI:argparse(标准库)
    # MCP 加分项:mcp>=1.0(FastMCP),optional dependency

不依赖 LangGraph/LangChain(契约硬要求自研轻量循环)。C 不直接 subprocess(由 Tool 封装)。

### 6.3 LLM

    主用:Anthropic Claude(claude-sonnet-4,settings.toml [llm] claude_model)
    预留:OpenAI 兼容(Qwen-Plus / DeepSeek),经 OpenAICompatProvider,加分项
    API key:ANTHROPIC_API_KEY 环境变量(不进 settings.toml,不进 git)

### 6.4 settings.toml 关键段(契约 §6)

    [llm]
    provider = "claude"
    claude_model = "claude-sonnet-4"
    temperature = 0.0
    max_tokens = 4096

    [eda]
    wsl_enabled = true
    yosys_cmd = "yosys"; iverilog_cmd = "iverilog"; vvp_cmd = "vvp"; opensta_cmd = "sta"
    tool_timeout_s = 120
    default_lib_path = "data/lib/sky130_xx.lib"

    [planner]
    mode = "llm"                    # v1.2 默认 llm(rule 降级)
    max_iterations = 8

    [budget]
    planner_max_iterations = 8
    skill_max_iterations = 5
    run_budget_s = 600
    skill_diagnose_budget_s = 60
    skill_self_heal_budget_s = 480

---

## 7. 实现优先级 Phase 1-4(给新终端的路线图)

每个 Phase 给产出 + 验证方法。Phase 之间严格串行(后 Phase 依赖前 Phase 产出)。

### Phase 0:Day0.5 前置 gate(0.5 天,两人共做)

- 产出:WSL2 yosys/iverilog/opensta 装好;data/lib/ 有 liberty;hello-world yosys synth 跑通。
- 验证:`yosys -p "synth -top counter; stat -json"` 输出合法 JSON。
- 不通过则:不写任何 Tool 封装,先把工具装好。

### Phase 1:契约与基座(Day1-2,两人共做)

- 产出:contracts.py(CONTRACT_VERSION + artifact_ref 工厂 + ARTIFACT_FROM_STATE + §2 全部 dataclass)+ errors.py + registry.py + settings.py + runner.py(含 append_step + 僵尸自愈)+ build_registry 工厂骨架。
- 验证:`pytest tests/test_contracts.py -v` 全过(dataclass 字段完整性 + _schema 元字段 + artifact_ref 工厂返回形状);`pytest tests/test_registry.py` 全过。

### Phase 2:EDA Tool + LLM provider(Day3-4,两人分工)

- 产出(AI 成员):8 个 inject bug + TB + fault_manifest.json(data/examples/,healable=true);ClaudeProvider + CountingProvider + make_provider 工厂。
- 产出(系统成员):Yosys/iverilog/OpenSTA Tool(numeric 走 stat -json / TB 打印协议)+ 真跑单测。
- 验证:`pytest tests/test_yosys_tool.py tests/test_iverilog_tool.py tests/test_opensta_tool.py -v -m needs_eda` 全过,parsed 字段齐全;inject bug 矩阵 >= 8 条入库。

### Phase 3:Skills A + B(Day5-6,两人分工)

- 产出(AI 成员,Day5):A 诊断器(RuleLayer + LLMAttributor + ErrorKB + DiagnosisReport.to_parsed 含 11 字段;confidence 饱和项 + contradiction;Top-1 加权三支)。
- 产出(系统成员,Day6):B 自修复(_stage_* + _diagnose_and_patch + 版本栈 + _maybe_rollback + 主循环;fail_signals 包装成 ErrorItem;best_iter tie-break 最早 + candidates_at_best_score)。
- 验证:`pytest tests/test_diagnose_skill.py tests/test_self_heal_skill.py -v`(含 mock + needs_eda 用例)全过;A 语料 Top-1 加权总分 >= 0.60。

### Phase 4:C Planner + e2e + 对比实验(Day7-9,两人合流)

- 产出(Day7):CPlanner per-process + LLM planner(主)+ rule planner(降级)+ 独立验证步 + CLI 三子命令 + e2e 串联。
- 产出(Day8):experiment_manifest + experiment_summary 对比实验(baseline vs self_heal 多 run 轨迹)+ 文档对齐(三份组件文档与契约字段级互查)。
- 产出(Day9):buffer + 演示脚本 + 非技术成员整理实验记录 + eval_snapshot 预跑。
- 验证:
  - `pytest tests/test_e2e_pipeline.py -v -m needs_eda`(必过A 可执行性 + 必过B 修复效力至少 1 个 all_pass + 必过C 独立验证步)。
  - `scripts/summarize_eval.py` 产出 experiment_summary.json,min_group_pass_rate >= 0.50 且 bitwidth 类 >= 1 passed。
  - 三份组件文档与契约字段级互查 checklist 全过(非技术成员执行)。

集训 4 天(0712-0715):稳定化 + 现场演示 + 按评审反馈补加分项(OpenAICompat / MCP)。

---

## 8. 风险与对策(摘要,详见各组件文档 §11)

| 风险 | 等级 | 对策 |
|---|---|---|
| WSL2 工具 Day0.5 没装好 | 高 | Day0.5 前置 gate;C 的 stub e2e(Step C5)保底不依赖真工具 |
| LLM 不可用/限流 | 中 | `_call_llm_safe` 降级到规则 planner;规则 planner 不依赖 LLM |
| LLM tool_calls 幻觉 | 中 | 契约 §2.5 强制 registry.get 校验 + eda.tool_not_found 回灌 |
| LLM patch 语法错率高 | 高 | diff 优先 + iverilog -t null 预检,坏 patch 不入栈;降级 full_rewrite;再降级 diagnose_only |
| 预算被 B 独占 | 中 | 双层预算仲裁:C 调 B 前算 remaining 经 _remaining_budget_s 下传;B 内 min(self.budget_s, remaining);settings 给 B 480s 子预算 |
| inject bug 可修性不可控 | 中 | 偏 AI 成员 Day3 先写 8 个 bug + TB(非技术成员只标注);限定"人能 < 5 行 diff 修";healable=true 才进 50% 门槛集 |
| 评审机无 EDA 工具 | 中 | needs_eda marker 评审时 skip;提交 runs/eval_snapshot/ 预跑快照 + experiment_manifest.json 截图作为替代证据 |
| namespace/agentic 充分性被质疑 | 中 | planner_mode 默认 llm(ReAct);patch_source/convergence_cause/best_iter 入契约;C 独立验证步;演示主路径用 llm 模式 run |
| 对比实验"自己 inject 自己修"被质疑 | 高 | baseline run 定义锁死(yosys+iverilog 不调 B);fault_manifest.json 记 ground_truth_patch + healable;experiment_summary.json 分组最小值 |
| 指标全绿但诊断无用 | 中 | confidence 加饱和项 + contradiction;Top-1 加权三支总分 >= 0.6;严格/宽松命中率同报 |

---

## 9. 待确认决策(给用户的开放问题)

> 以下决策不影响 MVP 主路径推进,但集训期或评审反馈后可能需要调整。带 [v1.2 已裁决] 的已关闭。

1. [v1.2 已裁决] planner_mode 默认值 → llm(rule 降级)。
2. [v1.2 已裁决] best_iter tie-break → 取最早达到 best_score 的轮。
3. [v1.2 已裁决] experiment_manifest pass_rate 口径 → 分组最小值(均值作辅助)。
4. [v1.2 已裁决] C 独立验证步 → MVP 必加。
5. [v1.2 已裁决] inject bug 谁造 → 偏 AI 成员 Day3 写(非技术成员只标注)。
6. [v1.2 已裁决] needs_rtl_patch 口径 → 按 severity 判。
7. [开放] ErrorKB 持久化是否拆 seed/grown 两文件(A §12 #1)。
8. [开放] Top-1 命中率是否引入 LLM-as-judge(A §12 #2,MVP 用关键词)。
9. [开放] LLM planner 是否支持并行多 tool_calls(C §12 #2,MVP 取首)。
10. [开放] MCP server 是否进 MVP(C §12 #5,默认加分项)。
11. [开放] provider 切换对比实验是否集训期做(默认加分项,贴国产模型生态)。

完整 open questions 清单见各组件文档 §12 + CONTRACTS.md §12 minor 列表。
