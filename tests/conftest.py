"""tests/conftest.py —— pytest 共享 fixture。

``_restore_cwd`` autouse fixture:每个测试结束后把 cwd 恢复到**会话起始**目录,
避免某些测试用 ``os.chdir(tmp_path)`` 改全局 cwd 后(tmp_path 被 pytest 自动删除)
污染后续测试(在已删除目录下创建文件 hang)。

不强制 chdir(尊重测试内部自己的 cwd 决策);只在 teardown 阶段恢复到 session root。
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def _session_root() -> Path:
    """会话起始 cwd(项目根),全 session 不变。"""
    return Path.cwd()


@pytest.fixture(autouse=True)
def _restore_cwd(_session_root: Path) -> None:
    """teardown 时把 cwd 恢复到会话起始(项目根)。

    某些测试用 ``os.chdir(tmp_path)``(非 monkeypatch.chdir)改全局 cwd,
    pytest 删除 tmp_path 后 cwd 失效;本 fixture 在每个测试结束后恢复。
    """
    yield
    try:
        if not Path.cwd().exists():
            import os
            os.chdir(str(_session_root))
    except OSError:
        import os
        os.chdir(str(_session_root))
