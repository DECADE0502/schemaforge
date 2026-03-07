"""后台引擎工作线程

提供 QThread worker 驱动 AI 链路：

- ``SchemaForgeWorker``:             SchemaForgeSession（统一工作台：精确型号+公式驱动+多轮修改）
- ``SchemaForgeReviseWorker``:       多轮对话修改
- ``IngestAssetWorker``:             器件补录（PDF/图片资料导入）
- ``ConfirmImportWorker``:           导入确认
- ``SchemaForgeOrchestratedWorker``: AI 多轮编排（高级模式）

用法::

    worker = SchemaForgeWorker("用 TPS54202 搭一个 20V转5V的DCDC电路")
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
        session: object | None = None,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self.user_input = user_input
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


class IngestAssetWorker(QThread):
    """在后台线程中运行 SchemaForgeSession.ingest_asset()。

    用于器件补录：用户上传 PDF/图片 → AI 提取引脚与参数 → 返回预览。

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
        filepath: str,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self._session = session
        self.filepath = filepath

    def run(self) -> None:
        """执行资料导入流程。"""
        try:
            self.progress.emit("正在解析上传资料…", 20)
            result = self._session.ingest_asset(self.filepath)  # type: ignore[union-attr]
            self.progress.emit("资料解析完成", 100)
            self.finished.emit(result)
        except Exception as exc:
            tb = traceback.format_exc()
            self.error.emit(f"{exc}\n\n{tb}")


class ConfirmImportWorker(QThread):
    """在后台线程中运行 SchemaForgeSession.confirm_import()。

    用于器件导入确认：用户确认预览信息后入库并续设计。

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
        answers: dict | None = None,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self._session = session
        self.answers = answers or {}

    def run(self) -> None:
        """执行导入确认流程。"""
        try:
            self.progress.emit("正在确认导入并生成设计…", 30)
            result = self._session.confirm_import(self.answers)  # type: ignore[union-attr]
            self.progress.emit("导入完成", 100)
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
