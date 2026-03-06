"""测试 schemaforge.common 基础模块

覆盖: errors, events, progress, session_store
"""

import tempfile
from pathlib import Path

from schemaforge.common.errors import (
    ErrorCode,
    SchemaForgeError,
    ToolError,
)
from schemaforge.common.events import (
    EventType,
    LogEvent,
    ProgressEvent,
    QuestionEvent,
    StateChangeEvent,
)
from schemaforge.common.progress import ProgressTracker
from schemaforge.common.session_store import SessionStore


# ============================================================
# ToolError
# ============================================================

class TestToolError:
    def test_basic_creation(self):
        err = ToolError(code=ErrorCode.FILE_NOT_FOUND, message="test.pdf")
        assert err.code == ErrorCode.FILE_NOT_FOUND
        assert err.retriable is False

    def test_user_message(self):
        err = ToolError(code=ErrorCode.FILE_ENCRYPTED)
        assert "加密" in err.user_message

    def test_to_dict(self):
        err = ToolError(code=ErrorCode.TIMEOUT, message="10s", retriable=True)
        d = err.to_dict()
        assert d["error"] is True
        assert d["code"] == "timeout"
        assert d["retriable"] is True

    def test_unknown_code_user_message(self):
        err = ToolError(code=ErrorCode.UNKNOWN, message="自定义消息")
        assert err.user_message == "自定义消息"

    def test_schemaforge_error_carries_tool_error(self):
        te = ToolError(code=ErrorCode.AI_CALL_FAILED, message="503")
        exc = SchemaForgeError("AI挂了", tool_error=te)
        assert exc.tool_error.code == ErrorCode.AI_CALL_FAILED


# ============================================================
# Events
# ============================================================

class TestEvents:
    def test_progress_event(self):
        e = ProgressEvent(message="渲染中", percentage=70, stage="render")
        assert e.event_type == EventType.PROGRESS
        assert e.percentage == 70

    def test_log_event(self):
        e = LogEvent(event_type=EventType.LOG_WARNING, message="低置信度", source="pdf")
        assert e.event_type == EventType.LOG_WARNING

    def test_question_event(self):
        e = QuestionEvent(
            question_id="q1",
            text="GND 引脚是第几脚？",
            answer_type="number",
            required=True,
        )
        assert e.question_id == "q1"
        assert e.answer_type == "number"

    def test_state_change_event(self):
        e = StateChangeEvent(old_state="idle", new_state="collecting", reason="用户上传PDF")
        assert e.old_state == "idle"
        assert e.new_state == "collecting"


# ============================================================
# ProgressTracker
# ============================================================

class TestProgressTracker:
    def test_stage_emits_progress(self):
        events = []
        tracker = ProgressTracker(on_event=events.append, source="test")
        tracker.stage("解析PDF", 10)
        assert len(events) == 1
        assert events[0].percentage == 10

    def test_log_emits_log_event(self):
        events = []
        tracker = ProgressTracker(on_event=events.append)
        tracker.log("测试消息", "warning")
        assert len(events) == 1
        assert events[0].event_type == EventType.LOG_WARNING

    def test_done_sets_100(self):
        events = []
        tracker = ProgressTracker(on_event=events.append)
        tracker.done()
        assert events[0].percentage == 100

    def test_engine_callback(self):
        events = []
        tracker = ProgressTracker(on_event=events.append)
        cb = tracker.engine_callback()
        cb("渲染SVG", 70)
        assert events[0].percentage == 70
        assert events[0].message == "渲染SVG"

    def test_no_callback_no_error(self):
        tracker = ProgressTracker()
        tracker.stage("test", 50)
        tracker.log("msg")
        tracker.done()


# ============================================================
# SessionStore
# ============================================================

class TestSessionStore:
    def test_create_and_get(self):
        store = SessionStore()
        session = store.create("library_import")
        assert session.session_type == "library_import"
        assert session.state == "idle"

        got = store.get(session.session_id)
        assert got is not None
        assert got.session_id == session.session_id

    def test_add_message(self):
        store = SessionStore()
        session = store.create("design")
        session.add_message("user", "设计一个Buck电路")
        session.add_message("assistant", "好的，请确认输入电压")
        assert len(session.messages) == 2
        assert session.messages[0].role == "user"

    def test_save_and_load_from_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SessionStore(persist_dir=Path(tmpdir))
            session = store.create("library_import")
            session.add_message("user", "上传 TPS54202.pdf")
            store.save(session)

            # 新建 store 实例，从磁盘恢复
            store2 = SessionStore(persist_dir=Path(tmpdir))
            loaded = store2.get(session.session_id)
            assert loaded is not None
            assert loaded.session_type == "library_import"
            assert len(loaded.messages) == 1

    def test_delete(self):
        store = SessionStore()
        session = store.create("test")
        sid = session.session_id
        assert store.delete(sid) is True
        assert store.get(sid) is None

    def test_list_sessions(self):
        store = SessionStore()
        store.create("library_import")
        store.create("library_import")
        store.create("design")

        lib_sessions = store.list_sessions("library_import")
        assert len(lib_sessions) == 2

        all_sessions = store.list_sessions()
        assert len(all_sessions) == 3

    def test_get_nonexistent(self):
        store = SessionStore()
        assert store.get("nonexistent") is None
