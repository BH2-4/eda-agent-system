# Eval Diagnose Report

- generated_at: `2026-07-05T15:16:04.814326+00:00`
- corpus: `data\logs_corpus\corpus.jsonl`
- with_llm: `True`
- n_samples: `30`
- pass: **True**
- failed_checks: `[]`

## Metrics (A 编号)

| 编号 | 指标 | 值 | 门槛 | 通过 |
| --- | --- | --- | --- | --- |
| A1 | top1_weighted | 0.7067 | >= 0.6 | True |
| A1.code | code_acc | 0.6333 | - | - |
| A1.rc | root_cause_acc | 0.7667 | - | - |
| A1.hint | hint_acc | 0.7333 | - | - |
| A2 | rule_coverage | 1.0 (18/18) | >= 0.4 | True |
| A3 | llm_conf_mean | 0.7818 (11) | >= 0.6 | True |
| A4 | rule_conf_mean | 0.8211 (19) | >= 0.7 | True |
| A8 | p50/p95 | 0.0s / 31.8037s | <= 20.0/60.0s | True |
| A9 | needs_patch_acc | 0.9333 (tp=24,fp=2,fn=0,tn=4) | - | - |

## A11 Buckets

- total: `30` (>= 30)
- by_stage: `{'synth': 13, 'sim': 11, 'sta': 5, 'multi': 1}` (synth/sim/sta 各 >= 5)
- by_bucket: `{'seed': 13, 'grown': 5, 'novel': 12}`
- by_difficulty: `{'easy': 8, 'medium': 15, 'hard': 7}` (hard >= 3)
- by_tool: `{'yosys_synth': 14, 'iverilog_sim': 11, 'opensta_timing': 5}`

- fallback_rate: `0.3667`
- kb_corrupted: `False`

## Failures (A1 < 0.60)

| id | score | code | root_cause | hint | reason |
| --- | --- | --- | --- | --- | --- |
| grown_sim_001 | 0.4 | 0 | 0 | 1 | root_cause_low |
| novel_synth_001 | 0.0 | 0 | 0 | 0 | code_mismatch |
| novel_synth_003 | 0.2 | 0 | 0 | 0 | root_cause_low |
| novel_synth_004 | 0.2 | 0 | 0 | 1 | code_mismatch |
| novel_sim_002 | 0.0 | 0 | 0 | 0 | code_mismatch |
| novel_sim_004 | 0.4 | 0 | 1 | 0 | code_mismatch |
| novel_sim_005 | 0.4 | 0 | 1 | 0 | code_mismatch |
| novel_sta_001 | 0.4 | 0 | 1 | 0 | code_mismatch |
| novel_multi_001 | 0.4 | 1 | 0 | 0 | root_cause_low |