export const meta = {
  name: 'phase8-t56-doc-audit',
  description: 'T56 三组件文档与 CONTRACTS.md 字段级互查(6 维度并行)',
  phases: [
    { title: 'Check', detail: '6 维度并行:diagnose.parsed / ErrorItem / as_tool / needs_rtl_patch / CLI 子命令 / 接口签名' },
    { title: 'Report', detail: '汇总不一致清单 + 严重度分级' },
  ],
}

const PROJ = 'D:/eda agent system'

const CHECK_SCHEMA = {
  type: "object",
  properties: {
    dimension: { type: "string" },
    inconsistencies: {
      type: "array",
      items: {
        type: "object",
        properties: {
          field: { type: "string" },
          contract_says: { type: "string" },
          doc_says: { type: "string" },
          code_says: { type: "string" },
          severity: { type: "string", enum: ["major", "minor", "cosmetic"] },
          fix_suggestion: { type: "string" },
        },
        required: ["field", "severity"],
      },
    },
    consistent_fields: { type: "array", items: { type: "string" } },
    notes: { type: "string" },
  },
  required: ["dimension", "inconsistencies"],
}

const DIMS = [
  {
    key: "diagnose_parsed",
    desc: "skill_diagnose.parsed 字段集(字段数 + 字段名 + 语义)",
    targets: [
      `${PROJ}/CONTRACTS.md (搜 skill_diagnose / DiagnoseSkill / parsed 11/13 字段, §2.2 附近)`,
      `${PROJ}/组件A_诊断器.md (parsed 字段表)`,
      `${PROJ}/src/eda_agent/skills/diagnose.py (to_parsed 方法返回的 dict 键)`,
    ],
    focus: "字段数是否一致(11 vs 13)+ 字段名拼写 + _schema 元字段 + contract_version 锚点",
  },
  {
    key: "error_item",
    desc: "ErrorItem 字段(7 字段)+ namespace 登记(9 namespace + 12 eda.* + diagnose.*/heal.* 码)",
    targets: [
      `${PROJ}/CONTRACTS.md (§2.6 errors, ErrorItem dataclass, namespace 表)`,
      `${PROJ}/src/eda_agent/errors.py (ErrorItem 字段 + NAMESPACES + 错误码常量)`,
      `${PROJ}/组件A_诊断器.md + ${PROJ}/组件B_自修复闭环.md (ErrorItem 引用)`,
    ],
    focus: "ErrorItem 7 字段名/类型一致 + namespace 登记完整 + eda.*/diagnose.*/heal.* 别名码一致",
  },
  {
    key: "as_tool_mapping",
    desc: "Skill as_tool 映射(SkillResult → ToolResult,_reserved 下划线前缀剥离,budget_exhausted 覆写)",
    targets: [
      `${PROJ}/CONTRACTS.md (§2.3 Skill as_tool, SkillResult 字段)`,
      `${PROJ}/src/eda_agent/skills/base.py (SkillAdapter/as_tool 实现)`,
      `${PROJ}/组件A_诊断器.md + ${PROJ}/组件B_自修复闭环.md (as_tool 映射表)`,
    ],
    focus: "SkillResult 字段(patch_source/convergence_cause/best_iter=-1/budget_used_s)→ ToolResult parsed 的 _skill_* 元字段映射一致",
  },
  {
    key: "needs_rtl_patch",
    desc: "needs_rtl_patch 派生口径(按 severity 判 error/fatal→True)",
    targets: [
      `${PROJ}/CONTRACTS.md (开头裁决 #20 + §2.2 needs_rtl_patch 说明)`,
      `${PROJ}/src/eda_agent/skills/diagnose.py (needs_rtl_patch 计算逻辑)`,
      `${PROJ}/组件A_诊断器.md (needs_rtl_patch 判定描述)`,
    ],
    focus: "口径锁死按 severity(error/fatal→True),非按 namespace 前缀;三处描述一致",
  },
  {
    key: "cli_subcommands",
    desc: "CLI 3 子命令(self-heal / diagnose / report)+ 退出码(0/1/2/64)",
    targets: [
      `${PROJ}/CONTRACTS.md (§6.3 CLI 三子命令)`,
      `${PROJ}/src/eda_agent/cli.py (_build_parser + main + _EXIT_CODES)`,
      `${PROJ}/组件C_Planner_ToolUse.md (CLI 子命令描述)`,
    ],
    focus: "三子命令名 + 参数 + 退出码(0=ok/1=failed/2=budget_exhausted/64=report不存在)三处一致",
  },
  {
    key: "interface_signatures",
    desc: "核心接口签名(Tool/Skill/RunRequest/RunReport/LLMProvider)",
    targets: [
      `${PROJ}/CONTRACTS.md (§2 dataclass + Protocol 定义)`,
      `${PROJ}/src/eda_agent/contracts.py (RunRequest/RunReport/Tool/Skill/LLMProvider)`,
      `${PROJ}/组件A_诊断器.md + ${PROJ}/组件B_自修复闭环.md + ${PROJ}/组件C_Planner_ToolUse.md (接口签名引用)`,
    ],
    focus: "CPlanner(registry,llm,runner,settings) per-process + RunRequest 字段 + RunReport.metrics 13/14 字段 + Tool.__call__(ToolCall)→ToolResult 签名三处一致",
  },
]

phase('Check')
log('T56 6 维度字段级互查(每维度 opus agent 读 CONTRACTS + 组件文档 + 代码)')
const results = (await parallel(DIMS.map(d => () => agent(
  `字段级一致性互查 — 维度: ${d.key}(${d.desc})

读以下三处(用 Read/Grep,绝对路径):
${d.targets.map((t, i) => `${i + 1}. ${t}`).join('\n')}

互查重点: ${d.focus}

工作方式:
- 提取每处对该维度的字段定义/描述(字段集、字段名、字段数、语义、口径)
- 三处逐字段对比,找不一致(字段缺失/拼写/语义偏移/口径冲突)
- severity 分级:major(语义冲突致实现歧义)/minor(字段数或命名偏差,不影响实现)/cosmetic(措辞差异)
- fix_suggestion: 建议以哪处为准(CODE 通常最权威,代码已实现且测试覆盖)
返回: dimension="${d.key}" + inconsistencies 数组 + consistent_fields(确认一致的字段)+ notes(总体评价)。
严谨字段级核对,不放过 minor。若三处完全一致,inconsistencies 为空数组,consistent_fields 列全部字段。`,
  { label: `check:${d.key}`, phase: 'Check', schema: CHECK_SCHEMA, model: 'opus' }
)))).filter(Boolean)

phase('Report')
const REPORT_SCHEMA = {
  type: "object",
  properties: {
    total_inconsistencies: { type: "integer" },
    major_count: { type: "integer" },
    minor_count: { type: "integer" },
    cosmetic_count: { type: "integer" },
    all_major: { type: "array", items: { type: "string" } },
    all_minor: { type: "array", items: { type: "string" } },
    dimensions_clean: { type: "array", items: { type: "string" } },
    overall_verdict: { type: "string" },
  },
  required: ["total_inconsistencies", "major_count", "overall_verdict"],
}
const results_json = JSON.stringify(results)
const report = await agent(
  `汇总 T56 字段级互查结果,分级统计。

各维度结果 JSON:
${results_json}

统计:
- total_inconsistencies: 所有维度 inconsistencies 总数
- major_count / minor_count / cosmetic_count: 分级计数
- all_major: 所有 major 不一致的简要描述(维度:字段:冲突)
- all_minor: 所有 minor 不一致
- dimensions_clean: 完全一致的维度(inconsistencies 空)
- overall_verdict: 总体一致性评价(如"6 维度 X major Y minor,文档与实现高度一致"或"X 处 major 需修")
按严重度排序,major 优先。`,
  { label: 'report:汇总', phase: 'Report', schema: REPORT_SCHEMA, model: 'opus' }
)

return { results, report }
