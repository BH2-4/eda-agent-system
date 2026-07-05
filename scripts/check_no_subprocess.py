"""检查 planner/ 与 cli.py 无 subprocess 调用(C2)+ 无重定义契约类型(C1)。

ast.walk(非 grep),跨平台。Phase4 的 C 写完后此脚本是 C1/C2 的自动化验收:
    python scripts/check_no_subprocess.py [project_root]

退出码 0=干净,1=发现违规。Phase1 时 planner/ 与 cli.py 尚不存在,优雅退出 0。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# C1:这些契约类型由 eda_agent.contracts / eda_agent.errors 权威定义,
# planner/ 与 cli.py 不得重新定义同名 class。
CONTRACT_TYPES = {
    "ToolCall", "ToolResult", "Tool",
    "SkillResult", "Skill",
    "RunRequest", "RunReport", "RunRecord", "StepRecord",
    "Message", "LLMResponse", "LLMProvider",
    "ErrorItem",
}

# C2:这些是 subprocess 模块里发起子进程的 API(读常量如 PIPE 也算泄漏)。
SUBPROCESS_ATTRS = {"run", "Popen", "call", "check_call", "check_output",
                    "PIPE", "STDOUT", "TimeoutExpired"}


def _walk(tree: ast.AST, filename: str) -> list[str]:
    problems: list[str] = []
    for node in ast.walk(tree):
        # C2: import subprocess / from subprocess import ...
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    problems.append(f"{filename}:{node.lineno}: C2 `import subprocess`")
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            problems.append(f"{filename}:{node.lineno}: C2 `from subprocess import ...`")
        # C2: subprocess.<api>(...) 调用
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess":
                if node.func.attr in SUBPROCESS_ATTRS:
                    problems.append(
                        f"{filename}:{node.lineno}: C2 subprocess.{node.func.attr}() call"
                    )
        # C1: class <ContractType>
        if isinstance(node, ast.ClassDef) and node.name in CONTRACT_TYPES:
            problems.append(f"{filename}:{node.lineno}: C1 redefines contract type `{node.name}`")
    return problems


def check_file(path: Path) -> list[str]:
    if not path.exists() or path.name == "__init__.py":
        return []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return _walk(tree, str(path))


def main(root: str = ".") -> int:
    pkg = Path(root) / "src" / "eda_agent"
    targets: list[Path] = []
    planner = pkg / "planner"
    if planner.is_dir():
        targets.extend(sorted(planner.glob("*.py")))
    cli = pkg / "cli.py"
    if cli.exists():
        targets.append(cli)

    if not targets:
        print("OK: planner/ and cli.py not yet present; nothing to check (Phase1).")
        return 0

    all_problems: list[str] = []
    for t in targets:
        all_problems.extend(check_file(t))

    if all_problems:
        print("FAIL: contract violations found:")
        for p in all_problems:
            print("  " + p)
        return 1

    print(f"OK: scanned {len(targets)} file(s); "
          f"no subprocess calls, no redefined contract types.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
