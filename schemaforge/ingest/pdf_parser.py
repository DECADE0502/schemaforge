"""PDF 解析器

从 PDF datasheet 提取文字、表格、关键页图片。
AI 只调用此接口，不负责底层实现。

依赖:
- PyMuPDF (fitz) — 主解析器
- pdfplumber — 表格提取备选
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from schemaforge.agent.tool_registry import ToolResult
from schemaforge.common.errors import ErrorCode, ToolError


@dataclass
class TableData:
    """提取的表格"""

    page: int
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    caption: str = ""


@dataclass
class ImageRef:
    """页面渲染的图片引用"""

    page: int
    image_bytes: bytes = b""
    width: int = 0
    height: int = 0
    format: str = "png"

    @property
    def base64(self) -> str:
        """Base64 编码，用于发送给 AI vision"""
        return base64.b64encode(self.image_bytes).decode("ascii")

    @property
    def data_url(self) -> str:
        """data URL 格式"""
        return f"data:image/{self.format};base64,{self.base64}"


@dataclass
class PdfParseResult:
    """PDF 解析结果"""

    filepath: str = ""
    total_pages: int = 0
    text_by_page: dict[int, str] = field(default_factory=dict)
    tables: list[TableData] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        """合并全部页面文字"""
        return "\n\n".join(
            f"--- 第{p}页 ---\n{text}"
            for p, text in sorted(self.text_by_page.items())
        )

    @property
    def summary(self) -> str:
        """摘要信息，用于日志"""
        return (
            f"PDF: {self.filepath} | {self.total_pages}页 | "
            f"{len(self.tables)}个表格 | {len(self.images)}张图片"
        )


def parse_pdf(
    filepath: str,
    page_limit: int | None = None,
) -> ToolResult:
    """解析 PDF 文件，提取文字和表格

    Args:
        filepath: PDF 文件路径
        page_limit: 最大解析页数（None=全部）

    Returns:
        ToolResult，data 为 PdfParseResult
    """
    path = Path(filepath)
    if not path.exists():
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"文件不存在: {filepath}",
        ))

    if path.suffix.lower() != ".pdf":
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.INVALID_FORMAT,
            message=f"不是 PDF 文件: {path.suffix}",
        ))

    # 文件大小检查（50MB 上限）
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > 50:
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.FILE_TOO_LARGE,
            message=f"PDF 过大: {size_mb:.1f}MB（上限 50MB）",
        ))

    try:
        import fitz  # PyMuPDF
    except ImportError:
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.PDF_PARSE_FAILED,
            message="缺少依赖 PyMuPDF，请安装: pip install PyMuPDF",
            retriable=False,
        ))

    result = PdfParseResult(filepath=filepath)

    try:
        doc = fitz.open(filepath)
    except Exception as exc:
        # 可能是加密或损坏
        msg = str(exc).lower()
        if "encrypted" in msg or "password" in msg:
            return ToolResult(success=False, error=ToolError(
                code=ErrorCode.FILE_ENCRYPTED,
                message="PDF 文件已加密，请解密后重试",
            ))
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.FILE_CORRUPTED,
            message=f"PDF 打开失败: {exc}",
        ))

    result.total_pages = len(doc)
    result.metadata = dict(doc.metadata) if doc.metadata else {}

    max_pages = page_limit or len(doc)

    for page_num in range(min(max_pages, len(doc))):
        try:
            page = doc[page_num]
            text = page.get_text("text")
            if text.strip():
                result.text_by_page[page_num + 1] = text.strip()
        except Exception as exc:
            result.errors.append(f"第{page_num + 1}页文字提取失败: {exc}")

    doc.close()

    if not result.text_by_page:
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.PDF_NO_TEXT,
            message="PDF 中未检测到文本（可能是纯扫描件），建议转为图片后用 AI 识别",
            retriable=False,
        ))

    return ToolResult(success=True, data=result)


def render_pdf_pages(
    filepath: str,
    pages: list[int] | None = None,
    dpi: int = 180,
) -> ToolResult:
    """将 PDF 指定页面渲染为图片

    Args:
        filepath: PDF 文件路径
        pages: 要渲染的页码列表（1-indexed），None=全部
        dpi: 渲染 DPI

    Returns:
        ToolResult，data 为 list[ImageRef]
    """
    path = Path(filepath)
    if not path.exists():
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"文件不存在: {filepath}",
        ))

    try:
        import fitz
    except ImportError:
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.PDF_PARSE_FAILED,
            message="缺少依赖 PyMuPDF，请安装: pip install PyMuPDF",
            retriable=False,
        ))

    try:
        doc = fitz.open(filepath)
    except Exception as exc:
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.FILE_CORRUPTED,
            message=f"PDF 打开失败: {exc}",
        ))

    images: list[ImageRef] = []
    target_pages = pages or list(range(1, len(doc) + 1))
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    for page_num in target_pages:
        if page_num < 1 or page_num > len(doc):
            continue
        try:
            page = doc[page_num - 1]
            pix = page.get_pixmap(matrix=matrix)
            img_bytes = pix.tobytes("png")
            images.append(ImageRef(
                page=page_num,
                image_bytes=img_bytes,
                width=pix.width,
                height=pix.height,
                format="png",
            ))
        except Exception:
            continue

    doc.close()

    return ToolResult(success=True, data=images)
