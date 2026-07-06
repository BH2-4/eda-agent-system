# 组件B_自修复闭环.md — Agentic RTL 自修复闭环(B)

> 本文档严格遵守 `D:/eda agent system/CONTRACTS.md`(下称"契约")v1.2。所有 dataclass 字段、Tool/Skill 接口、artifact 路径、错误码均从 `eda_agent.contracts` import,不重新定义。任何与契约冲突之处以契约为准。契约 §13 已为本组件登记对齐状态:`组件B_自修复闭环.md —— v1.2 已对齐`。
>
> 组件代号 = **B**,Registry name = `skill_self_heal`,skill 文件 = `src/eda_agent/skills/self_heal.py`(对应契约 §1 对照表)。本文代码段 4 空格缩进,**禁用反引号代码块**。所有 `contract_version` 引用必须 `from eda_agent.contracts import CONTRACT_VERSION`,禁止裸字符串。

---

## 1. 定位与职责边界

### 1.1 做什么(必做)

B 是一个**复合 skill**(L2,契约 §1),对外注册为 Tool(走契约 §2.1 `Tool` Protocol + §2.3 `as_tool()`),对内继承 `Skill` 基类。它把"RTL 设计出错了,帮我把设计修到通过仿真(可选通过时序)"这件事收敛成一个可被 C(CPlanner)或 CLI 单次调用的闭环:

- 输入:一份 RTL 源(`rtl_path`)+ 一份 TB(`tb_path`)+ 一个目标(`goal`,如 "pass all tests")+ 可选的 A 诊断器结果(`diagnose`)+ 迭代上限(`max_iter`)。
- 内部循环:`综合 → 失败则诊断→patch` / `仿真 → 失败则诊断→patch` / `(可选)时序 → 违例则诊断→patch`,直到全通过(`all_pass`)或触发停机条件。
- 输出:`SkillResult`,经 `as_tool()` 映射为 `ToolResult`(契约 §2.3),其中 `_skill_status / _convergence_cause / _best_iter / _patch_source` 是评委判定"迭代是真的、智能也是真的"的契约级证据(契约 §2.3 设计要点 + §10 agentic 自检)。

### 1.2 不做什么(明确边界,与 A / C / 工具的切分)

| 不做的事 | 谁来做 | 理由 |
| --- | --- | --- |
| 不重写 `yosys_synth` / `iverilog_sim` / `opensta_timing` 的子进程封装 | L1 Tool 封装层(契约 §2.2) | B 只**编排**已有 Tool,不重复造子进程逻辑(开闭原则)。 |
| 不自己产 ErrorItem 归因 | A 诊断器(`skill_diagnose`) | B 内部失败时调用 `skill_diagnose` 获取 `root_causes`/`fix_suggestion`;B 只把诊断喂给 LLM 出 patch,不重写归因逻辑。 |
| 不做任务分解 / 工具发现 / LLM tool-use 回环 | C(CPlanner,L4) | B 是被 C 调用的 Tool,**不**反向编排 C;B 内部的 LLM 调用只用于"出 patch"这一窄任务,不进入 ReAct 工具选择。 |
| 不生成独立 run_id | runner.py(契约 §2.4) | B 的每一轮子 Tool 调用以 `StepRecord` 追加到父 RunRecord,标 `skill_name="self_heal"` + `iter=N`,不嵌套 RunRecord。 |
| 不修不可修的 bug | (人工) | B 的 demo 验收基线是"修预 inject 的已知可修 bug"(契约 §7 + §11 裁决);对结构性设计错误(架构级),B 降级为"只报诊断不自动改"(见 §6 防退化降级)。 |
| 不做多模态 / 版图 / PnR | 加分项(不在 MVP) | 契约 §7 MVP 边界明确。 |

### 1.3 一句话定位

> B = "把 RTL 设计 + TB 喂进来,内部迭代 综合→仿真→(时序)→诊断→LLM patch→重试,带版本回退与防退化,直到通过或耗尽预算;过程全落盘" 的复合 skill,注册名为 `skill_self_heal`。

---

## 2. 在整体系统中的位置(调用关系图)

B 处于契约 §1 的 L2(Skills),与 A 并列;二者都通过 `as_tool()` 注册进 L3 Registry,供 L4 C 调用;B 内部反向依赖 L1 的三个 EDA Tool + L2 的 A 诊断器 + L1 的 LLM provider。

调用关系图(ASCII,与契约 §1 五层对齐):

    ┌──────────────────────────────────────────────────────────────────┐
    │ L5  CLI: eda self-heal --rtl .. --tb .. --goal "pass all tests"  │
    │      → RunRequest(kind="self_heal")                              │
    └───────────────────────────────┬──────────────────────────────────┘
                                    │
    ┌───────────────────────────────▼──────────────────────────────────┐
    │ L4  C (CPlanner) plan-execute 循环                              │
    │      若直接 self_heal 路径:C 构造 ToolCall("skill_self_heal",  │
    │      {"rtl":..,"tb":..,"diagnose":<可选>,"max_iter":5})        │
    │      经 registry.get 校验后调用(契约 §2.5 幻觉校验)          │
    └───────────────────────────────┬──────────────────────────────────┘
                                    │ registry.get("skill_self_heal")(call)
    ┌───────────────────────────────▼──────────────────────────────────┐
    │ L3  ToolRegistry  ── name="skill_self_heal", category="skill"   │
    └───────────────────────────────┬──────────────────────────────────┘
                                    │ as_tool() 解包 → SelfHealSkill.run(...)
    ┌───────────────────────────────▼──────────────────────────────────┐
    │ L2  B (SelfHealSkill)  ┌─ 内部迭代(不生成独立 run_id) ─────┐  │
    │                        │  每轮:                              │  │
    │                        │   registry.get("yosys_synth")   ──┐  │  │
    │                        │   registry.get("iverilog_sim")  ─┐│  │  │
    │                        │   registry.get("opensta_timing") ││  │  │  ← L1 EDA 工具
    │                        │   registry.get("skill_diagnose")─┘│  │  │  ← L2 A 诊断器
    │                        │   LLM provider.chat(patch 生成) ──┘  │  │  ← L1 LLM
    │                        │   每步 → StepRecord 进父 RunRecord   │  │
    │                        └──────────────────────────────────────┘  │
    └───────────────────────────────┬──────────────────────────────────┘
                                    │ SkillResult → as_tool() → ToolResult
    ┌───────────────────────────────▼──────────────────────────────────┐
    │ L0  runs/<run_id>/skills/self_heal/iter_<n>/rtl_snapshot.v       │
    │      runs/<run_id>/skills/self_heal/iter_<n>/rtl_patch.diff      │
    │      runs/<run_id>/skills/self_heal/best/rtl.v                   │
    │      runs/<run_id>/steps/<idx>_skill_self_heal/...               │
    └──────────────────────────────────────────────────────────────────┘

依赖方向(严格自上而下,不反向):

- B → (向下) 调 yosys_synth / iverilog_sim / opensta_timing / skill_diagnose(经 Registry)+ LLM provider(经构造注入)。
- B → (向上) 被 C 或 CLI 调用,B 不知道调用方是谁(只接 ToolCall)。
- B ↔ A:B 调 A(A 不知道 B 存在);A 的 `root_causes[].fix_suggestion` 是 B 出 patch 的关键输入。
- B ↔ C:C 把 `remaining_budget_s` 下传给 B(双层预算仲裁,契约 §2.3);B 不调用 C。

调用频次预期(MVP):一次 `eda self-heal` 调用 = C 发起 1 次 `skill_self_heal`,B 内部最多 `max_iter=5` 轮,每轮 ≤3 个 EDA Tool + ≤1 次 `skill_diagnose` + ≤1 次 LLM patch 调用。

---

## 3. 内部架构(子模块拆分 + 数据流图)

### 3.1 子模块拆分(均在 `src/eda_agent/skills/self_heal.py` 一个文件内,按 class 分块;不拆多文件以降低 2 人协作冲突)

    SelfHealSkill(Skill)                    ← 主类,实现 Skill(契约 §2.3);普通类,非 @dataclass
        ├── run(run_id, inputs, remaining_budget_s)  ← 契约入口,编排下面 4 个私有方法
        ├── _stage_synth(rtl_snap, run_id, iter_n) -> ToolResult
        ├── _stage_sim(rtl_snap, tb, run_id, iter_n) -> ToolResult
        ├── _stage_sta(netlist, lib, clock, run_id, iter_n) -> ToolResult | None
        ├── _diagnose_and_patch(stage_results, rtl_snap, run_id, iter_n, seed, strategy, snap_dir) -> PatchOutcome
        ├── _apply_patch(rtl_text, patch) -> (rtl_text_new, ok)
        ├── _syntax_check(rtl_text) -> bool          ← iverilog -t null 预检
        ├── _version_stack: list[Path]               ← 版本栈(回退基准)
        └── as_tool() -> Tool                         ← 契约 §2.3 适配 + reserved 拆包
    # 持有 self._llm / self._runner / self._registry(命名统一:self._llm 下划线表内部持有)

    PatchOutcome(dataclass)                ← 单轮 patch 的结构化结果
    HealGoal(dataclass)                    ← 把 goal 字符串解析成可机器判定的通过条件
    SelfHealPrompts                        ← prompt 模板常量(diff 优先 / full_rewrite 降级 / 降级不自动改)

设计要点:

- `_stage_*` 三个方法**只负责构造 ToolCall + 调 Registry + 落 StepRecord**,不做业务判断(综合失败还是仿真失败的判定在 `run` 里)。
- `_diagnose_and_patch` 是 B 的"智能核心":调 `skill_diagnose` 拿 `DiagnosisReport` → 取出错 RTL 片段(按 `ErrorItem.evidence` 的行号定位)→ LLM 出 patch(diff 优先)→ `_apply_patch` + `_syntax_check`。
- `_version_stack` 每轮 push 一份 `rtl_snapshot.v` 绝对路径;回退 = pop 到上一版。
- `_syntax_check` 用 `iverilog -t null -o /dev/null <rtl>`(不接 TB,只检语法),失败则 patch 作废、回退、换策略。

### 3.2 数据流图(单轮迭代,B 内部)

    inputs: {rtl, tb, diagnose?, max_iter}
        │
        ▼
    ┌─────────────────────────────────────────────────────────────┐
    │ run() 初始化:                                               │
    │   解析 goal → HealGoal(num_pass_required)                   │
    │   best_iter=-1, best_score=-inf, _version_stack=[]          │
    │   current_rtl_text = read(rtl)                              │
    │   push iter_0/rtl_snapshot.v                                │
    └───────────────────────────────┬─────────────────────────────┘
                                    │ for iter_n in range(max_iter):
    ┌───────────────────────────────▼─────────────────────────────┐
    │ _stage_synth(current_rtl_text)                              │
    │   → synth_res: ToolResult(parsed.success, num_cells, ...)   │
    │   → StepRecord[skill=self_heal, iter=iter_n]                │
    └───────────────┬─────────────────────────────────┬───────────┘
       success=True │                                 │ success=False
                   │                                 ▼
                   │              ┌──────────────────────────────────┐
                   │              │ _diagnose_and_patch([synth_res]) │
                   │              │   diagnose = skill_diagnose(...) │
                   │              │   patch = LLM(diff 优先)         │
                   │              │   _syntax_check → ok?            │
                   │              │   _apply_patch → current_rtl_text│
                   │              │   push iter_(n+1)/rtl_snapshot.v │
                   │              └──────────────┬───────────────────┘
                   │                             │ continue(下一轮重跑综合)
    ┌──────────────▼─────────────────────────────▼────────────────┐
    │ _stage_sim(current_rtl_text, tb)                            │
    │   → sim_res: ToolResult(parsed.num_passed/num_failed)       │
    └───────────────┬─────────────────────────────────┬───────────┘
        num_passed >= required │                                 │ < required
                          │                                     ▼
                          │              ┌──────────────────────────────────┐
                          │              │ _diagnose_and_patch([synth_res,  │
                          │              │   sim_res]) → patch + 回退判定   │
                          │              │   防退化:新 num_passed < 旧?    │
                          │              │     是→回退(pop 栈)+ 换策略    │
                          │              │     否→应用 + push 快照         │
                          │              └──────────────┬───────────────────┘
                          │                             │ continue
    ┌─────────────────────▼─────────────────────────────▼─────────┐
    │ (lib & clock 提供?) _stage_sta(netlist, lib, clock)         │
    │   → sta_res: ToolResult(parsed.wns)                         │
    │   wns>=0? ─否→ 诊断→patch→continue; ─是↓                    │
    └───────────────────────────────┬─────────────────────────────┘
                                    │ 全通过
    ┌───────────────────────────────▼─────────────────────────────┐
    │ best_iter=iter_n; convergence_cause="all_pass"; break       │
    │ 拷 current_rtl_text → skills/self_heal/best/rtl.v           │
    └─────────────────────────────────────────────────────────────┘
                                    │ 循环出口(max_iter / budget / regression 死锁)
                                    ▼
                              SkillResult(见 §4)

---

## 4. 核心数据结构(Python dataclass 伪代码)

本节 dataclass **只新增 B 自己用的工作类型**;`SkillResult` / `ToolResult` / `ToolCall` / `ErrorItem` / `artifact_ref` 全部从 `eda_agent.contracts` import,不在此重定义(契约 §13 一致性约束)。

    from __future__ import annotations
    from dataclasses import dataclass, field
    from pathlib import Path
    from typing import Any, Literal
    from eda_agent.contracts import (
        CONTRACT_VERSION, Skill, SkillResult, Tool, ToolCall, ToolResult,
        ErrorItem, LLMProvider, ToolRegistry, artifact_ref,
    )

    # —— B 自有的工作类型(不入契约,仅本文件内用) ——

    @dataclass
    class HealGoal:
        # 把 RunRequest.goal 这句自然语言解析成机器可判定的通过条件。
        # MVP 只支持两种 goal 模板(见 §5.2 错误码 eda.heal.unsupported_goal):
        #   "pass all tests"   → pass_mode="all",  num_required = total_cases
        #   "pass N tests"     → pass_mode="at_least", num_required = N
        pass_mode: Literal["all", "at_least"]
        num_required: int | None              # all 模式下运行时由 TB 协议行总数填入
        sta_required: bool = False            # goal 含 "timing" / "no violation" 时置 True

    @dataclass
    class PatchOutcome:
        # _diagnose_and_patch 的返回:一轮 patch 的完整证据。
        iter_n: int                           # 第几轮(从 0 起)
        diagnose_parsed: dict[str, Any]       # skill_diagnose 的 parsed(含 root_causes)
        patch_source: Literal["llm_diff", "llm_full_rewrite", "rule_based", "none"]
        patch_diff: str                       # 应用的补丁文本(diff 或整文件,落盘用)
        target_lines: tuple[int, int] | None  # 出错 RTL 片段的起止行号(None=全文)
        applied: bool                         # 是否成功应用(语法校验通过)
        rolled_back: bool                     # 是否因退化回退到上一版
        regression: bool                      # 是否引入了新错误(防退化触发)
        llm_tokens_in: int                    # 本次 LLM 调用 tokens(经 CountingProvider)
        llm_tokens_out: int
        rationale: str                        # LLM 一句话解释为什么这么改(入 trajectory)

    @dataclass
    class IterSnapshot:
        # 每轮的完整轨迹单元,序列化后塞进 SkillResult.trajectory(契约 §2.3)。
        iter_n: int
        rtl_snapshot_ref: dict[str, str]      # artifact_ref({"run_id":..,"rel_path":"skills/self_heal/iter_<n>/rtl_snapshot.v"})
        patch_ref: dict[str, str] | None      # artifact_ref 指向 rtl_patch.diff;iter_0 为 None
        synth_parsed: dict[str, Any]
        sim_parsed: dict[str, Any] | None
        sta_parsed: dict[str, Any] | None
        diagnose_parsed: dict[str, Any] | None
        patch_outcome: PatchOutcome | None
        num_passed: int                       # 该轮仿真通过数(用于 best_iter 判定)
        is_best: bool                         # 是否为历史最佳(B 退出时回填)

字段级注释要点:

- `HealGoal.pass_mode="all"` 是 MVP 默认(契约 §5 e2e 示例的 goal 就是 "pass all tests");`at_least` 模式预留,集训期再补解析。
- `PatchOutcome.patch_source` 与 `SkillResult.patch_source` 取值集**完全一致**(契约 §2.3),B 在每轮记录后,`SkillResult.patch_source` 取**最后一次尝试**的来源(即使 applied=False;仅当"全程一次 LLM 都没调"即 convergence=budget 且 iterations=0 时才为 "none")。失败 run 的 patch_source 也应记录"最后一次尝试的 source"(契约 §10 agentic 自检 v1.2)。
- `IterSnapshot.num_passed` 是 `best_iter` 判定的唯一数值依据(防退化核心):
  - 初始:`best_iter = -1`,`best_score = -1`(v1.2:best_iter 初始 -1 而非 0,语义"无任何轮通过仿真")。
  - 更新条件:`if num_passed > best_score and num_passed > 0: best_score=num_passed; best_iter=iter_n`(避免 0 通过也更新)。
  - tie-break:**取最早达到 best_score 的轮**(v1.2 已裁决,见 §11 风险表);`candidates_at_best_score` 计数达到该 score 的轮数,写入 best/meta.json 供人工复核。
  - 收尾:若 `best_iter < 0`,best/rtl.v 写原版 RTL 并 meta.json 标 `best_iter=-1`(语义:无任何轮通过仿真)。
- `target_lines` 来自 `ErrorItem.evidence` 里行号标记(B 同时匹配多种格式:`r":(\d+):"` / `r"\((\d+)\)"` / `r":(\d+):\s*\d+"`,适配 yosys 与 iverilog 不同行号风格);定位出错片段喂给 LLM,缩小改动范围(防退化策略之一)。`target_lines=None` 时记 trajectory 的 `locate_failed` 事件,LLM 拿到全文。

---

## 5. 对外接口契约(函数签名 + schema + 错误码 + 副作用)

### 5.1 主接口(契约 §2.3 Skill Protocol + as_tool)

B 实现契约 §2.3 的 `Skill` Protocol,通过 `as_tool()` 暴露为 `Tool`。签名严格对齐契约:

    class SelfHealSkill:                       # implements Skill(契约 §2.3);普通类,非 @dataclass
        name: str = "skill_self_heal"          # 契约 §1 对照表
        description: str = (
            "Agentic RTL self-heal loop: synth -> sim -> (sta) -> diagnose -> "
            "LLM patch, with version rollback and regression guard."
        )
        max_iterations: int                    # 由 settings.toml [budget] skill_max_iterations 注入,默认 5
        budget_s: float                        # 由 settings [budget] skill_self_heal_budget_s 注入,默认 480(v1.2)

        def __init__(
            self,
            registry: ToolRegistry,            # B 通过 registry 取 EDA Tool + skill_diagnose(契约 §3)
            llm: LLMProvider,                  # 命名统一:存为 self._llm(下划线表内部持有)
            runner: "Runner",                  # v1.2:Skill 构造统一注入 runner(契约 §2.3)
            max_iterations: int = 5,
            budget_s: float | None = None,     # None 则从 settings.skill_self_heal_budget_s 读(默认 480)
            settings: "Settings | None" = None,
        ) -> None: ...

        def run(
            self,
            run_id: str,
            inputs: dict[str, Any],
            remaining_budget_s: float | None = None,   # 契约 §2.3 双层预算仲裁(as_tool 拆 _remaining_budget_s 下传)
        ) -> SkillResult: ...

        def as_tool(self) -> Tool: ...         # 契约 §2.3 映射表 + reserved 字段拆包

`inputs` schema(契约 §2.3 v1.2 `skill_self_heal.as_tool args schema` 逐字对齐):

    {
        "rtl": str,                            # RTL 源绝对路径(必须存在)
        "tb": str,                             # TB 源绝对路径(必须存在,且遵守 TB 打印协议 契约 §2.2)
        "diagnose": dict | None,               # 可选:外部(C)预先调过 skill_diagnose 的 parsed;None 则 B 内部自己调
        "max_iter": int,                       # 覆盖 settings.skill_max_iterations
        "goal": str,                           # v1.2 新增:RunRequest.goal 原样下传(MVP 模板见 §5.2 HealGoal)
        "lib": str | None,                     # v1.2 新增:RunRequest.lib_path;None 则 B 跳过 STA
        "clock": str | None,                   # v1.2 新增:RunRequest.clock_name;None 则 B 跳过 STA
        "top_module": str | None               # v1.2 新增:RunRequest.top_module;None 由 Yosys 推断
        # 注:_remaining_budget_s 是 reserved 字段,由 as_tool 拆包,不在此 LLM 可见 schema 中
    }

### 5.2 错误码(B namespace,v1.2 已正式登记于契约 §2.6 namespace 表)

B 声明自己的 namespace `heal`,登记如下错误码(v1.2 已正式登记于契约 §2.6 namespace 表,不再"不改核心表"):

    heal.unsupported_goal      # goal 字符串无法解析成 HealGoal(severity=error)
    heal.rtl_not_found         # inputs["rtl"] 路径不存在(severity=error)
    heal.tb_not_found          # inputs["tb"] 路径不存在(severity=error)
    heal.diagnose_failed       # 内部 skill_diagnose 返回 status=error 且无 root_causes(severity=error)
    heal.patch_syntax_invalid  # LLM 出的 patch 语法校验失败,且已耗尽策略降级(severity=warn)
    heal.regression_deadlock   # 连续 N 轮(num_passed 逐轮下降)触发死锁保护,主动停机(severity=warn)
    heal.reduced_to_diagnose   # 防退化降级:本 run 只产出诊断报告,不自动改(severity=info)

其中 budget 耗尽复用契约 `eda.budget_exhausted`(severity=warn),不另立码。

### 5.3 输出 schema(经 as_tool 后的 ToolResult.parsed,契约 §2.3 映射)

`SkillResult.final_parsed` 自带 `_schema`(契约 §2.1),形状:

    final_parsed = {
        "_schema": {"name": "skill_self_heal", "version": "0.1.0", "contract_version": CONTRACT_VERSION},
        "skill": "self_heal",
        "passed": bool,                         # convergence_cause == "all_pass"
        "iterations": int,
        "best_iter": int,
        "convergence_cause": str,               # all_pass | max_iter | regression | budget | none
        "patch_source": str,                    # llm_diff | llm_full_rewrite | rule_based | none
        "fixed_rtl_ref": dict[str, str] | None, # artifact_ref 指向 skills/self_heal/best/rtl.v(失败为 None)
        "final_report_ref": dict[str, str],     # artifact_ref 指向 skills/self_heal/report.md
        "summary": str,
    }

经 `as_tool()` 后 `ToolResult.parsed` 在此基础上追加 `_skill_status / _iterations / _trajectory / _patch_source / _convergence_cause / _best_iter`(契约 §2.3 as_tool 映射表逐字)。

### 5.4 副作用 / 产出工件(契约 §2.4 路径约定,run_id 为父 RunRecord 的)

每次 `SelfHealSkill.run` 产生以下工件(全部在 `runs/<run_id>/` 下):

    runs/<run_id>/skills/self_heal/
        iter_0/rtl_snapshot.v                   # 初始 RTL 快照(回退基准,无 patch)
        iter_1/
            rtl_snapshot.v                      # 该轮应用 patch 后的 RTL
            rtl_patch.diff                      # LLM 产的 diff(或 full_rewrite 标记)
            sim_result.json                     # 该轮仿真 parsed 摘要
        iter_2/ ...
        best/
            rtl.v                               # best_iter 对应的 RTL(契约 §2.4 明列)
            meta.json                           # {best_iter, num_passed, convergence_cause}
        report.md                               # 人类可读:每轮概览 + 失败案例 + token 统计
    runs/<run_id>/steps/<idx>_skill_self_heal/  # B 入口步(契约 §2.4)
        tool_call.json / tool_result.json
    runs/<run_id>/steps/<idx>_yosys_synth/      # B 内部每轮子步,skill_name=self_heal, iter=N
    runs/<run_id>/steps/<idx>_iverilog_sim/
    runs/<run_id>/steps/<idx>_opensta_timing/
    runs/<run_id>/steps/<idx>_skill_diagnose/

artifact_ref 协议(契约 §2.4):B 的所有 `SkillResult.artifacts` 元素必须是 `{"run_id": <父run_id>, "rel_path": "skills/self_heal/..."}`,禁止裸 str。

### 5.5 输入/输出错误与异常

- B **不抛业务异常**给上层(契约 §2.5 tool-use 回环精神):所有失败路径走 `SkillResult.status="error"` + `error_code`,或 `"budget_exhausted"`。
- 仅以下两类异常允许穿透(由 C 兜底):contracts schema 不匹配(`eda.schema_mismatch`,severity=error)、runner 落盘 IO 错(`eda.internal`,severity=fatal)。

---

## 6. 关键流程伪代码(Python 风格,4 空格缩进)

    def run(self, run_id, inputs, remaining_budget_s=None):
        # —— 0. 预算仲裁(契约 §2.3 双层) ——
        budget = min(self.budget_s, remaining_budget_s or self.budget_s)
        t_start = monotonic()

        # —— 1. 输入校验 + goal 解析(v1.2:读 inputs['goal'],不再读 inputs['goal_text']) ——
        rtl_path = Path(inputs["rtl"])
        tb_path = Path(inputs["tb"])
        if not rtl_path.exists():
            return self._fail("heal.rtl_not_found", f"rtl not found: {rtl_path}")
        if not tb_path.exists():
            return self._fail("heal.tb_not_found", f"tb not found: {tb_path}")
        goal = self._parse_goal(inputs.get("goal") or "pass all tests")   # v1.2:字段名 'goal'
        if goal is None:
            return self._fail("heal.unsupported_goal", "goal must be 'pass all tests' or 'pass N tests'")

        max_iter = inputs.get("max_iter", self.max_iterations)
        diagnose_seed = inputs.get("diagnose")
        lib = inputs.get("lib")        # v1.2:从 inputs 读 STA 参数(契约 args schema 扩展)
        clock = inputs.get("clock")
        top_module = inputs.get("top_module")

        # —— 2. 版本栈初始化 ——
        self._version_stack: list[Path] = []
        rtl_text = rtl_path.read_text()
        base_dir = self._skill_dir(run_id)
        snap0 = base_dir / "iter_0" / "rtl_snapshot.v"
        snap0.parent.mkdir(parents=True, exist_ok=True)
        snap0.write_text(rtl_text)
        self._version_stack.append(snap0)

        trajectory: list[dict] = []
        best_iter = -1                            # v1.2:初始 -1(语义:无任何轮通过仿真)
        best_score = -1
        candidates_at_best = 0                    # v1.2:达到 best_score 的轮数(tie-break 复核)
        best_rtl_text = rtl_text
        convergence = "none"
        patch_source_final = "none"
        strategy = "diff"                         # diff -> full_rewrite -> diagnose_only
        regression_streak = 0                     # 应用失败计数(_maybe_rollback)
        num_passed_dropped_streak = 0             # v1.2:仿真退化计数(独立于应用失败)

        # —— 3. 主循环 ——
        for iter_n in range(max_iter):
            if monotonic() - t_start > budget:
                convergence = "budget"
                break

            current_snap = self._version_stack[-1]
            current_rtl = current_snap.read_text()

            # 3.1 综合
            synth_res = self._stage_synth(current_snap, run_id, iter_n)
            stage_results = [synth_res]
            if not synth_res.is_ok() or not synth_res.parsed.get("success"):
                outcome = self._diagnose_and_patch(
                    stage_results, current_rtl, run_id, iter_n,
                    seed=diagnose_seed if iter_n == 0 else None,
                    strategy=strategy, snap_dir=base_dir / f"iter_{iter_n + 1}")
                strategy, regression_streak = self._maybe_rollback(
                    outcome, strategy=strategy, streak=regression_streak,
                    run_id=run_id, iter_n=iter_n)
                trajectory.append(self._snap(run_id, iter_n, synth_res, None, None, outcome))
                if outcome.patch_source != "none":
                    patch_source_final = outcome.patch_source   # 记最后一次尝试(即使 applied=False)
                if regression_streak >= 3:
                    convergence = "regression"
                    break
                continue

            # 3.2 仿真
            sim_res = self._stage_sim(current_snap, tb_path, run_id, iter_n)
            stage_results.append(sim_res)
            num_passed = sim_res.parsed.get("num_passed") or 0
            total = (sim_res.parsed.get("num_passed") or 0) + (sim_res.parsed.get("num_failed") or 0)
            required = total if goal.pass_mode == "all" else (goal.num_required or total)
            sim_ok = (num_passed >= required) and (sim_res.parsed.get("passed") is not False)

            # v1.2 best_iter 更新(防退化数值依据,tie-break 最早)
            if num_passed > best_score and num_passed > 0:
                best_score = num_passed
                best_iter = iter_n
                candidates_at_best = 1
                best_rtl_text = current_rtl
            elif num_passed == best_score and num_passed > 0:
                candidates_at_best += 1   # tie:不更新 best_iter(保留最早),只增计数

            # v1.2 仿真退化计数(独立于 _maybe_rollback 的应用失败计数)
            if num_passed < best_score and best_score > 0:
                num_passed_dropped_streak += 1
                trajectory.append({"step": "regression_event", "iter": iter_n,
                                   "detail": f"num_passed {num_passed} < best {best_score}"})
            else:
                num_passed_dropped_streak = 0

            if not sim_ok:
                outcome = self._diagnose_and_patch(
                    stage_results, current_rtl, run_id, iter_n,
                    seed=None, strategy=strategy,
                    snap_dir=base_dir / f"iter_{iter_n + 1}")
                strategy, regression_streak = self._maybe_rollback(
                    outcome, strategy=strategy, streak=regression_streak,
                    run_id=run_id, iter_n=iter_n)
                trajectory.append(self._snap(run_id, iter_n, synth_res, sim_res, None, outcome))
                if outcome.patch_source != "none":
                    patch_source_final = outcome.patch_source
                # v1.2:任一 streak >= 3 触发 regression 死锁
                if regression_streak >= 3 or num_passed_dropped_streak >= 3:
                    convergence = "regression"
                    break
                continue

            # 3.3 STA(v1.2:读 inputs['lib']/inputs['clock'],与 args schema 对齐)
            sta_res = None
            if goal.sta_required and lib and clock:
                netlist_ref = synth_res.artifacts[0]
                sta_res = self._stage_sta(netlist_ref, lib, clock, run_id, iter_n)
                stage_results.append(sta_res)
                wns = sta_res.parsed.get("wns")
                if wns is not None and wns < 0:
                    outcome = self._diagnose_and_patch(
                        stage_results, current_rtl, run_id, iter_n,
                        seed=None, strategy=strategy,
                        snap_dir=base_dir / f"iter_{iter_n + 1}")
                    strategy, regression_streak = self._maybe_rollback(
                        outcome, strategy=strategy, streak=regression_streak,
                        run_id=run_id, iter_n=iter_n)
                    trajectory.append(self._snap(run_id, iter_n, synth_res, sim_res, sta_res, outcome))
                    if outcome.patch_source != "none":
                        patch_source_final = outcome.patch_source
                    if regression_streak >= 3:
                        convergence = "regression"
                        break
                    continue

            # 3.4 全通过
            convergence = "all_pass"
            best_iter = iter_n
            best_rtl_text = current_rtl
            trajectory.append(self._snap(run_id, iter_n, synth_res, sim_res, sta_res, None))
            break

        # —— 4. 收尾:best 快照 + report ——
        if convergence != "all_pass":
            convergence = convergence if convergence != "none" else ("max_iter" if best_iter >= 0 else "none")

        best_dir = base_dir / "best"
        best_dir.mkdir(parents=True, exist_ok=True)
        # v1.2:best_iter < 0 时写原版 RTL + meta 标 best_iter=-1
        (best_dir / "rtl.v").write_text(best_rtl_text)
        (best_dir / "meta.json").write_text(json.dumps({
            "best_iter": best_iter, "num_passed": best_score,
            "convergence_cause": convergence,
            "candidates_at_best_score": candidates_at_best}))

        report_ref = self._write_report(run_id, trajectory, convergence, best_iter, patch_source_final)

        # —— 5. 组装 SkillResult(契约 §2.3 字段逐字对齐) ——
        passed = (convergence == "all_pass")
        return SkillResult(
            status="ok" if passed else ("budget_exhausted" if convergence == "budget" else "error"),
            iterations=len(trajectory),
            final_parsed={
                "_schema": {"name": "skill_self_heal", "version": "0.1.0",
                            "contract_version": CONTRACT_VERSION},
                "skill": "self_heal",
                "passed": passed,
                "iterations": len(trajectory),
                "best_iter": best_iter,
                "convergence_cause": convergence,
                "patch_source": patch_source_final,
                "fixed_rtl_ref": artifact_ref(run_id, "skills/self_heal/best/rtl.v"),
                "final_report_ref": report_ref,
                "summary": self._summary(convergence, best_iter, best_score),
            },
            trajectory=trajectory,
            artifacts=[
                artifact_ref(run_id, "skills/self_heal/best/rtl.v"),
                artifact_ref(run_id, "skills/self_heal/best/meta.json"),
                report_ref,
            ],
            summary=self._summary(convergence, best_iter, best_score),
            patch_source=patch_source_final,
            convergence_cause=convergence,
            best_iter=best_iter,
            error_code=None if passed else ("eda.budget_exhausted" if convergence == "budget" else "heal.regression_deadlock"),
            budget_used_s=monotonic() - t_start,
        )


    def _diagnose_and_patch(self, stage_results, rtl_text, run_id, iter_n, seed, strategy, snap_dir):
        # 1) 诊断:C 没预调或非首轮 → B 内部调 skill_diagnose
        if seed is None:
            diag_call = ToolCall(
                name="skill_diagnose",
                # v1.2:tool_results 元素补 step_idx + run_id(契约 §2.3 args schema 强制)
                args={"tool_results": [dict(asdict(r), step_idx=i, run_id=run_id)
                                       for i, r in enumerate(stage_results)]},
                caller="self_heal", llm_tool_call_id=None)
            diag_tool = self._registry.get("skill_diagnose")
            diag_res = diag_tool(diag_call) if diag_tool else None
            if diag_res is None or not diag_res.is_ok():
                diagnose_parsed = {}   # 契约 §2.6 A→B 第 4 条:失败时返回空 root_causes 让 B 降级
            else:
                diagnose_parsed = diag_res.parsed
        else:
            diagnose_parsed = seed

        # v1.2:root_causes 元素必须是 ErrorItem.asdict;若为空,fallback 把 sim fail_signals 包装成 ErrorItem
        root_causes = diagnose_parsed.get("root_causes", [])
        if not root_causes:
            wrapped = []
            for r in stage_results:
                if r.tool == "iverilog_sim":
                    for sig in (r.parsed.get("fail_signals") or []):
                        wrapped.append(ErrorItem(
                            code="sim.fail_signal", namespace="sim", tool="iverilog_sim",
                            severity="error", message=sig, evidence=[], fix_suggestion=None))
            root_causes = [asdict(e) for e in wrapped]   # 契约 §2.6 A→B 第 3 条:禁止裸字符串列表当 ErrorItem
            diagnose_parsed = {**diagnose_parsed, "root_causes": root_causes}

        target_lines = self._locate_lines(root_causes, rtl_text)   # 多格式正则:见 §4 注释

        # 2) 选 prompt 策略(diff 优先,失败降级 full_rewrite,再失败降级 diagnose_only)
        prompt = self._build_prompt(strategy, rtl_text, root_causes, target_lines)
        llm_resp = self._llm.chat(messages=prompt, temperature=0.0, max_tokens=4096)   # v1.2:self._llm
        patch_text, is_full_rewrite = self._extract_patch(llm_resp.text, strategy)

        # 3) 应用 + 语法校验
        rtl_new, applied_ok = self._apply_patch(rtl_text, patch_text, is_full_rewrite)
        if applied_ok and self._syntax_check(rtl_new):
            snap_dir.mkdir(parents=True, exist_ok=True)
            (snap_dir / "rtl_snapshot.v").write_text(rtl_new)
            (snap_dir / "rtl_patch.diff").write_text(patch_text)
            self._version_stack.append(snap_dir / "rtl_snapshot.v")
            source = "llm_full_rewrite" if is_full_rewrite else "llm_diff"
            return PatchOutcome(iter_n=iter_n, diagnose_parsed=diagnose_parsed,
                                patch_source=source, patch_diff=patch_text,
                                target_lines=target_lines, applied=True,
                                rolled_back=False, regression=False,
                                llm_tokens_in=llm_resp.tokens_in,
                                llm_tokens_out=llm_resp.tokens_out,
                                rationale=llm_resp.text.splitlines()[0][:200])
        # patch 失败 → 不入栈,等上层 _maybe_rollback 换策略
        return PatchOutcome(iter_n=iter_n, diagnose_parsed=diagnose_parsed,
                            patch_source=source if is_full_rewrite else "llm_diff",  # v1.2:记最后一次尝试
                            patch_diff=patch_text,
                            target_lines=target_lines, applied=False,
                            rolled_back=False, regression=False,
                            llm_tokens_in=llm_resp.tokens_in,
                            llm_tokens_out=llm_resp.tokens_out,
                            rationale="patch syntax invalid or apply failed")


    def _maybe_rollback(self, outcome, strategy, streak, run_id, iter_n):
        # v1.2:仅管 patch 应用失败的策略升级;仿真退化(num_passed 下降)在主循环 sim 分支计数
        if not outcome.applied:
            next_strategy = {"diff": "full_rewrite", "full_rewrite": "diagnose_only"}.get(strategy, "diagnose_only")
            if next_strategy == "diagnose_only":
                self._append_diagnose_only_note(run_id, iter_n, outcome)
            return next_strategy, streak + 1
        return strategy, streak

防退化策略升级阶梯(写死,降低 LLM 自由度):

    diff (默认)  ──失败──▶  full_rewrite  ──失败──▶  diagnose_only(只报诊断不自动改)
                                       └─ 触发 heal.reduced_to_diagnose,本 run 必失败但产出可用诊断报告

回退规则(契约 §2.3 patch 回退契约):

- 版本栈 push 仅在 patch 应用成功 + 语法校验通过时;失败不 push,天然回退到上一版。
- 仿真轮次判定退化:若本轮 `num_passed < best_score`,记 `regression=True` 事件入 trajectory(但**不**自动 pop,而是触发策略升级;连续 3 轮退化触发 `regression_deadlock` 主动停机)。

---

## 7. 与共享契约的对接(用到哪些 Tool / Skill / 工件 / LLM provider)

| 契约条目 | B 用到的内容 | B 的用法 |
| --- | --- | --- |
| §2.1 Tool / ToolCall / ToolResult | `ToolCall(name,args,caller,llm_tool_call_id=None)`、`ToolResult.is_ok()` | B 调子 Tool 时构造 ToolCall;读 ToolResult.parsed 判成功。 |
| §2.2 yosys_synth parsed | `parsed.success / num_cells / errors` | B `_stage_synth` 判综合是否通过;`errors` 喂给 diagnose。 |
| §2.2 iverilog_sim parsed + TB 打印协议 | `parsed.num_passed / num_failed / passed` | B `_stage_sim` 判仿真;**B 不解析裸文本**,只信 TB 协议行(依赖 TB 遵守契约 §2.2)。 |
| §2.2 opensta_timing parsed | `parsed.wns / violations` | B `_stage_sta` 判时序;`wns<0` 触发诊断+patch。 |
| §2.2 skill_diagnose parsed | `parsed.root_causes(ErrorItem.asdict,7 字段齐全) / needs_rtl_patch / confidence / fix_hints` | B `_diagnose_and_patch` 调 A;读 root_causes[].fix_suggestion 喂 prompt,读 needs_rtl_patch 决定是否值得出 patch(False 且 fail_signals 也空 → 直接停,避免无谓 LLM 调用)。 |
| §2.3 Skill / SkillResult / as_tool | 全部字段(`patch_source/convergence_cause/best_iter=-1/budget_used_s`) | B 实现 Skill,run() 返回 SkillResult,as_tool() 按映射表 + reserved 拆包转 ToolResult。 |
| §2.3 双层预算仲裁 | `run(remaining_budget_s)`(as_tool 从 args._remaining_budget_s 拆包下传) | B 入口 `budget = min(self.budget_s, remaining)`。 |
| §2.3 patch 回退契约 | "新 num_passed < 旧 → 记 regression 事件 + best_iter 取最早" | B `_maybe_rollback`(应用失败)+ 主循环 sim 分支(仿真退化,独立 streak)严格兑现。 |
| §2.4 RunRecord / StepRecord | 子步以 `StepRecord(skill_name="self_heal", iter=N)` 经 self._runner.append_step 追加父 RunRecord | B **不生成独立 run_id**;通过构造注入的 runner 句柄 append step。 |
| §2.4 artifact_ref 协议 | `artifact_ref(run_id, "skills/self_heal/...")` 返回的 dict | B 所有 artifacts 元素用 artifact_ref 工厂构造(契约强制)。 |
| §2.4 iter 落盘 | `skills/self_heal/iter_<n>/rtl_snapshot.v` + `best/rtl.v` + `best/meta.json`(含 candidates_at_best_score) | B 收尾落 best 快照(契约明列)。 |
| §2.5 LLMProvider / Message | `self._llm.chat(messages, tools=None, temperature=0.0, max_tokens=4096)` | B 出 patch 走 self._llm(经 make_provider 包 CountingProvider,token 自动计数)。 |
| §2.6 ErrorItem + namespace.code | `ErrorItem.evidence / fix_suggestion / code`;A→B 消费契约 | B 从 root_causes 提取 evidence 定位行号(多格式正则)、fix_suggestion 喂 prompt;fail_signals 必须先包装成 ErrorItem 再喂 LLM。B 用 `heal.*` namespace(契约 §2.6 已登记)。 |
| §3 ToolRegistry | `self._registry.get(name)` / `self._registry.list()` | B 通过 self._registry 取子 Tool + skill_diagnose;**不直接 import 子 Tool 类**(开闭原则)。 |
| §2.0 RunRequest | `RunRequest.goal / rtl_path / tb_path / lib_path / clock_name / top_module / max_iter` | C 把 RunRequest 翻成 B 的 inputs(含 goal/lib/clock/top_module,v1.2 扩展)。 |

LLM 调用边界:B 内部 LLM 调用**只用于出 patch**(prompt = 系统+出错 RTL 片段+ErrorItem 列表+策略指令),**不**用 LLM 做 tool-use 选择(B 不调 to_llm_tools;tool-use 是 C 的职责)。命名统一为 self._llm。

---

## 8. 依赖(EDA 工具 / Python 库 / LLM,带建议版本)

### 8.1 EDA 工具(跑在 WSL2 Ubuntu,契约 §6)

    yosys       >= 0.30     # apt install yosys;stat -json 支持(契约 §2.2 硬规则)
    iverilog    >= 12.0     # apt install iverilog;含 vvp
    opensta     >= 2.3      # 源码或 conda install -c openroad opensta;契约 §11 上调 MVP

验证命令(Day0.5 前置 gate,契约 §7):

    yosys -V && iverilog -V && vvp -V && sta -version

### 8.2 Python 库(pyproject.toml 依赖,契约 §6 已列)

    anthropic    >= 0.40    # ClaudeProvider(MVP 唯一 provider)
    openai       >= 1.30    # 加分项:OpenAICompatProvider(Qwen/DeepSeek)
    jsonschema   >= 4       # args schema 校验
    tomli        (py<3.11)  # settings.toml 解析

B 自身**不引入新的第三方依赖**(只依赖 contracts/registry/llm 的内部抽象)。这降低 2 人协作的依赖冲突面。

### 8.3 LLM

    主用:Anthropic Claude(claude-sonnet-4,settings.toml [llm] claude_model)
    预留:OpenAI 兼容(Qwen-Plus / DeepSeek),经 OpenAICompatProvider,加分项不进 MVP 测试
    API key:ANTHROPIC_API_KEY 环境变量(不进 settings.toml,不进 git,契约 §6)

### 8.4 RTL benchmark 来源(data/examples/,契约 §4 v1.2 唯一权威路径)

v1.2:**所有 inject bug RTL+TB 统一放 `data/examples/`**(契约 §4 目录树为唯一权威,与 C §9 一致;不再使用 `data/rtl_samples/`)。

| 设计 | 路径 | 来源 | License | fault_type | healable |
| --- | --- | --- | --- | --- | --- |
| counter(4-bit 带复位计数器) | data/examples/counter/ | 教学自造 | MIT | bitwidth / timing_reset / comb_logic / syntax | true |
| adder(8-bit 行波进位加法器) | data/examples/adder/ | 教学自造 | MIT | comb_logic | true |
| mux2(2 选 1) | data/examples/mux2/ | 教学自造 | MIT | syntax | true |
| shift_reg(4 位移位寄存器) | data/examples/shift_reg/ | 教学自造 | MIT | timing_reset | true |
| tiny_fsm(三态 Mealy) | data/examples/tiny_fsm/ | 教学自造 | MIT | comb_logic | **false**(失败案例演示,综合可过仿真挂) |
| adder_pipe(8-bit 流水加法器,2 级) | data/examples/adder_pipe/ | 教学自造 | MIT | timing_reset(wns<0,STA 触发) | true |

inject bug 矩阵(v1.2,fault_manifest.json 至少 8 条,bitwidth>=2/comb_logic>=2/timing_reset>=2/syntax>=2):

    counter/rtl_bitwidth_bug.v       bitwidth       healable=true
    counter/rtl_reset_bug.v          timing_reset   healable=true
    counter/rtl_offbyone_bug.v       comb_logic     healable=true
    counter/rtl_syntax_bug.v         syntax         healable=true
    adder/rtl_carry_break_bug.v      comb_logic     healable=true
    mux2/rtl_port_bug.v              syntax         healable=true
    shift_reg/rtl_reset_missing_bug.v timing_reset  healable=true
    adder_pipe/rtl_timing_bug.v      timing_reset   healable=true  (STA 触发,演示时序闭环)
    tiny_fsm/rtl_state_bug.v         comb_logic     healable=false (失败案例)

License 全部 MIT,在 `data/examples/LICENSE` 声明;自造样例避免拉外部仓库的 license 审查负担。所有 bug 限定为"人能用 < 5 行 diff 修"的高可修性故障(healable=true 子集),tiny_fsm 状态错只作失败案例演示不进 50% 门槛集。

---

## 9. 实现步骤拆解(给新终端的有序子任务,每步带验证方法)

> 假设前置 gate 已过:WSL2 yosys/iverilog/opensta 装好、contracts.py + registry + runner + LLMProvider + 三个 EDA Tool + skill_diagnose(A)已可调。本组件给 1 人约 1.5-2 天(Day6,契约 §7 排期)。

### 步骤 B0:建文件骨架 + 类型对齐(0.5h)

    # 新终端(WSL2,项目根 eda-agent-system/)
    touch src/eda_agent/skills/self_heal.py
    # 写入:imports + SelfHealSkill 类签名 + HealGoal/PatchOutcome/IterSnapshot dataclass + 常量 SelfHealPrompts

验证:

    python -c "from eda_agent.skills.self_heal import SelfHealSkill, HealGoal, PatchOutcome; print('ok')"

### 步骤 B1:实现 `_parse_goal` + 输入校验 + `_fail` 帮助函数(0.5h)

    # 支持 "pass all tests" / "pass N tests" / 含 "timing" 置 sta_required=True

验证:`pytest tests/test_self_heal_skill.py::test_parse_goal -q`

    # 用例:
    #   "pass all tests" -> HealGoal(pass_mode="all", num_required=None)
    #   "pass 3 tests"   -> HealGoal(pass_mode="at_least", num_required=3)
    #   "pass all tests, no timing violation" -> sta_required=True
    #   "make it fast" -> 抛 heal.unsupported_goal(SkillResult.error)

### 步骤 B2:实现 `_stage_synth / _stage_sim / _stage_sta`(1h)

    # 三方法只做:构造 ToolCall → registry.get(name)(call) → runner.append_step(skill_name="self_heal", iter=N) → return ToolResult
    # 关键:ToolCall.caller="self_heal", llm_tool_call_id=None(B 内部非 LLM 发起)
    # _stage_sta 取 synth_res.artifacts[0] 的 netlist artifact_ref 解析回真实路径

验证:`pytest tests/test_self_heal_skill.py::test_stage_tools -q -m needs_eda`
    # 用一个已知能通过的 counter/rtl.v + tb.v,断言三个 _stage_* 都返回 is_ok()

### 步骤 B3:实现 `_diagnose_and_patch`(1.5h,核心)

    # 1. seed 为 None 时调 skill_diagnose(经 registry.get)
    # 2. _locate_lines:从 ErrorItem.evidence 解析行号(正则 r":(\d+):")
    # 3. _build_prompt:策略 diff/full_rewrite/diagnose_only 三套 prompt 模板
    # 4. llm.chat 调用(temperature=0.0, max_tokens=4096)
    # 5. _extract_patch:从 LLM 回复提取 ```diff``` 块(diff 策略)或整文件(full_rewrite)
    # 6. _apply_patch + _syntax_check(iverilog -t null)

验证:`pytest tests/test_self_heal_skill.py::test_diagnose_and_patch -q -m needs_eda`
    # 用 counter/rtl_bitwidth_bug.v + tb.v,断言:
    #   - diagnose_parsed["root_causes"] 非空
    #   - patch_source in {"llm_diff","llm_full_rewrite"}
    #   - applied=True 且 _syntax_check 通过

### 步骤 B4:实现版本栈 + `_maybe_rollback` + 主循环(1.5h,核心)

    # _version_stack: list[Path],push 仅在 applied + syntax ok
    # _maybe_rollback:策略阶梯 diff->full_rewrite->diagnose_only,连续 3 次失败记 regression_streak
    # 主循环 run():严格按 §6 伪代码

验证:`pytest tests/test_self_heal_skill.py::test_run_pass -q -m needs_eda`
    # 用 counter/rtl_bitwidth_bug.v,断言 max_iter=5 内:
    #   - SkillResult.status == "ok"
    #   - convergence_cause == "all_pass"
    #   - best_iter >= 0
    #   - patch_source != "none"
    #   - trajectory 非空且每元素含 rtl_snapshot_ref

### 步骤 B5:实现 `as_tool()` + 落盘工件 + report.md(1h)

    # as_tool() 严格按契约 §2.3 映射表
    # _write_report:每轮一节(综合/仿真/STA parsed 摘要 + patch diff 链接 + 退化/回退事件)
    # 收尾拷 best_rtl_text -> skills/self_heal/best/rtl.v

验证:

    pytest tests/test_self_heal_skill.py::test_as_tool_mapping -q
    # 断言 ToolResult.parsed 含 _skill_status/_iterations/_trajectory/_patch_source/_convergence_cause/_best_iter
    # 断言 artifacts 元素都是 artifact_ref 形状({"run_id":..,"rel_path":..})

### 步骤 B6:实现回退单测 + 失败案例(1h)

    # test_rollback:构造一个"故意让 LLM 出坏 patch"的 mock provider,断言版本栈不前进、策略升级
    # test_budget_exhausted:把 budget_s 设 0.001,断言 convergence="budget"、status="budget_exhausted"
    # test_regression_deadlock:mock provider 每轮出退化 patch,断言 3 轮后停机

验证:`pytest tests/test_self_heal_skill.py -q`(无需 needs_eda,全 mock)

### 步骤 B7:bootstrap 注册 + e2e 串联(0.5h)

    # tools/bootstrap.py 增加(import 路径 eda_agent.tools.bootstrap):
    #   heal_skill = SelfHealSkill(registry=registry, llm=provider, runner=runner,
    #                              max_iterations=settings.skill_max_iterations,
    #                              budget_s=settings.skill_self_heal_budget_s, settings=settings)
    #   registry.register(ToolEntry(tool=heal_skill.as_tool(), name="skill_self_heal",
    #                                category="skill", schema=<args schema>, parsed_schema_ref={"name":"skill_self_heal","version":"0.1.0"}))

验证:`pytest tests/test_e2e_pipeline.py -q -m needs_eda`
    # 跑 eda self-heal --rtl data/examples/counter/rtl_bitwidth_bug.v --tb data/examples/counter/tb.v --goal "pass all tests"
    # 检查 runs/<run_id>/report.md 生成 + experiment_manifest.json 字段齐全

### 步骤 B8:experiment_manifest 对比实验 + 文档对齐(0.5h,非技术成员协助)

    # 跑 6 个 inject bug 样例,每个产出一个 run 目录,汇总 experiment_manifest.json
    # 与 A/C 文档做字段级互查(契约 §13 Day8 任务)

验证:`python -m eda_agent.cli report <run_id>` 能渲染报告;三份文档字段互查 checklist 全过。

---

## 10. 验收标准(量化指标 + 通过门槛 + 测试方法)

> 本节指标与门槛**可直接照搬执行**,契合契约 §10 agentic 自检 + §7 B 验收基线。所有测试命令在项目根 `eda-agent-system/` 执行,WSL2 工具就绪。

### 10.1 指标 1:自修复通过率(完赛奖第 3 条核心;v1.2 单一硬门槛,消除 v1.1 三套口径分叉)

    指标:在 N>=8 个 healable=true inject bug 上,B 在 max_iter=5 内收敛到 all_pass 的比例(按 fault_type 分组取最小值)
    样例集:data/examples/ 下 healable=true 的 inject bug(见 §8.4 inject bug 矩阵;tiny_fsm 不进门槛集)
    门槛(单一硬门槛,契约 §7 v1.2):
        min(by_fault_type.*.passed / by_fault_type.*.total) >= 0.50
        且 bitwidth 类至少 1 个 convergence_cause == "all_pass"
    测试方法:
        pytest tests/test_self_heal_skill.py::test_pass_rate -q -m needs_eda   # 建议加 -n auto 并行
        # 跑完后跑 scripts/summarize_eval.py 聚合 experiment_manifest.json → experiment_summary.json
        # 断言 experiment_summary.json.min_group_pass_rate >= 0.50 且 by_fault_type.bitwidth.passed >= 1
    验收墙钟:整体验收 <= 30 分钟(超时降级为只跑 bitwidth 类作为代表;或直接 diff 准备期预跑快照 runs/eval_snapshot/)

### 10.2 指标 2:平均迭代次数(agentic 效率)

    指标:成功收敛的 run 的平均 iterations
    门槛:<= 3.5(在 5 上限内,体现"不是盲目跑满")
    测试方法:从指标 1 的成功 run 集合取 SkillResult.iterations 均值
    通过判据:mean(iterations | converged) <= 3.5

### 10.3 指标 3:回滚机制单测(防退化契约,契约 §2.3)

    指标:LLM 出坏 patch 时,B 必须不前进版本栈 + 升级策略 + 记 trajectory 事件
    门槛:100% 通过(mock provider 注入 3 种坏 patch:语法错/应用失败/退化)
    测试方法:
        pytest tests/test_self_heal_skill.py::test_rollback_unit -q
    用例:
        (a) mock LLM 回含语法错的 diff -> 断言 _version_stack 长度不变 + strategy diff->full_rewrite
        (b) mock LLM 回无法 apply 的 diff -> 同上
        (c) mock 连续 3 轮 num_passed 下降 -> 断言 convergence="regression" + 主动停机
    通过判据:3 用例全 pass

### 10.4 指标 4:trace 完整性(契约 §2.4 隶属关系 + §10 agentic 自检)

    指标:每个 run 目录的 trajectory 与 StepRecord 一一对应,且关键字段非空
    门槛:100%
    测试方法:
        pytest tests/test_self_heal_skill.py::test_trace_integrity -q -m needs_eda
    检查项(逐 run):
        - runs/<id>/skills/self_heal/iter_*/rtl_snapshot.v 数量 == iterations
        - runs/<id>/skills/self_heal/best/rtl.v 存在
        - runs/<id>/skills/self_heal/best/meta.json 含 best_iter/num_passed/convergence_cause/candidates_at_best_score
        - v1.2:失败 run 允许 best_iter==-1(无任何轮通过仿真),验收脚本必须接受 -1
        - v1.2:若 num_passed 在多轮相同,meta.json.candidates_at_best_score 与 trajectory 中达到该 score 的轮数一致
        - 父 RunRecord.steps 中 skill_name="self_heal" 的步数 == iterations*(1~4)
        - ToolResult.parsed._schema.name == "skill_self_heal" 且 contract_version==CONTRACT_VERSION
        - SkillResult.patch_source != "none"(当 convergence=="all_pass");失败 run 也应有"最后一次尝试"的 source
    通过判据:所有检查项全 pass

### 10.5 指标 5:失败案例真实有结构(契约 §7 B 验收基线第 2 条;v1.2 max_iter 也算合规失败)

    指标:至少 1 个 inject bug 演示失败案例
    门槛:至少 1 个失败 run(不是"全部成功",也不是"全部崩")
    测试方法:从指标 1 的样例集中,挑一个最难(tiny_fsm 状态错,healable=false)跑,断言:
        convergence_cause in {"budget","regression","max_iter"}   # v1.2:max_iter 也算合规失败
        且 SkillResult.status in {"error","budget_exhausted"}
        且 trajectory 含至少 2 轮(证明真迭代过)
    通过判据:至少 1 个 run 满足上述

### 10.6 指标 6:接口对齐 + 语义验收(契约 §13 一致性;v1.2 加语义断言)

    指标:as_tool() 输出的 ToolResult 与契约 §2.3 映射表逐字段对齐 + B 真能用 A 的 fix_suggestion
    门槛:100%
    测试方法:
        pytest tests/test_contracts.py::test_self_heal_as_tool_schema -q
        pytest tests/test_self_heal_skill.py::test_consume_diagnose_fix_suggestion -q   # v1.2 新增语义断言
    检查项:
        ToolResult.status = "ok" iff SkillResult.status=="ok"
        ToolResult.parsed._skill_status / _iterations / _trajectory / _patch_source / _convergence_cause / _best_iter 全在
        ToolResult.artifacts 元素都是 artifact_ref(...) 返回的 dict 形状
        ToolResult.error_code == "eda.budget_exhausted" iff status=="budget_exhausted"
        v1.2 语义断言:mock A 返回含 fix_suggestion="把 reg[3:0] 改成 reg[7:0]" 的 root_causes,
                      断言 B 的 LLM prompt 文本里包含该 fix_suggestion(避免 as_tool 对齐测试全绿但 B 拿不到归因)
    通过判据:全 pass

### 10.7 汇总验收命令(一键跑全部)

    pytest tests/test_self_heal_skill.py tests/test_contracts.py::test_self_heal_as_tool_schema -v -m "needs_eda or not needs_eda"
    # + scripts/summarize_eval.py 聚合 experiment_manifest.json,断言 experiment_summary.json 字段齐全

---

## 11. 风险与对策

| 风险 | 等级 | 对策 |
| --- | --- | --- |
| **LLM 出的 patch 语法错率高**,导致 max_iter 内修不动 | 高 | (1) diff 优先 + iverilog `-t null` 语法预检,坏 patch 不入栈;(2) 降级 full_rewrite;(3) 再降级 diagnose_only(只报诊断不自动改,本 run 仍产出可用 report);(4) prompt 强约束"只改出错行±5 行,别动其他"。 |
| **iverilog 无结构化输出**,TB 打印协议是唯一信号源 | 高(契约 §2.2 已知限制) | B **不解析裸 stdout**,严格只读 `TEST_PASS n/total` 与 `TEST_FAIL <signal>` 协议行;`data/examples/*/tb.v` 全部遵守协议(Day3 偏 AI 成员负责);TB 协议违反时 B 视为 sim 编译失败而非误判通过。 |
| **退化死锁**(LLM 反复出同一坏 patch / num_passed 逐轮下降) | 中 | v1.2 两套计数:`_maybe_rollback` 记 patch 应用失败 streak(策略升级);主循环 sim 分支记 num_passed 下降 streak;任一 >= 3 触发主动停机(convergence="regression"),不耗光预算;trajectory 记录每次退化事件。 |
| **best_iter 语义歧义**(num_passed 相同时取哪轮) | 中 | v1.2 已裁决:tie-break 取**最早**达到 best_score 的轮(早收敛更优);meta.json 记 `best_iter` + `num_passed` + `candidates_at_best_score`(达到该 score 的轮数),人工可复核是否真发生过 tie。 |
| **A 诊断器返回 root_causes 为空 / needs_rtl_patch=False 但仿真仍挂**(归因缺失) | 中 | B 不强依赖 A 完美;`_diagnose_and_patch` 在 root_causes 为空时,fallback 把 sim_res 的 `fail_signals` **先包装成 ErrorItem**(code=sim.fail_signal, severity=error)再喂 LLM(契约 §2.6 A→B 第 3 条,禁止裸字符串列表当 ErrorItem);包装后 LLM 仍可出 patch。 |
| **预算双层仲裁被 B 独占**(C 饿死) | 中(契约 §2.3) | B 入口 `budget = min(self.budget_s, remaining)`,每轮入口检查;`budget_used_s` 字段如实上报,C 可事后审计。 |
| **WSL2 工具未就绪**(Day0.5 gate 没过) | 高(契约 §7) | B 的 needs_eda 测试在工具缺失时 skip(`@pytest.mark.needs_eda`);mock provider 测试不依赖 EDA 工具,保证逻辑层先行可测。 |
| **artifact_ref 路径写错**(裸 str 而非 dict) | 低(但破坏契约) | `test_trace_integrity` 强校验 artifacts 元素形状是 `{"run_id":..,"rel_path":..}`;contracts 层加 `artifact_ref()` 工厂函数强制构造。 |
| **国产 provider 切换时 tool_calls 归一雷**(契约 §11 裁决 5) | 低(MVP 不做) | MVP 只 ClaudeProvider;B 内部 LLM 调用不用 tool_calls(只出 patch 文本),provider 切换对 B 透明。 |
| **inject bug 样例本身设计错**(可修性不可控) | 中 | fault_manifest.json 记每个 inject bug 的 ground-truth patch + 期望 fault_type;Day3 非技术成员 + 偏 AI 成员互查;B 验收前先用 ground-truth patch 手验"人能修",再让 B 修。 |

---

## 12. 待确认决策(列给用户的开放问题)

1. **goal 模板的覆盖范围**:MVP 只支持 "pass all tests" / "pass N tests" / "no timing violation" 三模板。是否需要支持更自由的 goal(如 "reduce cell area by 20%")?——倾向 MVP 不做(超出"自修复"语义,变成"优化")。

2. **inject bug 的 8 个样例,谁来造、何时造**:[v1.2 已裁决] 偏 AI 成员 Day3 先把 8 个 inject bug + 对应 TB 全部写好(不依赖非技术成员学 Verilog,与其角色描述一致);非技术成员只做 fault_manifest.json 标注 + 实验记录整理。样例限定为"人能 < 5 行 diff 修"的高可修性故障(healable=true),tiny_fsm 状态错只作失败案例(healable=false)。

3. **STA 在 B 中是强制还是可选**:当前设计 `goal.sta_required=True` 才跑 STA(避免没 liberty 时报错)。但契约 §11 把 OpenSTA 上调 MVP。**需确认**:验收时是否要求至少 1 个 run 演示 STA 触发的 patch(adder_pipe timing bug)?——倾向"是,作为加分演示项,不进 50% 通过率硬门槛"。

4. **best_iter tie-break 规则**:[v1.2 已裁决] 取**最早**达到 best_score 的轮;meta.json 加 `candidates_at_best_score` 字段供人工复核是否真发生过 tie。本决策关闭。

5. **降级到 diagnose_only 时,本 run 算成功还是失败**:当前算失败(status="error",error_code="heal.reduced_to_diagnose"),但产出可用诊断报告。**需确认**评委视角算不算"完赛"——倾向算失败案例(契约 §7 第 2 条要的就是"真实有结构的失败"),report.md 里高亮"已降级为诊断模式"作为亮点。

6. **LLM 出 patch 的 prompt 是否要喂历史轨迹**:当前 prompt 只喂当前 RTL + 本轮 ErrorItem。**需确认**是否把前几轮失败的 patch 也喂进去(避免重复出相同坏 patch)——倾向"连续退化时把历史坏 patch 列入 prompt negative examples",作为防退化增强。

7. **MVP 是否需要 rule_based patch_source**:当前三策略 diff/full_rewrite/diagnose_only,没有 rule_based。契约 §2.3 把 rule_based 列为合法值。**需确认**是否为"位宽 bug"这类高规则性故障写一个简单规则补丁(如 `reg [3:0]` → `reg [7:0]`)作为 fast path——倾向 MVP 不做(LLM 已能处理位宽),留集训期视通过率决定。

8. **experiment_manifest.json 的 self_heal_pass_rate 计算口径**:[v1.2 已裁决] 验收用**按 fault_type 分组的最小值**(更严,避免"只修简单类刷均值");均值作为辅助报进 experiment_summary.json.overall_pass_rate。单一硬门槛 = 分组最小值 >= 0.50 且 bitwidth 类至少 1 个 all_pass。本决策关闭。
