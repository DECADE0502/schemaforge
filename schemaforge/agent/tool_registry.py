"""SchemaForge 工具注册表

管理 AI 可调用的本地工具。每个工具注册后，
控制器在 AI 发起 ToolCallRequest 时查表执行。

设计原则：
- 工具不抛裸异常，统一返回 ToolResult
- 工具签名清晰，便于生成给 AI 的工具描述
- 支持运行时动态注册（不同工作流可用不同工具集）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from schemaforge.common.errors import ErrorCode, ToolError


@dataclass
class ToolResult:
    """工具执行结果"""

    success: bool = True
    data: Any = None
    error: ToolError | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，便于作为 tool_result 消息发给 AI"""
        if self.success:
            return {"success": True, "data": self.data}
        return {
            "success": False,
            "error": self.error.to_dict() if self.error else {"message": "unknown"},
        }


@dataclass
class ToolDef:
    """工具定义"""

    name: str
    description: str  # 中文描述，给 AI 的 system prompt 用
    handler: Callable[..., ToolResult]
    parameters_schema: dict[str, Any] = field(default_factory=dict)
    # 参数 JSON Schema，用于生成工具描述
    required_params: list[str] = field(default_factory=list)
    category: str = ""  # ingest, library, design, engine


class ToolRegistry:
    """工具注册表

    用法::

        registry = ToolRegistry()

        @registry.register(
            name="parse_pdf",
            description="解析 PDF 文件，提取文字和表格",
            parameters_schema={
                "filepath": {"type": "string", "description": "PDF 文件路径"},
                "page_limit": {"type": "integer", "description": "最大解析页数"},
            },
            required_params=["filepath"],
            category="ingest",
        )
        def parse_pdf(filepath: str, page_limit: int | None = None) -> ToolResult:
            ...

        # AI 调用
        result = registry.execute("parse_pdf", {"filepath": "/tmp/ds.pdf"})
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters_schema: dict[str, Any] | None = None,
        required_params: list[str] | None = None,
        category: str = "",
    ) -> Callable[..., Any]:
        """装饰器：注册工具"""
        def decorator(fn: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
            tool_def = ToolDef(
                name=name,
                description=description,
                handler=fn,
                parameters_schema=parameters_schema or {},
                required_params=required_params or [],
                category=category,
            )
            self._tools[name] = tool_def
            return fn
        return decorator

    def register_fn(
        self,
        name: str,
        description: str,
        handler: Callable[..., ToolResult],
        parameters_schema: dict[str, Any] | None = None,
        required_params: list[str] | None = None,
        category: str = "",
    ) -> None:
        """直接注册工具（非装饰器方式）"""
        tool_def = ToolDef(
            name=name,
            description=description,
            handler=handler,
            parameters_schema=parameters_schema or {},
            required_params=required_params or [],
            category=category,
        )
        self._tools[name] = tool_def

    def execute(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        """执行工具（安全包裹，不抛异常）"""
        tool_def = self._tools.get(name)
        if tool_def is None:
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.UNKNOWN,
                    message=f"未知工具: {name}",
                    retriable=False,
                ),
            )

        try:
            return tool_def.handler(**(arguments or {}))
        except Exception as exc:
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.UNKNOWN,
                    message=f"工具 {name} 执行异常: {exc}",
                    retriable=True,
                    details={"exception_type": type(exc).__name__},
                ),
            )

    def get_tool(self, name: str) -> ToolDef | None:
        """获取工具定义"""
        return self._tools.get(name)

    def list_tools(self, category: str | None = None) -> list[ToolDef]:
        """列出所有工具（可按分类过滤）"""
        tools = list(self._tools.values())
        if category:
            tools = [t for t in tools if t.category == category]
        return tools

    def get_tool_descriptions(self, category: str | None = None) -> list[dict[str, Any]]:
        """生成工具描述列表（用于 AI system prompt）

        格式接近 OpenAI function calling schema。
        """
        descriptions: list[dict[str, Any]] = []
        for tool_def in self.list_tools(category):
            desc: dict[str, Any] = {
                "name": tool_def.name,
                "description": tool_def.description,
            }
            if tool_def.parameters_schema:
                desc["parameters"] = {
                    "type": "object",
                    "properties": tool_def.parameters_schema,
                    "required": tool_def.required_params,
                }
            descriptions.append(desc)
        return descriptions
