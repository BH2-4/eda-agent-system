"""Runner — 工件存储 L0(契约 §2.4)。

职责:
- run_id 生成: ``YYYYmmdd_HHMMSS_<4hex>``
- RunRecord 落盘(run.json,实时增量更新 status / steps)+ request.json
- append_step(parent_run_id, call, result, skill_name, iter):追加 StepRecord 并落
  steps/<idx>_<tool>/{tool_call.json,tool_result.json,stdout.log,stderr.log[,.full.log]}
- stdout/stderr 64KB 裁剪(头 32K + 尾 32K + 中段标记 + 完整版 .full.log)
- scavenge_zombies: 进程启动扫 runs/*,超时 running 态标 failed 写 status.json
- make_config_snapshot: 构造 §2.4 config_snapshot(llm/eda/contract_version/settings_hash/planner_mode)

B 内部迭代的子步**不生成独立 run_id**,经 append_step(..., skill_name="self_heal", iter=n)
追加到父 RunRecord.steps,序号继续递增。
"""
from __future__ import annotations

import json
import re
import secrets
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from eda_agent.contracts import (
    CONTRACT_VERSION,
    RunRecord,
    RunRequest,
    StepRecord,
    ToolCall,
    ToolResult,
)
from eda_agent.settings import Settings, settings_hash

RUN_ID_RE = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{4}$")

# stdout/stderr 裁剪阈值(契约 §2.1:总长 ≤64K 原样;>64K 头32K+尾32K+中段标记)
_CLIP_HEAD = 32768
_CLIP_TAIL = 32768


def new_run_id() -> str:
    """生成 ``YYYYmmdd_HHMMSS_<4hex>``,如 ``20260715_103022_a3f1``。"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{secrets.token_hex(2)}"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clip_io(text: str, label: str = "stdout") -> tuple[str, str | None]:
    """裁剪 stdout/stderr。

    返回 (落盘到 stdout.log 的裁剪版, 完整版或 None)。
    - len ≤ head+tail: 原样返回, full=None。
    - len > head+tail: 头 head + 中段标记 + 尾 tail, full=原文(落 .full.log)。
    """
    if text is None:
        return "", None
    if len(text) <= _CLIP_HEAD + _CLIP_TAIL:
        return text, None
    head_part = text[:_CLIP_HEAD]
    tail_part = text[-_CLIP_TAIL:]
    middle = text[_CLIP_HEAD:-_CLIP_TAIL]
    n_lines = middle.count("\n")
    marker = (
        f"\n[...{n_lines} lines ({len(middle)} chars) truncated, "
        f"see {label}.full.log]\n"
    )
    return head_part + marker + tail_part, text


def _to_jsonable(obj: Any) -> Any:
    """dataclass → dict(asdict);其余原样(json.dumps 能处理)。"""
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    return obj


def make_config_snapshot(
    settings: Settings,
    *,
    settings_path: str | Path = "settings.toml",
    eda_versions: dict[str, str] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """构造 §2.4 config_snapshot(实验可复现性 + provider 切换对比依据)。

    eda_versions 由 CPlanner 在真跑工具后填({yosys_version, iverilog_version, opensta_version});
    Phase1 / 工具未就绪时为 {}。
    """
    return {
        "llm": {
            "provider": settings.llm.provider,
            "model": model or settings.llm.claude_model,
            "temperature": settings.llm.temperature,
            "max_tokens": settings.llm.max_tokens,
        },
        "eda": eda_versions or {},
        "contract_version": CONTRACT_VERSION,
        "settings_hash": settings_hash(settings_path),
        "planner_mode": settings.planner_mode,
    }


class Runner:
    """每次 RunRequest 一个 run_id,落 runs/<run_id>/ 目录。"""

    def __init__(
        self,
        runs_dir: str | Path = "runs",
        settings: Settings | None = None,
    ) -> None:
        self._runs_dir = Path(runs_dir)
        self._settings = settings or Settings()
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, RunRecord] = {}
        self._start_times: dict[str, datetime] = {}

    # ── 创建 / 终态 ──────────────────────────────────────────────────

    def create(
        self,
        request: RunRequest,
        *,
        provider_used: str = "claude",
        config_snapshot: dict[str, Any] | None = None,
    ) -> RunRecord:
        run_id = new_run_id()
        run_dir = self._runs_dir / run_id
        (run_dir / "steps").mkdir(parents=True, exist_ok=True)
        rec = RunRecord(
            run_id=run_id,
            request=asdict(request),
            created_at=_iso_now(),
            status="running",
            steps=[],
            final_report_path=None,
            total_duration_s=0.0,
            llm_calls=0,
            llm_tokens_in=0,
            llm_tokens_out=0,
            provider_used=provider_used,
            contract_version=CONTRACT_VERSION,
            config_snapshot=config_snapshot or {},
        )
        self._records[run_id] = rec
        self._start_times[run_id] = datetime.now(timezone.utc)
        (run_dir / "request.json").write_text(
            json.dumps(asdict(request), ensure_ascii=False, indent=2)
        )
        self._persist(rec)
        return rec

    def finalize(
        self,
        run_id: str,
        status: str,
        *,
        final_report_path: str | None = None,
        llm_calls: int = 0,
        llm_tokens_in: int = 0,
        llm_tokens_out: int = 0,
        total_duration_s: float | None = None,
    ) -> RunRecord:
        """写终态:更新 run.json + 落 status.json(仅终态写)。"""
        rec = self._records[run_id]
        rec.status = status
        rec.final_report_path = final_report_path
        rec.llm_calls = llm_calls
        rec.llm_tokens_in = llm_tokens_in
        rec.llm_tokens_out = llm_tokens_out
        if total_duration_s is not None:
            rec.total_duration_s = total_duration_s
        else:
            start = self._start_times.get(run_id)
            if start is not None:
                rec.total_duration_s = (datetime.now(timezone.utc) - start).total_seconds()
        self._persist(rec)
        (self._runs_dir / run_id / "status.json").write_text(
            json.dumps({"run_id": run_id, "status": status}, ensure_ascii=False, indent=2)
        )
        return rec

    def get_record(self, run_id: str) -> RunRecord | None:
        return self._records.get(run_id)

    def load_record(self, run_id: str) -> dict[str, Any] | None:
        """从磁盘读 run.json(跨进程 / 渲染 report 时用)。"""
        p = self._runs_dir / run_id / "run.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    # ── Step 落盘 ────────────────────────────────────────────────────

    def append_step(
        self,
        run_id: str,
        call: ToolCall,
        result: ToolResult,
        *,
        skill_name: str | None = None,
        iter: int | None = None,
    ) -> StepRecord:
        """把一次 ToolCall/ToolResult 作为 StepRecord 追加到父 RunRecord 并落盘。

        B 内部迭代的子步传 skill_name="self_heal" + iter=n;C 直接调的传 None。
        """
        rec = self._records[run_id]
        index = len(rec.steps) + 1
        idx_name = f"{index:03d}_{call.name}"
        step_dir = self._runs_dir / run_id / "steps" / idx_name
        step_dir.mkdir(parents=True, exist_ok=True)

        call_path = step_dir / "tool_call.json"
        result_path = step_dir / "tool_result.json"
        call_path.write_text(
            json.dumps(_to_jsonable(call), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result_path.write_text(
            json.dumps(_to_jsonable(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # stdout / stderr 裁剪 + 完整版落盘
        for label, text in (("stdout", result.stdout), ("stderr", result.stderr)):
            clipped, full = clip_io(text or "", label=label)
            (step_dir / f"{label}.log").write_text(clipped, encoding="utf-8")
            if full is not None:
                (step_dir / f"{label}.full.log").write_text(full, encoding="utf-8")

        started_at = datetime.now(timezone.utc) - timedelta(seconds=result.duration_s)
        sr = StepRecord(
            index=index,
            tool_name=call.name,
            tool_call_path=str(call_path),
            tool_result_path=str(result_path),
            started_at=started_at.isoformat(),
            duration_s=result.duration_s,
            status=result.status,
            skill_name=skill_name,
            iter=iter,
        )
        rec.steps.append(sr)
        self._persist(rec)
        return sr

    # ── 僵尸 run 自愈 ────────────────────────────────────────────────

    def scavenge_zombies(self, run_budget_s: float) -> int:
        """扫描所有 run 目录:running 态且 created_at 距今超过 run_budget_s*2 → 标 failed。

        返回清理的僵尸 run 数。保证对比实验 diff 两个 run 目录时不因僵尸误判。
        """
        threshold = run_budget_s * 2
        now = datetime.now(timezone.utc)
        n = 0
        for run_dir in self._runs_dir.iterdir():
            if not run_dir.is_dir():
                continue
            run_json = run_dir / "run.json"
            if not run_json.exists():
                continue
            try:
                data = json.loads(run_json.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if data.get("status") != "running":
                continue
            created = data.get("created_at")
            ct = _parse_iso(created)
            if ct is None:
                continue
            age = (now - ct).total_seconds()
            if age > threshold:
                data["status"] = "failed"
                run_json.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                (run_dir / "status.json").write_text(
                    json.dumps(
                        {
                            "run_id": data.get("run_id"),
                            "status": "failed",
                            "crash": True,
                            "reason": "zombie_scavenged",
                            "age_s": age,
                            "threshold_s": threshold,
                            "scavenged_at": now.isoformat(),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                n += 1
        return n

    # ── 内部 ────────────────────────────────────────────────────────

    def _persist(self, rec: RunRecord) -> None:
        """把 RunRecord(含 steps)增量写 run.json。"""
        (self._runs_dir / rec.run_id / "run.json").write_text(
            json.dumps(_to_jsonable(rec), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
