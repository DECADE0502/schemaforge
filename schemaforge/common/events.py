"""SchemaForge 事件体系

统一的事件对象，用于工作流 → GUI 的通信。
所有信号通过事件对象传递，不使用裸字符串。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """事件类型"""

    # 进度
    PROGRESS = "progress"
    STAGE_CHANGE = "stage_change"

    # 日志
    LOG_INFO = "log_info"
    LOG_WARNING = "log_warning"
    LOG_ERROR = "log_error"
    LOG_DEBUG = "log_debug"

    # AI 交互
    AI_THINKING = "ai_thinking"
    AI_QUESTION = "ai_question"
    AI_PROPOSAL = "ai_proposal"
    AI_TOOL_CALL = "ai_tool_call"
    AI_TOOL_RESULT = "ai_tool_result"

    # 预览更新
    PREVIEW_SVG = "preview_svg"
    PREVIEW_IMAGE = "preview_image"
    PREVIEW_DRAFT = "preview_draft"

    # 状态变化
    STATE_CHANGE = "state_change"
    SESSION_DONE = "session_done"
    SESSION_ERROR = "session_error"


class ProgressEvent(BaseModel):
    """进度事件"""

    event_type: EventType = EventType.PROGRESS
    message: str = ""
    percentage: int = 0  # 0-100, 0 表示不确定
    stage: str = ""
    timestamp: datetime = Field(default_factory=datetime.now)


class LogEvent(BaseModel):
    """日志事件"""

    event_type: EventType = EventType.LOG_INFO
    message: str = ""
    source: str = ""  # 事件来源模块
    timestamp: datetime = Field(default_factory=datetime.now)
    extra: dict[str, Any] = Field(default_factory=dict)


class QuestionEvent(BaseModel):
    """AI 提问事件 — AI 向用户提问，等待回答"""

    event_type: EventType = EventType.AI_QUESTION
    question_id: str = ""
    text: str = ""  # 问题文本（中文）
    field_path: str = ""  # 关联的数据字段路径
    answer_type: str = "text"  # text, choice, confirm, number
    choices: list[str] = Field(default_factory=list)
    required: bool = True
    default: str = ""
    evidence_summary: str = ""  # AI 为什么要问这个问题
    timestamp: datetime = Field(default_factory=datetime.now)


class ProposalEvent(BaseModel):
    """AI 提案事件 — AI 提出建议，等待用户确认"""

    event_type: EventType = EventType.AI_PROPOSAL
    proposal_id: str = ""
    title: str = ""
    description: str = ""
    changes: list[dict[str, Any]] = Field(default_factory=list)
    auto_accept: bool = False  # 低风险提案可自动接受
    timestamp: datetime = Field(default_factory=datetime.now)


class PreviewEvent(BaseModel):
    """预览更新事件"""

    event_type: EventType = EventType.PREVIEW_DRAFT
    preview_type: str = ""  # svg, image, draft_table, pin_diagram
    data: Any = None  # 路径、base64 或结构化数据
    label: str = ""
    timestamp: datetime = Field(default_factory=datetime.now)


class StateChangeEvent(BaseModel):
    """状态机转换事件"""

    event_type: EventType = EventType.STATE_CHANGE
    old_state: str = ""
    new_state: str = ""
    reason: str = ""
    timestamp: datetime = Field(default_factory=datetime.now)


# 所有事件的联合类型
WorkflowEvent = (
    ProgressEvent
    | LogEvent
    | QuestionEvent
    | ProposalEvent
    | PreviewEvent
    | StateChangeEvent
)
