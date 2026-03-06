"""SchemaForge 进度管理

提供进度追踪器和 GUI 桥接，将工作流进度事件转为 Qt 信号。
"""

from __future__ import annotations

from typing import Callable

from schemaforge.common.events import (
    EventType,
    LogEvent,
    ProgressEvent,
    WorkflowEvent,
)


class ProgressTracker:
    """进度追踪器

    工作流用此对象汇报进度和日志，ProgressTracker 转为事件
    推送给注册的回调。支持嵌套子任务。

    用法::

        tracker = ProgressTracker(on_event=my_callback)
        tracker.stage("解析PDF", 10)
        tracker.log("正在提取第1页文字...")
        tracker.advance(20)
        tracker.stage("AI分析", 30)
    """

    def __init__(
        self,
        on_event: Callable[[WorkflowEvent], None] | None = None,
        source: str = "",
    ) -> None:
        self._on_event = on_event
        self._source = source
        self._current_stage = ""
        self._percentage = 0

    def _emit(self, event: WorkflowEvent) -> None:
        if self._on_event:
            self._on_event(event)

    def stage(self, name: str, percentage: int | None = None) -> None:
        """进入新阶段"""
        self._current_stage = name
        if percentage is not None:
            self._percentage = percentage
        self._emit(ProgressEvent(
            message=name,
            percentage=self._percentage,
            stage=name,
        ))

    def advance(self, percentage: int) -> None:
        """更新进度百分比"""
        self._percentage = percentage
        self._emit(ProgressEvent(
            message=self._current_stage,
            percentage=percentage,
            stage=self._current_stage,
        ))

    def log(self, message: str, level: str = "info") -> None:
        """写入日志"""
        event_type = {
            "info": EventType.LOG_INFO,
            "warning": EventType.LOG_WARNING,
            "error": EventType.LOG_ERROR,
            "debug": EventType.LOG_DEBUG,
        }.get(level, EventType.LOG_INFO)

        self._emit(LogEvent(
            event_type=event_type,
            message=message,
            source=self._source,
        ))

    def done(self, message: str = "完成") -> None:
        """标记完成"""
        self._percentage = 100
        self._emit(ProgressEvent(
            message=message,
            percentage=100,
            stage="done",
        ))

    def error(self, message: str) -> None:
        """记录错误"""
        self._emit(LogEvent(
            event_type=EventType.LOG_ERROR,
            message=message,
            source=self._source,
        ))

    def engine_callback(self) -> Callable[[str, int], None]:
        """返回兼容 engine.process(progress_callback=...) 的回调

        桥接现有引擎的 progress_callback(message, percentage) 接口。
        """
        def _cb(message: str, percentage: int) -> None:
            self._current_stage = message
            self._percentage = percentage
            self._emit(ProgressEvent(
                message=message,
                percentage=percentage,
                stage=message,
            ))

        return _cb
