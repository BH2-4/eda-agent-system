"""Skill 层(A 诊断器 / B 自修复)共享基础设施。

本包只放 skill 适配代码与基类;具体 skill 实现在各自模块。``as_tool`` 把任意
``Skill Protocol`` 包装成 ``Tool Protocol`` 注册进 Registry(契约 §2.3)。
"""
from __future__ import annotations

from eda_agent.skills.base import SkillAdapter, as_tool

__all__ = ["SkillAdapter", "as_tool"]
