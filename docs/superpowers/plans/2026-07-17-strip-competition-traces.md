# 清理比赛痕迹 — 实现计划（Tasks）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `BH2-4/eda-agent-system` 仓库的比赛/黑客松框架痕迹全部清除，回归普通开源项目，保留全部技术内核。

**Architecture:** 纯文档/元数据/注释/门禁脚本修改，不碰 `src/` 业务代码、prompts、tests。唯一改逻辑的脚本是 `check_integrity.py`（重写为通用门禁）。

**Tech Stack:** Markdown, TOML, Python（门禁脚本）。

**Spec:** `docs/superpowers/specs/2026-07-17-strip-competition-traces-design.md`

**验收硬命中词（任一文件出现即 fail）：** `完赛奖|Hackathon|Agentic4Systems|参赛|比赛|集训|3000 ?元|提交截止|赛题|赛道`

**通用验证命令（每文档任务后跑，预期 0 命中）：**
```bash
grep -nE "完赛奖|Hackathon|Agentic4Systems|参赛|比赛|集训|3000 ?元|提交截止|赛题|赛道" <file>
```

---

## Task 1: 删除纯比赛产物文件 + 死代码脚本

**Files:**
- Delete: `实现执行计划.md`
- Delete: `_审计报告_v1.md`
- Delete: `docs/演示脚本.md`
- Delete: `scripts/gates/_rebuild_wiki.py`

- [ ] **Step 1: 删除 4 个文件**
```bash
git rm "实现执行计划.md" "_审计报告_v1.md" "docs/演示脚本.md" scripts/gates/_rebuild_wiki.py
```

- [ ] **Step 2: 验证已删文件无残留内容**
```bash
# 预期: 4 文件均 No such file
ls "实现执行计划.md" "_审计报告_v1.md" "docs/演示脚本.md" scripts/gates/_rebuild_wiki.py 2>&1 | grep -c "No such file"
```
Expected: `4`

- [ ] **Step 3: commit**
```bash
git commit -m "chore: remove competition-only artifacts and dead rebuild script

- 实现执行计划.md / _审计报告_v1.md / docs/演示脚本.md: 比赛日程/审计/演示产物
- scripts/gates/_rebuild_wiki.py: 死代码(硬编码 D:/ 路径 + 依赖不存在的 .zread/)"
```

---

## Task 2: 包元数据 + 配置注释

**Files:**
- Modify: `pyproject.toml:8`
- Modify: `settings.toml:2, 4, 6, 21`

- [ ] **Step 1: pyproject.toml description**
- before (L8): `description = "Agentic EDA pipeline for Agentic4Systems Hackathon (CPlanner + Tool Registry + A diagnose + B self_heal)"`
- after: `description = "Agentic EDA pipeline: LLM planner + tool registry + diagnose + self-heal closed loop"`

- [ ] **Step 2: settings.toml L2 注释**
- before: `# planner_mode 默认 llm(契约裁决 #7:Track 01 agentic 充分性);rule 为降级路径。`
- after: `# planner_mode 默认 llm(契约裁决 #7:保证 planner 真正组织工具迭代);rule 为降级路径。`

- [ ] **Step 3: settings.toml L4 provider 注释**
- before: `provider = "glm"               # glm(智谱,默认) | claude(备用) | qwen | deepseek(加分项)`
- after: `provider = "glm"               # glm(智谱,默认) | claude(备用) | qwen | deepseek`

- [ ] **Step 4: settings.toml L6 glm_base_url 注释（去 "Coding Plan" 措辞）**
- before: `glm_base_url = "https://open.bigmodel.cn/api/coding/paas/v4"  # Coding Plan OpenAI 兼容端点(非通用 /api/paas/v4,否则 429/1113)`
- after: `glm_base_url = "https://open.bigmodel.cn/api/coding/paas/v4"  # 智谱 OpenAI 兼容端点(非通用 /api/paas/v4,否则 429/1113)`

- [ ] **Step 5: settings.toml L21 qwen 注释（去"加分项"）**
- before: `# qwen_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"   # 加分项`
- after: `# qwen_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"   # 可选 provider`

- [ ] **Step 6: settings.toml default_lib_path 注释（去 "Day0.5 gate"）**
- before: `default_lib_path = "data/lib/sky130_xx.lib"   # Day0.5 gate 确认的默认 liberty`
- after: `default_lib_path = "data/lib/sky130_xx.lib"   # 默认 liberty 工艺库`

- [ ] **Step 7: 验证两文件零硬命中 + 零上下文词残留**
```bash
grep -nE "完赛奖|Hackathon|Agentic4Systems|参赛|比赛|集训|赛题|赛道|Track ?01|加分项|Day[0-9]|Coding Plan" pyproject.toml settings.toml
```
Expected: 无输出

- [ ] **Step 8: commit**
```bash
git add pyproject.toml settings.toml
git commit -m "chore: strip competition framing from package metadata and config

- pyproject.toml: description 去 Agentic4Systems Hackathon
- settings.toml: 注释去 Track 01/加分项/Day0.5 gate/Coding Plan 措辞"
```

---

## Task 3: 重写 check_integrity.py 为通用项目门禁

**Files:**
- Modify: `scripts/gates/check_integrity.py`

**背景**：G3 当前要求 README 必含 `Agentic4Systems`/`完赛奖`/`2026-07-15`；G1 锁死仅可改 README。清理后这两条必然 fail，必须同步改脚本。

- [ ] **Step 1: G3_KEYWORDS 去比赛词（L46-58 附近）**
- before:
```python
G3_KEYWORDS = {
    "Agentic4Systems",
    "完赛奖",
    "CONTRACT_VERSION",
    "eda self-heal",
    "eval_snapshot",
    "Phase",
    "2026-07-15",
    "artifact_ref",
    "as_tool",
    "baseline",
    "CONTRACTS.md",
}
```
- after:
```python
G3_KEYWORDS = {
    "CONTRACT_VERSION",
    "eda self-heal",
    "eval_snapshot",
    "Phase",
    "artifact_ref",
    "as_tool",
    "baseline",
    "CONTRACTS.md",
}
```

- [ ] **Step 2: G1 放宽修改白名单（ALLOWED_M 与 M 判定逻辑）**
- 目标：允许修改任意 `*.md`/`*.toml`/`docs/**`/`scripts/**`，不再锁死 README。删除 D（删除）禁令、R（重命名）禁令（普通项目允许文件增删改）。
- 具体改 `gate_g1()`：将"禁 D/R、M 仅白名单、?? 仅 docs/wiki"的逻辑，替换为宽松版——只保留"暂存区不含 .env/.zread/、非 eval_snapshot 的 runs/"清洁度检查（这些已由 G5 覆盖）。
- 简化方案：将 `gate_g1()` 改为仅做"前置 .zread ignore 检查 + 暂存区不含 .zread/"两项断言（其余结构约束移除，因为普通项目无需锁死文件改动）。保留函数签名与 `_result("G1", ok, ...)` 返回结构不变。

- [ ] **Step 3: 验证脚本自身零硬命中**
```bash
grep -nE "完赛奖|Hackathon|Agentic4Systems|参赛|比赛|集训|赛题|赛道|Track ?01|2026-07-15" scripts/gates/check_integrity.py
```
Expected: 无输出

- [ ] **Step 4: 验证脚本可运行（语法正确）**
```bash
python -c "import ast; ast.parse(open('scripts/gates/check_integrity.py').read()); print('syntax ok')"
```
Expected: `syntax ok`

- [ ] **Step 5: commit**
```bash
git add scripts/gates/check_integrity.py
git commit -m "refactor(gate): rewrite check_integrity.py as generic project gate

- G3 keywords: drop competition terms (Agentic4Systems/完赛奖/2026-07-15)
- G1: relax file-modification whitelist (allow *.md/*.toml/docs/**);
  remove delete/rename prohibitions (普通项目允许文件增删改)"
```

---

## Task 4: README.md

**Files:**
- Modify: `README.md`（L1, L3, L13, L15-21, L196-219, L258, L284, L292, L304）

- [ ] **Step 1: 标题（L1）**
- before: `# Agentic EDA Agent System — Agentic4Systems Hackathon 参赛项目`
- after: `# Agentic EDA Agent System`

- [ ] **Step 2: banner（L3）**
- before: `> Track 01 Agentic EDA Infra | 完赛奖 3000 元 | 提交截止 2026-07-15 11:00`
- after: `> Agentic EDA Infra — LLM 驱动的 RTL 自愈流水线（diagnose → patch → 验证 → 归档）`

- [ ] **Step 3: §1 引言（L13）去"完赛奖 4 条硬指标"**
- before: `...并设计为满足完赛奖 4 条硬指标:`
- after: `...并围绕四条工程目标设计:`

- [ ] **Step 4: §1 表头（L15）**
- before: `| 完赛奖硬指标 | 本项目落点 |`
- after: `| 工程目标 | 本项目落点 |`

- [ ] **Step 5: 删除整个 §6 比赛/完赛奖映射（L196-219 整节）**
- 用 Edit 删除从 `## 6. 比赛 / 完赛奖映射` 到下一个 `## ` 之前的全部内容。

- [ ] **Step 6: 文件导航表（L258）**
- before: `| **README.md**(本文件) | 项目目标 + 比赛 + 五层架构 + 数据流 + 组件 + 快速开始 + 已裁决决策 + 进度 | 所有人先读 |`
- after: `| **README.md**(本文件) | 项目目标 + 五层架构 + 数据流 + 组件 + 快速开始 + 已裁决决策 + 进度 | 所有人先读 |`
- 同时删除文件导航表中指向 `实现执行计划.md`、`_审计报告_v1.md`、`docs/演示脚本.md` 的行（若存在）。

- [ ] **Step 7: §10 关键开放项（L284）**
- before: `关键开放项(不阻塞 MVP,集训期定):ErrorKB 是否拆 seed/grown 两文件;Top-1 命中率是否引入 LLM-as-judge;LLM planner 是否支持并行多 tool_calls;MCP server / OpenAICompat 是否集训期做。`
- after: `关键开放项(不阻塞 MVP,后续迭代定):ErrorKB 是否拆 seed/grown 两文件;Top-1 命中率是否引入 LLM-as-judge;LLM planner 是否支持并行多 tool_calls;MCP server / OpenAICompat 是否后续做。`

- [ ] **Step 8: §11 GLM 实验结果（L292）去"完赛奖 S1 达标"**
- before: `...bitwidth 类 2/2 all_pass ≥ 1 门槛 → **完赛奖 S1 达标 + 良好线**(综合通过率 0.875)。`
- after: `...bitwidth 类 2/2 all_pass ≥ 1 门槛 → **S1 硬门槛达标 + 良好线**(综合通过率 0.875)。`

- [ ] **Step 9: §11 删除截止日期（L304）**
- before: `- **比赛提交截止**:**2026-07-15 11:00**。`
- after: 删除整行。

- [ ] **Step 10: 验证 README 零硬命中**
```bash
grep -nE "完赛奖|Hackathon|Agentic4Systems|参赛|比赛|集训|赛题|赛道|Track ?01|3000" README.md
```
Expected: 无输出

- [ ] **Step 11: 验证无指向已删文件的链接**
```bash
grep -nE "实现执行计划|_审计报告_v1|演示脚本" README.md
```
Expected: 无输出

- [ ] **Step 12: commit**
```bash
git add README.md
git commit -m "docs(README): reframe as generic project, drop competition framing

- 标题/banner 去 参赛项目/Track01/完赛奖/截止
- §1 工程目标表 去 硬指标表头
- 删 §6 比赛/完赛奖映射整节
- 文件导航去 比赛 字样 + 删已删文件行
- §11 删截止日期, GLM 结果去 完赛奖 S1 达标"
```

---

## Task 5: ARCHITECTURE.md

**Files:**
- Modify: `ARCHITECTURE.md`（L3, L11-15, L17-28, L39, L162, L377, L400, L412）

- [ ] **Step 1: banner（L3）**
- before: `> 本文件是 Agentic4Systems 暑期学校 Hackathon 参赛系统的**项目总览**。...`
- after: `> 本文件是 Agentic EDA Agent System 的**项目总览**。...`（保留后半句引用 CONTRACTS/验收标准/组件文档的部分）

- [ ] **Step 2: §1.1 标题 + 内容（L11-15）**
- L11 before: `### 1.1 比赛与目标` → after: `### 1.1 背景与目标`
- L13-15（比赛/赛道/完赛奖/3000元/评审 整块）→ 替换为通用背景：
  - after: `- 背景:RTL 设计中的 bug 定位与修复高度依赖人工,本系统用 Agent 把 diagnose→patch→验证 串成可执行闭环。`
  - after: `- 目标:给定带 bug 的 RTL + 测试激励,系统能自主发现、修复、验证,直到通过测试或耗尽预算,且每次实验可追溯。`

- [ ] **Step 3: 删除 §1.2 完赛奖四条硬指标映射（L17-28 整节）**
- 删除从 `### 1.2 完赛奖四条硬指标 → 系统映射` 到下一个 `### ` 之前的全部内容（含 L19-28 的表）。

- [ ] **Step 4: 时间表（L39）**
- before: `- 时间:9 天准备期(现在→07-11)+ 4 天集训(0712-0715,0715 11:00 提交)。`
- after: `- 阶段:Phase0 基座 → Phase1 三 Tool 封装 → Phase2 故障注入+provider → Phase3 A/B 组件 → Phase4 C+e2e。`（或直接删除此行，若后续 §7 已有阶段说明）

- [ ] **Step 5: api.md 注释（L162）**
- before: `            api.md                          # 接口速查(集训期补)`
- after: `            api.md                          # 接口速查(待补)`

- [ ] **Step 6: §7 阶段（L377）**
- before: `集训 4 天(0712-0715):稳定化 + 现场演示 + 按评审反馈补加分项(OpenAICompat / MCP)。`
- after: `稳定化阶段:稳定性优化 + 可选扩展(OpenAICompat / MCP)按需补充。`

- [ ] **Step 7: §开放决策引言（L400）**
- before: `> 以下决策不影响 MVP 主路径推进,但集训期或评审反馈后可能需要调整。带 [v1.2 已裁决] 的已关闭。`
- after: `> 以下决策不影响 MVP 主路径推进,但后续迭代中可能需要调整。带 [v1.2 已裁决] 的已关闭。`

- [ ] **Step 8: §开放决策 #11（L412）**
- before: `11. [开放] provider 切换对比实验是否集训期做(默认加分项,贴国产模型生态)。`
- after: `11. [开放] provider 切换对比实验是否后续做(可选,贴国产模型生态)。`

- [ ] **Step 9: 验证零硬命中 + 零上下文词**
```bash
grep -nE "完赛奖|Hackathon|Agentic4Systems|参赛|比赛|集训|赛题|赛道|Track ?01|评审|评委|加分项|准备期|Day[0-9]|现场演示" ARCHITECTURE.md
```
Expected: 无输出

- [ ] **Step 10: commit**
```bash
git add ARCHITECTURE.md
git commit -m "docs(ARCHITECTURE): replace competition identity with engineering background

- banner/§1.1 去 Hackathon/比赛/赛道/完赛奖/3000元/评审
- 删 §1.2 完赛奖四条硬指标映射
- 时间表改 Phase 阶段, 去集训/准备期/Day4/现场演示
- §7/§开放决策 去 集训期/评审/加分项"
```

---

## Task 6: CONTRACTS.md

**Files:**
- Modify: `CONTRACTS.md`（~30 处：L3, L5, L19, L29, L63, L422, L502, L590, L633, L876, L881, L898, L988, L1110, L1145, L1150, L1173, L1192, L1232-1248, L1258, L1266, L1274-1282）

> 这是最大的一块。策略：先删整节（§10 矩阵），再批量替换高频理由引用，最后逐处处理剩余。

- [ ] **Step 1: banner（L3）**
- before: `> 本文件是 Agentic4Systems Hackathon 参赛系统的**最高约束文档**。...`
- after: `> 本文件是 Agentic EDA Agent System 的**最高约束文档**。...`

- [ ] **Step 2: 设计原则（L5）**
- before: `> 设计原则:开闭原则(...)、最小可用(MVP 先跑通完赛奖四条硬指标)、可追溯(...)...`
- after: `> 设计原则:开闭原则(...)、最小可用(MVP 聚焦四条核心目标)、可追溯(...)...`

- [ ] **Step 3: v1.2 说明（L19）**
- before: `...新增 baseline run 定义 + fault_manifest.json schema + 聚合层 experiment_summary.json,完赛奖第 3 条指标对比可复现。`
- after: `...新增 baseline run 定义 + fault_manifest.json schema + 聚合层 experiment_summary.json,实验指标对比可复现。`

- [ ] **Step 4: §11/§12 引用（L29）**
- before: `> 文末附 §11 裁决说明(...)与 §12 已知次要问题(minor 列表,留待集训期处理)。`
- after: `> 文末附 §11 裁决说明(...)与 §12 已知次要问题(minor 列表,留待后续迭代处理)。`

- [ ] **Step 5: L0 工件存储理由（L63）**
- before: `- L0 工件存储:每次 run 一个目录,记录完整轨迹,满足完赛奖的"实验记录 / 失败案例"要求。`
- after: `- L0 工件存储:每次 run 一个目录,记录完整轨迹,满足"实验记录 / 失败案例"的可追溯要求。`

- [ ] **Step 6: Skill 区分理由（L422）**
- before: `...完赛奖要的"失败案例"主要靠此状态。`
- after: `..."失败案例"的可追溯主要靠此状态。`

- [ ] **Step 7: config_snapshot（L502）**
- before: `config_snapshot 字段(完赛奖第 3 条"指标对比"与 provider 切换实验复现依据):`
- after: `config_snapshot 字段("指标对比"与 provider 切换实验复现依据):`

- [ ] **Step 8: baseline run（L590）**
- before: `baseline run 定义(v1.2,完赛奖第 3 条指标对比的复现依据):`
- after: `baseline run 定义(v1.2,指标对比的复现依据):`

- [ ] **Step 9: 存储满足（L633）**
- before: `这套存储直接满足完赛奖第 3 条"实验证据 / 指标对比":...`
- after: `这套存储直接满足"实验证据 / 指标对比":...`

- [ ] **Step 10: MCP 适配（L876, L881, L898）**
- L876 before: `MCP 适配约定(集训期实施前必读):` → after: `MCP 适配约定(实施前必读):`
- L881 before: `若集训评估 4 天做不完,降级为...,不要让加分项反噬 MVP。` → after: `若评估做不完,降级为...,不要让扩展项反噬 MVP。`
- L898 before: `api.md # 接口速查(集训期补)` → after: `api.md # 接口速查(待补)`

- [ ] **Step 11: fault_manifest（L988）**
- before: `fault_manifest.json schema(v1.2 新增,完赛奖第 3 条复现依据):`
- after: `fault_manifest.json schema(v1.2 新增,复现依据):`

- [ ] **Step 12: planner_mode=llm 理由（L1110）**
- before: `...契合 Track 01 agentic 充分性`
- after: `...保证 planner 真正组织工具迭代`

- [ ] **Step 13: §9 MVP checklist 标题（L1145）**
- before: `完赛奖四条硬指标 → MVP 必须做(打勾对应硬指标):`
- after: `MVP 核心目标 → 必须做:`

- [ ] **Step 14: §9 checklist 三赛道项（L1150）**
- before: `- [x] EDA 三赛道且 agentic:...`
- after: `- [x] agentic 充分:C 默认 LLM planner 用 ReAct 组织工具迭代,...`（去"三赛道"）

- [ ] **Step 15: §9 加分项标题（L1173）**
- before: `加分项(有余力再做,不阻塞完赛奖):`
- after: `可选扩展(有余力再做,不阻塞 MVP):`

- [ ] **Step 16: §9 集训行（L1192）**
- before: `- 集训 4 天(0712-0715):稳定化 + 现场演示 + 按评审反馈补加分项(OpenAICompat/MCP)。`
- after: `- 稳定化阶段:稳定性优化 + 按需补可选扩展(OpenAICompat/MCP)。`

- [ ] **Step 17: 删除 §10 完赛奖对齐矩阵（L1232-1248 整节）**
- 删除从 `## 10. 与完赛奖四条硬指标的对齐矩阵` 到下一个 `## ` 之前的全部内容。
- 注意：删除后后续 `## 11.`/`## 12.` 等章节号需保持原样（markdown 标题文本，不影响渲染）。若 §10 后紧跟 §11，可直接删 §10；若担心编号跳号，可把原 §11 重编为 §10（但为减少改动，保留原编号亦可，因为 CONTRACTS 内部引用用的是语义名而非纯数字——执行时核对）。

- [ ] **Step 18: §11 冲突裁决（L1258, L1266）**
- L1258 before: `...diagnose 单点 demo 对评委展示 A 诊断器能力有显著加分...` → after: `...diagnose 单点 demo 对展示 A 诊断器能力有显著价值...`
- L1266 before: `冲突 7(v1.2 新增):planner_mode 默认值 → **默认 llm**。理由:Track 01 原文要求"agent 组织工具、迭代、自修复"..."避免被评委会判定..."` → after: `冲突 7(v1.2 新增):planner_mode 默认值 → **默认 llm**。理由:保证"agent 组织工具、迭代、自修复"的主路径..."避免被误判为会循环的 wrapper"...`

- [ ] **Step 19: §12 minor 标题与内容（L1274, L1276, L1278, L1281, L1282）**
- L1274 before: `## 12. 已知次要问题(minor,留待集训期处理)` → after: `## 12. 已知次要问题(minor,留待后续迭代处理)`
- L1276 before: `以下 minor 问题不阻塞 v1.2 交付,记录在此供集训期按需处理:` → after: `以下 minor 问题不阻塞 v1.2 交付,记录在此供后续迭代按需处理:`
- L1278 before: `12. [minor]decorator 注册(加分项)... 集训期若做装饰器再补。` → after: `12. [minor]decorator 注册(可选扩展)... 后续若做装饰器再补。`
- L1281 before: `15. [minor]... 评审无工具时提交 runs/eval_snapshot/ 预跑快照作为替代证据...` → after: `15. [minor]... 无 EDA 工具环境时用 runs/eval_snapshot/ 快照作为可复现证据...`
- L1282 before: `16. [minor]LLM planner 每轮取首个 tool_call —— MVP 取首,集训期若评委看重...` → after: `16. [minor]LLM planner 每轮取首个 tool_call —— MVP 取首,后续若需"一次规划多步"...`

- [ ] **Step 20: 全文扫荡剩余上下文词（评委/评审/Track 01/硬指标）**
```bash
grep -nE "评委|评审|Track ?01|硬指标|加分项|集训|准备期|现场演示|演示主路径" CONTRACTS.md
```
- 对每处命中：把"为评委/完赛奖/Track 01"的理由 → 工程理由。逐行 Edit。

- [ ] **Step 21: 验证零硬命中**
```bash
grep -nE "完赛奖|Hackathon|Agentic4Systems|参赛|比赛|集训|赛题|赛道|Track ?01|评委|评审|加分项|硬指标" CONTRACTS.md
```
Expected: 无输出

- [ ] **Step 22: commit**
```bash
git add CONTRACTS.md
git commit -m "docs(CONTRACTS): strip competition rationale, keep contract mechanics

- banner/设计原则 去 Hackathon/完赛奖
- 删 §10 完赛奖对齐矩阵整节
- §9 checklist 去 硬指标编号, 加分项→可选扩展
- ~30 处理由引用: 契合Track01/避免评委判定 → 工程理由
- 集训期→后续迭代, 完赛奖第N条→实验可复现要求"
```

---

## Task 7: 验收标准.md → QA checklist

**Files:**
- Modify: `验收标准.md`（L5, L11, L18, L104-114, L120, L126, L151, L159, L161）

- [ ] **Step 1: §1 标题（L11）**
- before: `## 1. 系统级硬指标(完赛奖四条,必过)`
- after: `## 1. 系统级验收项`

- [ ] **Step 2: §1 表 S4 行（L18）**
- before: `| S4 | (4) EDA 三赛道且 agentic | ...`
- after: `| S4 | agentic 充分 | ...`（去"三赛道"）

- [ ] **Step 3: §5 needs_eda 说明（L5）**
- before: `...needs_eda marker 需 yosys/iverilog/opensta 装好;评审无工具时见 §6 替代证据。`
- after: `...needs_eda marker 需 yosys/iverilog/opensta 装好;无 EDA 工具环境时见 §6 替代证据。`

- [ ] **Step 4: §6 标题与内容（L104-114）**
- L104 before: `## 6. 评审无工具时的替代证据(needs_eda skip 场景)` → after: `## 6. 无 EDA 工具环境的替代证据(needs_eda skip 场景)`
- L106 before: `评审机若未装 yosys/iverilog/opensta,needs_eda 测试会 skip。此时团队提交以下替代证据(准备期 Day9 预跑):` → after: `若环境未装 yosys/iverilog/opensta,needs_eda 测试会 skip。此时提供以下替代证据(预跑快照):`
- L114 before: `| E5 | 现场重跑(若评审允许)| 团队自带环境现场重跑 1-2 个 inject bug 复核 |` → after: `| E5 | 现场重跑(可选)| 自带环境重跑 1-2 个 inject bug 复核 |`

- [ ] **Step 5: §7.1（L120）**
- before: `### 7.1 验收前置(团队 Day9 提交前自检)`
- after: `### 7.1 验收前置(贡献者自检清单)`

- [ ] **Step 6: §7.2（L126）**
- before: `### 7.2 Claude 验收步骤(评审现场或远程)`
- after: `### 7.2 自动化验收步骤`
- 并把 §7.2 正文里"Claude 作为验收方/评审"的框架 → 通用 CI/手动验收步骤（逐句改，去"评审现场/远程"）。

- [ ] **Step 7: §7 Step6（L151）**
- before: `**Step 6:替代证据(评审无工具时)**`
- after: `**Step 6:替代证据(无 EDA 工具环境时)**`

- [ ] **Step 8: §7 结果判定（L159, L161）**
- L159 before: `- 所有"必过"项 ☑ → 完赛奖达成。` → after: `- 所有"必过"项 ☑ → MVP 验收线达成。`
- L161 before: `- "加分"项不影响完赛奖判定,但影响评委对"为基座贡献关键能力"的印象分。` → after: `- "可选扩展"项不影响 MVP 验收,但体现项目能力的广度。`

- [ ] **Step 9: 验证零硬命中 + 零上下文词**
```bash
grep -nE "完赛奖|Hackathon|参赛|比赛|集训|赛题|赛道|Track ?01|评委|评审|加分|硬指标|Day[0-9]|团队" 验收标准.md
```
Expected: 无输出（注意"团队"可能合法出现，逐处判断）

- [ ] **Step 10: commit**
```bash
git add 验收标准.md
git commit -m "docs(验收标准): reframe competition rubric as generic QA checklist

- §1 去 完赛奖四条; §6 评审→集成环境; §7.1 团队Day9→贡献者自检
- §7.2 Claude验收(评委)→自动化验收
- 完赛奖达成→MVP验收线; 加分项→可选扩展"
```

---

## Task 8: 组件A_诊断器.md

**Files:**
- Modify: `组件A_诊断器.md`（L221, L756, L827）

- [ ] **Step 1: §4.4 标题（L221）**
- before: `### 4.4 ErrorKB 增长机制(兑现完赛奖"失败案例沉淀为系统能力")`
- after: `### 4.4 ErrorKB 增长机制(失败案例沉淀为系统能力)`

- [ ] **Step 2: 命中率报告（L756）**
- before: `严格命中率(...)与宽松命中率(...)同时报,让评委自行判断。`
- after: `严格命中率(...)与宽松命中率(...)同时报,供读者自行判断。`

- [ ] **Step 3: Top-1 判据（L827）**
- before: `...倾向:MVP 用关键词,集训期若评委质疑再升级。`
- after: `...倾向:MVP 用关键词,后续若需语义近似再升级。`

- [ ] **Step 4: 验证零硬命中 + 零上下文词**
```bash
grep -nE "完赛奖|集训|赛题|赛道|Track ?01|评委|评审|加分项" 组件A_诊断器.md
```
Expected: 无输出

- [ ] **Step 5: commit**
```bash
git add 组件A_诊断器.md
git commit -m "docs(组件A): drop competition parentheticals in ErrorKB/hit-rate sections"
```

---

## Task 9: 组件B_自修复闭环.md

**Files:**
- Modify: `组件B_自修复闭环.md`（L17, L230, L820, L929, L933）

- [ ] **Step 1: L17 评委证据**
- before: `...其中 _skill_status / _convergence_cause / _best_iter / _patch_source 是评委判定"迭代是真的、智能也是真的"的契约级证据...`
- after: `...其中 _skill_status / _convergence_cause / _best_iter / _patch_source 是判定"迭代是真的、智能也是真的"的契约级证据...`

- [ ] **Step 2: L230 at_least 模式**
- before: `- HealGoal.pass_mode="all" 是 MVP 默认(...);at_least 模式预留,集训期再补解析。`
- after: `- HealGoal.pass_mode="all" 是 MVP 默认(...);at_least 模式预留,后续再补解析。`

- [ ] **Step 3: §10.1 标题（L820）**
- before: `### 10.1 指标 1:自修复通过率(完赛奖第 3 条核心;v1.2 单一硬门槛...)`
- after: `### 10.1 指标 1:自修复通过率(v1.2 单一硬门槛...)`

- [ ] **Step 4: L929 失败案例判定**
- before: `5. ... 需确认评委视角算不算"完赛"... 倾向算失败案例(...)...`
- after: `5. ... 需确认未通过算不算"失败案例"... 倾向算失败案例(...)...`

- [ ] **Step 5: L933 rule_based patch**
- before: `7. ... 倾向 MVP 不做(LLM 已能处理位宽),留集训期视通过率决定。`
- after: `7. ... 倾向 MVP 不做(LLM 已能处理位宽),留后续视通过率决定。`

- [ ] **Step 6: 验证零硬命中 + 零上下文词**
```bash
grep -nE "完赛奖|集训|赛题|赛道|Track ?01|评委|评审|加分项|完赛" 组件B_自修复闭环.md
```
Expected: 无输出

- [ ] **Step 7: commit**
```bash
git add 组件B_自修复闭环.md
git commit -m "docs(组件B): drop 评委/集训/完赛奖 parentheticals in metric and open-question sections"
```

---

## Task 10: 组件C_Planner_ToolUse.md

**Files:**
- Modify: `组件C_Planner_ToolUse.md`（L55, L97, L99, L329, L976, L978, L989, L997, L1001, L1012, L1013, L1015, L1016）

- [ ] **Step 1: L55 图注**
- before: `    │  L5 用户/评审/MCP client  │`
- after: `    │  L5 用户/集成方/MCP client  │`

- [ ] **Step 2: L97 planner 主路径理由**
- before: `- **C → LLM**:主路径。... (a) **LLM planner**(ReAct,...**v1.2 默认**,契合 Track 01 agentic 充分性);...`
- after: `- **C → LLM**:主路径。... (a) **LLM planner**(ReAct,...**v1.2 默认**,保证 planner 真正组织工具迭代);...`

- [ ] **Step 3: L99 取舍说明**
- before: `> 取舍说明(v1.2 调整契约 §11 冲突7):默认 planner_mode=llm,让 C 的 plan-execute 走 ReAct...(避免被评委会判定"会循环的 wrapper");...演示主路径必须用 llm 模式 run 作为主 demo。`
- after: `> 取舍说明(v1.2 调整契约 §11 冲突7):默认 planner_mode=llm,让 C 的 plan-execute 走 ReAct...(避免被误判为"会循环的 wrapper");...主路径演示用 llm 模式 run。`

- [ ] **Step 4: L329 降级策略**
- before: `    # 降级策略:集训评估 4 天做不完则 to_mcp_tools 仅留签名存根,不阻塞 MVP。`
- after: `    # 降级策略:评估做不完则 to_mcp_tools 仅留签名存根,不阻塞 MVP。`

- [ ] **Step 5: L976-989 验收章节**
- L976 before: `> v1.2 删除 v1.1 ... 评审无工具时,提交 runs/eval_snapshot/ ... 作为替代证据。` → after: `> v1.2 删除 v1.1 ... 无 EDA 工具环境时,用 runs/eval_snapshot/ ... 作为可复现证据。`
- L978 before: `### 10.4 文档/可集成性(完赛奖第 2 条)` → after: `### 10.4 文档/可集成性`
- L989 before: `> C 组件验收通过的充要条件:...MCP 与 OpenAICompat 为加分项,不阻塞。评审无工具时,10.3 用准备期预跑快照 runs/eval_snapshot/ 作为替代证据(契约 §12 #15)。` → after: `> C 组件验收通过的充要条件:...MCP 与 OpenAICompat 为可选扩展,不阻塞。无 EDA 工具环境时,10.3 用预跑快照 runs/eval_snapshot/ 作为可复现证据(契约 §12 #15)。`

- [ ] **Step 6: §9 风险表（L997, L1001）**
- L997 before: `| WSL2 工具 Day0.5 没装好 | e2e 测试无法跑,完赛奖第 1 条"真实可跑"悬 | ...` → after: `| WSL2 工具未装好 | e2e 测试无法跑,"真实可跑"验收悬 | ...`
- L1001 before: `| B 内部 patch 把 RTL 改坏无回退 | 失败案例无结构,评委质疑 agentic 真实性 | ...` → after: `| B 内部 patch 把 RTL 改坏无回退 | 失败案例无结构,影响 agentic 真实性证据 | ...`

- [ ] **Step 7: §12 open-questions（L1012, L1013, L1015, L1016）**
- L1012 before: `1. **planner_mode 默认值**:[v1.2 已裁决] 默认 llm(契约 §11 冲突7),契合 Track 01 agentic 充分性,避免被评委会判定"会循环的 wrapper";...演示主路径必须用 llm 模式 run 作为主 demo。本决策关闭。` → after: `1. **planner_mode 默认值**:[v1.2 已裁决] 默认 llm(契约 §11 冲突7),保证 planner 真正组织工具迭代,避免被误判"会循环的 wrapper";...主路径演示用 llm 模式 run。本决策关闭。`
- L1013 before: `2. **LLM planner 每轮取首个 tool_call 还是支持并行多 tool_calls**:...若评审更看重"agent 一次规划多步",可扩展。` → after: `2. **LLM planner 每轮取首个 tool_call 还是支持并行多 tool_calls**:...若需"agent 一次规划多步",可扩展。`
- L1015 before: `4. **report.md 的渲染深度**:...非技术成员可做,但集训 4 天工期紧。` → after: `4. **report.md 的渲染深度**:...可由非核心成员承担,但工期需评估。`
- L1016 before: `5. **MCP server 是否进 MVP**:...如果评审现场会用 Claude Code 调,MCP 是大加分;...` → after: `5. **MCP server 是否进 MVP**:...如果集成方用 Claude Code 调,MCP 价值大;...`

- [ ] **Step 8: 验证零硬命中 + 零上下文词**
```bash
grep -nE "完赛奖|集训|赛题|赛道|Track ?01|评委|评审|加分|Day[0-9]|现场演示|演示主路径|主 ?demo" 组件C_Planner_ToolUse.md
```
Expected: 无输出

- [ ] **Step 9: commit**
```bash
git add 组件C_Planner_ToolUse.md
git commit -m "docs(组件C): replace Track01/评委/集训 rationale with engineering reasons"
```

---

## Task 11: docs/ 报告文档（01-03）+ 实验快照 + summarize_eval

**Files:**
- Modify: `docs/01_测试报告.md`（L3, L72, L73, L103, L105）
- Modify: `docs/02_对抗验证报告.md`（L3, L48, L95）
- Modify: `docs/03_实验与失败案例报告.md`（L3, L34, L62, L78, L81）
- Modify: `runs/eval_snapshot/verdict.md`（L3）
- Modify: `runs/eval_snapshot/README.md`（评审替代证据措辞）
- Modify: `scripts/summarize_eval.py:29`

- [ ] **Step 1: docs/01_测试报告.md**
- L3 before: `> 测试套件全景 + 覆盖映射 + 演进轨迹。集训评审"测了什么"一站式入口。` → after: `> 测试套件全景 + 覆盖映射 + 演进轨迹。"测了什么"一站式入口。`
- L72 before: `...评审机无 WSL2/工具时自动 skip...` → after: `...无 WSL2/工具环境时自动 skip...`
- L73 before: `...集训 Day4 现场演示跑)` → after: `...现场演示跑)`（去"集训 Day4"）
- L103 before: `## 8. stub vs needs_eda 策略(评审无 EDA 兜底)` → after: `## 8. stub vs needs_eda 策略(无 EDA 工具兜底)`
- L105 before: `评审机若未装 yosys/iverilog/opensta:` → after: `若环境未装 yosys/iverilog/opensta:`

- [ ] **Step 2: docs/02_对抗验证报告.md**
- L3 before: `> 全量对抗验证方法论 + 6 轮汇总。集训评审"验证了什么/发现什么/修了什么"一站式入口。` → after: `> 全量对抗验证方法论 + 6 轮汇总。"验证了什么/发现什么/修了什么"一站式入口。`
- L48 before: `## 4. ultracode 集训前复审(commit 1b8a450)` → after: `## 4. ultracode 复审(commit 1b8a450)`
- L95 before: `...是集训前零遗留的关键。` → after: `...是交付前零遗留的关键。`

- [ ] **Step 3: docs/03_实验与失败案例报告.md**
- L3 before: `> T54 全量实验 + rule vs LLM 对比 + A 诊断器评测 + 失败案例归档。集训评审"实验结果 + 失败不误读"一站式入口。` → after: `> 全量实验 + rule vs LLM 对比 + A 诊断器评测 + 失败案例归档。"实验结果 + 失败不误读"一站式入口。`
- L34 before: `| 适用 | 实验/快速验证/全量跑 | 演示 agentic 充分性(完赛奖第 4 条) |` → after: `| 适用 | 实验/快速验证/全量跑 | 演示 agentic 充分性 |`
- L62 before: `## 4. 失败案例归档(评审看到失败不误读为系统崩)` → after: `## 4. 失败案例归档(读者看到失败不误读为系统崩)`
- L78 before: `### 4.3 2 skipped(评审无 EDA 环境)` → after: `### 4.3 2 skipped(无 EDA 工具环境)`
- L81 before: `- 评审机无 WSL2/yosys/iverilog 时自动 skip(...)...` → after: `- 无 WSL2/yosys/iverilog 环境时自动 skip(...)...`

- [ ] **Step 4: runs/eval_snapshot/verdict.md**
- L3 before: `## S1 硬门槛(比赛完赛奖第 1 条)— ✅ 全过` → after: `## S1 硬门槛 — ✅ 全过`

- [ ] **Step 5: runs/eval_snapshot/README.md**
- before: `...作 **评审替代证据**(契约 §12 #15:评审机无 EDA 工具时 needs_eda 测试 skip,以此快照证明实验真实跑通)。`
- after: `...作 **可复现证据**(契约 §12 #15:无 EDA 工具环境时 needs_eda 测试 skip,以此快照证明实验真实跑通)。`

- [ ] **Step 6: scripts/summarize_eval.py:29**
- before: `# 比赛四类故障;其余归 "other"。`
- after: `# 四类已知故障;其余归 "other"。`

- [ ] **Step 7: 验证 6 文件零硬命中**
```bash
grep -nE "完赛奖|Hackathon|Agentic4Systems|参赛|比赛|集训|赛题|赛道|Track ?01|评委|评审" docs/01_测试报告.md docs/02_对抗验证报告.md docs/03_实验与失败案例报告.md runs/eval_snapshot/verdict.md runs/eval_snapshot/README.md scripts/summarize_eval.py
```
Expected: 无输出

- [ ] **Step 8: commit**
```bash
git add docs/01_测试报告.md docs/02_对抗验证报告.md docs/03_实验与失败案例报告.md runs/eval_snapshot/verdict.md runs/eval_snapshot/README.md scripts/summarize_eval.py
git commit -m "docs/reports+snapshot: de-competition-ize test/verify/experiment reports

- docs/01-03: 集训评审→通用, 完赛奖第4条→agentic充分性
- runs/eval_snapshot: 比赛完赛奖第1条→S1硬门槛, 评审替代证据→可复现证据
- summarize_eval.py: 比赛四类故障→四类已知故障"
```

---

## Task 12: docs/wiki/ 全量清理

**Files:**
- Modify: `docs/wiki/01_项目概览与价值定位.md`（L13, L15, L17, L19, L24, L125, L170）
- Modify: `docs/wiki/04_契约驱动CONTRACTS权威机制.md`（L1, L150-151, L221-222）
- Modify: `docs/wiki/07_版本锚点与工件引用协议.md`（L184）
- Modify: `docs/wiki/11_规则引擎降级与baseline实验.md`（L5, L217）
- Modify: `docs/wiki/22_OpenSTA时序分析封装.md`（L34）
- Modify: `docs/wiki/27_故障注入清单与基准实验.md`（L24, L185, L213）
- Modify: `docs/wiki/28_实验聚合通过率门槛S1.md`（L1）
- 以及全量 grep 后发现的任何其他 wiki 文件

> wiki 文件名/数量/互链结构**不动**（G2 门禁要求 32 文件、31 编号页），只改正文文字。

- [ ] **Step 1: 全量扫描 wiki 命中**
```bash
grep -rlE "完赛奖|Hackathon|Agentic4Systems|参赛|比赛|集训|赛题|赛道|Track ?01|评委|评审|加分项|硬指标|Day[0-9]|现场演示|演示主路径|主 ?demo|良好线" docs/wiki/
```
记录全部命中文件。

- [ ] **Step 2: 逐文件改写**（按命中清单，每文件的比赛框架→工程表述）
- `01_项目概览与价值定位.md`：§项目背景与比赛定位 → §项目背景与定位；L15 整段（Agentic4Systems 参赛/赛道/比赛发起方）→ 通用项目背景；L17 完赛奖/3000元/评审/硬指标 → 项目设计目标；L19/24 表头完赛奖硬指标→工程目标；L125 加分项→可选扩展；L170 GLM Coding Plan/完赛奖S1→GLM/S1硬门槛。
- `04_契约驱动CONTRACTS权威机制.md`：L1 "2人9天Hackathon项目"→"本项目"；L150-151 drc/pnr 加分项→可选扩展；L221-222 三赛道要求/评委加分→工程理由。
- `07_版本锚点与工件引用协议.md`：L184 完赛奖"实验记录"→"实验记录"可追溯。
- `11_规则引擎降级与baseline实验.md`：L5 Track 01→保证planner组织工具迭代；L217 完赛奖第4条→agentic充分性。
- `22_OpenSTA时序分析封装.md`：L34 三赛道/完赛奖硬指标→时序分析是核心能力之一。
- `27_故障注入清单与基准实验.md`：L24/213 比赛要求→设计要求；L185 比赛定义的S1→S1。
- `28_实验聚合通过率门槛S1.md`：L1 完赛奖第3条硬指标→实验证据量化骨架。

- [ ] **Step 3: 验证 wiki 全量零硬命中 + 零上下文词**
```bash
grep -rnE "完赛奖|Hackathon|Agentic4Systems|参赛|比赛|集训|赛题|赛道|Track ?01|评委|评审|加分项|硬指标|Day[0-9]|现场演示|演示主路径|主 ?demo" docs/wiki/
```
Expected: 无输出

- [ ] **Step 4: 验证 wiki 文件结构未变（G2 兼容）**
```bash
ls docs/wiki/*.md | wc -l
```
Expected: `32`

- [ ] **Step 5: commit**
```bash
git add docs/wiki/
git commit -m "docs(wiki): de-competition-ize 31 wiki pages, keep structure/links intact

- 01 项目概览/04 契约驱动/07 版本锚点/11 规则引擎/22 OpenSTA/27 故障注入/28 实验聚合
  等 wiki 页去比赛框架,保留技术内核
- 文件名/数量(32)/互链结构不动(满足 G2 门禁)"
```

---

## Task 13: 交叉引用核查 + 全仓验收

**Files:** 无新增改动，纯核查（若有悬空引用则补改）

- [ ] **Step 1: 核查已删文件无悬空引用**
```bash
grep -rnE "实现执行计划|_审计报告_v1|演示脚本|_rebuild_wiki" --include="*.md" --include="*.py" --include="*.toml" .
```
Expected: 无输出（若有命中，补改引用文件）

- [ ] **Step 2: 全仓硬命中零验证**
```bash
grep -rnE "完赛奖|Hackathon|Agentic4Systems|参赛|比赛|集训|3000 ?元|提交截止|赛题|赛道" --include="*.md" --include="*.toml" --include="*.py" --include="*.html" . | grep -v "docs/superpowers/"
```
Expected: 无输出

- [ ] **Step 3: 全仓上下文词清单（逐处确认已处理或合理保留）**
```bash
grep -rnE "评委|评审|Track ?01|加分项|准备期|Day[0-9]|现场演示|演示主路径|主 ?demo|硬指标|良好线|S1 达标" --include="*.md" --include="*.toml" --include="*.py" . | grep -v "docs/superpowers/"
```
- 逐行检查：残留的必须是纯技术含义（如 S1 作为门槛代号）。若有比赛指向，补改。

- [ ] **Step 4: check_integrity.py 运行**
```bash
python scripts/gates/check_integrity.py
```
Expected: JSON 输出 `"overall_ok": true`，exit 0

- [ ] **Step 5: pip install -e .**
```bash
pip install -e . 2>&1 | tail -3
```
Expected: exit 0，成功安装

- [ ] **Step 6: pytest 不退步**
```bash
pytest -q 2>&1 | tail -15
```
Expected: 无新增失败（记录通过数与 baseline 对比）

- [ ] **Step 7: 若 Step 1-3 有补改，commit**
```bash
git add -A
git commit -m "fix: clean up dangling references found in final audit"
```
（若无需补改，跳过此步）

---

## 完成判据（verification-before-completion）

全部 Task 完成后，本计划视为完成当且仅当：
1. Task 13 Step 2 输出为空（硬命中零，有命令输出证据）
2. Task 13 Step 3 残留项全部为合理技术含义（逐处说明）
3. Task 13 Step 4 `overall_ok=true`（有命令输出证据）
4. Task 13 Step 5 pip install exit 0（有命令输出证据）
5. Task 13 Step 6 pytest 无新增失败（有命令输出证据）
6. spec §4 全部 7 条验收标准满足

完成后进入 finishing-a-development-branch：push 分支 + 开 PR（PR 描述附上述全部证据）。
