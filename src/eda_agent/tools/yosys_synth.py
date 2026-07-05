"""YosysSynthTool — 综合工具封装(契约 §2.2 §2.1)。

封装 ``yosys -p "read_verilog <rtl>; synth -top <top>; write_verilog <netlist>; stat -json"``,
解析 stat-json(走 stdout,非 stderr)提取 cell/wire 数,产出 ``ToolResult``。

Day0.5 gate 实测要点(见任务卡 §A):
- stat-json 输出在 **stdout**;``>`` 会被 yosys 当 selection 参数,**禁止**内部重定向。
- 提取用 ``text.find("{")`` 定位首花括号,再 ``JSONDecoder().raw_decode`` 解析。
- module key 带反斜杠前缀(yosys escaped identifier,如 ``\\counter``),需 strip。
- ``num_ports`` 不在 stat-json 模块字段里 → 读 stdout 文本 ``Number of ports:`` 行;
  找不到保守置 0 并注释说明。
- ``cell_area``:无 liberty 时 stat-json 无面积字段 → None。
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from eda_agent.contracts import (
    CONTRACT_VERSION,
    ToolCall,
    ToolResult,
    artifact_ref,
)
from eda_agent.errors import EDA_SUBPROCESS_TIMEOUT, EDA_TOOL_ARGS_INVALID
from eda_agent.settings import Settings

# synth namespace 已登记(errors.NAMESPACES),用规范二段式码(优先于 eda.* 别名)。
_SYNTH_FAILED_CODE = "synth.synth_failed"

# 正则:行首 Warning / ERROR(从 stdout+stderr best-effort 抽取)。
_WARN_RE = re.compile(r"^Warning:.*$", re.MULTILINE)
_ERROR_RE = re.compile(r"^ERROR.*$", re.MULTILINE)


def _to_wsl_path(win_path: str) -> str:
    """Windows 路径转 WSL 路径:D:\\x\\y → /mnt/d/x/y。

    盘符小写,反斜杠转正斜杠,去冒号。相对路径或无盘符路径原样返回。
    """
    p = win_path.strip()
    if len(p) >= 2 and p[1] == ":" and p[0].isalpha():
        drive = p[0].lower()
        rest = p[2:].replace("\\", "/")
        if not rest.startswith("/"):
            rest = "/" + rest
        return f"/mnt/{drive}{rest}"
    return p.replace("\\", "/")


def _parse_stat_json(text: str) -> dict[str, Any] | None:
    """从 yosys stdout 末尾提取 stat-json 块(模块级函数,便于单测)。

    实测:stat-json 是 stdout 末尾的纯 JSON 块,前面是 synth 日志。
    定位首个 ``{`` 后用 ``JSONDecoder().raw_decode`` 解析(末尾多余文本忽略)。
    无 JSON 或解析失败 → 返回 None,不抛。
    """
    if not text:
        return None
    idx = text.find("{")
    if idx < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[idx:])
        return obj
    except (json.JSONDecodeError, ValueError):
        return None


def _strip_yosys_id(key: str) -> str:
    """strip yosys escaped identifier 前缀反斜杠: ``\\counter`` → ``counter``。"""
    return key[1:] if key.startswith("\\") else key


class YosysSynthTool:
    """``Tool`` Protocol 实现:跑 yosys 综合,stat-json 解析 cell/wire 数。"""

    name = "yosys_synth"
    description = "综合 Verilog RTL 到网表,stat -json 解析 cell/wire 数"
    schema: dict[str, Any] = {
        "name": "yosys_synth",
        "description": "综合 Verilog RTL 到网表,stat -json 解析 cell/wire 数",
        "parsed": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "num_cells": {"type": "integer"},
                "cell_area": {"type": ["integer", "number", "null"]},
                "num_wires": {"type": "integer"},
                "num_ports": {"type": "integer"},
                "module_name": {"type": "string"},
                "script_used": {"type": "string"},
                "warnings": {"type": "array", "items": {"type": "string"}},
                "errors": {"type": "array", "items": {"type": "string"}},
            },
        },
        "input_schema": {
            "type": "object",
            "properties": {
                "rtl": {"type": "string"},
                "top_module": {"type": "string"},
                "run_id": {"type": "string"},
            },
            "required": ["rtl"],
        },
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def __call__(self, call: ToolCall) -> ToolResult:
        t0 = time.monotonic()

        rtl = call.args.get("rtl")
        top_module = call.args.get("top_module")
        run_id = call.args.get("run_id", "run")

        # ── 1. 参数校验:rtl 必填 ──────────────────────────────────────
        if not rtl:
            return ToolResult(
                status="error",
                exit_code=None,
                stdout="",
                stderr="",
                parsed={
                    "_schema": {
                        "name": "yosys_synth",
                        "version": "0.1.0",
                        "contract_version": CONTRACT_VERSION,
                    },
                    "success": False,
                    "num_cells": 0,
                    "cell_area": None,
                    "num_wires": 0,
                    "num_ports": 0,
                    "module_name": "",
                    "script_used": "",
                    "warnings": [],
                    "errors": [],
                },
                artifacts=[],
                duration_s=0.0,
                tool="yosys_synth",
                error_code=EDA_TOOL_ARGS_INVALID,
                error_hint="rtl required",
            )

        # ── 2. 工作目录 + 跨 WSL 路径转换 ─────────────────────────────
        work_dir = Path("runs") / str(run_id) / "synth"
        work_dir.mkdir(parents=True, exist_ok=True)
        netlist_rel = "synth/netlist.v"
        netlist_abs = Path("runs") / str(run_id) / netlist_rel

        wsl = bool(self.settings.eda.wsl_enabled)
        rtl_w = _to_wsl_path(str(rtl)) if wsl else str(rtl)
        netlist_w = _to_wsl_path(str(netlist_abs)) if wsl else str(netlist_abs)

        # ── 3. yosys 脚本(top 为空则 synth 不带 -top;stat -json 后禁 > 重定向)
        if top_module:
            synth_part = f"synth -top {top_module}"
        else:
            synth_part = "synth"
        script = (
            f"read_verilog {rtl_w}; {synth_part} ; "
            f"write_verilog {netlist_w}; stat -json"
        )

        # ── 4. 构造命令 + 跑 ───────────────────────────────────────────
        if wsl:
            cmd = [
                "wsl.exe", "-d", "Ubuntu-24.04", "-e",
                self.settings.eda.yosys_cmd, "-p", script,
            ]
        else:
            cmd = [self.settings.eda.yosys_cmd, "-p", script]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.eda.tool_timeout_s,
            )
        except subprocess.TimeoutExpired as e:
            duration_s = float(time.monotonic() - t0)
            return ToolResult(
                status="error",
                exit_code=None,
                stdout=e.stdout if isinstance(e.stdout, str) else "",
                stderr=e.stderr if isinstance(e.stderr, str) else "",
                parsed={
                    "_schema": {
                        "name": "yosys_synth",
                        "version": "0.1.0",
                        "contract_version": CONTRACT_VERSION,
                    },
                    "success": False,
                    "num_cells": 0,
                    "cell_area": None,
                    "num_wires": 0,
                    "num_ports": 0,
                    "module_name": "",
                    "script_used": script,
                    "warnings": [],
                    "errors": [f"timeout after {self.settings.eda.tool_timeout_s}s"],
                },
                artifacts=[],
                duration_s=duration_s,
                tool="yosys_synth",
                error_code=EDA_SUBPROCESS_TIMEOUT,
                error_hint=f"subprocess timeout after {self.settings.eda.tool_timeout_s}s",
            )

        duration_s = float(time.monotonic() - t0)
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        success = proc.returncode == 0

        # ── 5. 解析 stat-json ──────────────────────────────────────────
        stat_obj = _parse_stat_json(stdout)
        modules = stat_obj.get("modules", {}) if stat_obj else {}

        # 选 module key:top 优先(strip 反斜杠匹配),否则取第一个 key。
        chosen_key = ""
        if modules:
            if top_module:
                for k in modules.keys():
                    if _strip_yosys_id(k) == top_module:
                        chosen_key = k
                        break
            if not chosen_key:
                chosen_key = next(iter(modules.keys()))

        mod = modules.get(chosen_key, {}) if chosen_key else {}
        num_cells = int(mod.get("num_cells", 0))
        num_wires = int(mod.get("num_wires", 0))
        # num_ports 不在 stat-json 模块字段里 → 读 stdout 文本 "Number of ports:" 行,
        # 找不到保守置 0(Day0.5 gate 实测确认)。
        num_ports = 0
        m = re.search(r"Number of ports:\s*(\d+)", stdout)
        if m:
            num_ports = int(m.group(1))
        module_name = _strip_yosys_id(chosen_key) if chosen_key else ""

        # 把提取的 stat JSON 落 <work>/synth.json(供后续诊断/STA 复用)。
        if stat_obj is not None:
            try:
                (work_dir / "synth.json").write_text(
                    json.dumps(stat_obj, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass

        # ── 6. warnings / errors 行(从 stdout+stderr best-effort)
        combined = stdout + "\n" + stderr
        warnings = _WARN_RE.findall(combined)
        errors = _ERROR_RE.findall(combined)

        # ── 7. parsed ─────────────────────────────────────────────────
        parsed: dict[str, Any] = {
            "_schema": {
                "name": "yosys_synth",
                "version": "0.1.0",
                "contract_version": CONTRACT_VERSION,
            },
            "success": bool(success),
            "num_cells": num_cells,
            "cell_area": None,                  # 无 liberty 时 stat-json 无面积字段 → None
            "num_wires": num_wires,
            "num_ports": num_ports,
            "module_name": module_name,
            "script_used": script,
            "warnings": warnings,
            "errors": errors,
        }

        # ── 8. artifacts ──────────────────────────────────────────────
        artifacts = [
            artifact_ref(str(run_id), "synth/netlist.v"),
            artifact_ref(str(run_id), "synth/synth.json"),
        ]

        # ── 9. status / error_code ────────────────────────────────────
        if success:
            status = "ok"
            error_code: str | None = None
            error_hint: str | None = None
        else:
            status = "error"
            error_code = _SYNTH_FAILED_CODE  # synth namespace 已登记
            error_hint = "yosys synth failed (returncode!=0 or stat parse)"

        return ToolResult(
            status=status,
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            parsed=parsed,
            artifacts=artifacts,
            duration_s=duration_s,
            tool="yosys_synth",
            error_code=error_code,
            error_hint=error_hint,
        )
