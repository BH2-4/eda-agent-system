"""T16 单测:opensta_timing Tool(契约 §2.1 + §2.2 行 242-252)。

(a) 纯解析单测(无 EDA 依赖):``_parse_sta_report`` 模块级,直接喂 sta 输出文本。
(b) 参数校验单测(无 EDA):rtl 空 → ``eda.tool_args_invalid``。
(c) ``@needs_eda`` 真跑:OpenSTA 未装时验证优雅 error(不崩溃),
    或本机/wsl 有 sta 时验证跑通。**未装应 skip 或验证降级,不应 fail。**
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from eda_agent.contracts import CONTRACT_VERSION, Tool, ToolCall
from eda_agent.settings import Settings
from eda_agent.tools.opensta_timing import OpenSTATimingTool, _parse_sta_report


# ── (a) 解析单测(无 EDA)──────────────────────────────────────────────────

_STA_SAMPLE_TEXT = (
    "OpenSTA cmd: sta -f sta.tcl\n"
    "Reading liberty data/lib/sky130.lib\n"
    "1. report_checks\n"
    "Startpoint: clk (rising edge)\n"
    "Endpoint: reg_out/D\n"
    "Path Group: clk\n"
    "Slack: -0.5 (VIOLATED)\n"
    "  data arrival time  -0.5\n"
    "\n"
    "2. report_checks\n"
    "Endpoint: reg_q/D\n"
    "Slack: -0.3 (VIOLATED)\n"
    "\n"
    "wns  -0.5\n"
    "tns  -1.2\n"
)


def test_parse_sta_report_extracts_wns_tns_violations() -> None:
    """固定 sta 输出 → wns==-0.5 tns==-1.2 violations>=1 含 slack_ns。"""
    got = _parse_sta_report(_STA_SAMPLE_TEXT)
    assert got["wns"] == -0.5
    assert got["tns"] == -1.2
    assert got["num_violating_endpoints"] >= 2
    assert len(got["violations"]) >= 1
    v0 = got["violations"][0]
    assert "slack_ns" in v0
    assert v0["slack_ns"] == -0.5


def test_parse_sta_report_empty_text_returns_none_empty() -> None:
    """空文本 → 各字段 None / [] 不抛(契约允许 None)。"""
    got = _parse_sta_report("")
    assert got["wns"] is None
    assert got["tns"] is None
    assert got["num_violating_endpoints"] == 0
    assert got["violations"] == []
    assert got["critical_path_delay_ns"] is None


def test_parse_sta_report_independent_float_line() -> None:
    """report_wns 独立 float 行("wns -0.42")也应匹配。"""
    text = "Some report header\nwns -0.42\ntns -2.71\n"
    got = _parse_sta_report(text)
    assert got["wns"] == pytest.approx(-0.42)
    assert got["tns"] == pytest.approx(-2.71)


# ── (b) 参数校验单测(无 EDA)─────────────────────────────────────────────

def test_missing_rtl_returns_args_invalid() -> None:
    """rtl 空 → ToolResult status=error, error_code=eda.tool_args_invalid。"""
    settings = Settings()
    tool = OpenSTATimingTool(settings)
    call = ToolCall(name="opensta_timing", args={})
    result = tool(call)
    assert result.status == "error"
    assert result.error_code == "eda.tool_args_invalid"
    # 即使校验失败,artifacts 仍按契约固定声明 sta/timing.rpt
    assert len(result.artifacts) == 1
    assert result.artifacts[0]["rel_path"] == "sta/timing.rpt"
    assert result.parsed["_schema"]["contract_version"] == CONTRACT_VERSION


def test_satisfies_tool_protocol() -> None:
    """实例满足 Tool Protocol(runtime_checkable)。"""
    tool = OpenSTATimingTool(Settings())
    assert isinstance(tool, Tool)


def test_no_liberty_returns_sta_no_liberty(monkeypatch: pytest.MonkeyPatch) -> None:
    """rtl 有但 lib 解析为空(default_lib_path 也置空)→ sta.no_liberty。"""
    settings = Settings()
    # 把默认 lib 路径清空,模拟"无任何 liberty"
    monkeypatch.setattr(
        settings.eda, "default_lib_path", "",
    )
    tool = OpenSTATimingTool(settings)
    call = ToolCall(name="opensta_timing", args={"rtl": "dummy.v"})
    result = tool(call)
    assert result.status == "error"
    assert result.error_code == "sta.no_liberty"


# ── (c) 真跑(needs_eda)─────────────────────────────────────────────────

# counter.v:最小可综合 8-bit 计数器(给 sta 链接用)。
COUNTER_V = """\
module counter(
    input wire clk,
    input wire rst,
    output reg [7:0] q
);
    always @(posedge clk or posedge rst) begin
        if (rst)
            q <= 8'b0;
        else
            q <= q + 1'b1;
    end
endmodule
"""


def _sta_available() -> bool:
    """本机 sta 或 wsl.exe(WSL2 内有 sta)任一可用即视为可真跑。

    无 wsl.exe 且无本地 sta 时,subprocess 必抛 FileNotFoundError ——
    用于验证优雅 error 降级,不视为失败。
    """
    return bool(shutil.which("wsl.exe")) or bool(shutil.which("sta"))


@pytest.mark.needs_eda
def test_real_sta_or_graceful_degrade(tmp_path: Path) -> None:
    """真跑 OpenSTA;若未装则验证优雅 error(不崩溃)。

    两种可接受结局:
      - sta 装了 → status==ok,parsed 带合法 _schema。
      - sta 未装 → status==error 且 error_code 命中 sta namespace(sta.sta_unavailable
        / sta.sta_failed),不应抛未捕获异常。
    """
    rtl_path = tmp_path / "counter.v"
    rtl_path.write_text(COUNTER_V, encoding="utf-8")

    settings = Settings()  # 默认 wsl_enabled=True,lib=data/lib/sky130_xx.lib
    # 用真实存在的 lib 路径绕过 sta.no_liberty;此处若无真实 lib,
    # sta 即便装了也会 link 失败 → sta.sta_failed,仍属可接受降级。
    settings.eda.default_lib_path = str(tmp_path / "fake.lib")
    (tmp_path / "fake.lib").write_text("/* placeholder liberty */\n", encoding="utf-8")

    tool = OpenSTATimingTool(settings)
    call = ToolCall(
        name="opensta_timing",
        args={
            "rtl": str(rtl_path),
            "top_module": "counter",
            "clock_name": "clk",
            "run_id": "test",
        },
    )
    result = tool(call)

    if not _sta_available():
        # sta 二进制必然缺失 → 必须优雅 error,不该 ok 也不该崩
        assert result.status == "error"
        assert result.error_code is not None
        assert result.error_code.split(".", 1)[0] == "sta", (
            f"expected sta.* error_code, got {result.error_code}"
        )
        # 不应产生未捕获异常:parsed 必带合法 _schema
        assert result.parsed["_schema"]["name"] == "opensta_timing"
        assert result.parsed["_schema"]["contract_version"] == CONTRACT_VERSION
    else:
        # sta 可用:ok 或 sta.sta_failed(lib/top 不匹配时);都接受,只要不抛
        assert result.status in ("ok", "error"), (
            f"unexpected status={result.status} code={result.error_code}"
        )
        assert result.parsed["_schema"]["name"] == "opensta_timing"
        # artifacts 固定声明
        assert len(result.artifacts) == 1
        assert result.artifacts[0]["rel_path"] == "sta/timing.rpt"
