#!/usr/bin/env python3
"""SchemaForge GUI 入口

PySide6 桌面应用，VS Code 暗色主题。

启动方式::

    python gui.py
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from schemaforge.gui.main_window import MainWindow
from schemaforge.gui.theme import apply_theme


def main() -> None:
    """启动 SchemaForge 桌面 GUI."""
    # 配置日志输出到 stderr（控制台可见）
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    app = QApplication(sys.argv)
    app.setApplicationName("SchemaForge")
    app.setOrganizationName("SchemaForge")

    apply_theme(app)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
