"""SchemaForge AI 协议定义

核心设计原则：AI 决策，本地执行，状态机控场。

AgentStep 是 AI 每轮输出的结构化动作，控制器解析后执行。
不让模型自由输出文本后由代码猜意图。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AgentAction(str, Enum):
    """AI 允许的动作类型（严格白名单）"""

    CALL_TOOLS = "call_tools"       # 调用本地工具
    ASK_USER = "ask_user"           # 向用户提问
    PRESENT_DRAFT = "present_draft" # 展示草稿/方案
    APPLY_PATCH = "apply_patch"     # 修改已有设计
    FINALIZE = "finalize"           # 完成当前步骤
    FAIL = "fail"                   # 报告失败


class ToolCallRequest(BaseModel):
    """工具调用请求"""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str = ""  # 用于关联调用结果


class QuestionItem(BaseModel):
    """AI 提问项"""

    question_id: str = ""
    text: str  # 问题文本（中文）
    field_path: str = ""  # 关联的数据字段路径（如 "pins[3].name"）
    answer_type: str = "text"  # text, choice, confirm, number, image
    choices: list[str] = Field(default_factory=list)
    required: bool = True
    default: str = ""
    evidence: str = ""  # AI 为什么要问（引用依据）


class RationalityIssue(BaseModel):
    """合理性检查发现的问题"""

    rule_id: str = ""
    severity: str = "warning"  # info, warning, error
    message: str = ""
    suggestion: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class PatchOp(BaseModel):
    """设计修改操作"""

    op: str  # set, add, remove, replace
    path: str  # JSON path，如 "modules[0].parameters.c_out"
    value: Any = None
    reason: str = ""


class EvidenceRef(BaseModel):
    """证据引用 — 追溯 AI 关键结论的来源"""

    source_type: str  # "pdf", "image", "easyeda", "user", "ai_inferred"
    path: str = ""  # 文件路径或 URL
    page: int | None = None
    bbox: list[float] = Field(default_factory=list)  # [x0, y0, x1, y1]
    summary: str = ""
    confidence: float = 1.0  # 0-1，AI 推断的低于阈值需要用户确认


class AgentStep(BaseModel):
    """AI 每轮输出的结构化动作

    控制器解析此对象后执行对应逻辑，
    不让 AI 直接控制状态持久化。

    典型流程:
        1. AI 输出 AgentStep(action=CALL_TOOLS, tool_calls=[...])
        2. 控制器执行工具，收集结果
        3. 将结果作为 tool_result 消息发回 AI
        4. AI 输出下一个 AgentStep
        5. 重复直到 action=FINALIZE 或 FAIL
    """

    action: AgentAction
    message: str = ""  # 给用户看的中文说明

    # CALL_TOOLS
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)

    # ASK_USER
    questions: list[QuestionItem] = Field(default_factory=list)

    # PRESENT_DRAFT / APPLY_PATCH
    proposal: dict[str, Any] = Field(default_factory=dict)
    patch_ops: list[PatchOp] = Field(default_factory=list)

    # 合理性检查结果
    checks: list[RationalityIssue] = Field(default_factory=list)

    # 状态提示（建议下一状态，由控制器决定是否采纳）
    next_state: str = ""

    @classmethod
    def tools(
        cls,
        calls: list[ToolCallRequest],
        message: str = "",
    ) -> AgentStep:
        """快捷构造：调用工具"""
        return cls(action=AgentAction.CALL_TOOLS, tool_calls=calls, message=message)

    @classmethod
    def ask(
        cls,
        questions: list[QuestionItem],
        message: str = "",
    ) -> AgentStep:
        """快捷构造：提问"""
        return cls(action=AgentAction.ASK_USER, questions=questions, message=message)

    @classmethod
    def draft(
        cls,
        proposal: dict[str, Any],
        message: str = "",
        checks: list[RationalityIssue] | None = None,
    ) -> AgentStep:
        """快捷构造：展示草稿"""
        return cls(
            action=AgentAction.PRESENT_DRAFT,
            proposal=proposal,
            message=message,
            checks=checks or [],
        )

    @classmethod
    def done(cls, message: str = "完成") -> AgentStep:
        """快捷构造：完成"""
        return cls(action=AgentAction.FINALIZE, message=message)

    @classmethod
    def fail(cls, message: str) -> AgentStep:
        """快捷构造：失败"""
        return cls(action=AgentAction.FAIL, message=message)
