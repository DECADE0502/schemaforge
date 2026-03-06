"""图片识别器

将用户截图 / 拍照 / PDF 转图片发送给 AI vision 模型识别。
本模块封装图片预处理和 AI 调用，对外暴露统一接口。

AI vision 使用 kimi-k2.5 的 base64 图片输入（OpenAI 兼容格式）。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from schemaforge.agent.tool_registry import ToolResult
from schemaforge.common.errors import ErrorCode, ToolError


@dataclass
class RecognitionResult:
    """图片识别结果"""

    raw_text: str = ""  # AI 返回的原始文本
    structured: dict[str, Any] = field(default_factory=dict)  # 结构化解析
    confidence: float = 0.0  # 整体置信度 0-1
    source_type: str = "image"  # image, pdf_page, screenshot
    warnings: list[str] = field(default_factory=list)


def _validate_image(image_bytes: bytes) -> ToolError | None:
    """验证图片基本有效性"""
    if not image_bytes:
        return ToolError(
            code=ErrorCode.IMAGE_UNREADABLE,
            message="图片数据为空",
        )

    # 检查文件头 magic bytes
    if image_bytes[:4] == b"\x89PNG":
        return None  # PNG
    if image_bytes[:2] == b"\xff\xd8":
        return None  # JPEG
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return None  # WebP
    if image_bytes[:3] == b"GIF":
        return None  # GIF

    # 大小检查（20MB 上限）
    if len(image_bytes) > 20 * 1024 * 1024:
        return ToolError(
            code=ErrorCode.FILE_TOO_LARGE,
            message=f"图片过大: {len(image_bytes) / 1024 / 1024:.1f}MB（上限 20MB）",
        )

    return ToolError(
        code=ErrorCode.INVALID_FORMAT,
        message="不支持的图片格式（支持 PNG/JPEG/WebP/GIF）",
    )


def _guess_mime(image_bytes: bytes) -> str:
    """猜测 MIME 类型"""
    if image_bytes[:4] == b"\x89PNG":
        return "image/png"
    if image_bytes[:2] == b"\xff\xd8":
        return "image/jpeg"
    if image_bytes[:4] == b"RIFF":
        return "image/webp"
    return "image/png"  # 默认


def recognize_image(
    image_bytes: bytes,
    task_hint: str = "",
) -> ToolResult:
    """用 AI vision 识别图片内容

    Args:
        image_bytes: 图片二进制数据
        task_hint: 任务提示（如 "识别引脚表"、"识别封装图"）

    Returns:
        ToolResult，data 为 RecognitionResult
    """
    # 验证
    err = _validate_image(image_bytes)
    if err:
        return ToolResult(success=False, error=err)

    # 构建 AI 请求
    mime = _guess_mime(image_bytes)
    b64 = base64.b64encode(image_bytes).decode("ascii")

    prompt = "请分析这张电子元器件相关的图片。"
    if task_hint:
        prompt = f"请分析这张图片，重点关注：{task_hint}"
    prompt += (
        "\n\n请用中文回答，输出结构化 JSON，包含："
        "\n- pins: 引脚列表 [{name, number, type, description}]"
        "\n- package: 封装信息 {type, pin_count, dimensions}"
        "\n- parameters: 电气参数 [{name, value, unit, condition}]"
        "\n- notes: 其他发现"
        "\n\n如果某些信息无法确定，请标注 confidence: 'low'。"
    )

    try:
        from schemaforge.ai.client import get_client, DEFAULT_MODEL

        client = get_client()
        response = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime};base64,{b64}",
                            },
                        },
                    ],
                },
            ],
            temperature=0.1,
            max_tokens=2048,
        )

        raw_text = response.choices[0].message.content or ""

        result = RecognitionResult(
            raw_text=raw_text,
            source_type="image",
            confidence=0.7,  # 默认中等置信度
        )

        # 尝试从 AI 输出中提取 JSON
        import json
        try:
            # 找 JSON 块
            start = raw_text.find("{")
            end = raw_text.rfind("}")
            if start != -1 and end > start:
                result.structured = json.loads(raw_text[start:end + 1])
                result.confidence = 0.8
        except json.JSONDecodeError:
            result.warnings.append("AI 输出未能解析为 JSON，仅保留原始文本")

        return ToolResult(success=True, data=result)

    except Exception as exc:
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.AI_CALL_FAILED,
            message=f"AI 图片识别调用失败: {exc}",
            retriable=True,
        ))


def recognize_image_file(
    filepath: str,
    task_hint: str = "",
) -> ToolResult:
    """从文件路径识别图片

    Args:
        filepath: 图片文件路径
        task_hint: 任务提示

    Returns:
        ToolResult
    """
    path = Path(filepath)
    if not path.exists():
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"文件不存在: {filepath}",
        ))

    image_bytes = path.read_bytes()
    return recognize_image(image_bytes, task_hint)
