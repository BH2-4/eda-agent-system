"""EDA 工具封装层(契约 §2.1 Tool Protocol + §2.2 工具协议)。

每个 Tool 把外部 EDA 二进制(yosys / iverilog / opensta / ...)包成 ``Tool Protocol``,
只产 ``ToolResult``,不调 ``runner.append_step``(由 C 调用方负责,契约 T14 §8)。

跨 WSL 调用:settings.eda.wsl_enabled=true 时,subprocess 命令前缀
``["wsl.exe","-d","Ubuntu-24.04","-e", "<tool>", ...]``,Windows 路径转 WSL 路径
(见各 Tool 的 ``_to_wsl_path``)。
"""
from __future__ import annotations

from eda_agent.tools.iverilog_sim import IverilogSimTool, _parse_tb_protocol

# T14(yosys_synth)由另一并行 agent 实现;落地前用容错 import 避免包加载失败。
try:  # pragma: no cover — T14 落地后此 try 必成功
    from eda_agent.tools.yosys_synth import YosysSynthTool, _parse_stat_json  # noqa: F401
    __all__ = ["IverilogSimTool", "_parse_tb_protocol", "YosysSynthTool", "_parse_stat_json"]
except ImportError:  # pragma: no cover
    __all__ = ["IverilogSimTool", "_parse_tb_protocol"]
