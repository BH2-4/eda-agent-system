// T27-T28: A 诊断器评测语料(corpus.jsonl>=30) + build_corpus.py + eval_diagnose.py
// 验收 A1-A4/A8/A9/A11/A12。数据走文件(_batch_*.jsonl),避免 prompt 膨胀。
export const meta = {
  name: 't27-t28-corpus-eval',
  description: 'T27-T28 A 诊断器评测语料 + eval_diagnose.py,验收 A1-A4/A8/A9/A11/A12',
  phases: [
    { title: 'Scout', detail: '定 corpus.jsonl schema + eval_diagnose.py 接口' },
    { title: 'Build', detail: 'fan-out 8 桶造 >=30 条语料,对齐 ErrorKB 12 seed regex' },
    { title: 'Merge+Impl', detail: '合并 corpus.jsonl + 写 build_corpus.py + eval_diagnose.py' },
    { title: 'Evaluate', detail: '跑 eval(规则层优先,过门槛再 --with-llm 跑 A3)' },
    { title: 'Verify', detail: '6 维对抗验证 schema/A11/regex/门槛/A12/无发明接口' },
  ],
}

const ROOT = 'D:/eda agent system'

const SCOUT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['corpus_schema_json', 'eval_cli', 'eval_metrics_fields', 'coverage_plan'],
  properties: {
    corpus_schema_json: { type: 'string', description: 'corpus.jsonl 每行一条 JSON 的字段定义(字段表+类型+示例一条完整样本)' },
    eval_cli: { type: 'string', description: 'eval_diagnose.py CLI + 退出码语义' },
    eval_metrics_fields: { type: 'string', description: '输出 metrics JSON 字段清单(对应 A1-A4/A8/A9/A11/A12)' },
    coverage_plan: { type: 'string', description: '12 seed 覆盖 + grown/novel 计划,保证 A11 + 规则覆盖>=40%' },
  },
}

const BATCH_SUMMARY_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['bucket', 'count', 'ids', 'file'],
  properties: {
    bucket: { type: 'string' },
    count: { type: 'integer' },
    ids: { type: 'array', items: { type: 'string' } },
    file: { type: 'string', description: '写入的 _batch_*.jsonl 绝对路径' },
  },
}

const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['dimension', 'pass', 'issues', 'evidence'],
  properties: {
    dimension: { type: 'string' },
    pass: { type: 'boolean' },
    issues: { type: 'array', items: { type: 'string' }, description: '具体问题(空=无问题)' },
    evidence: { type: 'string', description: '判定依据(数据/样本id/文件:行)' },
  },
}

// 8 桶 = 6+2+6+2+5+1+4+4 = 30 条;synth=12 sim=12 sta=6;hard=2+2+1+4+4=13>=3
const BUCKETS = [
  { name: 'synth-easy', tool: 'yosys_synth', bucket: 'synth', difficulty: 'easy', count: 6,
    pids: 'synth.rtl_syntax_001, synth.multi_driver_001, synth.undefined_signal_001, synth.module_not_found_001, synth.port_unconnected_001' },
  { name: 'synth-hard', tool: 'yosys_synth', bucket: 'synth', difficulty: 'hard', count: 2,
    pids: 'synth.rtl_syntax_001, synth.multi_driver_001' },
  { name: 'sim-easy', tool: 'iverilog_sim', bucket: 'sim', difficulty: 'easy', count: 6,
    pids: 'sim.compile_failed_001, sim.assert_failed_001, sim.mismatch_001' },
  { name: 'sim-hard', tool: 'iverilog_sim', bucket: 'sim', difficulty: 'hard', count: 2,
    pids: 'sim.mismatch_001, sim.assert_failed_001' },
  { name: 'sta-easy', tool: 'opensta_timing', bucket: 'sta', difficulty: 'easy', count: 5,
    pids: 'sta.timing_violation_001, sta.setup_violation_001' },
  { name: 'sta-hard', tool: 'opensta_timing', bucket: 'sta', difficulty: 'hard', count: 1,
    pids: 'sta.setup_violation_001' },
  { name: 'grown-timeout', tool: 'iverilog_sim', bucket: 'sim', difficulty: 'hard', count: 4,
    pids: 'sim.subprocess_timeout_001, synth.subprocess_timeout_001' },
  { name: 'grown-novel', tool: 'yosys_synth', bucket: 'synth', difficulty: 'hard', count: 4,
    pids: '(novel: 造 ErrorKB 未登记的新 error_code,如 synth.width_mismatch / synth.infer_latch / sim.x_propagation,测 LLM 归因 fallback)' },
]

const KB_REF = `ErrorKB 12 seed pattern(精确 regex/example 见 ${ROOT}/data/error_kb.json,必读):
- synth.rtl_syntax_001 / synth.multi_driver_001 / synth.undefined_signal_001 / synth.port_unconnected_001(warn) / synth.module_not_found_001 (yosys 综合)
- sim.compile_failed_001 / sim.assert_failed_001 / sim.mismatch_001 (iverilog 仿真)
- sta.timing_violation_001 / sta.setup_violation_001 (opensta 时序)
- sim.subprocess_timeout_001 / synth.subprocess_timeout_001 (子进程超时)`

phase('Scout')
log('T27-T28 启动: scout corpus schema + eval 接口')
const scout = await agent(
  `你是 EDA Agent System 的 A 诊断器评测语料架构师。为 T27-T28 定义 corpus.jsonl schema + eval_diagnose.py 接口。

权威依据(必读):
1. Read ${ROOT}/CONTRACTS.md 找 §4(A 诊断器)/§4.2(ErrorKB lookup)
2. Read ${ROOT}/src/eda_agent/skills/diagnose.py 看 DiagnosisReport + DiagnoseSkill.run 输入(log_text)输出(parsed.root_causes/errors/confidence)字段 + RuleLayer/LLMAttributor 类名
3. Read ${ROOT}/data/error_kb.json
4. Read ${ROOT}/验收标准.md 的 A1-A14(A1=Top-1 加权 0.4code+0.4root_cause+0.2hint>=0.60;A2=规则覆盖>=40%;A3=LLM conf>=0.60;A4=规则 conf>=0.70;A11=>=30 synth/sim/sta 各>=5 hard>=3)

${KB_REF}

定义:
A) corpus.jsonl 每行一条 JSON 字段 schema。含 id/tool/bucket/difficulty/log_text/ground_truth。ground_truth 支持 A1 三维(code 精确匹配/root_cause 关键词重叠/fix_hint 关键词重叠)+ A2(expected_pattern_id)+ A9(needs_rtl_patch by severity)。给字段表+类型+一条完整示例。
B) eval_diagnose.py CLI(--corpus 必填/--no-llm 默认只规则层/--with-llm 跑 A3)+ 输出 metrics JSON 字段(A1_top1_weighted/A2_rule_coverage/A3_llm_conf_mean/A4_rule_conf_mean/A8_p50_s/A8_p95_s/A9_needs_patch_acc/A11_buckets/fallback_rate/per_bucket)+ 退出码(0=全过/1=部分挂/2=运行错)。
C) 覆盖计划: 12 seed 各>=1 条(保 A2>=40%)+ grown/novel 测 fallback。

约束: 不发明 CONTRACTS 之外字段;root_cause_keywords/fix_hint_keywords 是关键词数组(eval 用 Jaccard>=0.3 算 match)。输出 SCOUT_SCHEMA。`,
  { schema: SCOUT_SCHEMA, phase: 'Scout', label: 'scout:schema' }
)

phase('Build')
log('fan-out 造语料: ' + BUCKETS.length + ' 桶,目标 >=30 条')
const batches = (await parallel(BUCKETS.map(b => () =>
  agent(
    `你是 EDA 语料构造员。为 ${b.name} 桶造 ${b.count} 条诊断语料,Write 到 ${ROOT}/data/logs_corpus/_batch_${b.name}.jsonl(每行一条 JSON,UTF-8 无 BOM;目录不存在则创建)。

${KB_REF}

桶: tool=${b.tool} bucket=${b.bucket} difficulty=${b.difficulty} 目标 ${b.count} 条
建议覆盖 pid: ${b.pids}

构造规则(严格遵守):
1. 先 Read ${ROOT}/data/error_kb.json 拿每个 pid 的精确 regex + example_log。
2. 每条 log_text 必须能被对应 regex 命中。最稳方式: 基于 example_log 改写——保留 regex 锚点核心子串(如 "ERROR: Syntax error in line " + 数字 + ":"),改变量值(行号/信号名/数值)+ 加真实上下文(yosys/iverilog/sta 前缀输出片段、无关 warning 行)。
3. 同 pid 至少 2 变体(不同变量值/上下文)。
4. difficulty=hard: log_text 更长(嵌无关 warning/info 干扰行)或多 error 混合。
5. grown-novel 桶: 造 ErrorKB 未登记的新 error_code(synth.width_mismatch / synth.infer_latch / sim.x_propagation 等),expected_pattern_id 填 grown.xxx,测规则 miss→LLM fallback。
6. ground_truth 字段:
   - error_code: 二段式,与 log_text 真实错误一致
   - expected_pattern_id: ErrorKB pid;novel 用 grown.xxx
   - severity: error/warn/fatal/info(与 ErrorKB 一致;novel 按性质)
   - expected_needs_rtl_patch: severity in (error,fatal) 且 RTL 源 → true;否则 false
   - root_cause_keywords: 3-6 个关键词(中/英)描述根因
   - fix_hint_keywords: 3-6 个关键词描述修复方向
7. id: <bucket>.<subtype>.<3位序号>(如 synth.syntax.001 / sim.mismatch.002 / sta.setup.001 / grown.latch.001)
8. tool: yosys_synth/iverilog_sim/opensta_timing(按桶)

每条 log_text 是真实感多行 EDA 日志(非一句话)。Write _batch_${b.name}.jsonl 后返回 BATCH_SUMMARY_SCHEMA(file 字段填写入的绝对路径)。`,
    { schema: BATCH_SUMMARY_SCHEMA, phase: 'Build', label: 'build:' + b.name }
  )
))).filter(Boolean)

const totalSamples = batches.reduce((n, b) => n + (b.count || 0), 0)
log('语料构造: ' + batches.length + ' 桶,共 ' + totalSamples + ' 条')

phase('Merge+Impl')
const batchFiles = batches.map(b => b.file).filter(Boolean).join('\n')
const [merged, impl] = await parallel([
  () => agent(
    `你是 EDA 语料合并员。合并 _batch_*.jsonl 为 ${ROOT}/data/logs_corpus/corpus.jsonl。

batch 文件列表:
${batchFiles}

步骤:
1. Read 每个 _batch_*.jsonl,汇总所有样本(每行一条 JSON)。
2. 按 id 去重(保留首条)。
3. 自检每条: 字段齐全(id/tool/bucket/difficulty/log_text/ground_truth 含 error_code/expected_pattern_id/severity/expected_needs_rtl_patch/root_cause_keywords/fix_hint_keywords);tool 与 bucket 一致;id 唯一。
4. regex 自检: Read ${ROOT}/data/error_kb.json,对每条 expected_pattern_id(非 grown.*)对应的 regex 跑 re.search(log_text) 必须命中;grown.* 豁免。失败则剔除+记录。
5. 分桶统计: synth/sim/sta 各多少/easy vs hard/seed vs novel。
6. Write ${ROOT}/data/logs_corpus/corpus.jsonl(每行一条 JSON,UTF-8 无 BOM)。
7. 删 _batch_*.jsonl 临时文件。
8. A11 自检: total>=30 且 synth>=5 且 sim>=5 且 sta>=5 且 hard>=3。

返回: 写入路径 + 总条数 + 分桶统计 + A11 是否达标 + regex 自检失败的 id 列表(若有)。`,
    { phase: 'Merge+Impl', label: 'merge:corpus.jsonl' }
  ),
  () => agent(
    `你是 EDA 评测脚本实现员。实现 ${ROOT}/scripts/build_corpus.py + ${ROOT}/scripts/eval_diagnose.py。

Scout 设计(接口权威):
${JSON.stringify(scout).slice(0, 18000)}

参考脚本风格: Read ${ROOT}/scripts/run_baseline.py + ${ROOT}/scripts/summarize_eval.py,匹配 argparse + JSON stdout。

== build_corpus.py ==
- 读 data/logs_corpus/corpus.jsonl
- schema 校验 + regex 自检(对 expected_pattern_id 非 grown.* 的 ErrorKB regex 跑 re.search(log_text))
- 分桶统计 + JSON stdout({"total":N,"buckets":{synth,sim,sta},"difficulty":{easy,hard},"seed_novel":{seed,novel},"a11_pass":bool,"regex_fail_ids":[...]})
- 退出码 0=OK/1=校验失败

== eval_diagnose.py ==
- --corpus(默认 data/logs_corpus/corpus.jsonl)/--no-llm(默认)/--with-llm
- 对每条跑诊断: 规则层 ErrorKB.load + RuleLayer.match(log_text,tool)→errors+规则confidence;LLM 层(仅 --with-llm)LLMAttributor.attribute→LLM confidence+root_cause
- 必须先 Read ${ROOT}/src/eda_agent/skills/diagnose.py 看确切类名/import 路径/方法签名(ErrorKB.load/lookup/RuleLayer.match/DiagnoseSkill/LLMAttributor),用真实接口,不发明。若 DiagnoseSkill 需 registry/runner 构造过重,直接用 ErrorKB+RuleLayer(更轻)。
- A1_top1_weighted=mean(0.4*code_match+0.4*root_cause_match+0.2*hint_match):
  * code_match = 规则 errors[0].code == gt.error_code
  * root_cause_match = jaccard(关键词(errors[0].message), gt.root_cause_keywords) >= 0.3
  * hint_match = jaccard(关键词(errors[0].fix_suggestion), gt.fix_hint_keywords) >= 0.3
  * (--with-llm 时 root_cause/hint 改用 LLM root_cause 文本关键词)
- A2_rule_coverage = (errors 非空 且 code 匹配)/total
- A3_llm_conf_mean(--with-llm)/A4_rule_conf_mean(规则 confidence 均值)
- A8_p50_s/A8_p95_s(每条墙钟)
- A9_needs_patch_acc = (规则 severity 判 needs_patch == gt.expected_needs_rtl_patch)/total
- per_bucket 分桶 + fallback_rate(规则 miss 比例)
- JSON stdout + Write markdown 报告 data/logs_corpus/eval_report.md
- 退出码 0=A1>=0.60 且 A2>=0.40 且 A4>=0.70/1=部分挂/2=运行错
- 关键词提取: 中文按字符 bigram+单字/英文按空格 lowercase,不引外部 NLP 库
- --no-llm 不依赖 GLM_API_KEY;--with-llm 经 ${ROOT}/.env 的 GLM_API_KEY(Read ${ROOT}/src/eda_agent/llm/factory.py make_provider)

写完自跑(cwd="${ROOT}"): python scripts/build_corpus.py && python scripts/eval_diagnose.py --no-llm,确认不抛异常 + 打印 metrics。

返回: 两脚本路径 + 自跑规则层 metrics JSON + 任何 traceback。`,
    { phase: 'Merge+Impl', label: 'impl:eval_scripts' }
  ),
])

phase('Evaluate')
const evalResult = await agent(
  `你是 EDA 评测运行员。跑 ${ROOT}/scripts/eval_diagnose.py 验证 A1-A4/A8/A9 门槛。

步骤(cwd="${ROOT}"):
1. python scripts/build_corpus.py (schema+regex 自检)
2. python scripts/eval_diagnose.py --no-llm (规则层,快)
3. 读规则层 metrics。若 A2>=0.40 且 A4>=0.70,继续;否则报告规则层未达+根因(corpus 太 hard/ErrorKB 不够/regex 未命中)。
4. 若规则层达标 且 ${ROOT}/.env 有 GLM_API_KEY(Read 检查存在非空,绝不打印 key 内容): python scripts/eval_diagnose.py --with-llm(~30 次 GLM 调用,~10 分钟)。读 LLM metrics。
5. 判定 A1>=0.60/A2>=0.40/A3>=0.60(若跑)/A4>=0.70/A8_p50<=20s p95<=60s/A9 合理。

返回: 规则层 metrics JSON + LLM metrics JSON(若跑)+ 各门槛 pass/fail 表 + 失败根因。脚本异常则返回完整 traceback + 文件:行定位。`,
  { phase: 'Evaluate', label: 'eval:run' }
)

phase('Verify')
const dims = [
  { key: 'schema', desc: 'corpus.jsonl 每行 json.loads 合法 + 字段齐全 + 类型对' },
  { key: 'A11-coverage', desc: 'total>=30 且 synth>=5 sim>=5 sta>=5 hard>=3(build_corpus.py 分桶)' },
  { key: 'regex-selfcheck', desc: '每条 seed 样本(非 grown.*)log_text 被 ErrorKB regex 命中;novel 豁免' },
  { key: 'thresholds', desc: 'A1>=0.60/A2>=0.40/A3>=0.60(若跑)/A4>=0.70 达标?未达标列根因' },
  { key: 'A12-consistency', desc: '随机抽 20% 样本人工判 gt.error_code/root_cause_keywords 是否真对应 log_text(>=0.8 一致)' },
  { key: 'no-invented-interface', desc: 'eval_diagnose.py/build_corpus.py 只用 CONTRACTS/diagnose.py 已有接口,无发明字段(grep 源码对照)' },
]
const verify = (await parallel(dims.map(d => () =>
  agent(
    `你是 EDA 对抗验证员。维度: ${d.key} — ${d.desc}

资源: Read ${ROOT}/data/logs_corpus/corpus.jsonl + ${ROOT}/data/error_kb.json + ${ROOT}/scripts/build_corpus.py + ${ROOT}/scripts/eval_diagnose.py + ${ROOT}/src/eda_agent/skills/diagnose.py + ${ROOT}/CONTRACTS.md。Bash 跑 python 自检(cwd="${ROOT}")。

Evaluate metrics(参考): ${JSON.stringify(evalResult).slice(0, 6000)}

pass=true 当且仅当维度完全达标;否则 pass=false + issues 列具体问题(样本id/文件:行/数值)。evidence 给数据。输出 VERDICT_SCHEMA。`,
    { schema: VERDICT_SCHEMA, phase: 'Verify', label: 'verify:' + d.key }
  )
))).filter(Boolean)

const passed = verify.filter(v => v.pass).length
log('对抗验证: ' + passed + '/' + verify.length + ' 维度达标')

return {
  scout,
  corpus_total: totalSamples,
  merge: merged,
  impl,
  eval: evalResult,
  verify: { passed, total: verify.length, details: verify },
}
