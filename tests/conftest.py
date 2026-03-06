"""pytest 全局配置。

当前运行环境下，pytest 默认的 `tmp_path` / `.pytest_cache` 目录存在权限异常，
这里统一接管测试临时目录，确保质量门可稳定运行。
"""

from __future__ import annotations

import itertools
import os
import shutil
import tempfile
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import pytest

# 使用非交互式后端，避免 Tk/Tcl 依赖
matplotlib.use("Agg")

_TMP_ROOT = Path(__file__).resolve().parent.parent / ".test-runtime" / "tmp"
_TMP_ROOT.mkdir(parents=True, exist_ok=True)
_TMP_COUNTER = itertools.count()

os.environ["TMP"] = str(_TMP_ROOT)
os.environ["TEMP"] = str(_TMP_ROOT)
os.environ["TMPDIR"] = str(_TMP_ROOT)
tempfile.tempdir = str(_TMP_ROOT)


def _safe_mkdtemp(
    suffix: str | None = None,
    prefix: str | None = None,
    dir: str | None = None,
) -> str:
    base = Path(dir) if dir else _TMP_ROOT
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{prefix or 'tmp'}{next(_TMP_COUNTER):04d}{suffix or ''}"
    path.mkdir(parents=True, exist_ok=False)
    return str(path)


class _SafeTemporaryDirectory:
    def __init__(self, suffix: str | None = None, prefix: str | None = None, dir: str | None = None):
        self.name = _safe_mkdtemp(suffix=suffix, prefix=prefix, dir=dir)

    def __enter__(self) -> str:
        return self.name

    def __exit__(self, exc_type, exc, tb) -> None:
        shutil.rmtree(self.name, ignore_errors=True)

    def cleanup(self) -> None:
        shutil.rmtree(self.name, ignore_errors=True)


tempfile.mkdtemp = _safe_mkdtemp
tempfile.TemporaryDirectory = _SafeTemporaryDirectory


@pytest.fixture(autouse=True)
def _close_matplotlib_figures():
    """每个测试结束后关闭所有 matplotlib 图形，防止资源泄漏。"""
    yield
    plt.close("all")


@pytest.fixture
def tmp_path() -> Path:
    """覆盖 pytest 内置 tmp_path，规避宿主环境目录权限问题。"""
    path = _TMP_ROOT / f"case_{next(_TMP_COUNTER):04d}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
