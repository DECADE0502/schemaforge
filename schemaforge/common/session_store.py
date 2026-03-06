"""SchemaForge 会话存储

管理工作流会话（器件导入、原理图设计）的持久化和恢复。
至少支持本次运行内恢复，可选支持跨次打开继续。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """对话消息"""

    role: str  # "user", "assistant", "system", "tool"
    content: str = ""
    timestamp: datetime = Field(default_factory=datetime.now)
    extra: dict[str, Any] = Field(default_factory=dict)  # 图片、附件等


class SessionRecord(BaseModel):
    """一次会话的完整记录"""

    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    session_type: str = ""  # "library_import" | "design"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    state: str = "idle"  # 当前状态机状态
    messages: list[ChatMessage] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)  # 工作流上下文数据
    metadata: dict[str, Any] = Field(default_factory=dict)

    def add_message(self, role: str, content: str, **extra: Any) -> ChatMessage:
        """添加一条消息"""
        msg = ChatMessage(role=role, content=content, extra=extra)
        self.messages.append(msg)
        self.updated_at = datetime.now()
        return msg


class SessionStore:
    """会话存储管理器

    内存存储 + 可选的 JSON 文件持久化。

    用法::

        store = SessionStore(persist_dir=Path("sessions/"))
        session = store.create("library_import")
        session.add_message("user", "上传了 TPS54202 的 datasheet")
        store.save(session)

        # 恢复
        session = store.get(session_id)
    """

    def __init__(self, persist_dir: Path | None = None) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._persist_dir = persist_dir
        if persist_dir:
            persist_dir.mkdir(parents=True, exist_ok=True)

    def create(self, session_type: str, **context: Any) -> SessionRecord:
        """创建新会话"""
        session = SessionRecord(session_type=session_type, context=context)
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> SessionRecord | None:
        """获取会话（先查内存，再查磁盘）"""
        if session_id in self._sessions:
            return self._sessions[session_id]

        if self._persist_dir:
            path = self._persist_dir / f"{session_id}.json"
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                session = SessionRecord.model_validate(data)
                self._sessions[session_id] = session
                return session

        return None

    def save(self, session: SessionRecord) -> None:
        """保存会话到内存（+可选磁盘）"""
        session.updated_at = datetime.now()
        self._sessions[session.session_id] = session

        if self._persist_dir:
            path = self._persist_dir / f"{session.session_id}.json"
            path.write_text(
                session.model_dump_json(indent=2),
                encoding="utf-8",
            )

    def delete(self, session_id: str) -> bool:
        """删除会话"""
        removed = self._sessions.pop(session_id, None) is not None

        if self._persist_dir:
            path = self._persist_dir / f"{session_id}.json"
            if path.exists():
                path.unlink()
                removed = True

        return removed

    def list_sessions(
        self,
        session_type: str | None = None,
    ) -> list[SessionRecord]:
        """列出会话（可按类型过滤）"""
        # 先从磁盘加载所有未在内存中的
        if self._persist_dir:
            for path in self._persist_dir.glob("*.json"):
                sid = path.stem
                if sid not in self._sessions:
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                        session = SessionRecord.model_validate(data)
                        self._sessions[sid] = session
                    except Exception:
                        continue

        sessions = list(self._sessions.values())
        if session_type:
            sessions = [s for s in sessions if s.session_type == session_type]
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions
