"""SchemaForge 通用工作流状态机

状态机控场：不让 AI 随意漂移，所有状态流转由本地逻辑控制。

两条主工作流共享此基础设施：
- 器件入库: idle → collecting → extracting → questioning → validating → review → saving → done
- 原理图设计: idle → searching → planning → questioning → validating → compiling → rendering → revision → done

任意状态都支持: cancel, retry_last_tool, attach_more_evidence
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from schemaforge.common.events import StateChangeEvent, WorkflowEvent


class InvalidTransitionError(Exception):
    """非法状态转换"""

    def __init__(self, current: str, target: str, allowed: list[str]) -> None:
        self.current = current
        self.target = target
        self.allowed = allowed
        super().__init__(
            f"非法状态转换: {current} → {target}，"
            f"允许的目标: {allowed}"
        )


class WorkflowStateMachine:
    """通用工作流状态机

    用法::

        # 定义状态转换表
        transitions = {
            "idle":        ["collecting", "error"],
            "collecting":  ["extracting", "error", "idle"],
            "extracting":  ["questioning", "validating", "error"],
            "questioning": ["validating", "extracting", "error"],
            "validating":  ["review", "questioning", "error"],
            "review":      ["saving", "questioning", "error", "idle"],
            "saving":      ["done", "error"],
            "done":        ["idle"],
            "error":       ["idle"],
        }

        sm = WorkflowStateMachine(
            transitions=transitions,
            initial_state="idle",
            on_event=my_callback,
        )

        sm.transition("collecting", reason="用户上传了 PDF")
        sm.transition("extracting")
        print(sm.state)  # "extracting"
        print(sm.history)  # 完整状态变更历史
    """

    def __init__(
        self,
        transitions: dict[str, list[str]],
        initial_state: str = "idle",
        on_event: Callable[[WorkflowEvent], None] | None = None,
    ) -> None:
        self._transitions = transitions
        self._state = initial_state
        self._on_event = on_event
        self._history: list[dict[str, Any]] = []
        self._context: dict[str, Any] = {}

        # 记录初始状态
        self._history.append({
            "from": None,
            "to": initial_state,
            "reason": "初始化",
            "timestamp": datetime.now().isoformat(),
        })

    @property
    def state(self) -> str:
        """当前状态"""
        return self._state

    @property
    def history(self) -> list[dict[str, Any]]:
        """状态变更历史"""
        return list(self._history)

    @property
    def context(self) -> dict[str, Any]:
        """工作流上下文数据"""
        return self._context

    @property
    def allowed_transitions(self) -> list[str]:
        """当前状态允许的目标状态"""
        return list(self._transitions.get(self._state, []))

    @property
    def is_terminal(self) -> bool:
        """是否处于终态"""
        return self._state in ("done", "error")

    def can_transition(self, target: str) -> bool:
        """检查是否可以转换到目标状态"""
        allowed = self._transitions.get(self._state, [])
        return target in allowed

    def transition(self, target: str, reason: str = "") -> None:
        """执行状态转换

        Args:
            target: 目标状态
            reason: 转换原因

        Raises:
            InvalidTransitionError: 非法转换
        """
        allowed = self._transitions.get(self._state, [])
        if target not in allowed:
            raise InvalidTransitionError(self._state, target, allowed)

        old_state = self._state
        self._state = target

        record = {
            "from": old_state,
            "to": target,
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
        }
        self._history.append(record)

        # 发送状态变更事件
        if self._on_event:
            self._on_event(StateChangeEvent(
                old_state=old_state,
                new_state=target,
                reason=reason,
            ))

    def force_state(self, target: str, reason: str = "强制设置") -> None:
        """强制设置状态（不校验转换合法性）

        仅用于恢复会话、错误恢复等特殊场景。
        """
        old_state = self._state
        self._state = target
        self._history.append({
            "from": old_state,
            "to": target,
            "reason": f"[FORCE] {reason}",
            "timestamp": datetime.now().isoformat(),
        })

    def set_context(self, key: str, value: Any) -> None:
        """设置上下文数据"""
        self._context[key] = value

    def get_context(self, key: str, default: Any = None) -> Any:
        """获取上下文数据"""
        return self._context.get(key, default)

    def reset(self) -> None:
        """重置到初始状态"""
        self._state = "idle"
        self._context.clear()
        self._history.append({
            "from": self._state,
            "to": "idle",
            "reason": "重置",
            "timestamp": datetime.now().isoformat(),
        })


# ============================================================
# 预定义状态转换表
# ============================================================

#: 器件入库工作流状态转换
LIBRARY_IMPORT_TRANSITIONS: dict[str, list[str]] = {
    "idle":        ["collecting", "error"],
    "collecting":  ["extracting", "questioning", "error", "idle"],
    "extracting":  ["questioning", "validating", "error", "collecting"],
    "questioning": ["validating", "extracting", "collecting", "error"],
    "validating":  ["review", "questioning", "error"],
    "review":      ["saving", "questioning", "error", "idle"],
    "saving":      ["done", "error"],
    "done":        ["idle"],
    "error":       ["idle", "collecting"],
}

#: 原理图设计工作流状态转换
DESIGN_SESSION_TRANSITIONS: dict[str, list[str]] = {
    "idle":       ["searching", "error"],
    "searching":  ["planning", "error", "idle"],
    "planning":   ["questioning", "validating", "error", "searching"],
    "questioning": ["validating", "planning", "error"],
    "validating": ["compiling", "questioning", "error"],
    "compiling":  ["rendering", "error", "validating"],
    "rendering":  ["revision", "done", "error"],
    "revision":   ["planning", "compiling", "done", "error"],
    "done":       ["idle", "revision"],
    "error":      ["idle", "searching"],
}


def create_library_import_sm(
    on_event: Callable[[WorkflowEvent], None] | None = None,
) -> WorkflowStateMachine:
    """创建器件入库状态机"""
    return WorkflowStateMachine(
        transitions=LIBRARY_IMPORT_TRANSITIONS,
        initial_state="idle",
        on_event=on_event,
    )


def create_design_session_sm(
    on_event: Callable[[WorkflowEvent], None] | None = None,
) -> WorkflowStateMachine:
    """创建原理图设计状态机"""
    return WorkflowStateMachine(
        transitions=DESIGN_SESSION_TRANSITIONS,
        initial_state="idle",
        on_event=on_event,
    )
