"""工作流工作线程

在后台线程运行工作流，通过 Qt 信号与 GUI 通信。
每个会话一个 worker，防止 AI/工具调用阻塞 UI。

设计要求：
- AI 调用不阻塞 UI
- 工具调用不阻塞 UI
- 支持取消
- 事件信号统一
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from schemaforge.common.events import (
    LogEvent,
    ProgressEvent,
    QuestionEvent,
    WorkflowEvent,
)
from schemaforge.common.progress import ProgressTracker


class CancellationToken:
    """取消令牌 — 线程安全"""

    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def reset(self) -> None:
        self._cancelled.clear()


class WorkflowWorker(QObject):
    """通用工作流后台 Worker

    用法::

        worker = WorkflowWorker()
        thread = QThread()
        worker.moveToThread(thread)

        worker.sig_event.connect(on_workflow_event)
        worker.sig_question.connect(on_question)
        worker.sig_done.connect(on_done)
        worker.sig_error.connect(on_error)

        thread.started.connect(lambda: worker.run(my_workflow_fn))
        thread.start()

    信号:
        sig_event(WorkflowEvent): 通用事件（进度、日志、状态变更）
        sig_progress(str, int): 进度更新 (消息, 百分比)
        sig_log(str): 日志消息
        sig_question(dict): AI 提问（需要用户回答）
        sig_preview(dict): 预览更新
        sig_done(object): 工作流完成，携带结果
        sig_error(str): 工作流异常
        sig_state(str, str): 状态变更 (旧状态, 新状态)
    """

    sig_event = Signal(object)       # WorkflowEvent
    sig_progress = Signal(str, int)  # (message, percentage)
    sig_log = Signal(str)            # message
    sig_question = Signal(dict)      # question data dict
    sig_preview = Signal(dict)       # preview data dict
    sig_done = Signal(object)        # result
    sig_error = Signal(str)          # error message
    sig_state = Signal(str, str)     # (old_state, new_state)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cancel_token = CancellationToken()
        self._user_answer: str | None = None
        self._answer_event = threading.Event()

    def create_tracker(self, source: str = "workflow") -> ProgressTracker:
        """创建与此 Worker 绑定的 ProgressTracker"""
        return ProgressTracker(
            on_event=self._dispatch_event,
            source=source,
        )

    def _dispatch_event(self, event: WorkflowEvent) -> None:
        """分发事件到对应信号"""
        self.sig_event.emit(event)

        if isinstance(event, ProgressEvent):
            self.sig_progress.emit(event.message, event.percentage)
        elif isinstance(event, LogEvent):
            self.sig_log.emit(event.message)
        elif isinstance(event, QuestionEvent):
            self.sig_question.emit({
                "question_id": event.question_id,
                "text": event.text,
                "answer_type": event.answer_type,
                "choices": event.choices,
                "default": event.default,
                "evidence_summary": event.evidence_summary,
            })

    def run(self, workflow_fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """在当前线程中运行工作流函数

        workflow_fn 应接受 tracker 和 cancel_token 参数。
        """
        self.cancel_token.reset()

        try:
            tracker = self.create_tracker()
            result = workflow_fn(
                *args,
                tracker=tracker,
                cancel_token=self.cancel_token,
                **kwargs,
            )
            if self.cancel_token.is_cancelled:
                self.sig_error.emit("操作已取消")
            else:
                self.sig_done.emit(result)
        except Exception as exc:
            self.sig_error.emit(f"工作流异常: {exc}")

    def submit_answer(self, answer: str) -> None:
        """GUI 线程提交用户回答（线程安全）"""
        self._user_answer = answer
        self._answer_event.set()

    def wait_for_answer(self, timeout: float = 300.0) -> str | None:
        """在工作线程中等待用户回答（阻塞）

        Returns:
            用户回答文本，超时或取消返回 None
        """
        self._user_answer = None
        self._answer_event.clear()

        while not self._answer_event.wait(timeout=0.5):
            if self.cancel_token.is_cancelled:
                return None

        return self._user_answer

    def cancel(self) -> None:
        """请求取消"""
        self.cancel_token.cancel()
        self._answer_event.set()  # 唤醒等待中的线程
