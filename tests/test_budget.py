"""tests/test_budget.py —— Budget 预算仲裁器单测(T38)。

4 用例:elapsed_s / remaining_s / exhausted / 边界(0 预算 / 已耗尽)。
用 monkeypatch ``time.monotonic`` 控制时间。
"""
from __future__ import annotations

import pytest

from eda_agent.planner.budget import Budget


def test_elapsed_and_remaining_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    t = [100.0]

    def fake_monotonic() -> float:
        return t[0]

    monkeypatch.setattr(
        "eda_agent.planner.budget.monotonic", fake_monotonic
    )
    b = Budget(run_budget_s=600, started_at=100.0)
    assert b.elapsed_s() == 0.0
    assert b.remaining_s() == 600.0
    assert b.exhausted() is False

    t[0] = 150.0
    assert b.elapsed_s() == 50.0
    assert b.remaining_s() == 550.0
    assert b.exhausted() is False


def test_remaining_floored_at_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    t = [100.0]
    monkeypatch.setattr(
        "eda_agent.planner.budget.monotonic", lambda: t[0]
    )
    b = Budget(run_budget_s=60, started_at=100.0)
    # 超出预算 100s → 剩余应夹到 0.0,不出现负值
    t[0] = 200.0
    assert b.remaining_s() == 0.0
    assert b.exhausted() is True


def test_exhausted_exactly_at_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """remaining_s == 0 时 exhausted() 为 True(契约:<=0 视为耗尽)。"""
    t = [0.0]
    monkeypatch.setattr(
        "eda_agent.planner.budget.monotonic", lambda: t[0]
    )
    b = Budget(run_budget_s=100, started_at=0.0)
    t[0] = 100.0
    assert b.remaining_s() == 0.0
    assert b.exhausted() is True


def test_elapsed_uses_injected_monotonic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Budget.elapsed_s 基于注入的 monotonic,与 started_at 解耦。"""
    t = [35.0]
    monkeypatch.setattr(
        "eda_agent.planner.budget.monotonic", lambda: t[0]
    )
    b = Budget(run_budget_s=30, started_at=10.0)
    # elapsed = monotonic_now - started_at = 35 - 10 = 25
    assert b.elapsed_s() == 25.0
    assert b.remaining_s() == 5.0
    assert b.exhausted() is False
