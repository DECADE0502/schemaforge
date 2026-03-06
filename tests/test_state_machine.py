"""测试 schemaforge.workflows.state_machine

覆盖: WorkflowStateMachine, 器件入库/原理图设计状态转换表
"""

import pytest

from schemaforge.common.events import StateChangeEvent
from schemaforge.workflows.state_machine import (
    DESIGN_SESSION_TRANSITIONS,
    LIBRARY_IMPORT_TRANSITIONS,
    InvalidTransitionError,
    WorkflowStateMachine,
    create_design_session_sm,
    create_library_import_sm,
)


class TestWorkflowStateMachine:
    def test_initial_state(self):
        sm = WorkflowStateMachine(
            transitions={"idle": ["running"], "running": ["done"]},
        )
        assert sm.state == "idle"

    def test_valid_transition(self):
        sm = WorkflowStateMachine(
            transitions={"idle": ["running"], "running": ["done"]},
        )
        sm.transition("running", reason="开始工作")
        assert sm.state == "running"

    def test_invalid_transition_raises(self):
        sm = WorkflowStateMachine(
            transitions={"idle": ["running"], "running": ["done"]},
        )
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.transition("done")
        assert exc_info.value.current == "idle"
        assert exc_info.value.target == "done"
        assert "running" in exc_info.value.allowed

    def test_history_tracking(self):
        sm = WorkflowStateMachine(
            transitions={"idle": ["a"], "a": ["b"], "b": ["done"]},
        )
        sm.transition("a", reason="step1")
        sm.transition("b", reason="step2")
        sm.transition("done", reason="完成")

        # 初始化 + 3次转换 = 4条记录
        assert len(sm.history) == 4
        assert sm.history[-1]["to"] == "done"

    def test_allowed_transitions(self):
        sm = WorkflowStateMachine(
            transitions={"idle": ["a", "b"], "a": ["done"], "b": ["done"]},
        )
        assert set(sm.allowed_transitions) == {"a", "b"}

    def test_can_transition(self):
        sm = WorkflowStateMachine(
            transitions={"idle": ["running"], "running": ["done"]},
        )
        assert sm.can_transition("running") is True
        assert sm.can_transition("done") is False

    def test_is_terminal(self):
        sm = WorkflowStateMachine(
            transitions={"idle": ["done"], "done": [], "error": []},
        )
        assert sm.is_terminal is False
        sm.transition("done")
        assert sm.is_terminal is True

    def test_context(self):
        sm = WorkflowStateMachine(transitions={"idle": []})
        sm.set_context("filepath", "/tmp/test.pdf")
        assert sm.get_context("filepath") == "/tmp/test.pdf"
        assert sm.get_context("missing", "default") == "default"

    def test_force_state(self):
        sm = WorkflowStateMachine(
            transitions={"idle": ["running"], "running": ["done"]},
        )
        # 强制跳到 done（正常不允许从 idle 直接到 done）
        sm.force_state("done", reason="恢复会话")
        assert sm.state == "done"
        assert "[FORCE]" in sm.history[-1]["reason"]

    def test_reset(self):
        sm = WorkflowStateMachine(
            transitions={"idle": ["running"], "running": ["done"]},
        )
        sm.transition("running")
        sm.set_context("key", "value")
        sm.reset()
        assert sm.state == "idle"
        assert sm.get_context("key") is None

    def test_event_emission(self):
        events = []
        sm = WorkflowStateMachine(
            transitions={"idle": ["running"], "running": ["done"]},
            on_event=events.append,
        )
        sm.transition("running", reason="test")
        assert len(events) == 1
        assert isinstance(events[0], StateChangeEvent)
        assert events[0].old_state == "idle"
        assert events[0].new_state == "running"


class TestLibraryImportSM:
    def test_happy_path(self):
        sm = create_library_import_sm()
        assert sm.state == "idle"

        sm.transition("collecting")
        sm.transition("extracting")
        sm.transition("questioning")
        sm.transition("validating")
        sm.transition("review")
        sm.transition("saving")
        sm.transition("done")
        assert sm.state == "done"

    def test_questioning_loop(self):
        """AI 提问 → 用户回答 → 再次校验 → 可能再问"""
        sm = create_library_import_sm()
        sm.transition("collecting")
        sm.transition("extracting")
        sm.transition("questioning")
        sm.transition("validating")
        sm.transition("questioning")  # 校验发现还需确认
        sm.transition("validating")
        sm.transition("review")
        assert sm.state == "review"

    def test_error_recovery(self):
        sm = create_library_import_sm()
        sm.transition("collecting")
        sm.transition("error")
        sm.transition("idle")  # 从错误恢复
        assert sm.state == "idle"

    def test_all_states_exist(self):
        expected = {"idle", "collecting", "extracting", "questioning",
                    "validating", "review", "saving", "done", "error"}
        assert set(LIBRARY_IMPORT_TRANSITIONS.keys()) == expected


class TestDesignSessionSM:
    def test_happy_path(self):
        sm = create_design_session_sm()
        assert sm.state == "idle"

        sm.transition("searching")
        sm.transition("planning")
        sm.transition("questioning")
        sm.transition("validating")
        sm.transition("compiling")
        sm.transition("rendering")
        sm.transition("done")
        assert sm.state == "done"

    def test_revision_loop(self):
        """用户修改 → 重新规划 → 重新编译"""
        sm = create_design_session_sm()
        sm.transition("searching")
        sm.transition("planning")
        sm.transition("validating")
        sm.transition("compiling")
        sm.transition("rendering")
        sm.transition("revision")
        sm.transition("compiling")
        sm.transition("rendering")
        sm.transition("done")
        assert sm.state == "done"

    def test_all_states_exist(self):
        expected = {"idle", "searching", "planning", "questioning",
                    "validating", "compiling", "rendering", "revision",
                    "done", "error"}
        assert set(DESIGN_SESSION_TRANSITIONS.keys()) == expected
