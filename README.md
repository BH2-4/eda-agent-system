# Agentic EDA 系统 — Agentic4Systems Hackathon 参赛项目

> Track 01 Agentic EDA Infra | 完赛奖(3000 元)| 提交截止 2026-07-15 11:00
> 团队:2 人核心(AI/Agent/系统)+ 1 人非技术(数据/文档/演示)
> 环境:Win11 + RTX3060 Laptop | EDA 工具跑 WSL2 Ubuntu-24.04(yosys 0.33 / iverilog 12.0) | LLM 用智谱 GLM Coding Plan(glm-5.2)

---

## 1. 项目目标

把芯片设计流程变成可执行闭环:让智能体接入 EDA 工具链(Yosys 综合 / iverilog 仿真 / OpenSTA 时序),完成 设计生成 → 仿真验证 → 综合评估 → 错误分析 → 迭代优化 的全自动迭代,而非一次性脚本。

具体落地为一个 **Agentic EDA 流水线**,三个核心组件合成一个可被调用的接口:

- **C(Planner / Tool-Use 层,L4)** = 大脑:任务分解 + 工具编排(默认 LLM ReAct)+ 迭代控制 + 对外接口(CLI / SDK / MCP)。实现:`src/eda_agent/planner/c_planner.py`。
- **Tool Registry(L3)** = 手脚:把 EDA 工具 + skill 封装成统一 Tool 接口,供 C 调用。实现:`src/eda_agent/registry.py` + `tools/` + `skills/`。
- **A(Agentic EDA 诊断器,L2 skill)** = 日志归因 + 修复建议,注册为 Tool(skill_diagnose)。实现:`src/eda_agent/skills/diagnose.py`。
- **B(Agentic RTL 自修复闭环,L2 skill)** = 综合 → 仿真 → 诊断 → LLM patch 迭代,带版本回退与防退化,注册为 Tool(skill_self_heal)。实现:`src/eda_agent/skills/self_heal.py`。
- **工件存储(L0)** = runs/<run_id>/ 落每次实验的轨迹 / 日志 / 产物,满足完赛奖"实验记录 + 失败案例"。

---

## 2. 比赛 / 完赛奖映射

    完赛奖硬指标              本项目落点
    ────────────────────── ──────────────────────────────────────────────
    (1) 真实可跑组件         A + B + Yosys/iverilog/OpenSTA 三 Tool 真跑通(WSL2)
    (2) 清晰可调用接口       CLI(eda self-heal/diagnose/report)+ SDK(run_pipeline)+ MCP(加分)
    (3) 实验证据/指标对比    runs/ 落轨迹 + experiment_manifest + experiment_summary + baseline 对比
    (4) EDA 三赛道且 agentic C 默认 LLM planner + B 内部迭代 + patch 回退 + C 独立验证步

完赛奖官方标准:奖励完成扎实系统组件、为整体基座贡献关键能力的团队;评审看重**能被集成、能被验证、能推动基座继续生长**的真实贡献。本项目用 CONTRACT_VERSION 锚点 + artifact_ref 协议 + as_tool 映射表保证可被集成,用 baseline vs self_heal 对比实验 + 独立验证步保证可被验证。

---

## 3. 文件导航

| 文件 | 角色 | 读者 |
|---|---|---|
| README.md(本文件) | 项目目标 + 比赛 + 文件导航 + 快速开始 + 角色分工 | 所有人先读 |
| ARCHITECTURE.md | 项目总览(分层 + 端到端数据流 + 契约精华 + 目录树 + Phase 排期 + 风险) | 所有人 |
| CONTRACTS.md | **共享契约(宪法,权威)** — 全部 dataclass / Tool/Skill 接口 / 错误码 / 工件协议 | 实现者必读,冲突时以此为准 |
| 验收标准.md | 验收项汇总(可勾选表)+ 验收流程(Claude 如何验收) | 验收者(Claude)+ 非技术成员 |
| 组件A_诊断器.md | A(skill_diagnose)设计文档 — 日志归因 + 修复建议 | A 实现者 |
| 组件B_自修复闭环.md | B(skill_self_heal)设计文档 — RTL 自修复迭代闭环 | B 实现者 |
| 组件C_Planner_ToolUse.md | C(CPlanner)设计文档 — Planner / Tool-Use 层 + CLI/MCP | C 实现者 |

代码结构见 ARCHITECTURE.md §4 完整目录树(与 CONTRACTS.md §4 唯一对齐)。

---

## 4. 快速开始

    # 前置:WSL2 Ubuntu-24.04 装好 yosys/iverilog(opensta 可选,未装则 STA 降级)
    # LLM key 写入 .env 的 GLM_API_KEY(智谱 Coding Plan 订阅,key 不进 git)

    git clone <repo> && cd eda-agent-system
    pip install -e .
    cp .env.example .env  # 填入 GLM_API_KEY

    # 跑一个 inject bug 的自修复(端到端)
    eda self-heal \
        --rtl data/examples/counter_bitwidth/rtl.v \
        --tb  data/examples/counter_bitwidth/tb.v \
        --goal "pass all tests"
    # → 打印 runs/<run_id>/report.md 路径,退出码 0=ok/1=failed/2=budget_exhausted

    # 单点演示 A 诊断器
    eda diagnose --rtl data/examples/counter_bitwidth/rtl.v --tb data/examples/counter_bitwidth/tb.v

    # 渲染已有 run 的报告
    eda report <run_id>

    # Python SDK 一行调
    python -c "from eda_agent import run_pipeline; from eda_agent.contracts import RunRequest; \
        r = run_pipeline(RunRequest(kind='self_heal', goal='pass all tests', \
        rtl_path='data/examples/counter_bitwidth/rtl.v', \
        tb_path='data/examples/counter_bitwidth/tb.v', max_iter=5), None); print(r.report_path, r.status)"

实现进度:Phase 0-6 全完成(**200+ 单测绿**,GLM-5.2 Coding Plan 真跑通 e2e + T54 全量 8 bug 实验 S1 达标 + 良好线)。详见 ARCHITECTURE.md §7 + `runs/eval_snapshot/verdict.md`。

---

## 5. 角色分工

| 角色 | 职责 | 在本项目的位置 |
|---|---|---|
| **架构(本 workflow)** | 系统总设计 + 契约定稿 + 组件文档终稿 + ARCHITECTURE/验收标准/README 产出 | CONTRACTS.md(权威)+ 三份组件文档 + ARCHITECTURE.md |
| **实现(新终端)** | 照组件文档与契约写代码,Phase 1-4 逐步推进,每步带 pytest 验证 | src/eda_agent/ + tests/ + scripts/ |
| **验收(Claude)** | 按 验收标准.md §7 流程逐项验收,给 PASS/FAIL + 修复建议 | 验收标准.md §7 |
| **偏 AI 成员** | inject bug + TB + fault_manifest(Day3)+ ClaudeProvider + A 诊断器(Day5)+ 演示脚本 | data/examples/ + skills/diagnose.py |
| **偏系统成员** | WSL2 工具 gate + Yosys/iverilog/OpenSTA Tool + B 自修复(Day6)+ e2e 串联 | tools/ + skills/self_heal.py |
| **非技术成员** | fault_manifest.json 标注 + 实验记录整理 + 接口文档 + 演示脚本 + 文档字段级互查(Day8) | data/fault_manifest.json + runs/ 整理 + docs/ |

---

## 6. 待确认决策指引

带 **[v1.2 已裁决]** 的决策已关闭,实现者照做;带 **[开放]** 的决策不阻塞 MVP,集训期或评审反馈后调整。完整清单见:

- ARCHITECTURE.md §9(总览)
- CONTRACTS.md §11 裁决 + §12 minor
- 各组件文档 §12

关键已裁决项(实现者必照):
1. planner_mode 默认 `llm`(rule 仅作降级)。
2. best_iter tie-break 取最早达到 best_score 的轮。
3. 自修复通过率门槛 = 分组最小值 >= 0.50 且 bitwidth 类 >= 1 all_pass。
4. C 在 B 报 all_pass 后必须追加独立 iverilog_sim 验证步。
5. inject bug 由偏 AI 成员 Day3 写(非技术成员只标注),healable=true 才进 50% 门槛集。
6. needs_rtl_patch 按 severity 判(error/fatal → True)。
7. inject bug RTL 统一放 data/examples/(唯一权威路径)。
8. namespace 表含 diagnose(A)+ heal(B),C 按 namespace 聚类读 error_code。

关键开放项(不阻塞 MVP,集训期定):
- ErrorKB 是否拆 seed/grown 两文件。
- Top-1 命中率是否引入 LLM-as-judge。
- LLM planner 是否支持并行多 tool_calls。
- MCP server / OpenAICompat 是否集训期做(默认加分项)。

---

## 7. 联系与版本

- 契约版本:CONTRACT_VERSION = "0.1.0"(CONTRACTS.md 锚点)。
- 文档版本:doc v1.2(本 README 对齐 CONTRACTS.md v1.2)。
- 比赛提交截止:2026-07-15 11:00。
- 问题:先查 CONTRACTS.md(权威),再查 ARCHITECTURE.md(总览),再查对应组件文档。
