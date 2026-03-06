"""AI 工具注册

将所有可供 AI Agent 调用的本地工具注册到 ToolRegistry。
导入本模块即可完成注册。

已注册工具:
  - build_symbol: 从结构化引脚数据构建 KLC 兼容符号
  - parse_pdf: 解析 PDF 文件提取文字和表格
  - analyze_datasheet_text: AI 分析 datasheet 文本
  - analyze_image: AI vision 分析引脚图/封装图
  - analyze_combined: 文本 + 图片融合分析
  - save_device: 将 DeviceDraft 入库
  - get_device: 获取器件信息
  - search_devices: 搜索器件库
  - render_symbol_preview: 将 SymbolDef 渲染为 PNG
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from schemaforge.agent.tool_registry import ToolRegistry, ToolResult
from schemaforge.common.errors import ErrorCode, ToolError

logger = logging.getLogger(__name__)

# 全局默认 ToolRegistry 实例
default_registry = ToolRegistry()


# ============================================================
# 工具 1: build_symbol — 符号构建
# ============================================================


@default_registry.register(
    name="build_symbol",
    description="从结构化引脚数据构建 KLC 兼容的器件符号。"
    "输入引脚列表 (name/number/type/description)，"
    "输出完整 SymbolDef（含方位、槽位、尺寸）。",
    parameters_schema={
        "part_number": {
            "type": "string",
            "description": "器件型号，如 AMS1117-3.3",
        },
        "pins": {
            "type": "array",
            "description": "引脚列表，每项含 name, number, type, description",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "number": {"type": "string"},
                    "type": {"type": "string"},
                    "description": {"type": "string"},
                },
            },
        },
        "category": {
            "type": "string",
            "description": "器件类别 (ldo/buck/mcu/...)",
        },
        "package": {
            "type": "string",
            "description": "封装类型 (SOT-223/SOIC-8/...)",
        },
    },
    required_params=["part_number", "pins"],
    category="library",
)
def tool_build_symbol(
    part_number: str,
    pins: list[dict[str, str]],
    category: str = "",
    package: str = "",
) -> ToolResult:
    """构建器件符号。"""
    try:
        from schemaforge.library.symbol_builder import build_symbol

        symbol = build_symbol(
            part_number=part_number,
            pins_data=pins,
            category=category,
            package=package,
        )
        return ToolResult(
            success=True,
            data={
                "part_number": part_number,
                "pin_count": len(symbol.pins),
                "size": list(symbol.size) if symbol.size else None,
                "pins": [
                    {
                        "name": p.name,
                        "pin_number": p.pin_number,
                        "side": p.side.value,
                        "pin_type": p.pin_type.value,
                        "slot": p.slot,
                    }
                    for p in symbol.pins
                ],
                "symbol_def": symbol.model_dump(),
            },
        )
    except ValueError as exc:
        return ToolResult(
            success=False,
            error=ToolError(
                code=ErrorCode.DEVICE_VALIDATION,
                message=f"符号构建失败: {exc}",
                retriable=False,
            ),
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            error=ToolError(
                code=ErrorCode.UNKNOWN,
                message=f"符号构建异常: {exc}",
                retriable=True,
            ),
        )


# ============================================================
# 工具 2: parse_pdf — PDF 解析
# ============================================================


@default_registry.register(
    name="parse_pdf",
    description="解析 PDF 文件，提取文字内容和表格数据。",
    parameters_schema={
        "filepath": {
            "type": "string",
            "description": "PDF 文件路径",
        },
        "page_limit": {
            "type": "integer",
            "description": "最大解析页数 (默认10)",
        },
    },
    required_params=["filepath"],
    category="ingest",
)
def tool_parse_pdf(
    filepath: str,
    page_limit: int = 10,
) -> ToolResult:
    """解析 PDF 文件。"""
    from schemaforge.ingest.pdf_parser import parse_pdf

    return parse_pdf(filepath, page_limit=page_limit)


# ============================================================
# 工具 3: analyze_datasheet_text — AI 文本分析
# ============================================================


@default_registry.register(
    name="analyze_datasheet_text",
    description="用 AI 分析 datasheet 文本内容，提取器件型号、引脚、参数等。",
    parameters_schema={
        "text": {
            "type": "string",
            "description": "PDF 提取的文本内容",
        },
        "hint": {
            "type": "string",
            "description": "器件型号提示 (可选)",
        },
    },
    required_params=["text"],
    category="ingest",
)
def tool_analyze_text(
    text: str,
    hint: str = "",
) -> ToolResult:
    """AI 文本分析。"""
    from schemaforge.ingest.ai_analyzer import analyze_datasheet_text

    return analyze_datasheet_text(text, hint=hint)


# ============================================================
# 工具 4: analyze_image — AI 图片分析
# ============================================================


@default_registry.register(
    name="analyze_image",
    description="用 AI vision 分析引脚图或封装图，识别引脚定义。",
    parameters_schema={
        "filepath": {
            "type": "string",
            "description": "图片文件路径",
        },
        "task_hint": {
            "type": "string",
            "description": "分析提示 (可选)",
        },
    },
    required_params=["filepath"],
    category="ingest",
)
def tool_analyze_image(
    filepath: str,
    task_hint: str = "",
) -> ToolResult:
    """AI 图片分析。"""
    from schemaforge.ingest.ai_analyzer import analyze_image_file

    return analyze_image_file(filepath, task_hint=task_hint)


# ============================================================
# 工具 5: analyze_combined — 文本+图片融合分析
# ============================================================


@default_registry.register(
    name="analyze_combined",
    description="文本 + 多张图片融合分析，交叉验证引脚信息。",
    parameters_schema={
        "text": {
            "type": "string",
            "description": "PDF 提取的文本",
        },
        "image_paths": {
            "type": "array",
            "description": "图片文件路径列表",
            "items": {"type": "string"},
        },
        "hint": {
            "type": "string",
            "description": "器件型号提示 (可选)",
        },
    },
    required_params=["text", "image_paths"],
    category="ingest",
)
def tool_analyze_combined(
    text: str,
    image_paths: list[str],
    hint: str = "",
) -> ToolResult:
    """融合分析。"""
    image_list: list[bytes] = []
    for p in image_paths:
        fp = Path(p)
        if fp.exists() and fp.stat().st_size < 10 * 1024 * 1024:
            image_list.append(fp.read_bytes())

    from schemaforge.ingest.ai_analyzer import analyze_combined

    return analyze_combined(text, image_list, hint=hint)


# ============================================================
# 工具 6: save_device — 入库
# ============================================================


@default_registry.register(
    name="save_device",
    description="将器件草稿校验后保存到器件库。",
    parameters_schema={
        "part_number": {"type": "string", "description": "器件型号"},
        "category": {"type": "string", "description": "器件类别"},
        "manufacturer": {"type": "string", "description": "制造商"},
        "description": {"type": "string", "description": "器件描述"},
        "package": {"type": "string", "description": "封装"},
        "pins": {
            "type": "array",
            "description": "引脚列表",
            "items": {"type": "object"},
        },
        "specs": {"type": "object", "description": "电气参数"},
        "symbol_def": {
            "type": "object",
            "description": "SymbolDef (由 build_symbol 生成)",
        },
    },
    required_params=["part_number"],
    category="library",
)
def tool_save_device(
    part_number: str,
    category: str = "",
    manufacturer: str = "",
    description: str = "",
    package: str = "",
    pins: list[dict[str, str]] | None = None,
    specs: dict[str, str] | None = None,
    symbol_def: dict[str, Any] | None = None,
) -> ToolResult:
    """器件入库。"""
    from schemaforge.library.models import SymbolDef
    from schemaforge.library.service import LibraryService
    from schemaforge.library.validator import DeviceDraft, PinDraft

    pin_drafts: list[PinDraft] = []
    if pins:
        for p in pins:
            pin_drafts.append(PinDraft(
                name=p.get("name", ""),
                number=p.get("number", ""),
                pin_type=p.get("type", ""),
                side=p.get("side", ""),
                description=p.get("description", ""),
            ))

    draft = DeviceDraft(
        part_number=part_number,
        category=category,
        manufacturer=manufacturer,
        description=description,
        package=package,
        pins=pin_drafts,
        pin_count=len(pin_drafts),
        specs=specs or {},
        source="ai_pipeline",
    )

    service = LibraryService(store_dir="schemaforge/store")
    result = service.add_device_from_draft(draft, force=True)

    if result.success and result.device and symbol_def:
        try:
            sym = SymbolDef.model_validate(symbol_def)
            service.update_device_symbol(part_number, sym)
        except Exception as exc:
            logger.warning("符号保存失败: %s", exc)

    if result.success:
        return ToolResult(
            success=True,
            data=result.to_dict(),
        )
    return ToolResult(
        success=False,
        error=ToolError(
            code=ErrorCode.DEVICE_VALIDATION,
            message=result.error_message,
        ),
    )


# ============================================================
# 工具 7: get_device — 查询器件
# ============================================================


@default_registry.register(
    name="get_device",
    description="从器件库获取器件的完整信息。",
    parameters_schema={
        "part_number": {"type": "string", "description": "器件型号"},
    },
    required_params=["part_number"],
    category="library",
)
def tool_get_device(part_number: str) -> ToolResult:
    """查询器件。"""
    from schemaforge.library.service import LibraryService

    service = LibraryService(store_dir="schemaforge/store")
    device = service.get(part_number)
    if device is None:
        return ToolResult(
            success=False,
            error=ToolError(
                code=ErrorCode.DEVICE_NOT_FOUND,
                message=f"器件不存在: {part_number}",
            ),
        )
    return ToolResult(success=True, data=device.model_dump())


# ============================================================
# 工具 8: search_devices — 搜索器件库
# ============================================================


@default_registry.register(
    name="search_devices",
    description="搜索器件库，支持型号/类别/制造商模糊匹配。",
    parameters_schema={
        "query": {"type": "string", "description": "搜索关键词"},
        "category": {"type": "string", "description": "器件类别筛选"},
    },
    required_params=["query"],
    category="library",
)
def tool_search_devices(
    query: str,
    category: str = "",
) -> ToolResult:
    """搜索器件。"""
    from schemaforge.library.service import LibraryService

    service = LibraryService(store_dir="schemaforge/store")
    devices = service.search(query=query, category=category)
    return ToolResult(
        success=True,
        data=[
            {
                "part_number": d.part_number,
                "category": d.category,
                "manufacturer": d.manufacturer,
                "package": d.package,
                "description": d.description,
            }
            for d in devices
        ],
    )


# ============================================================
# 工具 9: render_symbol_preview — 渲染符号预览
# ============================================================


@default_registry.register(
    name="render_symbol_preview",
    description="将 SymbolDef 渲染为 PNG 图片字节流，用于视觉审查。",
    parameters_schema={
        "symbol_def": {
            "type": "object",
            "description": "SymbolDef JSON (由 build_symbol 输出)",
        },
        "label": {"type": "string", "description": "IC 标签 (器件型号)"},
    },
    required_params=["symbol_def"],
    category="library",
)
def tool_render_preview(
    symbol_def: dict[str, Any],
    label: str = "",
) -> ToolResult:
    """渲染符号预览。"""
    try:
        from schemaforge.library.models import SymbolDef
        from schemaforge.schematic.renderer import TopologyRenderer

        sym = SymbolDef.model_validate(symbol_def)
        png_bytes = TopologyRenderer.render_symbol_preview(sym, label=label)

        import base64

        return ToolResult(
            success=True,
            data={
                "png_base64": base64.b64encode(png_bytes).decode("ascii"),
                "pin_count": len(sym.pins),
                "size": list(sym.size) if sym.size else None,
            },
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            error=ToolError(
                code=ErrorCode.UNKNOWN,
                message=f"符号渲染失败: {exc}",
                retriable=True,
            ),
        )
