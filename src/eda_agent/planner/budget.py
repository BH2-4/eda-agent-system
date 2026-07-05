"""Budget —— Run 级预算仲裁器(T38,契约 §6.1)。

单 Run 共享上限(C 与所有 Skill):``run_budget_s`` 来自 settings;``skill.run``
经 ``_remaining_budget_s`` 下传 ``Budget.remaining_s()``(契约 §2.3 双层预算仲裁)。

C 在每次 phase 切换 / LLM 调用前调 ``exhausted()`` 判定是否进入 REPORTING。
"""
from __future__ import annotations

from time import monotonic


class Budget:
    """Run 级预算仲裁器(契约 §6.1)。

    ``started_at`` 由 C 在 execute 入口用 ``monotonic()`` 传入,确保与 Skill 共享
    同一时间基(``skill.run`` 内部也用 ``monotonic()``)。
    """

    def __init__(self, run_budget_s: int, started_at: float) -> None:
        self._run_budget_s = float(run_budget_s)
        self._started_at = float(started_at)

    def elapsed_s(self) -> float:
        """已耗秒数(单调钟,不受系统时间回拨影响)。"""
        return monotonic() - self._started_at

    def remaining_s(self) -> float:
        """剩余秒数;下界 0.0(传给 skill._remaining_budget_s 时不会负)。"""
        consumed = self.elapsed_s()
        return max(0.0, self._run_budget_s - consumed)

    def exhausted(self) -> bool:
        """是否已耗尽(剩余 ≤ 0)。"""
        return self.remaining_s() <= 0.0
