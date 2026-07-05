"""iverilog_sim Tool —— iverilog 编译 + vvp 仿真(契约 §2.1 + §2.2)。

把 ``iverilog`` / ``vvp`` 二进制包成 ``Tool Protocol``。只产 ``ToolResult``,
不调 ``runner.append_step``(由 C 调用方负责,契约 §8)。

TB 打印协议(契约 §2.2):TB 结束时打印固定标记行(行首无空格,大小写敏感):
    TEST_PASS <n>/<total>      例如 TEST_PASS 3/5
    TEST_FAIL <signal>         每个失败信号一行,例如 TEST_FAIL sum_out
本 Tool 只解析这两类标记行。未打印 ``TEST_PASS`` 行 → 协议违反,
``num_passed`` / ``num_failed`` / ``passed`` 返回 None;``fail_signals`` 仍尽力解析。

跨 WSL 调用:``settings.eda.wsl_enabled=True`` 时命令前缀
``["wsl.exe","-d","Ubuntu-24.04","-e", "<tool>", ...]``,Windows 路径转 WSL 路径
(见 ``_to_wsl_path``)。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from eda_agent.contracts import (
    CONTRACT_VERSION,
    Tool,
    ToolCall,
    ToolResult,
    artifact_ref,
)
from eda_agent.errors import (
    EDA_SUBPROCESS_FAILED,
    EDA_SUBPROCESS_TIMEOUT,
    EDA_TOOL_ARGS_INVALID,
)
from eda_agent.settings import Settings

# vvp stdout 末尾裁剪长度(契约 §2.2 "vvp 输出最后 2KB",这里取 2048 字符)。
_VVP_TAIL_LEN = 2048

# TB 协议标记行正则(契约 §2.2:行首无空格,大小写敏感)。
#   TEST_PASS <n>/<total>   → group "passed" / "total"
#   TEST_FAIL <signal>      → group "signal"
_TEST_PASS_RE = re.compile(r"^TEST_PASS\s+(\d+)\s*/\s*(\d+)\s*$", re.MULTILINE)
_TEST_FAIL_RE = re.compile(r"^TEST_FAIL\s+(\S+)\s*$", re.MULTILINE)


def _to_wsl_path(win_path: str) -> str:
    """Windows 路径 → WSL 路径(契约 Day0.5 gate C)。

    ``D:\\x\\y`` → ``/mnt/d/x/y``(盘符小写,去冒号,反斜杠转正斜杠)。
    相对路径或无盘符路径原样返回(WSL 在其 cwd 下解析)。
    """
    p = win_path.replace("\\", "/")
    if len(p) >= 2 and p[1] == ":" and p[0].isalpha():
        drive = p[0].lower()
        return "/mnt/" + drive + p[2:]
    return p


def _coerce(s: Any) -> str:
    """把 TimeoutExpired 残留的 stdout/stderr(bytes 或 str)安全转成 str。

    Windows 默认 ANSI(gbk)解码会爆;统一 utf-8 + replace,容错。
    """
    if s is None:
        return ""
    if isinstance(s, bytes):
        return s.decode("utf-8", errors="replace")
    return s


def _parse_tb_protocol(text: str) -> dict[str, Any]:
    """解析 TB 打印协议标记行(契约 §2.2)。

    输出字段:
        num_passed:   int | None  —— TEST_PASS 行的 n;无该行 → None
        total:        int | None  —— TEST_PASS 行的 total;无该行 → None
        num_failed:   int | None  —— total - num_passed;无 TEST_PASS 行 → None
        passed:       bool | None —— num_passed>0 且 num_failed==0;无 TEST_PASS 行 → None
        fail_signals: list[str]   —— 所有 TEST_FAIL 行的 signal(可能为空)

    多个 TEST_PASS 行时取第一个(协议约定结束打印一次,容错)。
    """
    fail_signals: list[str] = _TEST_FAIL_RE.findall(text)

    m = _TEST_PASS_RE.search(text)
    if m is None:
        return {
            "num_passed": None,
            "total": None,
            "num_failed": None,
            "passed": None,
            "fail_signals": fail_signals,
        }

    num_passed = int(m.group(1))
    total = int(m.group(2))
    num_failed = total - num_passed
    passed = num_passed > 0 and num_failed == 0
    return {
        "num_passed": num_passed,
        "total": total,
        "num_failed": num_failed,
        "passed": passed,
        "fail_signals": fail_signals,
    }


class IverilogSimTool:
    """iverilog 编译 + vvp 仿真,解析 TB 打印协议 TEST_PASS / TEST_FAIL。"""

    name = "iverilog_sim"
    description = (
        "iverilog 编译 + vvp 仿真,解析 TB 打印协议 TEST_PASS/TEST_FAIL"
    )
    schema: dict[str, Any] = {
        "name": "iverilog_sim",
        "description": description,
        "parsed": {
            "type": "object",
            "properties": {
                "compiled": {"type": "boolean"},
                "passed": {"type": ["boolean", "null"]},
                "num_passed": {"type": ["integer", "null"]},
                "num_failed": {"type": ["integer", "null"]},
                "fail_signals": {"type": "array", "items": {"type": "string"}},
                "vvp_stdout_tail": {"type": "string"},
            },
        },
        "input_schema": {
            "type": "object",
            "properties": {
                "rtl": {"type": "string"},
                "tb": {"type": "string"},
                "top_module": {"type": "string"},
                "run_id": {"type": "string"},
            },
            "required": ["rtl", "tb"],
        },
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def __call__(self, call: ToolCall) -> ToolResult:
        args = call.args
        rtl: str = args.get("rtl", "") or ""
        tb: str = args.get("tb", "") or ""
        run_id: str = args.get("run_id", "run") or "run"

        tool = self.name
        # ── 1. 参数校验:rtl/tb 空 → eda.tool_args_invalid ──────────────
        if not rtl or not tb:
            return ToolResult(
                status="error",
                exit_code=None,
                stdout="",
                stderr="",
                parsed={
                    "_schema": {
                        "name": tool,
                        "version": "0.1.0",
                        "contract_version": CONTRACT_VERSION,
                    },
                    "compiled": False,
                    "passed": None,
                    "num_passed": None,
                    "num_failed": None,
                    "fail_signals": [],
                    "vvp_stdout_tail": "",
                },
                artifacts=[artifact_ref(run_id, "sim/wave.vcd")],
                duration_s=0.0,
                tool=tool,
                error_code=EDA_TOOL_ARGS_INVALID,
                error_hint="rtl/tb 不能为空",
            )

        wsl = bool(self.settings.eda.wsl_enabled)
        timeout = self.settings.eda.tool_timeout_s

        # ── 2. 工作目录 + 路径转换 ─────────────────────────────────────
        work = Path("runs") / run_id / "sim"
        work.mkdir(parents=True, exist_ok=True)
        vvp_path = (work / "sim.vvp").as_posix()
        vvp_wsl = _to_wsl_path(vvp_path)
        rtl_wsl = _to_wsl_path(rtl)
        tb_wsl = _to_wsl_path(tb)

        compiled: bool = False
        last_exit: int | None = None
        compile_stdout = compile_stderr = ""
        vvp_stdout = vvp_stderr = ""
        error_code: str | None = None
        error_hint: str | None = None
        status: str = "ok"
        timed_out = False

        # ── 3. 编译:iverilog -o sim.vvp <rtl> <tb> ─────────────────────
        if wsl:
            cmd = [
                "wsl.exe", "-d", "Ubuntu-24.04", "-e",
                self.settings.eda.iverilog_cmd, "-o", vvp_wsl, rtl_wsl, tb_wsl,
            ]
        else:
            cmd = [self.settings.eda.iverilog_cmd, "-o", vvp_path, rtl, tb]
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
            compile_stdout = r.stdout or ""
            compile_stderr = r.stderr or ""
            last_exit = r.returncode
            compiled = (r.returncode == 0)
        except subprocess.TimeoutExpired as e:
            timed_out = True
            compile_stdout = _coerce(e.stdout)
            compile_stderr = _coerce(e.stderr) + \
                f"\n[iverilog compile timed out after {timeout}s]"
            last_exit = None
            error_code = EDA_SUBPROCESS_TIMEOUT
            error_hint = f"iverilog 编译超时({timeout}s)"
            status = "timeout"
        except OSError as e:
            last_exit = None
            compile_stderr = f"\n[iverilog 调起失败: {e}]"
            error_code = EDA_SUBPROCESS_FAILED
            error_hint = f"iverilog 子进程异常: {e}"
            status = "error"

        # ── 4. 仿真:仅 compiled 时跑 vvp sim.vvp ──────────────────────
        if compiled and not timed_out:
            if wsl:
                cmd2 = [
                    "wsl.exe", "-d", "Ubuntu-24.04", "-e",
                    self.settings.eda.vvp_cmd, vvp_wsl,
                ]
            else:
                cmd2 = [self.settings.eda.vvp_cmd, vvp_path]
            try:
                r2 = subprocess.run(
                    cmd2, capture_output=True, text=True, timeout=timeout,
                    encoding="utf-8", errors="replace",
                )
                vvp_stdout = r2.stdout or ""
                vvp_stderr = r2.stderr or ""
                last_exit = r2.returncode
            except subprocess.TimeoutExpired as e:
                timed_out = True
                vvp_stdout = _coerce(e.stdout)
                vvp_stderr = _coerce(e.stderr) + \
                    f"\n[vvp timed out after {timeout}s]"
                last_exit = None
                error_code = EDA_SUBPROCESS_TIMEOUT
                error_hint = f"vvp 仿真超时({timeout}s)"
                status = "timeout"
            except OSError as e:
                last_exit = None
                vvp_stderr = f"\n[vvp 调起失败: {e}]"
                error_code = EDA_SUBPROCESS_FAILED
                error_hint = f"vvp 子进程异常: {e}"
                status = "error"

        # ── 5. 解析 TB 协议标记行(仅编译成功且 vvp 未超时跑完) ──────────
        if compiled and not timed_out and status != "timeout" and status != "error":
            parsed_proto = _parse_tb_protocol(vvp_stdout)
        else:
            # 编译失败 / 超时 / 子进程异常 → 未跑出可用仿真输出,passed 等留 None
            parsed_proto = {
                "num_passed": None,
                "total": None,
                "num_failed": None,
                "passed": None,
                "fail_signals": [],
            }

        vvp_tail = vvp_stdout[-_VVP_TAIL_LEN:] if vvp_stdout else ""

        # ── 6/7/8. 组装 ToolResult ─────────────────────────────────────
        # exit_code: 最后一个子进程 returncode;编译失败取编译码(可能 None=超时)。
        stdout = compile_stdout + ("\n--- vvp stdout ---\n" + vvp_stdout if vvp_stdout else "")
        stderr = compile_stderr + ("\n--- vvp stderr ---\n" + vvp_stderr if vvp_stderr else "")

        parsed: dict[str, Any] = {
            "_schema": {
                "name": tool,
                "version": "0.1.0",
                "contract_version": CONTRACT_VERSION,
            },
            "compiled": compiled,
            "passed": parsed_proto["passed"],
            "num_passed": parsed_proto["num_passed"],
            "num_failed": parsed_proto["num_failed"],
            "fail_signals": parsed_proto["fail_signals"],
            "vvp_stdout_tail": vvp_tail,
        }

        return ToolResult(
            status=status,  # type: ignore[arg-type]
            exit_code=last_exit,
            stdout=stdout,
            stderr=stderr,
            parsed=parsed,
            artifacts=[artifact_ref(run_id, "sim/wave.vcd")],
            duration_s=0.0,
            tool=tool,
            error_code=error_code,
            error_hint=error_hint,
        )


# Tool Protocol(runtime_checkable)要求实例具备 name/description/schema/__call__;
# IverilogSimTool 满足,可在 ``isinstance(x, Tool)`` 校验中通过。
assert isinstance(IverilogSimTool(Settings()), Tool)
