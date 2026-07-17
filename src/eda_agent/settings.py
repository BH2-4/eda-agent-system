"""Settings(契约 §6 v1.2)。

读 settings.toml → Settings dataclass。``[planner] mode`` 默认 ``"llm"``
(契约裁决 #7:保证 planner 真正组织工具迭代;rule 仅作 LLM 不可用时的降级)。

API key 走环境变量(ANTHROPIC_API_KEY / DASHSCOPE_API_KEY / DEEPSEEK_API_KEY),
不进 settings.toml,不进 git。
"""
from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# .env 贯穿:任何 import settings 都先加载 .env(API key 进 os.environ)。
# load_dotenv 静默:.env 不存在时不动(用环境变量或默认),不阻塞测试/运行。
# 需在 import tomllib 之前调用(保证 .env 在任何后续读取前生效)。
load_dotenv()

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover — pyproject 声明 tomli; python_version<'3.11'
    import tomli as tomllib  # type: ignore


@dataclass
class LLMSettings:
    provider: str = "glm"                           # glm(智谱,默认) | claude(备用) | qwen | deepseek
    glm_model: str = "glm-5.2"                      # 智谱 GLM-5.2(1M 上下文,max 调度)
    glm_base_url: str = "https://open.bigmodel.cn/api/coding/paas/v4"  # Coding Plan OpenAI 兼容端点
    glm_api_key_env: str = "GLM_API_KEY"            # API key 环境变量名(.env 加载)
    glm_thinking: bool = True                       # GLM-5.2 思考模式(thinking.type=enabled)
    glm_reasoning_effort: str = "max"               # 推理努力档 max(用户指定"max 调度")
    claude_model: str = "claude-sonnet-4"
    temperature: float = 0.0
    max_tokens: int = 4096


@dataclass
class EDASettings:
    wsl_enabled: bool = True                       # 工具是否跑在 WSL2
    yosys_cmd: str = "yosys"
    iverilog_cmd: str = "iverilog"
    vvp_cmd: str = "vvp"
    opensta_cmd: str = "sta"
    tool_timeout_s: int = 120
    default_lib_path: str = "data/lib/sky130_xx.lib"


@dataclass
class PlannerSettings:
    mode: str = "llm"                              # rule | llm;默认 llm(契约裁决 #7)
    max_iterations: int = 8


@dataclass
class BudgetSettings:
    planner_max_iterations: int = 8
    skill_max_iterations: int = 5
    run_budget_s: int = 600                        # C 与所有 Skill 共享上限
    skill_diagnose_budget_s: int = 60              # A 子预算
    skill_self_heal_budget_s: int = 480            # B 子预算(留 120 给 C 的综合/仿真/STA)


@dataclass
class DataSettings:
    error_kb_path: str = "data/error_kb.json"
    fault_manifest_path: str = "data/fault_manifest.json"
    logs_corpus_path: str = "data/logs_corpus/corpus.jsonl"


@dataclass
class Settings:
    llm: LLMSettings = field(default_factory=LLMSettings)
    eda: EDASettings = field(default_factory=EDASettings)
    planner: PlannerSettings = field(default_factory=PlannerSettings)
    budget: BudgetSettings = field(default_factory=BudgetSettings)
    data: DataSettings = field(default_factory=DataSettings)

    # ── 扁平便捷访问(高频字段,代码里直接 settings.xxx 用) ──────────────
    @property
    def llm_provider(self) -> str:
        return self.llm.provider

    @property
    def planner_mode(self) -> str:
        """C12 config_snapshot.planner_mode 用;默认 'llm'。"""
        return self.planner.mode

    @property
    def skill_max_iterations(self) -> int:
        return self.budget.skill_max_iterations

    @property
    def planner_max_iterations(self) -> int:
        return self.budget.planner_max_iterations

    @property
    def run_budget_s(self) -> int:
        return self.budget.run_budget_s

    @property
    def skill_self_heal_budget_s(self) -> int:
        return self.budget.skill_self_heal_budget_s

    @property
    def skill_diagnose_budget_s(self) -> int:
        return self.budget.skill_diagnose_budget_s

    @property
    def error_kb_path(self) -> str:
        return self.data.error_kb_path

    @property
    def fault_manifest_path(self) -> str:
        return self.data.fault_manifest_path


def _section(cls, raw: dict | None):
    """从 toml 段构造子 settings;只取 dataclass 认识的字段,忽略注释掉的 key。"""
    if not raw:
        return cls()
    known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
    return cls(**known)


def load_settings(path: str | Path = "settings.toml") -> Settings:
    """读 settings.toml → Settings;文件缺失则返回全默认 Settings。"""
    p = Path(path)
    if not p.exists():
        return Settings()
    with p.open("rb") as f:
        raw = tomllib.load(f)
    return Settings(
        llm=_section(LLMSettings, raw.get("llm")),
        eda=_section(EDASettings, raw.get("eda")),
        planner=_section(PlannerSettings, raw.get("planner")),
        budget=_section(BudgetSettings, raw.get("budget")),
        data=_section(DataSettings, raw.get("data")),
    )


def settings_hash(path: str | Path = "settings.toml") -> str:
    """settings.toml 的 sha256 前 12 位(契约 §2.4 config_snapshot.settings_hash)。

    文件缺失返回空串(用默认 Settings 时无可复现哈希)。
    """
    p = Path(path)
    if not p.exists():
        return ""
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()[:12]
