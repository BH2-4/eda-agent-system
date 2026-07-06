# T54 实验快照(runs/eval_snapshot/)

`runs/` 目录整体 gitignore(EDA run 工件体积大且环境相关),仅本 `eval_snapshot/` 例外 track,作 **评审替代证据**(契约 §12 #15:评审机无 EDA 工具时 `needs_eda` 测试 skip,以此快照证明实验真实跑通)。

## 复现命令(T54 全量 8 bug)

```bash
# 单 bug self_heal(rule 模式, ~45-90s/bug, GLM-5.2 thinking+max)
python scripts/run_self_heal.py \
  --rtl data/examples/counter_bitwidth/rtl.v \
  --tb data/examples/counter_bitwidth/tb.v \
  --top-module counter --design-id counter_bitwidth \
  --fault-type bitwidth --mode rule --run-budget 400

# 全量 8 bug 并行(workflow 编排)
#   .wf/phase7_t54_full.js  (8 bug fan-out + summarize + S1 对抗验证)

# 聚合 manifest → summary
python scripts/summarize_eval.py --runs-dir runs --out experiment_summary.json
```

## 目录结构

- `experiment_summary.json` — 8 bug 聚合统计(overall_pass_rate / min_group_pass_rate / by_fault_type)
- `manifests/<design_id>.json` — 每个 run 的 `experiment_manifest.json`(14 字段)
- `reports/<design_id>.report.md` — B 自修复 trajectory(stage / patch / iter 明细)
- `fixed_rtl/<design_id>.rtl.v` — GLM 修复后的 `best/rtl.v`(失败案例为初始 buggy 版)

## 结果(2026-07-06)

见 `verdict.md`(S1 硬门槛 + 良好线判定 + 8 bug 逐项)。
