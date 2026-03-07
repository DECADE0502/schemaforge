"""后台引擎工作线程

提供三个 QThread worker，分别驱动三条链路：

- ``ClassicEngineWorker``:    SchemaForgeEngine（模板驱动）
- ``DesignSessionWorker``:    DesignSession（需求澄清→候选→审查→渲染）
- ``SchemaForgeWorker``:      SchemaForgeSession（统一工作台：精确型号+公式驱动+多轮修改）

用法::

    worker = SchemaForgeWorker("用 TPS54202 搭一个 20V转5V的DCDC电路", use_mock=True)
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


# ============================================================
# SchemaForgeWorker — 统一工作台（推荐）
# ============================================================


class SchemaForgeWorker(QThread):
    """在后台线程中运行 SchemaForgeSession.start()。

    SchemaForgeSession 是统一工作台，支持：
    - 精确型号匹配
    - Datasheet 公式驱动参数计算
    - 通用拓扑渲染
    - 多轮对话修改

    Signals:
        finished(object): 处理完成，携带 SchemaForgeTurnResult。
        error(str): 处理异常，携带错误描述。
        progress(str, int): 进度回调 (消息, 百分比)。
        session_ready(object): 会话对象创建后发出，供外部持久化保存。
    """

    finished = Signal(object)
    error = Signal(str)
    progress = Signal(str, int)
    session_ready = Signal(object)

    def __init__(
        self,
        user_input: str,
        use_mock: bool = True,
        session: object | None = None,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self.user_input = user_input
        self.use_mock = use_mock
        self._session = session

    def run(self) -> None:
        """执行统一工作台设计流程。"""
        try:
            self.progress.emit("正在解析设计需求…", 10)

            from schemaforge.workflows.schemaforge_session import SchemaForgeSession

            if isinstance(self._session, SchemaForgeSession):
                session = self._session
            else:
                session = SchemaForgeSession(
                    store_dir=Path("schemaforge/store"),
                    use_mock=self.use_mock,
                )
            self.session_ready.emit(session)

            self.progress.emit("正在匹配器件并生成设计…", 40)
            result = session.start(self.user_input)

            self.progress.emit("设计完成", 100)
            self.finished.emit(result)
        except Exception as exc:
            tb = traceback.format_exc()
            self.error.emit(f"{exc}\n\n{tb}")


class SchemaForgeReviseWorker(QThread):
    """在后台线程中运行 SchemaForgeSession.revise()。

    用于多轮对话修改 — 在已有设计基础上应用自然语言修改。

    Signals:
        finished(object): 处理完成，携带 SchemaForgeTurnResult。
        error(str): 处理异常，携带错误描述。
        progress(str, int): 进度回调 (消息, 百分比)。
    """

    finished = Signal(object)
    error = Signal(str)
    progress = Signal(str, int)

    def __init__(
        self,
        session: object,
        user_input: str,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self._session = session
        self.user_input = user_input

    def run(self) -> None:
        """执行修改流程。"""
        try:
            self.progress.emit("正在应用修改…", 30)
            result = self._session.revise(self.user_input)  # type: ignore[union-attr]
            self.progress.emit("修改完成", 100)
            self.finished.emit(result)
        except Exception as exc:
            tb = traceback.format_exc()
            self.error.emit(f"{exc}\n\n{tb}")


# ============================================================
# SchemaForgeOrchestratedWorker — AI 多轮编排（高级模式）
# ============================================================


class SchemaForgeOrchestratedWorker(QThread):
    """在后台线程中运行 SchemaForgeSession.run_orchestrated()。

    使用 Orchestrator AI 多轮循环：AI 解析需求 → 调用工具 → 判断下一步。
    返回 AgentStep 而非 SchemaForgeTurnResult。

    Signals:
        finished(object): 处理完成，携带 AgentStep。
        error(str): 处理异常，携带错误描述。
        progress(str, int): 进度回调 (消息, 百分比)。
        session_ready(object): 会话对象创建后发出，供外部持久化保存。
    """

    finished = Signal(object)
    error = Signal(str)
    progress = Signal(str, int)
    session_ready = Signal(object)

    def __init__(
        self,
        session: object,
        user_input: str,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self._session = session
        self.user_input = user_input

    def run(self) -> None:
        """执行 AI 编排流程。"""
        try:
            self.progress.emit("AI 正在分析需求…", 20)
            result = self._session.run_orchestrated(self.user_input)  # type: ignore[union-attr]
            self.progress.emit("AI 编排完成", 100)
            self.finished.emit(result)
        except Exception as exc:
            tb = traceback.format_exc()
            self.error.emit(f"{exc}\n\n{tb}")
