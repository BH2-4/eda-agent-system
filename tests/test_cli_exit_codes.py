"""tests/test_cli_exit_codes.py —— CLI 退出码单测(T48)。

覆盖:
- ``eda self-heal`` 退出码 0/1/2(对应 ok/failed/budget_exhausted);
- ``eda report <run_id>``:存在 → 0;不存在 → 64。

通过 monkeypatch 替换 ``eda_agent.cli.make_provider`` / ``build_registry`` /
``load_settings``,避免真调 anthropic / 真跑 EDA。用一个 fake planner 返回
可控 RunReport。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from eda_agent.cli import main
from eda_agent.contracts import RunReport


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: str = "ok",
    run_id: str = "20260715_103022_a3f1",
) -> None:
    """monkeypatch make_provider/build_provider,让 run_pipeline 返回可控 report。"""
    import eda_agent.cli as cli_mod

    class _FakeProvider:
        provider_name = "fake"

        def chat(self, *a, **kw):
            raise RuntimeError("should not be called")

    class _FakeRunner:
        def __init__(self, *a, **kw):
            pass

        def scavenge_zombies(self, *_a, **_kw):
            return 0

    class _FakePlanner:
        def __init__(self, *a, **kw):
            pass

        def execute(self, request):
            return RunReport(
                run_id=run_id,
                status=status,  # type: ignore[arg-type]
                report_path=f"runs/{run_id}/report.md",
                summary=f"stub {status}",
                metrics={"planner_iterations": 1},
            )

    monkeypatch.setattr(cli_mod, "make_provider", lambda settings: _FakeProvider())
    monkeypatch.setattr(cli_mod, "Runner", _FakeRunner)
    monkeypatch.setattr(cli_mod, "build_registry",
                        lambda **kw: object())  # registry 占位
    monkeypatch.setattr(cli_mod, "CPlanner", _FakePlanner)
    monkeypatch.setattr(cli_mod, "load_settings", lambda *a, **kw: _FakeSettings())


class _FakeSettings:
    class _LLM:
        claude_model = "fake-model"
    llm = _LLM()
    run_budget_s = 60


def test_cli_self_heal_ok_returns_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_pipeline(monkeypatch, status="ok")
    # 提供 rtl/tb 文件(实际不会被读):
    (tmp_path / "a.v").write_text("module a; endmodule")
    (tmp_path / "tb.v").write_text("module tb; endmodule")
    rc = main(["self-heal", "--rtl", "a.v", "--tb", "tb.v", "--goal", "pass all tests"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert "report.md" in out


def test_cli_self_heal_failed_returns_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_pipeline(monkeypatch, status="failed")
    (tmp_path / "a.v").write_text("m")
    (tmp_path / "tb.v").write_text("m")
    rc = main(["self-heal", "--rtl", "a.v", "--tb", "tb.v", "--goal", "pass all tests"])
    assert rc == 1


def test_cli_self_heal_budget_exhausted_returns_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_pipeline(monkeypatch, status="budget_exhausted")
    (tmp_path / "a.v").write_text("m")
    (tmp_path / "tb.v").write_text("m")
    rc = main(["self-heal", "--rtl", "a.v", "--tb", "tb.v", "--goal", "pass all tests"])
    assert rc == 2


def test_cli_report_existing_returns_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    run_id = "20260715_103022_a3f1"
    md = tmp_path / "runs" / run_id / "report.md"
    md.parent.mkdir(parents=True)
    md.write_text("# Report\nstatus: ok", encoding="utf-8")
    rc = main(["report", run_id])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Report" in out


def test_cli_report_missing_returns_64(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    rc = main(["report", "nonexistent_run_id"])
    assert rc == 64
    err = capsys.readouterr().err
    assert "not found" in err


def test_cli_self_heal_mode_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--mode / --run-budget 覆盖 settings(验证 settings 字段被写入)。"""
    monkeypatch.chdir(tmp_path)
    captured_settings = {}

    import eda_agent.cli as cli_mod
    from eda_agent.contracts import RunRequest

    class _Settings:
        class _LLM:
            claude_model = "fake-model"
        class _Planner:
            mode = "llm"
        class _Budget:
            run_budget_s = 600
        llm = _LLM()
        planner = _Planner()
        budget = _Budget()
        run_budget_s = 600

    def fake_load(*a, **kw):
        s = _Settings()
        captured_settings["settings"] = s
        return s

    class _FakeProvider:
        provider_name = "fake"

    class _FakeRunner:
        def __init__(self, *a, **kw): pass

        def scavenge_zombies(self, *_a, **_kw): return 0

    class _FakePlanner:
        def __init__(self, *a, **kw): pass

        def execute(self, request):
            return RunReport(
                run_id="rid", status="ok",
                report_path="runs/rid/report.md",
                summary="ok", metrics={},
            )

    monkeypatch.setattr(cli_mod, "make_provider", lambda s: _FakeProvider())
    monkeypatch.setattr(cli_mod, "Runner", _FakeRunner)
    monkeypatch.setattr(cli_mod, "build_registry", lambda **kw: object())
    monkeypatch.setattr(cli_mod, "CPlanner", _FakePlanner)
    monkeypatch.setattr(cli_mod, "load_settings", fake_load)
    (tmp_path / "a.v").write_text("m")
    (tmp_path / "tb.v").write_text("m")
    rc = main([
        "self-heal", "--rtl", "a.v", "--tb", "tb.v",
        "--goal", "pass all tests",
        "--mode", "rule", "--run-budget", "300",
    ])
    assert rc == 0
    s = captured_settings["settings"]
    assert s.planner.mode == "rule"
    assert s.budget.run_budget_s == 300
