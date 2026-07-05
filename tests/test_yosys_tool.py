"""YosysSynthTool 单测(契约 §2.1/§2.2)。

(a) 解析单测(无 EDA):_parse_stat_json 模块级函数。
(b) @pytest.mark.needs_eda 真跑:临时写 counter.v → tool(call) 断言。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from eda_agent.contracts import CONTRACT_VERSION, ToolCall
from eda_agent.settings import Settings
from eda_agent.tools.yosys_synth import YosysSynthTool, _parse_stat_json


# ── (a) 解析单测(纯 Python,无 EDA)──────────────────────────────────────

def test_parse_stat_json_extracts_module_fields() -> None:
    """含 stat-json 块的 stdout → 提取 num_cells==24 / num_wires==13。"""
    stdout = (
        "1. Executing SYNTH pass.\n"
        "Generating RTLIL representation for module \\counter.\n"
        "Warning: something minor.\n"
        '{\n'
        '  "creator": "yosys",\n'
        '  "modules": {\n'
        '    "\\\\counter": {\n'
        '      "num_wires": 13,\n'
        '      "num_wire_bits": 40,\n'
        '      "num_cells": 24,\n'
        '      "num_cells_by_type": {"$adff": 8}\n'
        '    }\n'
        '  }\n'
        '}'
    )
    obj = _parse_stat_json(stdout)
    assert obj is not None
    mod = obj["modules"]["\\counter"]
    assert mod["num_cells"] == 24
    assert mod["num_wires"] == 13


def test_parse_stat_json_no_json_returns_none() -> None:
    """无 JSON 文本 → 返回 None 不抛。"""
    assert _parse_stat_json("just synth log, no json here") is None
    assert _parse_stat_json("") is None


def test_parse_stat_json_ignores_trailing_text() -> None:
    """raw_decode 解到合法 JSON 闭合 ``}`` 后忽略后续文本(Day0.5 gate 实测行为)。

    stat-json 是 stdout 末尾的纯 JSON 块,yosys 后续若再打印日志会被忽略。
    """
    text = 'preamble\n{"modules": {"\\\\c": {"num_cells": 5}}}\ntrailing junk after close'
    obj = _parse_stat_json(text)
    assert obj is not None
    assert obj["modules"]["\\c"]["num_cells"] == 5


# ── 参数校验单测(无 EDA)────────────────────────────────────────────────

def test_missing_rtl_returns_error_tool_result() -> None:
    """rtl 空串 / 缺失 → error ToolResult, error_code=eda.tool_args_invalid。"""
    settings = Settings()
    tool = YosysSynthTool(settings)
    call = ToolCall(name="yosys_synth", args={})
    result = tool(call)
    assert result.status == "error"
    assert result.error_code == "eda.tool_args_invalid"
    assert result.artifacts == []
    assert result.parsed["_schema"]["contract_version"] == CONTRACT_VERSION


# ── (b) 真跑(needs_eda)─────────────────────────────────────────────────

# counter.v:最小可综合 8-bit 计数器。
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


def _has_eda() -> bool:
    """Windows 侧有 wsl.exe(WSL2 跑 yosys)或本地 yosys 任一可用即视为 EDA 就绪。"""
    return bool(shutil.which("wsl.exe")) or bool(shutil.which("yosys"))


@pytest.mark.needs_eda
def test_real_synth_counter(tmp_path: Path) -> None:
    if not _has_eda():
        pytest.skip("needs eda: neither wsl.exe nor yosys found on PATH")

    rtl_path = tmp_path / "counter.v"
    rtl_path.write_text(COUNTER_V, encoding="utf-8")

    settings = Settings()  # 默认 wsl_enabled=True,适配 Win11 主机
    tool = YosysSynthTool(settings)
    call = ToolCall(
        name="yosys_synth",
        args={
            "rtl": str(rtl_path),
            "top_module": "counter",
            "run_id": "test",
        },
    )
    result = tool(call)

    assert result.is_ok(), (
        f"expected ok, got status={result.status} "
        f"code={result.error_code} hint={result.error_hint} "
        f"stderr={result.stderr[:500]}"
    )
    assert result.parsed["success"] is True
    assert result.parsed["num_cells"] >= 1
    assert result.parsed["module_name"] == "counter"
    assert result.parsed["_schema"]["contract_version"] == CONTRACT_VERSION
    assert len(result.artifacts) == 2
    for art in result.artifacts:
        assert isinstance(art, dict)
        assert "run_id" in art
        assert "rel_path" in art
