"""pytest 全局配置

解决 matplotlib 在大量测试中打开过多 figure 导致 tcl_findLibrary 崩溃的问题。
"""

import matplotlib
import matplotlib.pyplot as plt
import pytest

# 使用非交互式后端，避免 Tk/Tcl 依赖
matplotlib.use("Agg")


@pytest.fixture(autouse=True)
def _close_matplotlib_figures():
    """每个测试结束后关闭所有 matplotlib 图形，防止资源泄漏。"""
    yield
    plt.close("all")
