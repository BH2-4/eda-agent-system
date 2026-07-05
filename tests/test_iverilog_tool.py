"""T15 单测:iverilog_sim Tool(契约 §2.1 + §2.2)。

(a) 纯解析单测(无 EDA 依赖):``_parse_tb_protocol`` 模块级,直接喂标记行字符串。
(b) ``@needs_eda`` 真跑:临时 counter.v + tb.v(TB 打印 TEST_PASS 1/1),跑 tool,
    断言 compiled=True / passed=True / num_passed=1。skip 机制见 ``_have_eda``。
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from eda_agent.contracts import CONTRACT_VERSION, Tool, ToolCall
from eda_agent.settings import Settings
from eda_agent.tools.iverilog_sim import IverilogSimTool, _parse_tb_protocol


# ── (a) 解析单测 ────────────────────────────────────────────────────────


def test_parse_partial_fail():
    """TEST_PASS 3/5 + 一个 TEST_FAIL → num_passed=3 / num_failed=2 / passed=False。"""
    text = "TEST_PASS 3/5\nTEST_FAIL sum_out\n"
    got = _parse_tb_protocol(text)
    assert got["num_passed"] == 3
    assert got["total"] == 5
    assert got["num_failed"] == 2
    assert got["passed"] is False
    assert got["fail_signals"] == ["sum_out"]


def test_parse_all_pass():
    """TEST_PASS 5/5 → passed=True / num_passed=5 / num_failed=0 / fail_signals=[]。"""
    text = "TEST_PASS 5/5\n"
    got = _parse_tb_protocol(text)
    assert got["num_passed"] == 5
    assert got["total"] == 5
    assert got["num_failed"] == 0
    assert got["passed"] is True
    assert got["fail_signals"] == []


def test_parse_no_markers():
    """无任何标记行 → num_passed/num_failed/passed 全 None;fail_signals=[]。"""
    text = "no markers here\njust some vvp noise\n"
    got = _parse_tb_protocol(text)
    assert got["num_passed"] is None
    assert got["total"] is None
    assert got["num_failed"] is None
    assert got["passed"] is None
    assert got["fail_signals"] == []


def test_parse_only_fail_marker():
    """只有 TEST_FAIL(无 TEST_PASS)→ 协议违反,num_passed=None 但 fail_signals 仍解析。"""
    text = "TEST_FAIL sum_out\nTEST_FAIL carry\n"
    got = _parse_tb_protocol(text)
    assert got["num_passed"] is None
    assert got["num_failed"] is None
    assert got["passed"] is None
    assert got["fail_signals"] == ["sum_out", "carry"]


# ── 工具基础属性 + 参数校验 ──────────────────────────────────────────────


def test_tool_satisfies_protocol():
    """IverilogSimTool 实例满足 Tool Protocol(runtime_checkable)。"""
    t = IverilogSimTool(Settings())
    assert t.name == "iverilog_sim"
    assert isinstance(t, Tool)
    assert t.schema["name"] == "iverilog_sim"
    assert t.schema["input_schema"]["required"] == ["rtl", "tb"]


def test_empty_args_rejected():
    """rtl/tb 空 → status=error / error_code=eda.tool_args_invalid。"""
    t = IverilogSimTool(Settings())
    res = t(ToolCall(name="iverilog_sim", args={"rtl": "", "tb": ""}))
    assert res.status == "error"
    assert res.error_code == "eda.tool_args_invalid"
    assert res.parsed["compiled"] is False
    assert res.parsed["passed"] is None
    # artifact 即使失败也按契约固定声明
    assert res.artifacts == [{"run_id": "run", "rel_path": "sim/wave.vcd"}]
    # _schema 元字段合规(硬规范 3/4)
    assert res.parsed["_schema"]["contract_version"] == CONTRACT_VERSION
    assert res.parsed["_schema"]["name"] == "iverilog_sim"
    assert res.parsed["_schema"]["version"] == "0.1.0"


# ── (b) @needs_eda 真跑 ─────────────────────────────────────────────────


COUNTER_V = """\
module counter(
    input wire clk,
    input wire rst,
    output reg [7:0] count
);
    always @(posedge clk) begin
        if (rst) count <= 8'd0;
        else count <= count + 8'd1;
    end
endmodule
"""

# TB 遵守打印协议(契约 §2.2):自由时钟,复位 3 拍后释放,数 5 个 posedge → count=5。
COUNTER_TB = """\
`timescale 1ns/1ps
module tb;
    reg clk;
    reg rst;
    wire [7:0] count;

    counter dut(.clk(clk), .rst(rst), .count(count));

    // 自由时钟:周期 10ns(posedge 时刻 5,15,25,...)。
    initial clk = 0;
    always #5 clk = ~clk;

    integer i;
    initial begin
        // 复位前 3 个 posedge(5/15/25),count 恒 0。
        rst = 1;
        for (i = 0; i < 3; i = i + 1) @(posedge clk);
        // 释放复位,空转 1 拍避开竞争。
        @(negedge clk);
        rst = 0;
        // 放 5 个 posedge → count 由 0 自增到 5。
        for (i = 0; i < 5; i = i + 1) @(posedge clk);
        // posedge 后下一拍稳定采样(用 negedge 对齐避免 NBA 竞争)。
        @(negedge clk);
        if (count === 8'd5)
            $display("TEST_PASS 1/1");
        else
            $display("TEST_FAIL count");
        $finish;
    end
endmodule
"""


def _have_eda() -> bool:
    """WSL 模式下 wsl.exe 可见即视为可能可用;非 WSL 要求本机有 iverilog。"""
    s = Settings()
    if s.eda.wsl_enabled:
        return shutil.which("wsl.exe") is not None
    return shutil.which(s.eda.iverilog_cmd) is not None and shutil.which(s.eda.vvp_cmd) is not None


@pytest.mark.needs_eda
@pytest.mark.skipif(not _have_eda(), reason="需要 iverilog/vvp(WSL 或本机)")
def test_real_sim_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """真跑 iverilog+vvp:counter.v + tb.v → compiled=True / passed=True / num_passed=1。"""
    rtl = tmp_path / "counter.v"
    tb = tmp_path / "tb.v"
    rtl.write_text(COUNTER_V, encoding="utf-8")
    tb.write_text(COUNTER_TB, encoding="utf-8")

    # 在临时 cwd 下跑,使 runs/<run_id>/sim 落到 tmp_path 内。
    monkeypatch.chdir(tmp_path)

    t = IverilogSimTool(Settings())
    res = t(ToolCall(
        name="iverilog_sim",
        args={"rtl": str(rtl), "tb": str(tb), "run_id": "test_run"},
    ))

    assert res.parsed["compiled"] is True, f"编译失败:\n{res.stderr}"
    assert res.parsed["passed"] is True, (
        f"passed!=True; tail=\n{res.parsed['vvp_stdout_tail']}"
    )
    assert res.parsed["num_passed"] == 1
    assert res.parsed["num_failed"] == 0
    assert res.parsed["fail_signals"] == []
    assert res.artifacts == [{"run_id": "test_run", "rel_path": "sim/wave.vcd"}]
    assert res.status == "ok"
