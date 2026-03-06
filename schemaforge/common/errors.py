"""SchemaForge 统一错误体系

所有工具层错误统一转为 ToolError，不向 AI 抛裸异常。
GUI 收到 ToolError 时同时更新日志和对话。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ErrorCode(str, Enum):
    """错误码分类"""

    # 通用
    UNKNOWN = "unknown"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

    # 文件/输入
    FILE_NOT_FOUND = "file_not_found"
    FILE_CORRUPTED = "file_corrupted"
    FILE_ENCRYPTED = "file_encrypted"
    FILE_TOO_LARGE = "file_too_large"
    INVALID_FORMAT = "invalid_format"

    # PDF 解析
    PDF_PARSE_FAILED = "pdf_parse_failed"
    PDF_NO_TEXT = "pdf_no_text"
    PDF_PAGE_ERROR = "pdf_page_error"

    # 图片识别
    IMAGE_UNREADABLE = "image_unreadable"
    IMAGE_NOT_CIRCUIT = "image_not_circuit"

    # EasyEDA
    EASYEDA_NOT_FOUND = "easyeda_not_found"
    EASYEDA_TIMEOUT = "easyeda_timeout"
    EASYEDA_RATE_LIMIT = "easyeda_rate_limit"

    # 器件库
    DEVICE_NOT_FOUND = "device_not_found"
    DEVICE_DUPLICATE = "device_duplicate"
    DEVICE_VALIDATION = "device_validation"

    # AI
    AI_CALL_FAILED = "ai_call_failed"
    AI_PARSE_FAILED = "ai_parse_failed"
    AI_CONTEXT_TOO_LONG = "ai_context_too_long"

    # 设计
    DESIGN_INVALID = "design_invalid"
    TEMPLATE_NOT_FOUND = "template_not_found"
    TOPOLOGY_MISSING = "topology_missing"

    # 引擎
    ENGINE_FAILED = "engine_failed"
    RENDER_FAILED = "render_failed"
    EXPORT_FAILED = "export_failed"


class ToolError(BaseModel):
    """统一工具错误返回

    所有本地方法遇到异常时都应转为此对象，
    由控制器决定是重试、降级还是提示用户。
    """

    code: ErrorCode = ErrorCode.UNKNOWN
    message: str = ""
    retriable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def user_message(self) -> str:
        """面向用户的中文提示"""
        _msg_map: dict[ErrorCode, str] = {
            ErrorCode.FILE_NOT_FOUND: "文件未找到",
            ErrorCode.FILE_CORRUPTED: "文件已损坏，无法解析",
            ErrorCode.FILE_ENCRYPTED: "文件已加密，请解密后重试",
            ErrorCode.FILE_TOO_LARGE: "文件过大，请裁剪后重试",
            ErrorCode.INVALID_FORMAT: "文件格式不支持",
            ErrorCode.PDF_PARSE_FAILED: "PDF 解析失败",
            ErrorCode.PDF_NO_TEXT: "PDF 中未检测到文本内容",
            ErrorCode.IMAGE_UNREADABLE: "图片无法识别",
            ErrorCode.IMAGE_NOT_CIRCUIT: "图片内容似乎不是电路相关",
            ErrorCode.EASYEDA_NOT_FOUND: "在 EasyEDA 中未找到此器件",
            ErrorCode.EASYEDA_TIMEOUT: "EasyEDA 请求超时，请稍后重试",
            ErrorCode.DEVICE_DUPLICATE: "器件库中已存在同名器件",
            ErrorCode.AI_CALL_FAILED: "AI 调用失败，请检查网络",
            ErrorCode.AI_CONTEXT_TOO_LONG: "输入内容过长，请精简后重试",
            ErrorCode.TIMEOUT: "操作超时",
            ErrorCode.CANCELLED: "操作已取消",
        }
        return _msg_map.get(self.code, self.message or "未知错误")

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，便于传给 AI"""
        return {
            "error": True,
            "code": self.code.value,
            "message": self.message,
            "retriable": self.retriable,
        }


class SchemaForgeError(Exception):
    """SchemaForge 基础异常

    可选携带 ToolError 结构化信息。
    """

    def __init__(self, message: str, tool_error: ToolError | None = None) -> None:
        super().__init__(message)
        self.tool_error = tool_error or ToolError(
            code=ErrorCode.UNKNOWN, message=message,
        )


class IngestError(SchemaForgeError):
    """解析层异常（PDF/图片/EasyEDA）"""


class DeviceLibraryError(SchemaForgeError):
    """器件库操作异常"""


class DesignError(SchemaForgeError):
    """设计编排异常"""


class AIError(SchemaForgeError):
    """AI 调用异常"""
