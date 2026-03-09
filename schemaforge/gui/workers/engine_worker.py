"""后台引擎工作线程

提供 QThread worker 驱动 AI 链路：

- ``SchemaForgeWorker``:             `SystemDesignSession` 默认系统主链
- ``SchemaForgeReviseWorker``:       系统主链文本 revise
- ``SchemaForgeImageReviseWorker``:  系统主链图片 revise
- ``IngestAssetWorker``:             旧兼容路径器件补录（PDF/图片资料导入）
- ``ConfirmImportWorker``:           旧兼容路径导入确认
- ``SchemaForgeOrchestratedWorker``: 旧兼容 AI 多轮编排模式

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
    """在后台线程中运行 `SystemDesignSession.start()`。

    `SystemDesignSession` 是默认系统主链，支持：
    - 精确型号匹配
    - Datasheet 公式驱动参数计算
    - 系统级模块连接与单张 SVG 渲染
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
        enable_visual_review: bool = False,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self.user_input = user_input
        self._session = session
        self._enable_visual_review = enable_visual_review

    def run(self) -> None:
        """执行系统级设计流程（AI agent 驱动）。"""
        try:
            self.progress.emit("正在初始化 AI 设计助手…", 10)

            from schemaforge.system.session import SystemDesignSession, SystemDesignResult
            from schemaforge.agent.orchestrator import Orchestrator
            from schemaforge.agent.design_tools_v3 import (
                AGENT_SYSTEM_PROMPT,
                build_atomic_design_tools,
            )
            from schemaforge.agent.tools import default_registry

            if (
                isinstance(self._session, SystemDesignSession)
                and self._session.visual_review_enabled == self._enable_visual_review
            ):
                session = self._session
            else:
                session = SystemDesignSession(
                    store_dir=Path("schemaforge/store"),
                    skip_ai_parse=False,
                    enable_visual_review=self._enable_visual_review,
                )
            self.session_ready.emit(session)

            # 构建 AI orchestrator + 原子工具集 (v3)
            design_tools = build_atomic_design_tools(session)
            merged = default_registry.merge(design_tools)
            orch = Orchestrator(
                tool_registry=merged,
                system_prompt=AGENT_SYSTEM_PROMPT,
                model="kimi-k2.5",
            )

            self.progress.emit("AI 正在分析需求并调用工具…", 40)
            step = orch.run_turn(self.user_input)

            self.progress.emit("系统设计完成", 100)

            # 从 session 获取最终结果（orchestrator 的工具调用已填充 session 状态）
            if session.bundle:
                result = SystemDesignResult(
                    status="generated",
                    message=step.message or "AI 设计完成",
                    bundle=session.bundle,
                )
            else:
                result = SystemDesignResult(
                    status="partial" if step.message else "failed",
                    message=step.message or "AI 未能完成设计",
                    bundle=session.bundle,
                )
            self.finished.emit(result)
        except Exception as exc:
            tb = traceback.format_exc()
            self.error.emit(f"{exc}\n\n{tb}")


class SchemaForgeReviseWorker(QThread):
    """在后台线程中运行 `SystemDesignSession.revise()`。

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


class SchemaForgeImageReviseWorker(QThread):
    """在后台线程中运行 SystemDesignSession.revise_from_image()。"""

    finished = Signal(object)
    error = Signal(str)
    progress = Signal(str, int)

    def __init__(
        self,
        session: object,
        base64_png: str,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self._session = session
        self.base64_png = base64_png

    def run(self) -> None:
        """执行图片反馈修改流程。"""
        try:
            self.progress.emit("正在分析图片修改意图…", 20)
            result = self._session.revise_from_image(self.base64_png)  # type: ignore[union-attr]
            self.progress.emit("图片修改完成", 100)
            self.finished.emit(result)
        except Exception as exc:
            tb = traceback.format_exc()
            self.error.emit(f"{exc}\n\n{tb}")



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
