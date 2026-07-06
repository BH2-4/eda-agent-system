# EDA Agent System Wiki 索引

> 本索引由 `scripts/gates/_rebuild_wiki.py` 从 zread wiki.json 确定性生成。


| 字段 | 值 |
|------|----|
| zread 版本 id | `2026-07-06-180551` |
| generated_at | `2026-07-06T10:05:51Z` |
| 源 wiki.json | `.zread/wiki/versions/2026-07-06-180551/wiki.json` |
| 刷新命令 | `zread generate` |

> **透明化口径矛盾**：wiki 第 5 页正文「六层」与标题「五层」系 zread 生成口径不一致，本项目对外统一用「五层 L0-L5（L0 工件存储不计入业务层）」。

> wiki 为 zread 自动生成快照，未人工校对；权威信息以 `src/` 与 `ARCHITECTURE.md` 为准。

## 页面清单

| 序号 | 标题 | section | group | level | 文件链接 |
|------|------|---------|-------|-------|----------|
| 1 | 项目概览与价值定位 | 快速入门 |  | Beginner | [01_项目概览与价值定位.md](01_项目概览与价值定位.md) |
| 2 | 环境搭建与首次运行 | 快速入门 |  | Beginner | [02_环境搭建与首次运行.md](02_环境搭建与首次运行.md) |
| 3 | 三子命令 CLI 与退出码体系 | 快速入门 |  | Beginner | [03_三子命令CLI与退出码.md](03_三子命令CLI与退出码.md) |
| 4 | 契约驱动开发哲学：CONTRACTS.md 权威机制 | 快速入门 |  | Intermediate | [04_契约驱动CONTRACTS权威机制.md](04_契约驱动CONTRACTS权威机制.md) |
| 5 | 五层分层架构总览（L0-L5） | 深入解析 | 系统架构与设计 | Intermediate | [05_五层分层架构总览.md](05_五层分层架构总览.md) |
| 6 | 端到端数据流：从用户需求到修复报告 | 深入解析 | 系统架构与设计 | Intermediate | [06_端到端数据流与修复报告.md](06_端到端数据流与修复报告.md) |
| 7 | 版本锚点与工件引用协议 | 深入解析 | 系统架构与设计 | Advanced | [07_版本锚点与工件引用协议.md](07_版本锚点与工件引用协议.md) |
| 8 | 配置系统与 settings.toml 结构 | 深入解析 | 系统架构与设计 | Intermediate | [08_配置系统与settings结构.md](08_配置系统与settings结构.md) |
| 9 | 五相状态机：PLANNING 到 DONE 的流转 | 深入解析 | 核心组件：编排大脑 CPlanner | Advanced | [09_五相状态机Planning到Done.md](09_五相状态机Planning到Done.md) |
| 10 | LLM ReAct 回环：工具决策与历史回灌 | 深入解析 | 核心组件：编排大脑 CPlanner | Advanced | [10_LLMReAct回环工具决策.md](10_LLMReAct回环工具决策.md) |
| 11 | 规则引擎降级路径与 baseline 实验 | 深入解析 | 核心组件：编排大脑 CPlanner | Intermediate | [11_规则引擎降级与baseline实验.md](11_规则引擎降级与baseline实验.md) |
| 12 | 独立验证步：B 报 all_pass 后的第三方校验 | 深入解析 | 核心组件：编排大脑 CPlanner | Advanced | [12_独立验证步第三方校验.md](12_独立验证步第三方校验.md) |
| 13 | 双层预算仲裁与迭代上限保护 | 深入解析 | 核心组件：编排大脑 CPlanner | Intermediate | [13_双层预算仲裁与迭代上限.md](13_双层预算仲裁与迭代上限.md) |
| 14 | EDA 诊断器（组件 A）：规则层与 LLM 归因 | 深入解析 | 智能技能层：诊断与自修复 | Advanced | [14_EDA诊断器组件A规则层.md](14_EDA诊断器组件A规则层.md) |
| 15 | RTL 自修复闭环（组件 B）：综合-仿真-诊断-Patch 迭代 | 深入解析 | 智能技能层：诊断与自修复 | Advanced | [15_RTL自修复闭环组件B.md](15_RTL自修复闭环组件B.md) |
| 16 | 三策略 LLM Patch：diff / full_rewrite / diagnose_only | 深入解析 | 智能技能层：诊断与自修复 | Advanced | [16_三策略LLMPatch.md](16_三策略LLMPatch.md) |
| 17 | 版本栈回退与防退化机制 | 深入解析 | 智能技能层：诊断与自修复 | Intermediate | [17_版本栈回退与防退化机制.md](17_版本栈回退与防退化机制.md) |
| 18 | Skill 到 Tool 的适配层与 reserved 字段拆包 | 深入解析 | 智能技能层：诊断与自修复 | Intermediate | [18_Skill到Tool适配层.md](18_Skill到Tool适配层.md) |
| 19 | 统一工具注册中心与开闭原则 | 深入解析 | EDA 工具封装与 LLM 集成 | Intermediate | [19_统一工具注册中心.md](19_统一工具注册中心.md) |
| 20 | Yosys 综合工具与 stat-json 解析 | 深入解析 | EDA 工具封装与 LLM 集成 | Intermediate | [20_Yosys综合工具stat-json解析.md](20_Yosys综合工具stat-json解析.md) |
| 21 | iverilog 仿真与 TB 打印协议（TEST_PASS/TEST_FAIL） | 深入解析 | EDA 工具封装与 LLM 集成 | Intermediate | [21_iverilog仿真TB协议.md](21_iverilog仿真TB协议.md) |
| 22 | OpenSTA 时序分析封装与优雅降级 | 深入解析 | EDA 工具封装与 LLM 集成 | Intermediate | [22_OpenSTA时序分析封装.md](22_OpenSTA时序分析封装.md) |
| 23 | LLM Provider 抽象协议与多后端支持 | 深入解析 | EDA 工具封装与 LLM 集成 | Intermediate | [23_LLMProvider抽象协议.md](23_LLMProvider抽象协议.md) |
| 24 | CountingProvider 装饰器与调用计量 | 深入解析 | EDA 工具封装与 LLM 集成 | Beginner | [24_CountingProvider调用计量.md](24_CountingProvider调用计量.md) |
| 25 | RunRecord 落盘与完整轨迹追溯 | 深入解析 | 工件存储与实验评估 | Intermediate | [25_RunRecord落盘轨迹追溯.md](25_RunRecord落盘轨迹追溯.md) |
| 26 | 僵尸 Run 自愈机制 | 深入解析 | 工件存储与实验评估 | Intermediate | [26_僵尸Run自愈机制.md](26_僵尸Run自愈机制.md) |
| 27 | 故障注入清单与基准实验对比 | 深入解析 | 工件存储与实验评估 | Intermediate | [27_故障注入清单与基准实验.md](27_故障注入清单与基准实验.md) |
| 28 | 实验聚合与通过率门槛（S1 达标规则） | 深入解析 | 工件存储与实验评估 | Intermediate | [28_实验聚合通过率门槛S1.md](28_实验聚合通过率门槛S1.md) |
| 29 | ErrorKB 错误知识库：模式匹配与增量增长 | 深入解析 | 工件存储与实验评估 | Advanced | [29_ErrorKB错误知识库.md](29_ErrorKB错误知识库.md) |
| 30 | 测试框架与 needs_eda / needs_llm 标记策略 | 深入解析 | 测试体系与扩展指南 | Beginner | [30_测试框架needs_eda标记策略.md](30_测试框架needs_eda标记策略.md) |
| 31 | 扩展新 EDA 工具的完整指南 | 深入解析 | 测试体系与扩展指南 | Intermediate | [31_扩展新EDA工具指南.md](31_扩展新EDA工具指南.md) |
