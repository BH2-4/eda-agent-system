#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EDa Agent System 项目门禁脚本 (G1-G5).

按 .zread/_congress_result.md 的 gate_adjustments 实现。
- 单跑: python scripts/gates/check_integrity.py --gate G1
- 全跑: python scripts/gates/check_integrity.py
- 输出 JSON 到 stdout, 任意 fail 返回非 0 退出码。

脚本顶部自动 cd 到项目根 (由 __file__ 推导)。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

# ---------------------------------------------------------------------------
# 切到项目根 (脚本位于 <root>/scripts/gates/check_integrity.py)
# ---------------------------------------------------------------------------
_SCRIPT_PATH = Path(__file__).resolve()
_PROJECT_ROOT = _SCRIPT_PATH.parents[2]
os.chdir(_PROJECT_ROOT)

# 通用常量
WIKI_DIR = _PROJECT_ROOT / "docs" / "wiki"
README_PATH = _PROJECT_ROOT / "README.md"

ALLOWED_M = {"README.md", ".gitignore", ".gitattributes"}
NUMERED_RE = re.compile(r"^[0-9]{2}_.+\.md$")
INDEX_NAME = "INDEX.md"
INDEX_WHITELIST = {INDEX_NAME}

# 旧 slug 残留模式 (无 .md 后缀的 N-slug 互链)
OLD_SLUG_RE = re.compile(r"\]\([0-9]+-[a-z0-9_-]+")

# markdown 链接正则
MD_LINK_RE = re.compile(r"\]\(([^)]+)\)")

# G3 关键词集(技术词)
G3_KEYWORDS = {
    "CONTRACT_VERSION",
    "eda self-heal",
    "eval_snapshot",
    "Phase",
    "artifact_ref",
    "as_tool",
    "baseline",
    "CONTRACTS.md",
}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
def _run(cmd, **kwargs):
    """跑子进程 (list 形式, 不用 shell). 返回 CompletedProcess.

    对 git 命令统一注入 -c core.quotepath=false, 强制中文路径原样 UTF-8 输出,
    避免 porcelain/diff 把 docs/wiki/NN_中文名.md 转义成八进制导致 G1 路径前缀
    断言 (startswith("docs/wiki/")) 失败.
    """
    if cmd and cmd[0] == "git":
        cmd = ["git", "-c", "core.quotepath=false"] + cmd[1:]
    kwargs.setdefault("cwd", str(_PROJECT_ROOT))
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    return subprocess.run(cmd, **kwargs)


def _result(name, ok, detail=None, error=None):
    return {
        "gate": name,
        "ok": bool(ok),
        "detail": detail or {},
        "error": error,
    }


# ---------------------------------------------------------------------------
# G1: git 仓库状态守卫
# ---------------------------------------------------------------------------
def gate_g1():
    """仓库清洁度门禁(通用版).

    前置: git check-ignore .zread exit=0 (命中 ignore).
    断言: 暂存区不含 .zread/ 路径.
    (历史版本曾锁死文件增删改类型,仅允许改 README;该约束为早期
    提交期专用,普通开源项目不应限制文件改动,故移除。文件内容质量由 G2/G3 与
    review 流程保证。)
    """
    # 前置断言: .zread 必须被 ignore
    pre = _run(["git", "check-ignore", ".zread"])
    if pre.returncode != 0:
        return _result(
            "G1", False,
            error="前置断言失败: git check-ignore .zread 未命中 (exit!=0). "
                  "请先在 .gitignore 添加 .zread/ 行.",
        )

    issues = []

    # ---- 断言: 暂存区不含 .zread/ ----
    cached = _run(["git", "diff", "--cached", "--name-only"])
    if cached.returncode != 0:
        return _result("G1", False,
                       error=f"git diff --cached 失败: {cached.stderr.strip()}")
    for p in cached.stdout.splitlines():
        norm = p.replace("\\", "/")
        if norm.startswith(".zread/"):
            issues.append(f"暂存区含 .zread/ 路径: {p}")

    ok = not issues
    return _result("G1", ok, detail={"issues": issues} if issues else {})


# ---------------------------------------------------------------------------
# G2: wiki 文件名 / 内链 / 双向一致性
# ---------------------------------------------------------------------------
def _list_wiki_md():
    """返回 docs/wiki/ 下所有 .md 文件名 (不含路径)."""
    if not WIKI_DIR.exists():
        return []
    return sorted(f.name for f in WIKI_DIR.iterdir()
                  if f.is_file() and f.suffix == ".md")


def gate_g2():
    """Wiki 文件结构门禁."""
    issues = []

    files = _list_wiki_md()
    numbered = [f for f in files if NUMERED_RE.match(f)]
    index_n = [f for f in files if f in INDEX_WHITELIST]
    others = [f for f in files if f not in numbered and f not in index_n]

    # 文件数断言
    if len(numbered) != 31:
        issues.append(f"编号页文件数 != 31 (实际 {len(numbered)}): {numbered}")
    if len(index_n) != 1:
        issues.append(f"INDEX.md 文件数 != 1 (实际 {len(index_n)}): {index_n}")
    if len(files) != 32:
        issues.append(f"docs/wiki/ 总 .md 数 != 32 (实际 {len(files)})")
    if others:
        issues.append(f"docs/wiki/ 存在非法命名文件: {others}")

    # 旧 slug 残留必须为 0
    if WIKI_DIR.exists():
        stale = []
        for f in WIKI_DIR.iterdir():
            if not (f.is_file() and f.suffix == ".md"):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in OLD_SLUG_RE.finditer(text):
                stale.append({"file": f.name, "match": m.group(0)})
        if stale:
            issues.append(f"旧 slug 残留 {len(stale)} 处 (应为 0): "
                          f"{stale[:5]}{'...' if len(stale) > 5 else ''}")

    # 内链校验 (两类) + INDEX 双向一致性
    disk_set = set(files)

    # ---- INDEX 双向一致性 ----
    index_path = WIKI_DIR / INDEX_NAME
    index_links = set()
    if index_path.exists():
        try:
            idx_text = index_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            idx_text = ""
        for raw in MD_LINK_RE.findall(idx_text):
            target = unquote(raw).strip()
            # 剥 fragment
            target = target.split("#", 1)[0]
            if not target:
                continue
            # 取 basename (避免 ./xx.md 或 docs/wiki/xx.md 形式干扰)
            base = os.path.basename(target.replace("\\", "/"))
            if NUMERED_RE.match(base) or base in INDEX_WHITELIST:
                index_links.add(base)
    else:
        issues.append("INDEX.md 不存在")

    expected_numbered = set(numbered)
    if index_links != expected_numbered:
        missing = expected_numbered - index_links
        extra = index_links - expected_numbered
        if missing:
            issues.append(f"INDEX 缺失条目 (磁盘有但 INDEX 未列): "
                          f"{sorted(missing)}")
        if extra:
            issues.append(f"INDEX 多余条目 (INDEX 列但磁盘无): {sorted(extra)}")
    if len(index_links) != 31 and expected_numbered:
        issues.append(f"INDEX 条目数 != 31 (实际 {len(index_links)})")

    # ---- 两类内链校验 ----
    dead_links = []
    if WIKI_DIR.exists():
        for f in WIKI_DIR.iterdir():
            if not (f.is_file() and f.suffix == ".md"):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for raw in MD_LINK_RE.findall(text):
                target = unquote(raw).strip()
                if not target:
                    continue
                # 跳过 http(s)/mailto/锚点
                low = target.lower()
                if low.startswith(("http://", "https://", "mailto:")):
                    continue
                if target.startswith("#"):
                    continue
                # 剥 fragment
                path_part = target.split("#", 1)[0]
                if not path_part:
                    continue

                # 类1: 页面间互链 (无 / 或 basename 在 wiki 目录)
                if "/" not in path_part:
                    # 形如 NN_中文名.md 或 INDEX.md
                    if path_part.endswith(".md"):
                        if path_part not in disk_set:
                            dead_links.append({"file": f.name,
                                               "target": target})
                    else:
                        # 无 .md 后缀的旧 slug (理论上 G2 旧 slug 检查已捕获)
                        # 防御性也作为死链
                        candidate = path_part + ".md"
                        if candidate not in disk_set:
                            dead_links.append({"file": f.name,
                                               "target": target})
                    continue

                # 类2: 顶层引用 ../../x.md 或 ../x.md
                resolved = os.path.normpath(
                    os.path.join(str(WIKI_DIR), path_part))
                if not os.path.exists(resolved):
                    dead_links.append({"file": f.name, "target": target,
                                       "resolved": resolved})

    if dead_links:
        issues.append(f"死链 {len(dead_links)} 处: "
                      f"{dead_links[:5]}{'...' if len(dead_links) > 5 else ''}")

    ok = not issues
    return _result("G2", ok,
                   detail={"issues": issues,
                           "counts": {
                               "numbered": len(numbered),
                               "index": len(index_n),
                               "total": len(files),
                               "index_links": len(index_links),
                               "dead_links": len(dead_links),
                           }})


# ---------------------------------------------------------------------------
# G3: README mermaid / 关键词 / docs/wiki/ 链接匹配
# ---------------------------------------------------------------------------
_MERMAID_FENCE_RE = re.compile(
    r"```mermaid\s*\n(.*?)\n```", re.DOTALL)


def gate_g3():
    """README 内容门禁.

    - mermaid >= 3 且含 1 stateDiagram + 1 (graph|flowchart block) + 1 数据流
      (sequenceDiagram / flowchart TD).
    - 关键词集全覆盖.
    - README 内 docs/wiki/ 链接目标与实际文件名精确匹配.
    """
    issues = []

    if not README_PATH.exists():
        return _result("G3", False, error="README.md 不存在")
    try:
        readme = README_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return _result("G3", False, error=f"读取 README 失败: {e}")

    # ---- mermaid 校验 ----
    mermaid_blocks = _MERMAID_FENCE_RE.findall(readme)
    has_state = any(re.search(r"\bstateDiagram", b) for b in mermaid_blocks)
    has_block = any(re.search(r"\b(?:graph|flowchart)\b", b)
                    for b in mermaid_blocks)
    has_dataflow = any(
        re.search(r"\bsequenceDiagram\b", b) or
        re.search(r"\bflowchart\s+TD\b", b)
        for b in mermaid_blocks
    )
    if len(mermaid_blocks) < 3:
        issues.append(f"mermaid 块数 < 3 (实际 {len(mermaid_blocks)})")
    if not has_state:
        issues.append("缺 stateDiagram (五相状态机)")
    if not has_block:
        issues.append("缺 graph/flowchart (架构 block 图)")
    if not has_dataflow:
        issues.append("缺 数据流图 (sequenceDiagram 或 flowchart TD)")

    # ---- 关键词 ----
    missing_kw = sorted(k for k in G3_KEYWORDS if k not in readme)
    if missing_kw:
        issues.append(f"README 缺关键词: {missing_kw}")

    # ---- docs/wiki/ 链接目标与实际文件名精确匹配 ----
    disk_set = set(_list_wiki_md())
    mismatches = []
    for raw in MD_LINK_RE.findall(readme):
        target = unquote(raw).strip()
        low = target.lower()
        if low.startswith(("http://", "https://", "mailto:")):
            continue
        path_part = target.split("#", 1)[0]
        if not path_part.startswith("docs/wiki/"):
            continue
        base = os.path.basename(path_part.replace("\\", "/"))
        if not base:
            continue
        if disk_set and base not in disk_set:
            mismatches.append({"target": target, "basename": base})

    if mismatches:
        issues.append(f"README docs/wiki/ 链接不匹配 {len(mismatches)} 处: "
                      f"{mismatches[:5]}")

    ok = not issues
    return _result("G3", ok, detail={
        "issues": issues,
        "counts": {
            "mermaid_blocks": len(mermaid_blocks),
            "has_state": has_state,
            "has_block": has_block,
            "has_dataflow": has_dataflow,
            "missing_keywords": missing_kw if 'missing_kw' in dir() else [],
        },
    })


# ---------------------------------------------------------------------------
# G4: 包可导入
# ---------------------------------------------------------------------------
def gate_g4():
    """python -c 'import eda_agent' 必须 exit=0."""
    # 项目用 src/ 布局, 需把 src 加入 PYTHONPATH
    env = os.environ.copy()
    src_dir = _PROJECT_ROOT / "src"
    sep = os.pathsep
    env["PYTHONPATH"] = (
        f"{src_dir}{sep}{env.get('PYTHONPATH', '')}"
        if env.get("PYTHONPATH")
        else str(src_dir)
    )
    p = _run([sys.executable, "-c", "import eda_agent"], env=env)
    ok = p.returncode == 0
    return _result("G4", ok, detail={
        "exit_code": p.returncode,
        "stdout": p.stdout.strip() if p.stdout else "",
        "stderr": p.stderr.strip() if p.stderr else "",
    })


# ---------------------------------------------------------------------------
# G5: ignore + 暂存区清洁度
# ---------------------------------------------------------------------------
def gate_g5():
    """.zread 被 ignore + 暂存区不含 .env / runs/(除 eval_snapshot) / .zread/."""
    issues = []

    pre = _run(["git", "check-ignore", ".zread"])
    if pre.returncode != 0:
        issues.append("前置断言失败: git check-ignore .zread 未命中 (应被 ignore)")

    cached = _run(["git", "diff", "--cached", "--name-only"])
    if cached.returncode != 0:
        return _result("G5", False,
                       error=f"git diff --cached 失败: {cached.stderr.strip()}")
    for p in cached.stdout.splitlines():
        norm = p.replace("\\", "/")
        if norm == ".env" or norm.startswith(".env/"):
            issues.append(f"暂存区含 .env: {p}")
            continue
        if norm.startswith(".zread/"):
            issues.append(f"暂存区含 .zread/: {p}")
            continue
        if norm.startswith("runs/"):
            # 允许 runs/eval_snapshot/...
            remainder = norm[len("runs/"):]
            if not remainder.startswith("eval_snapshot"):
                issues.append(f"暂存区含非 eval_snapshot 的 runs/: {p}")

    ok = not issues
    return _result("G5", ok, detail={"issues": issues})


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
GATES = {
    "G1": gate_g1,
    "G2": gate_g2,
    "G3": gate_g3,
    "G4": gate_g4,
    "G5": gate_g5,
}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="EDa Agent System 项目门禁 (G1-G5)")
    parser.add_argument(
        "--gate", choices=sorted(GATES.keys()),
        help="只跑指定门禁; 不指定则跑全部")
    args = parser.parse_args(argv)

    targets = [args.gate] if args.gate else list(GATES.keys())

    results = []
    for name in targets:
        try:
            r = GATES[name]()
        except Exception as e:  # noqa: BLE001 - 门禁不应被异常吞掉
            r = {"gate": name, "ok": False,
                 "error": f"门禁异常: {type(e).__name__}: {e}"}
        results.append(r)

    overall_ok = all(r.get("ok", False) for r in results)
    payload = {
        "project_root": str(_PROJECT_ROOT),
        "overall_ok": overall_ok,
        "results": results,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
