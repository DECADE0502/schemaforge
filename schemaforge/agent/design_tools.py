"""SchemaForge 设计工作台工具集。"""

from __future__ import annotations

from schemaforge.agent.tool_registry import ToolRegistry, ToolResult
from schemaforge.common.errors import ErrorCode, ToolError
from schemaforge.workflows.schemaforge_session import SchemaForgeSession


def build_design_tool_registry(session: SchemaForgeSession) -> ToolRegistry:
    """为指定会话构建可调用工具集。"""
    registry = ToolRegistry()

    registry.register_fn(
        name="start_design_request",
        description="从用户自然语言启动设计，会精确匹配显式型号。",
        handler=lambda user_input: ToolResult(
            success=True,
            data=session.start(user_input).to_dict(),
        ),
        parameters_schema={
            "user_input": {"type": "string", "description": "用户中文设计请求"}
        },
        required_params=["user_input"],
        category="design",
    )
    registry.register_fn(
        name="ingest_datasheet_asset",
        description="上传 PDF 或图片后，解析器件信息并返回导入预览。",
        handler=lambda filepath: ToolResult(
            success=True,
            data=session.ingest_asset(filepath).to_dict(),
        ),
        parameters_schema={
            "filepath": {"type": "string", "description": "本地 PDF 或图片路径"}
        },
        required_params=["filepath"],
        category="design",
    )
    registry.register_fn(
        name="confirm_import_device",
        description="确认导入器件并继续完成设计。",
        handler=lambda answers=None: ToolResult(
            success=True,
            data=session.confirm_import(answers).to_dict(),
        ),
        parameters_schema={
            "answers": {"type": "object", "description": "用户确认/补充信息"}
        },
        category="design",
    )
    registry.register_fn(
        name="apply_design_revision",
        description="在当前设计上应用自然语言修改。",
        handler=lambda user_input: ToolResult(
            success=True,
            data=session.revise(user_input).to_dict(),
        ),
        parameters_schema={
            "user_input": {"type": "string", "description": "用户中文修改请求"}
        },
        required_params=["user_input"],
        category="design",
    )
    return registry


def validate_design_tool_result(result: ToolResult) -> ToolResult:
    """对工具结果做最小一致性检查。"""
    if result.success or result.error is not None:
        return result
    return ToolResult(
        success=False,
        error=ToolError(
            code=ErrorCode.UNKNOWN,
            message="设计工具返回了无错误对象的失败结果。",
        ),
    )
