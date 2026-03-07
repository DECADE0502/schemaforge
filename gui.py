#!/usr/bin/env python3
"""SchemaForge GUI 入口

PySide6 桌面应用，VS Code 暗色主题。

启动方式::

    python gui.py
"""

from __future__ import annotations

import os
import sys

# 确保 GUI 模式下始终使用真实 AI（清除测试环境可能残留的跳过标记）
os.environ.pop("SCHEMAFORGE_SKIP_AI_PARSE", None)

from PySide6.QtWidgets import QApplication

from schemaforge.gui.main_window import MainWindow
from schemaforge.gui.theme import apply_theme


def main() -> None:
    """启动 SchemaForge 桌面 GUI."""
    app = QApplication(sys.argv)
    app.setApplicationName("SchemaForge")
    app.setOrganizationName("SchemaForge")

    apply_theme(app)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
