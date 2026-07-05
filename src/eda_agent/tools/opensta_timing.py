"""OpenSTATimingTool —— OpenSTA 时序分析(契约 §2.2 行 242-252 + §2.1)。

把 ``sta -f script.tcl`` 包成 ``Tool Protocol``。只产 ``ToolResult``,不调
``runner.append_step``(由 C 调用方负责,契约 §8)。

STA TCL 脚本(Day0.5 gate 文档,真跑搁置):
    read_liberty <lib>;
    read_verilog <rtl>;
    link [<top>];
    create_clock <name>;              # clock_name 非空时
    report_checks -fields {slew cap arc} -format full_clock;
    report_wns;
    report_tns;
    report_check_types -max_slew -max_cap -max_fanout;

OpenSTA 当前未装 → ``subprocess.run`` 抛 ``FileNotFoundError`` 或 ``returncode != 0``,
这是预期;真跑测试 skip 或验证优雅 error(见 tests/test_opensta_tool.py)。

跨 WSL 调用:``settings.eda.wsl_enabled=True`` 时命令前缀
``["wsl.exe","-d","Ubuntu-24.04","-e", "sta", "-f", <tcl_wsl>]``,Windows 路径转 WSL 路径
(见 ``_to_wsl_path``,与 yosys_synth/iverilog_sim 同款)。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from eda_agent.contracts import (
    CONTRACT_VERSION,
    ToolCall,
    ToolResult,
    artifact_ref,
)
from eda_agent.errors import (
    EDA_SUBPROCESS_TIMEOUT,
    EDA_TOOL_ARGS_INVALID,
)
from eda_agent.settings import Settings

# sta namespace 已登记(errors.NAMESPACES),用规范二段式码(优先于 eda.* 别名)。
_STA_UNAVAILABLE_CODE = "sta.sta_unavailable"   # sta 二进制缺失(FileNotFoundError)
_STA_FAILED_CODE = "sta.sta_failed"             # sta 跑了但 returncode != 0
_STA_NO_LIBERTY_CODE = "sta.no_liberty"         # 无 liberty,无法做 STA

# ── 解析正则(从 sta stdout / timing.rpt best-effort 提取) ──────────────
# report_wns / report_tns 输出形如 "wns -0.42" 或独立行 "-0.42";兼容 "wns  -0.5"。
_WNS_RE = re.compile(r"wns\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
_TNS_RE = re.compile(r"tns\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
# report_checks 块:Slack 行 + endpoint 行(VIOLATED 标记)。
_SLACK_RE = re.compile(r"Slack\s*:\s*(-?\d+(?:\.\d+)?)\s*\(VIOLATED\)", re.IGNORECASE)
# report_checks 的 path / endpoint 行(best-effort,容忍多空格)。
_ENDPOINT_RE = re.compile(r"endpoint\s*[:=]?\s*(\S+)", re.IGNORECASE)
# critical path delay(ns):report_checks 行如 "  data arrival time  -0.5" 或 path delay 行。
_PATH_DELAY_RE = re.compile(
    r"(?:data arrival time|path delay|data required time)\s+(-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_VIOLATED_RE = re.compile(r"\bVIOLATED\b", re.IGNORECASE)


def _to_wsl_path(win_path: str) -> str:
    """Windows 路径 → WSL 路径(对齐 yosys_synth/iverilog_sim 的同款辅助)。

    ``D:\\x\\y`` → ``/mnt/d/x/y``(盘符小写,去冒号,反斜杠转正斜杠)。
    相对路径或无盘符路径原样返回。
    """
    p = win_path.strip()
    if len(p) >= 2 and p[1] == ":" and p[0].isalpha():
        drive = p[0].lower()
        rest = p[2:].replace("\\", "/")
        if not rest.startswith("/"):
            rest = "/" + rest
        return f"/mnt/{drive}{rest}"
    return p.replace("\\", "/")


def _parse_sta_report(text: str) -> dict[str, Any]:
    """从 OpenSTA stdout / timing.rpt best-effort 解析时序指标(模块级,便于单测)。

    输出字段(找不到时 None / [],不抛):
        wns:                       float | None  —— 最差负松弛
        tns:                       float | None  —— 总负松弛
        num_violating_endpoints:   int           —— VIOLATED 出现次数(0 表示无)
        critical_path_delay_ns:    float | None  —— 关键路径延迟(ns)
        violations:                list[dict]    —— top 10,每条含
            {"endpoint": str, "slack_ns": float | None, "path": str}

    解析失败 / 空文本 → 各字段 None / [] ,绝不抛异常。
    """
    out: dict[str, Any] = {
        "wns": None,
        "tns": None,
        "num_violating_endpoints": 0,
        "critical_path_delay_ns": None,
        "violations": [],
    }
    if not text:
        return out

    # ── wns / tns(取首个匹配;report_wns/report_tns 只输出一个 float)
    m_w = _WNS_RE.search(text)
    if m_w:
        try:
            out["wns"] = float(m_w.group(1))
        except (TypeError, ValueError):
            out["wns"] = None
    m_t = _TNS_RE.search(text)
    if m_t:
        try:
            out["tns"] = float(m_t.group(1))
        except (TypeError, ValueError):
            out["tns"] = None

    # ── num_violating_endpoints:数 VIOLATED 出现次数(等价于违例端点数)
    out["num_violating_endpoints"] = len(_VIOLATED_RE.findall(text))

    # ── critical_path_delay_ns:取首个 path delay 行
    m_pd = _PATH_DELAY_RE.search(text)
    if m_pd:
        try:
            out["critical_path_delay_ns"] = float(m_pd.group(1))
        except (TypeError, ValueError):
            out["critical_path_delay_ns"] = None

    # ── violations:每条 Slack (VIOLATED) 行配一个 endpoint(best-effort)
    slack_hits = list(_SLACK_RE.finditer(text))
    endpoint_hits = list(_ENDPOINT_RE.finditer(text))
    violations: list[dict[str, Any]] = []
    for i, sh in enumerate(slack_hits[:10]):
        try:
            slack = float(sh.group(1))
        except (TypeError, ValueError):
            slack = None
        endpoint = endpoint_hits[i].group(1) if i < len(endpoint_hits) else ""
        # path:取 Slack 行附近一小段文本作为定位(best-effort,容忍格式多变)
        start = max(0, sh.start() - 80)
        end = min(len(text), sh.end() + 80)
        violations.append({
            "endpoint": endpoint,
            "slack_ns": slack,
            "path": text[start:end].strip(),
        })
    out["violations"] = violations

    return out


class OpenSTATimingTool:
    """``Tool`` Protocol 实现:跑 OpenSTA,解析 wns/tns/violations/critical path。"""

    name = "opensta_timing"
    description = (
        "OpenSTA 时序分析,解析 wns/tns/violations/critical path"
    )
    schema: dict[str, Any] = {
        "name": "opensta_timing",
        "description": description,
        "parsed": {
            "type": "object",
            "properties": {
                "wns": {"type": ["number", "null"]},
                "tns": {"type": ["number", "null"]},
                "num_violating_endpoints": {"type": "integer"},
                "critical_path_delay_ns": {"type": ["number", "null"]},
                "clock_name": {"type": ["string", "null"]},
                "violations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "endpoint": {"type": "string"},
                            "slack_ns": {"type": ["number", "null"]},
                            "path": {"type": "string"},
                        },
                    },
                },
                "script_used": {"type": "string"},
            },
        },
        "input_schema": {
            "type": "object",
            "properties": {
                "rtl": {"type": "string"},
                "lib": {"type": "string"},
                "clock_name": {"type": "string"},
                "top_module": {"type": "string"},
                "run_id": {"type": "string"},
            },
            "required": ["rtl"],
        },
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def __call__(self, call: ToolCall) -> ToolResult:
        tool = self.name
        args = call.args
        rtl: str = args.get("rtl", "") or ""
        lib: str | None = args.get("lib") or self.settings.eda.default_lib_path
        clock_name: str | None = args.get("clock_name") or None
        top_module: str | None = args.get("top_module") or None
        run_id: str = args.get("run_id", "run") or "run"

        # ── 1. 参数校验:rtl 空 → eda.tool_args_invalid ─────────────────
        if not rtl:
            return ToolResult(
                status="error",
                exit_code=None,
                stdout="",
                stderr="",
                parsed=self._empty_parsed(
                    clock_name=clock_name, script_str="",
                ),
                artifacts=[artifact_ref(run_id, "sta/timing.rpt")],
                duration_s=0.0,
                tool=tool,
                error_code=EDA_TOOL_ARGS_INVALID,
                error_hint="rtl required",
            )

        # ── 2. lib 缺失 → sta.no_liberty(无法做 STA)──────────────────
        if not lib:
            return ToolResult(
                status="error",
                exit_code=None,
                stdout="",
                stderr="",
                parsed=self._empty_parsed(
                    clock_name=clock_name, script_str="",
                ),
                artifacts=[artifact_ref(run_id, "sta/timing.rpt")],
                duration_s=0.0,
                tool=tool,
                error_code=_STA_NO_LIBERTY_CODE,
                error_hint="liberty (.lib) required for STA",
            )

        # ── 3. 工作目录 + 跨 WSL 路径转换 ──────────────────────────────
        wsl = bool(self.settings.eda.wsl_enabled)
        work = Path("runs") / run_id / "sta"
        work.mkdir(parents=True, exist_ok=True)
        tcl_path = work / "sta.tcl"
        rpt_path = work / "timing.rpt"

        rtl_w = _to_wsl_path(str(rtl)) if wsl else str(rtl)
        lib_w = _to_wsl_path(str(lib)) if wsl else str(lib)
        tcl_w = _to_wsl_path(tcl_path.as_posix()) if wsl else tcl_path.as_posix()

        # ── 4. 构造 OpenSTA TCL 脚本(写到 work/sta.tcl)────────────────
        lines: list[str] = []
        lines.append(f"read_liberty {lib_w}")
        lines.append(f"read_verilog {rtl_w}")
        lines.append(f"link {top_module}" if top_module else "link")
        if clock_name:
            lines.append(f"create_clock {clock_name}")
        lines.append("report_checks -fields {slew cap arc} -format full_clock")
        lines.append("report_wns")
        lines.append("report_tns")
        lines.append("report_check_types -max_slew -max_cap -max_fanout")
        script_str = "\n".join(lines) + "\n"
        tcl_path.write_text(script_str, encoding="utf-8")

        # ── 5. 构造命令 + 跑 ────────────────────────────────────────────
        if wsl:
            cmd = [
                "wsl.exe", "-d", "Ubuntu-24.04", "-e",
                self.settings.eda.opensta_cmd, "-f", tcl_w,
            ]
        else:
            cmd = [self.settings.eda.opensta_cmd, "-f", tcl_path.as_posix()]

        timeout = self.settings.eda.tool_timeout_s
        stdout = ""
        stderr = ""
        exit_code: int | None = None
        status = "ok"
        error_code: str | None = None
        error_hint: str | None = None

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            exit_code = proc.returncode
            if proc.returncode != 0:
                status = "error"
                error_code = _STA_FAILED_CODE
                error_hint = (
                    f"sta failed (returncode={proc.returncode})"
                )
        except subprocess.TimeoutExpired as e:
            status = "timeout"
            error_code = EDA_SUBPROCESS_TIMEOUT
            error_hint = f"sta subprocess timeout after {timeout}s"
            if isinstance(e.stdout, str):
                stdout = e.stdout
            if isinstance(e.stderr, str):
                stderr = e.stderr
            exit_code = None
        except FileNotFoundError:
            # sta 二进制缺失(wsl.exe 透传 -e sta 不存在,或本地无 sta)→ 优雅 error
            status = "error"
            error_code = _STA_UNAVAILABLE_CODE
            error_hint = "sta binary not found (OpenSTA 未安装)"
            exit_code = None

        # ── 6. 解析(sta 跑过或失败都尽力;stdout 为空时各字段 None/[])
        parsed_proto = _parse_sta_report(stdout)

        # ── 7. timing.rpt 落地(若跑过 sta)────────────────────────────
        if stdout:
            try:
                rpt_path.write_text(stdout, encoding="utf-8")
            except OSError:
                pass

        # ── 8. parsed ──────────────────────────────────────────────────
        parsed: dict[str, Any] = {
            "_schema": {
                "name": tool,
                "version": "0.1.0",
                "contract_version": CONTRACT_VERSION,
            },
            "wns": parsed_proto["wns"],
            "tns": parsed_proto["tns"],
            "num_violating_endpoints": parsed_proto["num_violating_endpoints"],
            "critical_path_delay_ns": parsed_proto["critical_path_delay_ns"],
            "clock_name": clock_name,
            "violations": parsed_proto["violations"],
            "script_used": script_str,
        }

        # ── 9. status 微调:解析全 None 但 sta returncode==0 仍 ok(契约允许 None)
        return ToolResult(
            status=status,  # type: ignore[arg-type]
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            parsed=parsed,
            artifacts=[artifact_ref(run_id, "sta/timing.rpt")],
            duration_s=0.0,
            tool=tool,
            error_code=error_code,
            error_hint=error_hint,
        )

    @staticmethod
    def _empty_parsed(
        *, clock_name: str | None, script_str: str,
    ) -> dict[str, Any]:
        """参数校验失败时构造的空 parsed(仍带 _schema)。"""
        return {
            "_schema": {
                "name": "opensta_timing",
                "version": "0.1.0",
                "contract_version": CONTRACT_VERSION,
            },
            "wns": None,
            "tns": None,
            "num_violating_endpoints": 0,
            "critical_path_delay_ns": None,
            "clock_name": clock_name,
            "violations": [],
            "script_used": script_str,
        }
