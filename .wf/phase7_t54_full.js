export const meta = {
  name: 'phase7-t54-full',
  description: 'T54 8 bug 全量 self_heal 实验(rule 模式)+ summarize + S1 对抗验证',
  phases: [
    { title: 'Run', detail: '并行 8 bug,每 bug 一 agent 跑 run_self_heal --mode rule --run-budget 400' },
    { title: 'Summarize', detail: 'summarize_eval 聚合 runs/*/experiment_manifest.json → experiment_summary.json' },
    { title: 'Verify', detail: 'S1 门槛 + healable=true 全 all_pass 良好线 + manifest 完整性 + 限流检测对抗验证' },
  ],
}

// 8 个 inject bug(来自 data/fault_manifest.json)
// healable 来自 fault_manifest;tiny_fsm 两个设计为不可修(合规失败案例)
const BUGS = [
  { id: "counter_bitwidth",   ft: "bitwidth",     top: "counter",         healable: true },
  { id: "adder_pipe_bitwidth",ft: "bitwidth",     top: "adder_pipe",      healable: true },
  { id: "counter_syntax",     ft: "syntax",       top: "counter",         healable: true },
  { id: "adder_pipe_syntax",  ft: "syntax",       top: "adder",           healable: true },
  { id: "counter_reset",      ft: "timing_reset", top: "counter_reset",   healable: true },
  { id: "adder_pipe_comb",    ft: "comb_logic",   top: "adder_pipe_comb", healable: true },
  { id: "tiny_fsm_comb",      ft: "comb_logic",   top: "tiny_fsm_comb",   healable: false },
  { id: "tiny_fsm_reset",     ft: "timing_reset", top: "tiny_fsm",        healable: false },
]

const RESULT_SCHEMA = {
  type: "object",
  properties: {
    design_id: { type: "string" },
    fault_type: { type: "string" },
    healable: { type: "boolean" },
    run_id: { type: "string" },
    status: { type: "string" },
    convergence: { type: "string" },
    pass_rate: { type: "number" },
    best_iter: { type: ["integer", "null"] },
    planner_iterations: { type: ["integer", "null"] },
    sim_passed: { type: ["boolean", "null"] },
    wall_time_s: { type: ["number", "null"] },
    manifest_exists: { type: "boolean" },
    bash_timeout: { type: "boolean" },
    error: { type: "string" },
  },
  required: ["design_id", "fault_type", "healable", "status", "convergence", "pass_rate", "manifest_exists"],
}

phase('Run')
log('T54 并行 8 bug self_heal(rule 模式, run_budget=400s, GLM-5.2 thinking max)')
const results = (await parallel(BUGS.map(b => () => agent(
  `在 EDA 项目根目录跑 self_heal 实验。项目根: D:/eda agent system

执行 bash(Bash 工具 timeout 参数务必设为 600000 毫秒,因 GLM-5.2 thinking+max 单 bug 可达 400s+):
  cd "D:/eda agent system" && python scripts/run_self_heal.py --rtl data/examples/${b.id}/rtl.v --tb data/examples/${b.id}/tb.v --top-module ${b.top} --design-id ${b.id} --fault-type ${b.ft} --mode rule --run-budget 400

命令 stdout 最后一行是 JSON,字段: run_id, status, report_path, manifest_path, self_heal_pass_rate, self_heal_convergence, self_heal_best_iter, planner_iterations, sim_passed, wall_time_s。

解析该 JSON,组装结果:
- design_id="${b.id}", fault_type="${b.ft}", healable=${b.healable}
- run_id/status/convergence 直接取
- pass_rate = self_heal_pass_rate
- best_iter = self_heal_best_iter
- planner_iterations, sim_passed, wall_time_s 直接取
- manifest_exists: 用 ls 检查 manifest_path 指向的文件是否存在
- bash_timeout: 若 Bash 因 timeout 杀掉(命令未正常退出),设 true
- error: 若有异常/timeout 填简要说明,否则空串

预期判断(用于 sanity,不强制重试):
${b.healable
    ? `此 bug healable=true,期望 convergence=all_pass / pass_rate=1.0。若出现 regression/budget/max_iter = 异常(可能 GLM 限流或 patch 失败),如实报。`
    : `此 bug healable=false(设计不可修,合规失败案例),期望 convergence∈{regression,budget,max_iter} / pass_rate=0.0。这是预期的失败,不要重试。`}

若 bash timeout 或异常,如实填(不重试,记录现状)。返回结构化结果。`,
  { label: `run:${b.id}`, phase: 'Run', schema: RESULT_SCHEMA, model: 'sonnet' }
)))).filter(Boolean)

const all_pass_count = results.filter(r => r.convergence === "all_pass").length
const timeout_count = results.filter(r => r.bash_timeout).length
log(`Run 完成: ${results.length}/8 返回, all_pass=${all_pass_count}, bash_timeout=${timeout_count}`)

phase('Summarize')
const SUMMARY_SCHEMA = {
  type: "object",
  properties: {
    total_runs: { type: "integer" },
    total_passed: { type: "integer" },
    overall_pass_rate: { type: "number" },
    min_group_pass_rate: { type: "number" },
    by_fault_type_json: { type: "string" },
  },
  required: ["total_runs", "total_passed", "overall_pass_rate", "min_group_pass_rate"],
}
const summary = await agent(
  `在 EDA 项目根跑聚合脚本:
  cd "D:/eda agent system" && python scripts/summarize_eval.py --runs-dir runs --out experiment_summary.json

stdout 末尾打印 {total_runs, total_passed, overall_pass_rate, min_group_pass_rate}。
再 Read experiment_summary.json 拿完整 by_fault_type 对象。

返回: total_runs, total_passed, overall_pass_rate, min_group_pass_rate(数值,非字符串) + by_fault_type_json(by_fault_type 对象的 JSON 字符串,含每类的 total/passed/pass_rate/mean_iters/mean_best_iter)。
注意: total_runs 应为 8(T54 的 8 个 self_heal run);若 !=8 说明 runs/ 混入旧 manifest,在 by_fault_type_json 里如实反映。`,
  { label: 'summarize', phase: 'Summarize', schema: SUMMARY_SCHEMA, model: 'sonnet' }
)

phase('Verify')
const VERDICT_SCHEMA = {
  type: "object",
  properties: {
    s1_min_group_pass: { type: "boolean" },
    s1_bitwidth_all_pass: { type: "boolean" },
    s1_pass: { type: "boolean" },
    good_line_healable_all_pass: { type: "boolean" },
    manifest_14_fields_ok: { type: "boolean" },
    tiny_fsm_failed_as_expected: { type: "boolean" },
    limit_flow_suspected: { type: "boolean" },
    anomalies: { type: "array", items: { type: "string" } },
    per_fault_type: { type: "array", items: { type: "object", properties: { fault_type: { type: "string" }, pass_rate: { type: "number" }, note: { type: "string" } } } },
    verdict: { type: "string" },
  },
  required: ["s1_pass", "good_line_healable_all_pass", "verdict"],
}
const results_json = JSON.stringify(results)
const verdict = await agent(
  `对抗验证 T54 实验。严格判定,不放过异常。

## Run phase 结果(JSON):
${results_json}

## Summary:
total_runs=${summary.total_runs}, total_passed=${summary.total_passed}, overall_pass_rate=${summary.overall_pass_rate}, min_group_pass_rate=${summary.min_group_pass_rate}
by_fault_type: ${summary.by_fault_type_json}

## 逐项判定:

1. **s1_min_group_pass**: min_group_pass_rate >= 0.50(比赛 S1 硬门槛)
2. **s1_bitwidth_all_pass**: bitwidth 类(counter_bitwidth + adder_pipe_bitwidth)中至少 1 个 convergence==all_pass(比赛 S1 硬门槛)
3. **s1_pass**: s1_min_group_pass AND s1_bitwidth_all_pass(比赛完赛奖第1条)
4. **good_line_healable_all_pass(良好线核心)**: 所有 6 个 healable=true 的 bug 全部 convergence==all_pass。这是"系统满分表现"良好线(数据集上限内全力;comb_logic/timing_reset 因各有1个 healable=false,类 pass_rate 上限 0.5,不计入良好线判定)
5. **manifest_14_fields_ok**: 检查每个 manifest 是否齐全 14 字段(从 results 的 manifest_exists + 字段完整性推断;run_id/design_id/fault_type/self_heal_pass_rate/self_heal_convergence/self_heal_best_iter/planner_iterations/llm_calls/tokens_total/wall_time_s/candidates_at_best_score/contract_version + baseline_run_id/baseline_pass_rate)
6. **tiny_fsm_failed_as_expected**: tiny_fsm_comb 和 tiny_fsm_reset(均 healable=false)都未 all_pass(合规失败,证明"至少1失败案例"门槛)
7. **limit_flow_suspected**: 是否 >2 个 healable=true 的 bug 出现非预期 regression/budget/timeout(提示 GLM 8 并发限流;≤2 个可能是 GLM thinking 随机性,不算限流)
8. **per_fault_type**: 4 类各 pass_rate + 备注
9. **anomalies**: 列出所有异常(bug 名 + 问题)
10. **verdict**: 总体结论字符串。格式如 "S1 达标 + 良好线(healable 全 all_pass) / S1 达标但良好线未达(X 个 healable 失败) / S1 未达标(原因)"

判定要点:
- healable=true 的 bug 必须 all_pass 才算良好线达标(用户原则:质量不削减)
- tiny_fsm 失败是合规的(数据集设计),不算异常
- bash_timeout=true 算异常(墙钟超 600s)`,
  { label: 'verify:S1+良好线', phase: 'Verify', schema: VERDICT_SCHEMA }
)

return { results, summary, verdict }
