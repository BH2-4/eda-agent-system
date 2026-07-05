"""Agentic EDA 系统共享契约(宪法)的实现层。

严格对齐 CONTRACTS.md §2 v1.2。A/B/C 三组件一律 ``from eda_agent.contracts import ...``
取符号,禁止各自重新定义同名结构。任何冲突以 CONTRACTS.md 为准。

约定:4 空格缩进;contract_version 引用必须走本模块的 ``CONTRACT_VERSION`` 常量,
禁止裸字符串 "0.1.0"(验收 I12 / A14)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

# ─────────────────────────────────────────────────────────────────────
# §2 顶部:契约版本锚点 + 工件引用工厂 + 工件注入占位常量
# ─────────────────────────────────────────────────────────────────────

CONTRACT_VERSION = "0.1.0"

# RulePlanner 用此标记"某参数需由 C._resolve_args 注入上游 artifact_ref"(契约 §6)。
ARTIFACT_FROM_STATE = "<from_state>"


def artifact_ref(run_id: str, rel_path: str) -> dict[str, str]:
    """构造跨组件工件引用 dict。

    所有 ``ToolResult.artifacts`` 与 ``SkillResult.artifacts`` 的元素必须由本函数
    构造(禁止裸 str 路径,禁止裸 dict 字面量)。实际路径 = ``runs/<run_id>/<rel_path>``。
    """
    return {"run_id": run_id, "rel_path": rel_path}


# ─────────────────────────────────────────────────────────────────────
# §2.0 对外入口:RunRequest / RunReport(CLI/MCP 入参出参)
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunRequest:
    kind: Literal["run", "diagnose", "self_heal"]
    goal: str                                # 自然语言目标,如 "pass all tests"
    rtl_path: str                            # RTL 源(绝对或相对项目根)
    tb_path: str | None = None               # TB 源;diagnose-only 可缺
    top_module: str | None = None            # 顶层模块名;None 由 Yosys 推断
    lib_path: str | None = None              # liberty(.lib);None 则 STA 跳过面积/时序
    clock_name: str | None = None            # 时钟信号名;None 则 STA 跳过
    max_iter: int | None = None              # 覆盖 settings 的 skill_max_iterations
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunReport:
    run_id: str
    status: Literal["ok", "failed", "budget_exhausted"]
    report_path: str                         # runs/<run_id>/report.md 绝对路径
    summary: str                             # 一句话结论
    metrics: dict[str, Any]                  # 关键指标(契约 §2.4 标准字段集)


# ─────────────────────────────────────────────────────────────────────
# §2.1 Tool 基类与 ToolResult
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolCall:
    name: str                                # Tool 名,如 "yosys_synth"
    args: dict[str, Any]                     # 调用参数,与 Tool.schema 对齐(含 reserved 字段)
    caller: str = "planner"                  # 调用方标识,便于追溯
    llm_tool_call_id: str | None = None      # 关联 LLM tool_calls.id;非 LLM 发起则为 None


@dataclass
class ToolResult:
    status: Literal["ok", "error", "timeout"]
    exit_code: int | None                    # 子进程退出码;skill 无子进程则 None
    stdout: str                              # 头 32KB + 尾 32KB 拼接;超过 64KB 则中段标记
    stderr: str                              # 同上裁剪策略
    parsed: dict[str, Any]                   # 结构化解析;必须含 _schema 元字段(见下)
    artifacts: list[dict[str, str]]          # 元素必须是 artifact_ref(...) 的返回
    duration_s: float
    tool: str                                # 产生该结果的 Tool 名
    error_code: str | None = None            # 二段式 namespace.code,取值见 errors.py
    error_hint: str | None = None            # 失败时一行人类可读提示

    def is_ok(self) -> bool:
        return self.status == "ok"


# parsed 的 _schema 元字段(契约版本锚点,强制):
#   parsed = {"_schema": {"name": <tool>, "version": "0.1.0",
#                         "contract_version": CONTRACT_VERSION}, ...}
# 集成方读 parsed 必须先校验 _schema.name 与 _schema.contract_version,不匹配抛
# eda.schema_mismatch。所有组件引用 contract_version 必须 from 本模块 import,禁止裸字符串。

# stdout/stderr 裁剪策略(见 §2.1):≤64KB 原样;>64KB 保留头 32KB + 尾 32KB,中段替换为
#   "[...N lines truncated, see stdout.full.log]",完整版落 steps/<idx>/stdout.full.log。
# A 诊断器读 evidence 优先读 .full.log 文件,不依赖内存里的裁剪串。


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    schema: dict[str, Any]                   # args 的 JSON Schema + parsed_schema_ref(见 §3)

    def __call__(self, call: ToolCall) -> ToolResult: ...


# ─────────────────────────────────────────────────────────────────────
# §2.3 Skill 基类、SkillResult 与 as_tool 适配契约
# ─────────────────────────────────────────────────────────────────────


@dataclass
class SkillResult:
    status: Literal["ok", "error", "budget_exhausted"]
    iterations: int                          # 实际迭代轮数(A 不迭代,恒 1)
    final_parsed: dict[str, Any]             # 最终 parsed(自带 _schema;as_tool 后作为 ToolResult.parsed)
    trajectory: list[dict]                   # 每步 {"step": tool_name, "status": ..., "iter": n, "detail": ...}
    artifacts: list[dict[str, str]]          # 元素必须是 artifact_ref(...) 返回
    summary: str
    # B 自修复专用字段(智能证据,入契约):
    patch_source: Literal["llm_full_rewrite", "llm_diff", "rule_based", "none"] = "none"
    convergence_cause: Literal["all_pass", "max_iter", "regression", "budget", "none"] = "none"
    best_iter: int = -1                      # 历史最佳轮;-1=无任何轮通过仿真
    error_code: str | None = None            # 二段式,见 errors.py
    budget_used_s: float = 0.0               # 实际消耗预算(双层预算仲裁用)


# A(诊断器)恒定语义:v1.2 显式 —— A 不修复不迭代,故 patch_source="none"、
# convergence_cause="none"、best_iter=-1(语义"无任何轮通过仿真",避免 C 误判 A 找到最佳轮)。


@runtime_checkable
class Skill(Protocol):
    name: str
    description: str
    max_iterations: int                      # 迭代上限,硬约束(A=1,B 由 settings 注入)
    budget_s: float                          # 时间预算秒,硬约束

    def run(
        self,
        run_id: str,
        inputs: dict[str, Any],
        remaining_budget_s: float | None = None,  # C 下传剩余 run 预算,Skill 不得独占
    ) -> SkillResult: ...

    def as_tool(self) -> Tool: ...           # 包装成 Tool 注册进 Registry


# as_tool 适配契约(SkillResult → ToolResult 映射 + reserved 字段拆包)实现见
# skills/base.py。要点:
#   - status: ok→ok,其余→error;budget_exhausted 的 error_code 覆写 eda.budget_exhausted
#   - parsed 补六个 _skill_* 元字段(_skill_status/_iterations/_trajectory/
#     _patch_source/_convergence_cause/_best_iter)
#   - _remaining_budget_s 由 as_tool 从 call.args.pop 取出,作为 run 的位置参数下传
# reserved 字段登记表(_remaining_budget_s / _artifact_ref)不进 LLM 可见 schema,
# registry.to_llm_tools 剥离下划线前缀字段(见 registry.py)。


# ─────────────────────────────────────────────────────────────────────
# §2.4 工件存储 RunRecord / StepRecord
# ─────────────────────────────────────────────────────────────────────


@dataclass
class StepRecord:
    index: int                               # 从 1 开始,父 RunRecord 全局序号
    tool_name: str
    tool_call_path: str                      # tool_call.json 绝对路径
    tool_result_path: str                    # tool_result.json 绝对路径
    started_at: str
    duration_s: float
    status: Literal["ok", "error", "timeout"]
    # 若该步属于某 Skill 的内部迭代,补记:
    skill_name: str | None = None            # 如 "self_heal"
    iter: int | None = None                  # 该步属于 Skill 的第几轮(从 0 起)


@dataclass
class RunRecord:
    run_id: str
    request: dict[str, Any]                  # 原始 RunRequest 的 asdict
    created_at: str                          # ISO8601
    status: Literal["running", "ok", "failed", "budget_exhausted"]
    steps: list[StepRecord]                  # 含 Skill 内部每一步(见 §2.4 隶属关系图)
    final_report_path: str | None
    total_duration_s: float
    llm_calls: int                           # 经 CountingProvider 统一计数
    llm_tokens_in: int
    llm_tokens_out: int
    provider_used: str                       # "claude" / "qwen" / "deepseek"
    contract_version: str                    # = CONTRACT_VERSION
    config_snapshot: dict[str, Any]          # 实验可复现性(契约 §2.4)


# run_id 格式: YYYYmmdd_HHMMSS_<4hex>,如 20260715_103022_a3f1。
# config_snapshot 字段集(契约 §2.4):
#   {"llm": {provider,model,temperature,max_tokens},
#    "eda": {yosys_version,iverilog_version,opensta_version},
#    "contract_version": CONTRACT_VERSION,
#    "settings_hash": "<settings.toml sha256 前 12 位>",
#    "planner_mode": "llm"}
# 僵尸 run 自愈:running 态不落 status.json;进程启动扫描 runs/*,超时 running 标
#   failed 写 status.json(见 runner.py scavenge_zombies)。


# ─────────────────────────────────────────────────────────────────────
# §2.5 LLM provider 抽象
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str                             # 仅承载文本;多模态/tool_use 走 tool_calls
    tool_call_id: str | None = None          # role="tool" 时必填,关联 assistant 的 tool_calls.id


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[dict]                   # 元素形状 {"id": str|None, "name": str, "args": dict}
    tokens_in: int
    tokens_out: int
    provider: str                            # "claude" / "qwen" / "deepseek"
    model: str
    raw: dict[str, Any] | None = None        # 原始响应,debug 用


@runtime_checkable
class LLMProvider(Protocol):
    provider_name: str

    def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,     # JSON Schema 工具描述(来自 Registry.to_llm_tools)
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse: ...


# 边界声明(契约 §2.5):
# - Message.content 仅承载文本;多模态与 assistant tool_use 只走 tool_calls,不进 content。
# - role="tool" 的 Message 必须带 tool_call_id(provider 层翻译:OpenAI 原生字段,Claude tool_result block)。
# - tool_calls.id 为 Optional:provider 层保证唯一(Claude 原生 id;OpenAI 兼容若无则 provider 生成 uuid)。
# - LLM tool-use 回环:ToolResult → Message(role="tool", content=json({status,parsed,error_hint}),
#   tool_call_id=<ToolCall.llm_tool_call_id>);C 收到 tool_calls 先经 registry.get 校验,
#   不存在→eda.tool_not_found 回灌,不抛异常(见 planner/c_planner.py)。
