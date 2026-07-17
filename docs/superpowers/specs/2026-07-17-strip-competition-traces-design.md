# 清理比赛痕迹 — 设计文档（Spec）

> **GSD 流程产物**：本文档是 brainstorming 阶段输出，经 spec self-review 与用户 review 后，交由 writing-plans 生成 bite-sized 任务计划。
> **日期**：2026-07-17
> **目标仓库**：`BH2-4/eda-agent-system`
> **分支**：`chore/strip-competition-traces`（已基于 main 建立隔离 worktree）

---

## 1. 背景与目标

仓库 `BH2-4/eda-agent-system` 当前是 "Agentic4Systems 暑期学校 Hackathon" 的参赛系统，文档、元数据、门禁脚本中大量绑定比赛框架（完赛奖、Track 01、集训、评审、提交截止 2026-07-15、3000 元等）。比赛已结束，现要将其作为**普通开源项目**对外发布留存。

**核心目标**：仓库可作为通用 Agentic EDA 自愈流水线项目对外发布，所有文档回归"项目本质"，去除一切比赛/黑客松框架痕迹，同时**保留全部有技术价值的内容**（架构、契约、实验证据、测试报告）。

## 2. 范围

- **改**：文档（`*.md`）、包元数据（`*.toml`）、门禁/工具脚本（`scripts/**/*.py`）、实验快照叙事文件。
- **不动**：`src/` 全部业务代码、LLM prompts、`tests/`（已确认零比赛痕迹）。
- **删**：纯比赛产物文件 + 死代码脚本。
- **新增**：本 spec + 任务计划（gsd 流程产物，提交到分支）。

## 3. 关键词分层（保证全面性，避免漏改与误伤）

### 3.1 硬命中（验收必须全仓零）
```
完赛奖 | Hackathon | Agentic4Systems | 参赛 | 比赛 | 集训 |
3000 ?元 | 提交截止 | 赛题 | 赛道
```
文件类型覆盖：`*.md *.toml *.py *.html`（`docs/wiki/*.md` 单独扫）。

### 3.2 上下文判读词（逐处定夺改/留）
```
评委 | 评审 | Track ?01 | 加分项 | 准备期 | Day[0-9] |
现场演示 | 演示主路径 | 主 ?demo | 硬指标 | 良好线 | S1 达标
```
- **改写**：把"为满足评委/完赛奖/Track 01"的**理由引用**改为工程理由（"保证 planner 真正 agent 组织工具迭代"等）。
- **保留**：纯技术含义的（如 `S1` 作为门槛代号、`硬门槛` 作为通用术语）可酌情保留，但需逐处判断不残留比赛指向。

### 3.3 不动词（真实技术实体）
`GLM` / `智谱` / `GLM-5.2` / `GLM_API_KEY` —— GLM 是项目实际使用的 LLM provider，保留。仅去除 "Coding Plan 订阅" 这类把 GLM 包装成比赛提供资源的措辞。

## 4. 验收标准

1. **硬命中零**：`grep -rnE "完赛奖|Hackathon|Agentic4Systems|参赛|比赛|集训|3000 ?元|提交截止|赛题|赛道" --include="*.md" --include="*.toml" --include="*.py" --include="*.html" .` 返回 0 行（证据：命令输出）。
2. **上下文词处理**：§3.2 词逐处列出处理结果（改写 or 合理保留 + 理由）。
3. **元数据干净**：`pyproject.toml` 的 `description` 不含比赛名。
4. **无悬空引用**：已删文件（`实现执行计划.md` / `_审计报告_v1.md` / `docs/演示脚本.md` / `_rebuild_wiki.py`）在仓库中无任何交叉引用残留。
5. **门禁可过**：重写后的 `check_integrity.py` 在清理后的仓库上 `python scripts/gates/check_integrity.py` 返回 `overall_ok=true`（exit 0）。
6. **包可装+测试不退步**：`pip install -e .` exit 0；`pytest -q` 不新增失败（证据：命令输出）。
7. **gsd 文档就位**：本 spec + 任务计划已产出并经用户 review。

## 5. 改动清单（按文件）

### 5.1 删除（4 个文件）
| 文件 | 理由 |
|---|---|
| `实现执行计划.md` | 整文件是黑客松日程（§D 集训 4 天、Day4 提交、T01–T58 绑定集训/提交） |
| `_审计报告_v1.md` | 内部审计报告，"集训期清"产物 |
| `docs/演示脚本.md` | 标题"集训现场演示脚本(Agentic4Systems Hackathon)"，纯比赛演示用途 |
| `scripts/gates/_rebuild_wiki.py` | 死代码：硬编码 `ROOT="D:/eda agent system"`，依赖 `.zread/wiki_sources/`（已 gitignore，仓库不存在），任何人无法运行 |

### 5.2 重写（1 个脚本）
| 文件 | 操作 |
|---|---|
| `scripts/gates/check_integrity.py` | 重写为通用项目门禁：① G3 `G3_KEYWORDS` 去比赛词（删 `Agentic4Systems`/`完赛奖`/`2026-07-15`，保留 `CONTRACTS.md`/`baseline`/`eval_snapshot`/`CONTRACT_VERSION`/`artifact_ref`/`as_tool`/`eda self-heal`/`Phase`）；② G1 修改白名单放宽（允许改任意 `*.md`/`*.toml`/`docs/**`，不再锁死 README）；③ 删除/重命名规则视项目定位调整（普通项目允许删文件） |

### 5.3 元数据（2 个文件）
| 文件 | 操作 |
|---|---|
| `pyproject.toml` L8 | `description` 去 "Agentic4Systems Hackathon" |
| `settings.toml` L2/L5 | 注释去 "Track 01 agentic 充分性"/"加分项" |

### 5.4 文档改写（保留技术内核，去比赛框架）
| 文件 | 操作 |
|---|---|
| `README.md` | 标题去"参赛项目"；banner（Track01/完赛奖3000元/截止）改项目简介；§1 表去"完赛奖硬指标"表头；删 §6 比赛/完赛奖映射；文件导航去"比赛"+删已删文件行；§11 删截止；GLM 指引去"Coding Plan 订阅" |
| `ARCHITECTURE.md` | §1.1 比赛与目标→背景与目标；§1.2 完赛奖四条硬指标映射→删（并入 README）；散落集训/评审/准备期/加分项 6 处→中性 |
| `CONTRACTS.md` | 删 §10 完赛奖对齐矩阵；§9 checklist 去硬指标编号；~30 处理由从"契合 Track01/避免评委判定"→工程理由；集训期→后续迭代；完赛奖第N条→实验可复现要求；评委→可审计证据 |
| `验收标准.md` | 改写为 QA checklist：§1 去完赛奖；§6 评审→集成环境；§7.1 团队Day9→贡献者自检；§7.2 Claude验收→自动化验收 |
| `组件A_诊断器.md` | 3 处括注（L221 §4.4 标题、L756/L827 评委/集训期）→中性 |
| `组件B_自修复闭环.md` | 5 处括注（L17/L230/L820/L929/L933 评委/集训/完赛奖第3条）→中性 |
| `组件C_Planner_ToolUse.md` | §10.4 标题、§9 风险表、§12 open-questions + 散落 Track01/评委/集训→工程理由 |

### 5.5 报告文档改写（docs/ 下）
| 文件 | 操作 |
|---|---|
| `docs/01_测试报告.md` | 去"集训评审"框架（L3/L72/L73/L103/L105）→通用测试报告表述 |
| `docs/02_对抗验证报告.md` | 去"集训评审/集训前"（L3/L48/L95）→通用验证报告 |
| `docs/03_实验与失败案例报告.md` | 去"集训评审/完赛奖第4条/评审"（L3/L34/L62/L78/L81）→通用实验报告 |

### 5.6 实验快照改文字（runs/eval_snapshot/）
| 文件 | 操作 |
|---|---|
| `runs/eval_snapshot/verdict.md` | "比赛完赛奖第 1 条"→"S1 硬门槛"；其余技术数据保留 |
| `runs/eval_snapshot/README.md` | "评审替代证据"→"实验快照（可复现证据）"；技术内容保留 |

### 5.7 工具脚本改注释
| 文件 | 操作 |
|---|---|
| `scripts/summarize_eval.py` L29 | "比赛四类故障"→"四类故障" |

### 5.8 docs/wiki/（31 个文件）
- wiki 生成器 `_rebuild_wiki.py` 已删且无法重跑（死代码），wiki 必须**手动改**。
- grep 扫描命中的 wiki 文件（至少 `01_项目概览与价值定位.md`/`04_契约驱动CONTRACTS权威机制.md`/`07_版本锚点与工件引用协议.md`/`11_规则引擎降级与baseline实验.md`/`22_OpenSTA时序分析封装.md`/`27_故障注入清单与基准实验.md`/`28_实验聚合通过率门槛S1.md`），逐处改写去比赛框架。
- 全量 grep 后可能发现更多命中文件，一并处理。

## 6. TDD 等价（已与用户确认：文档改动不写新测试）

本次绝大多数为文档/注释/元数据修改，无新增代码逻辑（唯一动的 .py 是 `check_integrity.py`/`summarize_eval.py`，前者属门禁脚本、后者只改注释）。

TDD 等价验证：
- 每个文档任务完成后：`grep` 验证该文件硬命中为 0。
- 全部完成后：`python scripts/gates/check_integrity.py`（重写后）overall_ok=true；`pip install -e .` exit 0；`pytest -q` 不退步。

不编造无意义的文档单测。

## 7. 回滚

所有改动在 `chore/strip-competition-traces` 分支，未 merge 前 main 不动。不满意可直接 `git checkout main` + 删分支。worktree 独立，不影响任何现有工作区。

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| `check_integrity.py` 重写后 G2 仍要求 wiki 32 文件、31 编号页——改 wiki 内容不能动文件名/数量 | 仅改正文文字，不改 wiki 文件名/数量/互链结构 |
| wiki 31 文件逐个手改工作量大、易漏 | 任务计划中 wiki 作为独立任务，用 grep 先定位全部命中再逐处改 |
| 删除文件后其他文档交叉引用断裂 | 任务计划含"交叉引用核查"独立任务，grep 全仓对已删文件名的引用 |
| 改动后 `pytest` 可能因 import 路径等受影响 | 验证阶段现跑 pytest 贴证据；改动不碰 src/ 理论上无影响 |
| `_rebuild_wiki.py` 删除后未来无法重建 wiki | 该脚本本就是死代码（硬编码 D 盘路径+依赖不存在的 .zread/），删除不损失可用功能；wiki 已是成品 |

## 9. gsd 流程映射

| GSD 阶段 | 本任务动作 | 产物 |
|---|---|---|
| brainstorming | 探索（2 Explore agent）+ 澄清（多轮 AskUserQuestion）+ 本 spec | 本文档 → 用户 review gate |
| writing-plans | 拆 12+ bite-sized 任务 | `docs/superpowers/plans/2026-07-17-strip-competition-traces.md` |
| using-git-worktrees | 已克隆+建分支 `chore/strip-competition-traces` | 隔离 worktree |
| subagent-driven-development | 逐任务派 implementer + 两阶段 review（spec 合规 + 质量） | 每 task 独立 commit |
| verification-before-completion | 现跑全套验证贴证据 | §4 全部证据 |
| finishing-a-development-branch | 测试通过→push+开 PR→清理 worktree | PR |
