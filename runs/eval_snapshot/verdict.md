# T54 S1 + 良好线判定

## S1 硬门槛 — ✅ 全过

- `min_group_pass_rate` = **0.50** >= 0.50 ✓
- `bitwidth` 类 **2/2 all_pass** ✓

## 良好线(6 healable=true 全 all_pass)— ✅ 达标

| bug | fault_type | healable | convergence | best_iter | wall_time |
|---|---|---|---|---|---|
| counter_bitwidth | bitwidth | true | all_pass | 2 | 62s |
| adder_pipe_bitwidth | bitwidth | true | all_pass | 2 | 77s |
| counter_syntax | syntax | true | all_pass | 2 | 87s |
| adder_pipe_syntax | syntax | true | all_pass | 2 | 68s |
| counter_reset | timing_reset | true | all_pass | 2 | 44s |
| adder_pipe_comb | comb_logic | true | all_pass | 2 | 44s |
| tiny_fsm_comb | comb_logic | **false** | regression | -1 | 413s |
| tiny_fsm_reset | timing_reset | **false** | all_pass | 2 | 41s |

**overall_pass_rate = 7/8 = 0.875**

## by_fault_type pass_rate

| fault_type | pass_rate | 说明 |
|---|---|---|
| bitwidth | 2/2 = 1.0 | 远超 S1 门槛(>=1 all_pass) |
| syntax | 2/2 = 1.0 | — |
| timing_reset | 2/2 = 1.0 | 含 tiny_fsm_reset bonus |
| comb_logic | 1/2 = 0.5 | tiny_fsm_comb healable=false 拉低(数据集上限) |

`min_group_pass_rate` = min(1.0, 1.0, 1.0, 0.5) = **0.50**

## 合规失败案例(满足 T54 "≥1 失败案例 tiny_fsm")

**tiny_fsm_comb** (healable=false): convergence=`regression`,pass_rate=0.0。两态 FSM 的 S0 分支无条件 `next=S0` 而非 `in?S1:S0`,GLM 多轮 patch 未修对,触发 regression 死锁保护。这是数据集设计的不可修案例。

## bonus

**tiny_fsm_reset** 标注 healable=false 但实际 all_pass(GLM-5.2 修对 reset 值 `state<=S0`)。超 conservative 设计标注("FSM reset-value bugs are hard to auto-repair reliably")。保留 `fault_manifest.json` 的 healable=false 作设计意图标注,实际表现记录于此。

## manifest 完整性

8/8 全 **14 字段**:`run_id` / `baseline_run_id` / `design_id` / `fault_type` / `baseline_pass_rate` / `self_heal_pass_rate` / `self_heal_convergence` / `self_heal_best_iter` / `planner_iterations` / `llm_calls` / `tokens_total` / `wall_time_s` / `candidates_at_best_score` / `contract_version`。

## 关键工程笔记

1. **rule 模式 >> LLM 模式**:rule 确定性 synth→sim→diagnose→self_heal→独立验证(44-87s/bug);LLM ReAct 决策乱序(先 self_heal 再 diagnose)浪费预算(550s budget_exhausted)。实验用 rule,演示 agentic 充分性用 LLM。
2. **修 3 处 inject bug TB 数据集缺陷**(TB 自身 bug,非答案泄露,RTL 仍由 GLM 修):
   - `counter_bitwidth/tb.v`:`.count(count[3:0])` → `.count(count)`(part-select 致 DUT 高 4 位悬空)
   - `adder_pipe_bitwidth/tb.v`:`wire[7:0]sum` → `wire[8:0]sum` + `{1'b0,sum}` → `sum`(位宽不够 MSB 悬空)
   - `counter_syntax/tb.v`:加 `#1` 等 NBA(原 RTL alwayss 编译失败致 TB 从未跑过,时序 bug 未暴露)
   iverilog 实证修 TB 后全 TEST_PASS。
3. **opensta 未装**:T54 "adder_pipe STA 触发" 未做(STA 降级,非 S1 硬门槛;lib_path/clock_name 为 None 时 B/C 跳过 STA 不崩)。
