"""测试 schemaforge.agent.tool_registry

覆盖: ToolRegistry 注册、执行、异常处理、工具描述生成
"""

from schemaforge.agent.tool_registry import ToolDef, ToolRegistry, ToolResult
from schemaforge.common.errors import ErrorCode, ToolError


class TestToolResult:
    def test_success_result(self):
        r = ToolResult(success=True, data={"pins": 8})
        d = r.to_dict()
        assert d["success"] is True
        assert d["data"]["pins"] == 8

    def test_error_result(self):
        r = ToolResult(
            success=False,
            error=ToolError(code=ErrorCode.FILE_NOT_FOUND, message="not found"),
        )
        d = r.to_dict()
        assert d["success"] is False
        assert d["error"]["code"] == "file_not_found"


class TestToolRegistry:
    def test_register_and_execute(self):
        registry = ToolRegistry()

        @registry.register(
            name="add_numbers",
            description="两数相加",
            parameters_schema={
                "a": {"type": "number", "description": "第一个数"},
                "b": {"type": "number", "description": "第二个数"},
            },
            required_params=["a", "b"],
            category="math",
        )
        def add_numbers(a: int, b: int) -> ToolResult:
            return ToolResult(success=True, data={"sum": a + b})

        result = registry.execute("add_numbers", {"a": 3, "b": 5})
        assert result.success
        assert result.data["sum"] == 8

    def test_register_fn(self):
        registry = ToolRegistry()

        def my_tool(x: str) -> ToolResult:
            return ToolResult(success=True, data=x.upper())

        registry.register_fn(
            name="upper",
            description="转大写",
            handler=my_tool,
            category="text",
        )

        result = registry.execute("upper", {"x": "hello"})
        assert result.success
        assert result.data == "HELLO"

    def test_execute_unknown_tool(self):
        registry = ToolRegistry()
        result = registry.execute("nonexistent")
        assert not result.success
        assert result.error is not None
        assert "未知工具" in result.error.message

    def test_execute_exception_handling(self):
        registry = ToolRegistry()

        @registry.register(name="boom", description="会爆炸的工具")
        def boom() -> ToolResult:
            raise RuntimeError("💥")

        result = registry.execute("boom")
        assert not result.success
        assert result.error is not None
        assert result.error.retriable is True

    def test_list_tools(self):
        registry = ToolRegistry()

        @registry.register(name="t1", description="工具1", category="ingest")
        def t1() -> ToolResult:
            return ToolResult()

        @registry.register(name="t2", description="工具2", category="design")
        def t2() -> ToolResult:
            return ToolResult()

        @registry.register(name="t3", description="工具3", category="ingest")
        def t3() -> ToolResult:
            return ToolResult()

        assert len(registry.list_tools()) == 3
        assert len(registry.list_tools("ingest")) == 2
        assert len(registry.list_tools("design")) == 1

    def test_get_tool(self):
        registry = ToolRegistry()

        @registry.register(name="test_tool", description="测试")
        def test_tool() -> ToolResult:
            return ToolResult()

        td = registry.get_tool("test_tool")
        assert td is not None
        assert td.name == "test_tool"

        assert registry.get_tool("nonexistent") is None

    def test_tool_descriptions(self):
        registry = ToolRegistry()

        @registry.register(
            name="parse_pdf",
            description="解析PDF文件",
            parameters_schema={
                "filepath": {"type": "string", "description": "PDF路径"},
            },
            required_params=["filepath"],
            category="ingest",
        )
        def parse_pdf(filepath: str) -> ToolResult:
            return ToolResult()

        descs = registry.get_tool_descriptions("ingest")
        assert len(descs) == 1
        assert descs[0]["name"] == "parse_pdf"
        assert "parameters" in descs[0]
        assert "filepath" in descs[0]["parameters"]["properties"]
