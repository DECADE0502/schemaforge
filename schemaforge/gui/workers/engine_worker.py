"""后台引擎工作线程

提供两个 QThread worker，分别驱动旧主链和新主链：

- ``ClassicEngineWorker``:  SchemaForgeEngine（模板驱动）
- ``DesignSessionWorker``: DesignSession（需求澄清→候选→审查→渲染）

用法::

    worker = ClassicEngineWorker("5V转3.3V稳压", use_mock=True)
    worker.progress.connect(on_progress)
    worker.finished.connect(on_done)
    worker.error.connect(on_error)
    worker.start()
"""

from __future__ import annotations

import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal


# ============================================================
# ClassicEngineWorker — 旧主链
# ============================================================


class ClassicEngineWorker(QThread):
    """在后台线程中运行 SchemaForgeEngine.process()。

    Signals:
        finished(object): 处理完成，携带 EngineResult。
        error(str): 处理异常，携带错误描述。
        progress(str, int): 进度回调 (消息, 百分比)。
    """

    finished = Signal(object)
    error = Signal(str)
    progress = Signal(str, int)

    def __init__(
        self,
        user_input: str,
        use_mock: bool = True,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self.user_input = user_input
        self.use_mock = use_mock

    def _on_progress(self, message: str, percentage: int) -> None:
        """进度回调，从引擎线程转发信号。"""
        self.progress.emit(message, percentage)

    def run(self) -> None:
        """执行引擎处理流程。"""
        try:
            from schemaforge.core.engine import SchemaForgeEngine

            engine = SchemaForgeEngine(use_mock=self.use_mock)
            result = engine.process(
                self.user_input,
                progress_callback=self._on_progress,
            )
            self.finished.emit(result)
        except Exception as exc:
            tb = traceback.format_exc()
            self.error.emit(f"{exc}\n\n{tb}")


# ============================================================
# DesignSessionWorker — 新主链
# ============================================================


class DesignSessionWorker(QThread):
    """在后台线程中运行 DesignSession.run()。

    Signals:
        finished(object): 处理完成，携带 DesignSessionResult。
        error(str): 处理异常，携带错误描述。
        progress(str, int): 进度回调 (消息, 百分比)。
    """

    finished = Signal(object)
    error = Signal(str)
    progress = Signal(str, int)

    def __init__(
        self,
        user_input: str,
        use_mock: bool = True,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self.user_input = user_input
        self.use_mock = use_mock

    def _on_progress(self, message: str, percentage: int) -> None:
        """进度回调，从会话线程转发信号。"""
        self.progress.emit(message, percentage)

    def run(self) -> None:
        """执行设计会话流程。"""
        try:
            from schemaforge.workflows.design_session import DesignSession

            session = DesignSession(
                store_dir=Path("schemaforge/store"),
                use_mock=self.use_mock,
                progress_callback=self._on_progress,
            )
            result = session.run(self.user_input)
            self.finished.emit(result)
        except Exception as exc:
            tb = traceback.format_exc()
            self.error.emit(f"{exc}\n\n{tb}")


class RetryDesignWorker(DesignSessionWorker):
    """重试设计 — 在用户补录缺失器件后重新运行同一需求。

    行为与 DesignSessionWorker 完全一致，仅语义不同：
    store 中的器件库已更新（新增了用户刚录入的器件），
    重新运行相同的 user_input 即可匹配到新器件。
    """

    pass
