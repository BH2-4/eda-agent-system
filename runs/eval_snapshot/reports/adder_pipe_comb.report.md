# Self-Heal Report

- goal: `修复 adder_pipe_comb 使测试台 pass all tests`
- convergence_cause: `all_pass`
- best_iter: `2`
- num_passed (best): `1`
- patch_source: `llm_full_rewrite`

## Trajectory

### iter 0

- **start**: goal=修复 adder_pipe_comb 使测试台 pass all tests
- **stage**: synth=True sim=False/0/0 sta=True
- **patch**: strategy=diff applied=False source=llm_diff
### iter 1

- **stage**: synth=True sim=False/0/0 sta=True
- **patch**: strategy=full_rewrite applied=True source=llm_full_rewrite
### iter 2

- **stage**: synth=True sim=True/1/1 sta=True
