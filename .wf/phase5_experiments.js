export const meta = {
  name: 'eda-phase5-experiments',
  description: 'Phase 5: 实验脚本(T50-T53): baseline + experiment_manifest + summarize_eval + e2e 真跑',
  phases: [
    { title: '实现实验脚本' },
    { title: '对抗验证' },
    { title: '单测' },
  ],
}

const CONTEXT = `
项目:EDA Agent System(Agentic4Systems 比赛,提交 2026-07-15)。
代码根:D:\\eda agent system\\(Windows,4 空格缩进,from __future__ import annotations,中文注释)。
工作目录:bash 命令先 cd '/d/eda agent system'。

== 当前进度(Phase 0/1/2/3/4 已完成,182 单测全绿)==
系统主干已就位:
- 契约基座 contracts.py/errors.py/registry.py/runner.py/settings.py
- LLM provider 链:eda_agent.llm.factory.make_provider(settings) -> LLMProvider(CountingProvider 包装,有 get_stats() -> LLMStats(llm_calls, tokens_in, tokens_out))
- 3 L1 EDA Tool(yosys_synth/iverilog_sim/opensta_timing)+ A(skill_diagnose)+ B(skill_self_heal),均经 eda_agent.tools.bootstrap.build_registry(provider, runner, settings) 注册
- C CPlanner:eda_agent.planner.c_planner.CPlanner(registry, llm, runner, settings).execute(request) -> RunReport;eda_agent.cli.run_pipeline(request, settings) -> RunReport
- CLI 三子命令:self-heal / diagnose / report(退出码 0/1/2/64)
- inject bug 语料:data/examples/<name>/{rtl.v, tb.v, meta.json}(8 个:counter_bitwidth/counter_syntax/counter_reset/adder_pipe_bitwidth/adder_pipe_comb/adder_pipe_syntax/tiny_fsm_reset/tiny_fsm_comb);data/fault_manifest.json 聚合(8 bugs,fault_type 四类:bitwidth×2/comb_logic×2/syntax×2/timing_reset×2,tiny_fsm_comb+reset healable=false)

== 编码硬规则 ==
- contract_version 用 from eda_agent.contracts import CONTRACT_VERSION(禁止裸 "0.1.0")
- artifacts 用 artifact_ref(run_id, rel_path) 工厂
- 禁止重定义 contracts/errors 已有类型
- C 不调 subprocess(planner/ + cli.py 经 ast 检查);脚本(scripts/)可以调 subprocess(跑 EDA),但优先复用 run_pipeline

== 基础设施 API(先 Read 核对)==
eda_agent.contracts:
  CONTRACT_VERSION, artifact_ref(run_id, rel_path)
  RunRequest(kind, goal, rtl_path, tb_path=None, top_module=None, lib_path=None, clock_name=None, max_iter=None, extra: dict = {})  [frozen;extra 是扩展位]
  RunReport(run_id, status, report_path, summary, metrics)
eda_agent.cli.run_pipeline(request: RunRequest, settings: Settings) -> RunReport
eda_agent.settings.Settings / load_settings()
eda_agent.planner.c_planner.CPlanner(registry, llm, runner, settings).execute(request) -> RunReport
eda_agent.runner.Runner(runs_dir, settings)
data/examples/<name>/meta.json:{"name","fault_type","healable","top_module","ground_truth_patch","description"}
data/fault_manifest.json:{"version":"0.1.0","bugs":[{同 meta 字段}]}
`

const T50_T53_CONTRACT = `
== T50 baseline-only plan(基线实验,不修复)==
目的:对 inject bug RTL 只跑 synth+sim,测"不修复时是否通过"(预期 baseline_pass_rate=0,因为是 bug)。
触发:RunRequest.extra["baseline_only"]=True。extra 还可带 design_id / fault_type(供 manifest 落盘引用)。
行为(C 的 _make_plan + _rule_after_reflect + _finalize 增量支持):
- _make_plan:if request.extra.get("baseline_only"): 返回 Plan(actions=[Action("yosys_synth", {rtl, top_module}), Action("iverilog_sim", {rtl, tb})], mode="rule")
- _rule_after_reflect:baseline_only 模式下,sim 跑完后直接 state.goal_achieved=True 且 return None(不注入 diagnose/self_heal/sta,即便 sim 失败)
- _finalize:baseline_only → status="ok"(baseline 跑完即成功,不管 sim pass 与否);metrics.baseline_pass_rate = (sim.passed is True) ? 1.0 : 0.0;metrics.sim_passed = sim.passed
注意:baseline 只 2 步(synth+sim),不调 B/diagnose/STA。

== T51 experiment_manifest.json(C 在 self_heal run 终态落盘,14 字段)==
落盘路径:runs/<run_id>/experiment_manifest.json
触发:C._finalize 中 if request.kind=="self_heal" and not request.extra.get("baseline_only"): 落 manifest
14 字段(契约 §2.4):
  run_id: str                           # 当前 self_heal run_id
  baseline_run_id: str | None           # 从 request.extra["baseline_run_id"] 读;None 则未跑 baseline
  design_id: str                        # 从 request.extra["design_id"] 读(inject bug name,如 "counter_bitwidth");缺则 "unknown"
  fault_type: str                       # 从 request.extra["fault_type"] 读;缺则 "unknown"
  baseline_pass_rate: float | None      # 从 request.extra["baseline_pass_rate"] 读(baseline run 传入);None 则未测
  self_heal_pass_rate: float            # = metrics.self_heal_pass_rate(1.0 if convergence=="all_pass" else 0.0;conv 为 None 则 0.0)
  self_heal_convergence: str | None     # = metrics.self_heal_convergence
  self_heal_best_iter: int | None       # = metrics.self_heal_best_iter
  planner_iterations: int               # = metrics.planner_iterations
  llm_calls: int                        # = metrics.llm_calls
  tokens_total: int                     # = metrics.llm_tokens_in + metrics.llm_tokens_out
  wall_time_s: float                    # = metrics.wall_time_s
  candidates_at_best_score: int | None  # 从 B 的 best/meta.json 读(若 self_heal run 落了 best/meta.json);否则 None
  contract_version: str                 # = CONTRACT_VERSION
实现:C._finalize 末尾加 self._write_manifest(record.run_id, request, state, metrics, llm_calls, tokens_in, tokens_out, wall_time_s)。
_write_manifest:读 runs/<run_id>/self_heal/best/meta.json(若存在)取 candidates_at_best_score;组装 14 字段;json.dump 到 experiment_manifest.json(原子写 tmp+os.replace)。
注意:baseline_only run 不落 manifest(只跑 synth+sim,无 self_heal 终态)。

== T50 scripts/run_baseline.py(独立脚本,跑 baseline + 输出结果)==
用法:python -m scripts.run_baseline --rtl <path> --tb <path> --top-module <name> --design-id <name> --fault-type <type> [--lib <p>] [--clock <n>]
行为:
  构造 RunRequest(kind="self_heal", goal="baseline only", rtl_path=..., tb_path=..., top_module=...,
                  extra={"baseline_only": True, "design_id": design_id, "fault_type": fault_type})
  rep = run_pipeline(req, load_settings())
  输出 JSON 到 stdout:{"baseline_run_id": rep.run_id, "baseline_pass_rate": rep.metrics["baseline_pass_rate"], "sim_passed": rep.metrics["sim_passed"]}
  退出码:0(ok)/1(failed)/2(budget_exhausted)
注意:scripts/ 需 __init__.py 或用绝对路径 import(从项目根跑 python -m scripts.run_baseline);或放 scripts/run_baseline.py 不打包,直接 python scripts/run_baseline.py(用 sys.path.insert 加 src)。
MVP:放 scripts/run_baseline.py,头部加 sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src")) + from eda_agent.cli import run_pipeline + from eda_agent.contracts import RunRequest + from eda_agent.settings import load_settings。argparse 解析。

== T52 scripts/summarize_eval.py(聚合 manifest → summary)==
用法:python scripts/summarize_eval.py [--runs-dir runs] [--out experiment_summary.json]
行为:
  扫描 runs/*/experiment_manifest.json(只读 self_heal run 的 manifest,跳过 baseline_only/无 manifest 的 run)
  聚合 by_fault_type(四类:bitwidth/comb_logic/syntax/timing_reset + 其他):
    每类:total(该类 manifest 数),passed(self_heal_pass_rate>=1.0 的数),mean_iters(planner_iterations 平均),mean_best_iter
  overall_pass_rate = total_passed / total_manifests
  min_group_pass_rate = min(各类 pass_rate)  # 各类 pass_rate = passed/total(类空则 1.0 或跳过)
  输出 experiment_summary.json:
    {"overall_pass_rate": float, "min_group_pass_rate": float, "by_fault_type": {type: {total, passed, pass_rate, mean_iters, mean_best_iter}}, "total_runs": int, "total_passed": int}
  退出码 0(扫描完即 0,不管 pass_rate)
比赛硬门槛(契约 §7):min_group_pass_rate >= 0.50 且 bitwidth 类 >= 1 all_pass。summarize 不判定门槛,只产出数据(门槛人工/脚本判)。

== T53 tests/test_e2e_pipeline.py(@needs_eda + @needs_llm,真跑 counter_bitwidth 完整流程)==
用 data/examples/counter_bitwidth/{rtl.v, tb.v, meta.json}(fault_type=bitwidth, healable=true, top_module=counter)。
流程:
  1. 先跑 baseline:run_baseline 逻辑(构造 baseline RunRequest + run_pipeline),拿 baseline_run_id + baseline_pass_rate(预期 0,sim passed=False)
  2. 再跑 self_heal:RunRequest(kind="self_heal", goal="pass all tests", rtl_path, tb_path, top_module, extra={"design_id":"counter_bitwidth", "fault_type":"bitwidth", "baseline_run_id":<上步>, "baseline_pass_rate":<上步>})
     rep = run_pipeline(req, settings)
  3. 断言:
     - baseline run:sim_passed is False 或 None(bug 存在);status="ok"(baseline 跑完)
     - self_heal run:rep.metrics["planner_iterations"] >= 1
     - self_heal run 的 runs/<run_id>/steps/ 下含 skill_self_heal 步(B 触发)
     - runs/<run_id>/experiment_manifest.json 存在且 14 字段齐全(design_id="counter_bitwidth", fault_type="bitwidth", baseline_run_id 非空, baseline_pass_rate==0.0, contract_version==CONTRACT_VERSION)
     - 独立验证步(C13):父 RunRecord.steps 含非 self_heal 的 iverilog_sim 步且 passed=True(B all_pass 后强制验证)
     - 若 self_heal 收敛 all_pass:self_heal_pass_rate==1.0(若 LLM 修复失败导致 regression/budget,不断言 pass_rate==1,只断言 manifest 字段齐全)
标记:@pytest.mark.needs_eda + @pytest.mark.skipif(not _have_eda(), reason="需 yosys/iverilog");@pytest.mark.needs_llm + skipif(无 ANTHROPIC_API_KEY 环境变量, reason="需 LLM API key 跑 self_heal patch")
注意:_have_eda() 参考 tests/test_iverilog_tool.py 的实现(WSL 模式查 wsl.exe)。_have_llm() = bool(os.environ.get("ANTHROPIC_API_KEY"))。

== T53 补充:tests/test_experiment_manifest.py(不需 LLM/EDA,stub+FakeLLM 验 manifest 落盘 + summarize)==
用 stub Tool(yosys ok / iverilog passed=False / skill_diagnose 返回 needs_rtl_patch / skill_self_heal 返回 convergence=all_pass+fixed_rtl_ref+best_iter) + FakeLLMProvider。
流程:
  构造 self_heal RunRequest(extra 含 baseline_run_id="bl_xxx", baseline_pass_rate=0.0, design_id="counter_bitwidth", fault_type="bitwidth")
  run_pipeline(req, settings with stub registry)
  断言:runs/<run_id>/experiment_manifest.json 14 字段齐全;baseline_run_id=="bl_xxx";design_id=="counter_bitwidth";self_heal_pass_rate==1.0(convergence=all_pass);contract_version==CONTRACT_VERSION
  再跑 summarize_eval.py 逻辑(或 import 函数)扫描该 manifest → summary;断言 by_fault_type.bitwidth.total>=1
这个测试不需真 EDA/LLM,验证 manifest + summarize 逻辑。

== 关键 ==
- _build_metrics 当前已填 13 字段(契约 §2.4);baseline_pass_rate 已在 metrics(T50 加,从 sim.passed 派生 for baseline_only run;self_heal run 的 baseline_pass_rate 从 extra 读)
- experiment_manifest 是 self_heal run 的额外落盘(不影响 RunReport.metrics)
- candidates_at_best_score 从 runs/<run_id>/self_heal/best/meta.json 读(B 已落盘 best/meta.json 含 candidates_at_best_score 字段)
- 脚本(scripts/)可调 subprocess,但 MVP 优先复用 run_pipeline(不经子进程)
`

const IMPL_SCHEMA = {
  type: "object",
  properties: {
    files_created: { type: "array", items: { type: "string" } },
    files_modified: { type: "array", items: { type: "string" } },
    key_classes: { type: "array", items: { type: "string" } },
    contract_decisions: { type: "array", items: { type: "string" } },
    pytest_target: { type: "string" },
    pytest_full: { type: "string" },
    errors: { type: ["string", "null"] },
  },
  required: ["files_created", "files_modified", "key_classes", "pytest_full"],
}
const VERDICT_SCHEMA = {
  type: "object",
  properties: {
    verdict: { type: "string", enum: ["pass", "fail"] },
    blockers: { type: "array", items: {
      type: "object",
      properties: {
        severity: { type: "string", enum: ["critical", "major", "minor"] },
        file: { type: "string" }, line: { type: ["string", "null"] },
        issue: { type: "string" }, fix: { type: "string" },
      },
      required: ["severity", "file", "issue", "fix"],
    }},
    passed_checks: { type: "array", items: { type: "string" } },
    notes: { type: "string" },
  },
  required: ["verdict", "blockers", "notes"],
}
const TEST_SCHEMA = {
  type: "object",
  properties: {
    tests_written: { type: "array", items: { type: "string" } },
    pytest_result: { type: "string" },
    passed_count: { type: "integer" },
    failed_tests: { type: "array", items: { type: "string" } },
    skipped_eda: { type: "array", items: { type: "string" } },
    notes: { type: "string" },
  },
  required: ["tests_written", "pytest_result", "passed_count"],
}

phase('实现实验脚本')
const impl = await agent(
  CONTEXT + "\n" + T50_T53_CONTRACT + "\n\n== 你的任务(T50-T53)==\n实现实验脚本 + manifest 落盘 + e2e。产出:\n1. src/eda_agent/planner/c_planner.py(改)— _make_plan baseline 支持 / _rule_after_reflect baseline 短路 / _finalize 落 experiment_manifest.json(self_heal run)/ _build_metrics baseline_pass_rate / 新增 _write_manifest 方法\n2. scripts/run_baseline.py(新)— argparse --rtl --tb --top-module --design-id --fault-type [--lib --clock],构造 baseline RunRequest,run_pipeline,输出 JSON {baseline_run_id, baseline_pass_rate, sim_passed}\n3. scripts/summarize_eval.py(新)— 扫描 runs/*/experiment_manifest.json,聚合 by_fault_type,输出 experiment_summary.json\n4. tests/test_e2e_pipeline.py(新)— @needs_eda + @needs_llm,counter_bitwidth 完整流程(baseline→self_heal),断言 manifest 14 字段 + 独立验证步\n5. tests/test_experiment_manifest.py(新)— stub+FakeLLM,验 manifest 落盘 + summarize(不需真 EDA/LLM)\n\n== 实现顺序 ==\n先 Read:src/eda_agent/planner/c_planner.py(_make_plan/_rule_after_reflect/_finalize/_build_metrics 现状), src/eda_agent/cli.py(run_pipeline 现状), src/eda_agent/contracts.py(RunRequest.extra), tests/_planner_helpers.py(stub Tool + FakeLLMProvider 复用), tests/test_e2e_stub.py(stub e2e 范式), tests/test_iverilog_tool.py(_have_eda 实现), data/examples/counter_bitwidth/(rtl.v/tb.v/meta.json)。\n再实现:c_planner.py 增量(baseline + manifest)→ run_baseline.py → summarize_eval.py → test_experiment_manifest.py → test_e2e_pipeline.py。\n\n== 关键注意 ==\n- _rule_after_reflect:baseline_only 模式在 sim 跑完后(sim is not None)直接 goal_achieved=True return None(优先于其他分支;放在 pending_self_heal_all_pass 检查之后)\n- _finalize:baseline_only → status='ok';metrics.baseline_pass_rate = 1.0 if (sim.passed is True) else 0.0\n- _finalize:self_heal run(非 baseline_only)调 _write_manifest;baseline_only run 不落 manifest\n- _write_manifest:读 runs/<run_id>/self_heal/best/meta.json(若存在)取 candidates_at_best_score;否则 None\n- run_baseline.py / summarize_eval.py 用 sys.path.insert 加 src(头部),from eda_agent import ...\n- test_e2e_pipeline 真跑需 LLM(B 的 patch);若环境无 ANTHROPIC_API_KEY,该测试 skipif(不 fail)\n- test_experiment_manifest 用 stub(不需 LLM/EDA),验 manifest 14 字段 + summarize 逻辑\n- experiment_manifest.json 原子写(tmp+os.replace)\n- summarize_eval 的 by_fault_type 四类(bitwidth/comb_logic/syntax/timing_reset)+ 其他;类空跳过或 pass_rate=1.0\n\n== 验收 ==\n- cd '/d/eda agent system' && python -m pytest tests/test_experiment_manifest.py -q → 全绿(不需 LLM/EDA)\n- cd '/d/eda agent system' && python -m pytest tests/test_e2e_pipeline.py -q → 全绿(若环境有 EDA+LLM 真跑;否则 skip)\n- cd '/d/eda agent system' && python -m pytest tests/ -q → 整体绿(182 + 新增,无回归)\n- cd '/d/eda agent system' && python scripts/run_baseline.py --rtl data/examples/counter_bitwidth/rtl.v --tb data/examples/counter_bitwidth/tb.v --top-module counter --design-id counter_bitwidth --fault-type bitwidth → 输出 JSON(baseline_pass_rate 预期 0.0,sim_passed False)\n\n返回 schema。contract_decisions 填裁决。errors 填问题(无则 null)。",
  { schema: IMPL_SCHEMA, phase: '实现实验脚本', label: 'EXP-impl' }
)

phase('对抗验证')
const verdicts = await parallel([
  () => agent(
    CONTEXT + "\n" + T50_T53_CONTRACT + "\n\n== 你的任务:契约合规对抗验证(只读)==\nRead src/eda_agent/planner/c_planner.py(改动部分)+ scripts/run_baseline.py + scripts/summarize_eval.py,逐项检查:\n1. experiment_manifest.json 14 字段齐全(run_id/baseline_run_id/design_id/fault_type/baseline_pass_rate/self_heal_pass_rate/self_heal_convergence/self_heal_best_iter/planner_iterations/llm_calls/tokens_total/wall_time_s/candidates_at_best_score/contract_version)\n2. contract_version 用 CONTRACT_VERSION 常量(禁止裸 '0.1.0')\n3. manifest 只在 self_heal run(非 baseline_only)落盘;baseline_only run 不落\n4. baseline_only plan 只 synth+sim(2 步);不调 B/diagnose/STA\n5. baseline_only → status='ok'(不管 sim pass);baseline_pass_rate = sim.passed?1.0:0.0\n6. self_heal_pass_rate = 1.0 if convergence=='all_pass' else 0.0(conv None 则 0.0)\n7. tokens_total = tokens_in + tokens_out\n8. candidates_at_best_score 从 best/meta.json 读(若存在);否则 None\n9. run_baseline.py:RunRequest.extra 含 baseline_only/design_id/fault_type;输出 JSON 含 baseline_run_id/baseline_pass_rate/sim_passed\n10. summarize_eval.py:扫描 runs/*/experiment_manifest.json;by_fault_type 四类聚合;min_group_pass_rate + overall_pass_rate\n11. manifest 原子写(tmp+os.replace)\n12. 不重定义 contracts/errors 已有类型;RunRequest.extra 用 frozen dataclass 的 extra 字段(不新建)\n13. 脚本(scripts/)不强制 check_no_subprocess(脚本可调 subprocess,但 MVP 复用 run_pipeline)\n返回 verdict + blockers(file:line+fix) + passed_checks。",
    { schema: VERDICT_SCHEMA, phase: '对抗验证', label: 'EXP-verify-contract' }
  ),
  () => agent(
    CONTEXT + "\n" + T50_T53_CONTRACT + "\n\n== 你的任务:逻辑/边界对抗验证(只读)==\nRead src/eda_agent/planner/c_planner.py + scripts/summarize_eval.py,检查:\n1. _rule_after_reflect baseline 短路:baseline_only + sim 跑完 → goal_achieved=True return None(不注入 diagnose/self_heal);放在合适优先级(pending_self_heal_all_pass 之后)\n2. _make_plan baseline_only 产 Plan([synth, sim])(mode='rule')\n3. _finalize baseline_only status='ok';self_heal run status 用原逻辑(goal_achieved/budget/failed)\n4. _write_manifest:best/meta.json 不存在时 candidates_at_best_score=None(不崩);manifest 字段类型正确(int/float/str/None)\n5. run_baseline.py:settings 加载;RunRequest 构造正确;输出 JSON 格式;退出码映射\n6. summarize_eval.py:无 manifest 时 overall_pass_rate=0 或 None(不崩除零);by_fault_type 类空处理(total=0 跳过或 pass_rate=1.0);min_group_pass_rate 空集处理\n7. test_e2e_pipeline:@needs_eda + @needs_llm skipif 正确;无环境时 skip 不 fail;断言 manifest 14 字段 + 独立验证步\n8. test_experiment_manifest:stub 覆盖 manifest 落盘 + summarize 逻辑;不依赖真 EDA/LLM\n9. 边界:baseline_only run 的 metrics 不含 self_heal_* (None);self_heal run 的 metrics.baseline_pass_rate 从 extra 读(若 extra 无则 None)\n10. _build_metrics:baseline_only run 的 baseline_pass_rate 从 sim.passed 派生;self_heal run 的 baseline_pass_rate 从 extra 读\n返回 verdict + blockers + passed_checks。除零/崩溃/类型错=critical/major。",
    { schema: VERDICT_SCHEMA, phase: '对抗验证', label: 'EXP-verify-logic' }
  ),
])

phase('单测')
const test = await agent(
  CONTEXT + "\n" + T50_T53_CONTRACT + "\n\n== 你的任务:补全实验脚本单测并跑绿 ==\n如果 EXP-impl 已写 tests/test_experiment_manifest.py + test_e2e_pipeline.py,Read 补强;否则按 spec 新建。\n必须覆盖:\ntest_experiment_manifest.py(不需 LLM/EDA,stub+FakeLLM):\n- baseline_only run:不落 manifest;metrics.baseline_pass_rate = sim.passed?1.0:0.0;status='ok'\n- self_heal run(stub all_pass):落 manifest 14 字段;design_id/fault_type/baseline_run_id/baseline_pass_rate 从 extra 读;self_heal_pass_rate==1.0;contract_version==CONTRACT_VERSION\n- self_heal run(stub regression):self_heal_pass_rate==0.0;manifest 仍落盘\n- candidates_at_best_score:best/meta.json 存在时读出;不存在时 None\n- summarize_eval:扫描多个 manifest → by_fault_type 聚合;overall_pass_rate;min_group_pass_rate;空集不崩\ntest_e2e_pipeline.py(@needs_eda + @needs_llm):\n- counter_bitwidth 完整流程:baseline(baseline_pass_rate=0,sim passed=False)→ self_heal(manifest 14 字段 + 独立验证步 + B 触发)\n- 无 ANTHROPIC_API_KEY 时 skipif;无 EDA 时 skipif\n\n跑 cd '/d/eda agent system' && python -m pytest tests/test_experiment_manifest.py tests/test_e2e_pipeline.py -v 2>&1 | tail -40。\n失败则 Read 源文件诊断修复(优先修源,其次修测试)。改完重跑直到绿(e2e 真跑项允许 skipif)。\n返回 tests_written/pytest_result/passed_count/failed_tests/skipped_eda。",
  { schema: TEST_SCHEMA, phase: '单测', label: 'EXP-test' }
)

return { impl, verdicts, test }
