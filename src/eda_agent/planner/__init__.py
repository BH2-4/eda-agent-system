"""C Planner 包(契约 §6 v1.2)。

L4 大脑:把 RunRequest 分解为对 Tool/Skill 的有序调用,执行,读 ToolResult.parsed
据结构化字段决策,直到 goal 达成 / 预算耗尽。

边界:C 不碰 EDA 子进程 / stdout / RTL patch / anthropic SDK,全经 Tool/Skill/A/B;
不重定义 contracts / errors 已有类型,一律 import。
"""
from __future__ import annotations
