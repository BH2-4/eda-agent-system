#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
确定性重建 wiki：把 .zread/wiki/versions/<id>/<slug>.md 整理到 docs/wiki/<NN_中文名>.md。

权威输入（Step0 产出，禁止 agent 自行推断；存放于 .zread/wiki_sources/，避免污染 docs/wiki/ 的 G2 门禁）：
  - .zread/wiki_sources/_rename_map.csv   slug -> new_filename（含 2 处 typo 的两种 slug 形式）
  - .zread/wiki_sources/_path_map.md       路径前缀重算规则（三类）
  - .zread/wiki_sources/_sources_rules.md  Sources 软化规则（A/B/C 三类）+ typo slug 修复

替换顺序（重要，不可乱序）：
  a. typo slug 修正（在互链替换之前，把 typo slug 也映射到 new_filename）
  b. 页面间互链 ](N-slug) -> ](NN_中文名.md)（无 .md 后缀才匹配）
  c. 路径前缀重算 + Sources 软化（A 类保留 #L，B 类去 #L）
  d. 正文文字不动（除上述确定性替换）

输出：
  - docs/wiki/<NN_中文名>.md  (31 页)
  - docs/wiki/INDEX.md        (含说明栏 + 31 行表格)
  - stdout: 各类替换计数（供 grep/wc 复核）
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import io
from urllib.parse import unquote
from datetime import datetime, timezone

# ---------- UTF-8 输出（Windows 控制台兼容） ----------
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# ---------- 路径 ----------
ROOT = r"D:/eda agent system"
VERSION_ID = "2026-07-06-180551"
SRC_DIR = os.path.join(ROOT, ".zread", "wiki", "versions", VERSION_ID)
WIKI_JSON = os.path.join(SRC_DIR, "wiki.json")
DST_DIR = os.path.join(ROOT, "docs", "wiki")
# 重建权威输入 (rename_map / path_map / sources_rules) 移出 docs/wiki/
# 以避免触发 G2 门禁 (docs/wiki/ 仅允许 NN_*.md 与 INDEX.md).
# .zread/ 已被 .gitignore 排除, 这些输入文件作为构建规格保存在此.
SOURCES_DIR = os.path.join(ROOT, ".zread", "wiki_sources")
RENAME_CSV = os.path.join(SOURCES_DIR, "_rename_map.csv")

# generated_at 直接从 wiki.json 读，权威值；下面是任务指定的展示值（与 wiki.json 一致）
GEN_AT_DISPLAY = "2026-07-06T10:05:51Z"

# ---------- 权威：顶层文件集合（B 类，去 #L） ----------
# 来自 _path_map.md ① + _sources_rules.md B 类判定集
TOPFILE_B_SET = {
    "README.md",
    "ARCHITECTURE.md",
    "CONTRACTS.md",
    "验收标准.md",
    "组件A_诊断器.md",
    "组件B_自修复闭环.md",
    "组件C_Planner_ToolUse.md",
    "实现执行计划.md",
    "_审计报告_v1.md",
    "pyproject.toml",
    "settings.toml",
    "experiment_summary.json",
    ".env.example",
}

# src/tests/data/scripts 类前缀（A 类，保留 #L）
CODE_PREFIXES = ("src/", "tests/", "data/", "scripts/", "runs/")

# 互链匹配正则：](N-slug) 无 .md 后缀
INTERLINK_RE = re.compile(r"\]\(([0-9]+-[a-z0-9_-]+)\)")
# 通用 markdown 链接 target 捕获（用于路径重算）
MDLINK_RE = re.compile(r"\]\(([^)]+)\)")


# ---------- 1. 加载 rename_map（slug -> new_filename） ----------
def load_rename_map() -> dict[str, str]:
    m: dict[str, str] = {}
    with open(RENAME_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            slug = row["slug"].strip()
            nf = row["new_filename"].strip()
            if slug and nf:
                m[slug] = nf
    # 显式补 2 处 typo slug（正文里出现的错误形式）-> 同一 new_filename
    # typo 1: page19 正文写成 tong-yi（多 yi），实际 slug 是 tong-gong
    m["19-tong-yi-gong-ju-zhu-ce-zhong-xin-yu-kai-bi-yuan-ze"] = m[
        "19-tong-gong-ju-zhu-ce-zhong-xin-yu-kai-bi-yuan-ze"
    ]
    # typo 2: page21 正文写成 test-pass，实际 slug 是 test_pass
    m["21-iverilog-fang-zhen-yu-tb-da-yin-xie-yi-test-pass-test-fail"] = m[
        "21-iverilog-fang-zhen-yu-tb-da-yin-xie-yi-test_pass-test_fail"
    ]
    return m


# ---------- 2. 替换单页内容 ----------
def transform(text: str, rename: dict[str, str], stats: dict, final_filenames: set[str]) -> str:
    """按 a->b->c 顺序做确定性替换，返回新文本。stats 累加各类计数。"""

    # (a)+(b) 页面间互链：](N-slug) -> ](NN_中文名.md)
    # 正则已限定"无 .md 后缀"，typo slug 已并入 rename 字典，一次替换覆盖两种形式。
    def _inter(m):
        slug = m.group(1)
        if slug in rename:
            stats["interlink"] += 1
            return "](" + rename[slug] + ")"
        # 不在映射里的旧 slug：保持原样，后续 G2 门禁会报警
        stats["interlink_unmapped"] += 1
        return m.group(0)

    text = INTERLINK_RE.sub(_inter, text)

    # (c) 路径前缀重算 + Sources 软化：逐链接处理
    def _link(m):
        full = m.group(1)  # 含可能的 #fragment
        # 跳过外部/锚点
        if full.startswith(("http://", "https://", "mailto:", "mailto", "#")):
            return m.group(0)
        # 跳过已是最终文件名的互链（由 INTERLINK_RE 替换产生，含 .md 后缀）
        if full in final_filenames:
            return m.group(0)
        decoded = unquote(full)
        # 剥 fragment
        if "#" in decoded:
            path_part, frag = decoded.split("#", 1)
            frag = "#" + frag
        else:
            path_part, frag = decoded, ""

        # A 类：src/ tests/ data/ scripts/ runs/ -> 加 ../../，保留 #L
        if path_part.startswith(CODE_PREFIXES):
            stats["src_prefix"] += 1
            return "](../../" + path_part + frag + ")"

        # C 类：docs/<file> -> ../<file>，保留 #L
        if path_part.startswith("docs/"):
            stats["docs_prefix"] += 1
            return "](../" + path_part[len("docs/"):] + frag + ")"

        # B 类：顶层文档 -> 加 ../../，去 #L
        if path_part in TOPFILE_B_SET:
            stats["topfile_B"] += 1
            return "](../../" + path_part + ")"

        # 边界：.wf/ 也是项目根下代码目录（带 #L 行号），按 A 类同规则保守处理
        # 注：_path_map.md 未显式列出，标为未分类计数供人工裁定。
        if path_part.startswith(".wf/"):
            stats["unclassified_wf"] += 1
            return "](../../" + path_part + frag + ")"

        # 边界：.gitignore 在项目根下，按顶层文件保守处理（保留 #L，因 .gitignore 本次不动）
        # 注：未在 _path_map.md 顶层清单内，标为未分类计数供人工裁定。
        if path_part == ".gitignore":
            stats["unclassified_gitignore"] += 1
            return "](../../" + path_part + frag + ")"

        # 其它：报告但不改（门禁 G2 会捕获死链）
        stats["unclassified_other"] += 1
        sys.stderr.write(f"[WARN] unclassified link target: {full}\n")
        return m.group(0)

    text = MDLINK_RE.sub(_link, text)
    return text


# ---------- 3. 生成 INDEX.md ----------
def build_index(pages_meta: list[dict], version_id: str, gen_at: str) -> str:
    lines = []
    lines.append("# EDA Agent System Wiki 索引\n")
    lines.append("> 本索引由 `scripts/gates/_rebuild_wiki.py` 从 zread wiki.json 确定性生成。\n")
    lines.append("")
    lines.append("| 字段 | 值 |")
    lines.append("|------|----|")
    lines.append(f"| zread 版本 id | `{version_id}` |")
    lines.append(f"| generated_at | `{gen_at}` |")
    lines.append(f"| 源 wiki.json | `.zread/wiki/versions/{version_id}/wiki.json` |")
    lines.append("| 刷新命令 | `zread generate` |")
    lines.append("")
    lines.append(
        "> **透明化口径矛盾**：wiki 第 5 页正文「六层」与标题「五层」系 zread 生成口径不一致，"
        "本项目对外统一用「五层 L0-L5（L0 工件存储不计入业务层）」。"
    )
    lines.append("")
    lines.append(
        "> wiki 为 zread 自动生成快照，未人工校对；权威信息以 `src/` 与 `ARCHITECTURE.md` 为准。"
    )
    lines.append("")
    lines.append("## 页面清单\n")
    lines.append("| 序号 | 标题 | section | group | level | 文件链接 |")
    lines.append("|------|------|---------|-------|-------|----------|")
    for p in pages_meta:
        seq = p["seq"]
        title = p["title"]
        section = p["section"] or ""
        group = p["group"] or ""
        level = p["level"] or ""
        fname = p["new_filename"]
        lines.append(
            f"| {seq} | {title} | {section} | {group} | {level} | [{fname}]({fname}) |"
        )
    lines.append("")
    return "\n".join(lines)


# ---------- main ----------
def main() -> int:
    os.makedirs(DST_DIR, exist_ok=True)

    rename = load_rename_map()
    print(f"[info] rename_map entries (含 2 typo 双形式): {len(rename)}")
    # 已是最终文件名的 target 集合（互链替换后的产物），路径重算时直接跳过
    final_filenames = set(rename.values())

    with open(WIKI_JSON, encoding="utf-8") as f:
        wj = json.load(f)
    version_id = wj["id"]
    gen_at_raw = wj["generated_at"]
    pages = wj["pages"]
    print(f"[info] wiki.json id={version_id} pages={len(pages)} generated_at={gen_at_raw}")

    total_stats = {
        "interlink": 0,
        "interlink_unmapped": 0,
        "src_prefix": 0,
        "docs_prefix": 0,
        "topfile_B": 0,
        "unclassified_wf": 0,
        "unclassified_gitignore": 0,
        "unclassified_other": 0,
    }

    pages_meta: list[dict] = []
    written = 0
    for idx, p in enumerate(pages, start=1):
        slug = p["slug"]
        src_file = p.get("file") or (slug + ".md")
        src_path = os.path.join(SRC_DIR, src_file)
        if not os.path.exists(src_path):
            sys.stderr.write(f"[ERR] missing source page: {src_path}\n")
            return 2
        if slug not in rename:
            sys.stderr.write(f"[ERR] slug not in rename_map: {slug}\n")
            return 3
        new_fname = rename[slug]

        raw = open(src_path, encoding="utf-8").read()
        page_stats = {k: 0 for k in total_stats}
        new_text = transform(raw, rename, page_stats, final_filenames)
        for k in total_stats:
            total_stats[k] += page_stats[k]

        dst_path = os.path.join(DST_DIR, new_fname)
        with open(dst_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
        written += 1

        pages_meta.append(
            {
                "seq": idx,
                "title": p.get("title", ""),
                "section": p.get("section", ""),
                "group": p.get("group", ""),
                "level": p.get("level", ""),
                "new_filename": new_fname,
            }
        )

    # INDEX.md
    index_md = build_index(pages_meta, version_id, GEN_AT_DISPLAY)
    with open(os.path.join(DST_DIR, "INDEX.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(index_md)

    # ---------- 摘要 ----------
    print("\n===== REBUILD SUMMARY =====")
    print(f"pages written         : {written}")
    print(f"INDEX.md              : 1")
    print(f"interlink replaced    : {total_stats['interlink']}")
    print(f"interlink unmapped    : {total_stats['interlink_unmapped']}  (应=0)")
    print(f"src/tests/data/... A类: {total_stats['src_prefix']}  (加../../, 保留#L)")
    print(f"docs/ C类             : {total_stats['docs_prefix']}  (改../, 保留#L)")
    print(f"顶层文档 B类          : {total_stats['topfile_B']}  (加../../, 去#L)")
    print(f"未分类 .wf/           : {total_stats['unclassified_wf']}  (保守按A类处理, 待人工裁定)")
    print(f"未分类 .gitignore     : {total_stats['unclassified_gitignore']}  (保守按顶层处理, 待人工裁定)")
    print(f"未分类 other          : {total_stats['unclassified_other']}  (应=0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
