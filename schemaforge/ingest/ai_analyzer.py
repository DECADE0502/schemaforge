"""AI 分析器 — 封装 AI 调用用于 datasheet 分析

两种模式:
1. 文本分析: 从 PDF 提取的文本 → AI → 结构化 JSON (器件信息)
2. 图片分析: 引脚图/封装图截图 → AI vision → 引脚定义

所有 AI 调用封装在此模块，其他模块不直接调用 client。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from schemaforge.agent.tool_registry import ToolResult
from schemaforge.common.errors import ErrorCode, ToolError


# ============================================================
# 分析结果
# ============================================================


@dataclass
class TextAnalysisResult:
    """文本分析结果"""

    part_number: str = ""
    manufacturer: str = ""
    description: str = ""
    category: str = ""
    package: str = ""
    pin_count: int = 0
    pins: list[dict[str, str]] = field(default_factory=list)
    specs: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0
    raw_response: str = ""
    missing_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ImageAnalysisResult:
    """图片分析结果"""

    pins: list[dict[str, str]] = field(default_factory=list)
    package: str = ""
    pin_count: int = 0
    extra_info: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    raw_response: str = ""
    warnings: list[str] = field(default_factory=list)


# ============================================================
# 系统提示词
# ============================================================

_TEXT_ANALYSIS_PROMPT = """\
你是电子元器件 datasheet 分析专家。请从以下 datasheet 文本中提取器件信息。

**必须输出严格 JSON，不要包含 markdown 代码块标记。**

输出格式:
{
  "part_number": "器件型号",
  "manufacturer": "制造商",
  "description": "简短描述(中文)",
  "category": "类别(ldo/buck/boost/mcu/opamp/mosfet/diode/resistor/capacitor/inductor/led/connector/other)",
  "package": "封装(如 SOT-23-6, QFN-32)",
  "pin_count": 引脚数(整数),
  "pins": [
    {"name": "引脚名", "number": "编号", "type": "input/output/power/passive/nc", "description": "说明"}
  ],
  "specs": {
    "v_in_max": "最大输入电压",
    "v_out_typ": "典型输出电压",
    "i_out_max": "最大输出电流",
    ...其他关键电气参数
  },
  "confidence": 0.0到1.0的置信度,
  "missing_fields": ["无法确定的字段列表"],
  "warnings": ["需要用户确认的地方"]
}

注意:
- 如果某个字段无法从文本中确定，设为空字符串/空数组，并加入 missing_fields
- 对不确定的信息，在 warnings 中说明
- confidence 反映整体信息完整度和确定性
- 引脚的 type 只能是: input, output, power, passive, nc
"""

_COMBINED_ANALYSIS_PROMPT = """\
你是电子元器件 datasheet 分析专家。我会同时提供 PDF 文本和引脚图/封装图图片。
请综合分析文本和图片，交叉验证引脚信息。

**必须输出严格 JSON，不要包含 markdown 代码块标记。**

输出格式:
{
  "part_number": "器件型号",
  "manufacturer": "制造商",
  "description": "简短描述(中文)",
  "category": "类别(ldo/buck/boost/mcu/opamp/mosfet/diode/resistor/capacitor/inductor/led/connector/other)",
  "package": "封装(如 SOT-23-6, QFN-32)",
  "pin_count": 引脚数(整数),
  "pins": [
    {"name": "引脚名", "number": "编号", "type": "input/output/power/passive/nc", "description": "说明"}
  ],
  "specs": {
    "v_in_max": "最大输入电压",
    "v_out_typ": "典型输出电压",
    "i_out_max": "最大输出电流",
    ...其他关键电气参数
  },
  "confidence": 0.0到1.0的置信度,
  "missing_fields": ["无法确定的字段列表"],
  "warnings": ["需要用户确认的地方"]
}

注意:
- 优先从图片中识别引脚名称和编号，用文本信息补充引脚功能描述
- 如果图片和文本的引脚信息有冲突，以图片为准，在 warnings 中说明差异
- 如果某个字段无法确定，设为空字符串/空数组，并加入 missing_fields
- confidence 反映综合信息完整度（文本+图片互补时应更高）
- 引脚的 type 只能是: input, output, power, passive, nc
"""

_IMAGE_ANALYSIS_PROMPT = """\
你是电子元器件图片分析专家。请分析这张图片中的引脚信息。

**必须输出严格 JSON，不要包含 markdown 代码块标记。**

输出格式:
{
  "pins": [
    {"name": "引脚名", "number": "编号", "type": "input/output/power/passive/nc", "description": "说明"}
  ],
  "package": "封装型号(如果能识别)",
  "pin_count": 引脚数(整数),
  "extra_info": {"其他识别到的信息"},
  "confidence": 0.0到1.0的置信度,
  "warnings": ["不确定的地方"]
}

注意:
- 仔细识别每个引脚的名称和编号
- 如果图片模糊无法确定，在 warnings 中说明
- 引脚的 type 只能是: input, output, power, passive, nc
"""


# ============================================================
# 文本分析
# ============================================================


def analyze_datasheet_text(
    text: str,
    hint: str = "",
    use_mock: bool = False,
) -> ToolResult:
    """用 AI 分析 datasheet 文本内容

    Args:
        text: PDF 提取的文本内容
        hint: 附加提示 (如用户提供的器件型号)
        use_mock: 是否使用 mock 模式

    Returns:
        ToolResult, data 为 TextAnalysisResult
    """
    if not text.strip():
        return ToolResult(
            success=False,
            error=ToolError(
                code=ErrorCode.INVALID_FORMAT,
                message="文本内容为空",
            ),
        )

    # 截断过长文本 (保留前后各 4000 字符)
    if len(text) > 10000:
        text = text[:4000] + "\n\n...(中间省略)...\n\n" + text[-4000:]

    user_msg = f"以下是 datasheet 文本内容:\n\n{text}"
    if hint:
        user_msg += f"\n\n用户提示: {hint}"

    if use_mock:
        return _mock_text_analysis(text, hint)

    try:
        from schemaforge.ai.client import call_llm, DEFAULT_MODEL, _extract_json

        raw = call_llm(
            system_prompt=_TEXT_ANALYSIS_PROMPT,
            user_message=user_msg,
            model=DEFAULT_MODEL,
            temperature=0.1,
            max_tokens=3000,
        )

        parsed = _extract_json(raw)
        if parsed is None:
            return ToolResult(
                success=True,
                data=TextAnalysisResult(
                    raw_response=raw,
                    confidence=0.3,
                    warnings=["AI 输出未能解析为 JSON，仅保留原始文本"],
                ),
            )

        result = TextAnalysisResult(
            part_number=parsed.get("part_number", ""),
            manufacturer=parsed.get("manufacturer", ""),
            description=parsed.get("description", ""),
            category=parsed.get("category", ""),
            package=parsed.get("package", ""),
            pin_count=int(parsed.get("pin_count", 0)),
            pins=parsed.get("pins", []),
            specs=parsed.get("specs", {}),
            confidence=float(parsed.get("confidence", 0.5)),
            raw_response=raw,
            missing_fields=parsed.get("missing_fields", []),
            warnings=parsed.get("warnings", []),
        )
        return ToolResult(success=True, data=result)

    except Exception as exc:
        return ToolResult(
            success=False,
            error=ToolError(
                code=ErrorCode.AI_CALL_FAILED,
                message=f"AI 文本分析调用失败: {exc}",
                retriable=True,
            ),
        )


# ============================================================
# 图片分析
# ============================================================


def analyze_image(
    image_bytes: bytes,
    task_hint: str = "",
    use_mock: bool = False,
) -> ToolResult:
    """用 AI vision 分析引脚图/封装图

    Args:
        image_bytes: 图片二进制数据
        task_hint: 任务提示
        use_mock: 是否使用 mock 模式

    Returns:
        ToolResult, data 为 ImageAnalysisResult
    """
    if not image_bytes:
        return ToolResult(
            success=False,
            error=ToolError(
                code=ErrorCode.IMAGE_UNREADABLE,
                message="图片数据为空",
            ),
        )

    if use_mock:
        return _mock_image_analysis()

    import base64

    # 验证图片格式
    from schemaforge.ingest.image_recognizer import _validate_image, _guess_mime

    err = _validate_image(image_bytes)
    if err:
        return ToolResult(success=False, error=err)

    mime = _guess_mime(image_bytes)
    b64 = base64.b64encode(image_bytes).decode("ascii")

    prompt = _IMAGE_ANALYSIS_PROMPT
    if task_hint:
        prompt += f"\n\n附加说明: {task_hint}"

    try:
        from schemaforge.ai.client import get_client, DEFAULT_MODEL, _extract_json

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
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                    ],
                },
            ],
            temperature=0.1,
            max_tokens=2048,
        )

        raw = response.choices[0].message.content or ""
        parsed = _extract_json(raw)

        if parsed is None:
            return ToolResult(
                success=True,
                data=ImageAnalysisResult(
                    raw_response=raw,
                    confidence=0.3,
                    warnings=["AI 输出未能解析为 JSON"],
                ),
            )

        result = ImageAnalysisResult(
            pins=parsed.get("pins", []),
            package=parsed.get("package", ""),
            pin_count=int(parsed.get("pin_count", 0)),
            extra_info=parsed.get("extra_info", {}),
            confidence=float(parsed.get("confidence", 0.5)),
            raw_response=raw,
            warnings=parsed.get("warnings", []),
        )
        return ToolResult(success=True, data=result)

    except Exception as exc:
        return ToolResult(
            success=False,
            error=ToolError(
                code=ErrorCode.AI_CALL_FAILED,
                message=f"AI 图片分析调用失败: {exc}",
                retriable=True,
            ),
        )


# ============================================================
# 融合分析（文本 + 多张图片）
# ============================================================


def analyze_combined(
    text: str,
    image_list: list[bytes],
    hint: str = "",
    use_mock: bool = False,
) -> ToolResult:
    """融合分析：PDF 文本 + 多张引脚图/封装图

    将文本和多张图片一起发送给 AI，让 AI 交叉验证引脚信息。

    Args:
        text: PDF 提取的文本
        image_list: 图片二进制数据列表
        hint: 用户提示
        use_mock: 是否使用 mock

    Returns:
        ToolResult, data 为 TextAnalysisResult
    """
    if not text.strip() and not image_list:
        return ToolResult(
            success=False,
            error=ToolError(
                code=ErrorCode.INVALID_FORMAT,
                message="文本和图片均为空",
            ),
        )

    if use_mock:
        return _mock_text_analysis(text, hint)

    import base64

    from schemaforge.ingest.image_recognizer import _guess_mime, _validate_image

    if len(text) > 10000:
        text = text[:4000] + "\n\n...(中间省略)...\n\n" + text[-4000:]

    content: list[dict[str, Any]] = []

    prompt = _COMBINED_ANALYSIS_PROMPT
    if hint:
        prompt += f"\n\n用户提示: {hint}"

    text_block = (
        f"以下是 datasheet 文本内容:\n\n{text}"
        if text.strip()
        else "(无文本，仅依据图片分析)"
    )
    content.append({"type": "text", "text": f"{prompt}\n\n{text_block}"})

    for img_bytes in image_list:
        err = _validate_image(img_bytes)
        if err:
            continue
        mime = _guess_mime(img_bytes)
        b64 = base64.b64encode(img_bytes).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            }
        )

    if len(content) < 2:
        return analyze_datasheet_text(text, hint=hint, use_mock=use_mock)

    try:
        from schemaforge.ai.client import DEFAULT_MODEL, _extract_json, get_client

        client = get_client()
        response = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[{"role": "user", "content": content}],
            temperature=0.1,
            max_tokens=3000,
        )

        raw = response.choices[0].message.content or ""
        parsed = _extract_json(raw)

        if parsed is None:
            return ToolResult(
                success=True,
                data=TextAnalysisResult(
                    raw_response=raw,
                    confidence=0.3,
                    warnings=["AI 输出未能解析为 JSON，仅保留原始文本"],
                ),
            )

        result = TextAnalysisResult(
            part_number=parsed.get("part_number", ""),
            manufacturer=parsed.get("manufacturer", ""),
            description=parsed.get("description", ""),
            category=parsed.get("category", ""),
            package=parsed.get("package", ""),
            pin_count=int(parsed.get("pin_count", 0)),
            pins=parsed.get("pins", []),
            specs=parsed.get("specs", {}),
            confidence=float(parsed.get("confidence", 0.5)),
            raw_response=raw,
            missing_fields=parsed.get("missing_fields", []),
            warnings=parsed.get("warnings", []),
        )
        return ToolResult(success=True, data=result)

    except Exception as exc:
        return ToolResult(
            success=False,
            error=ToolError(
                code=ErrorCode.AI_CALL_FAILED,
                message=f"AI 融合分析调用失败: {exc}",
                retriable=True,
            ),
        )


# ============================================================
# 图片文件分析
# ============================================================


def analyze_image_file(
    filepath: str,
    task_hint: str = "",
    use_mock: bool = False,
) -> ToolResult:
    """从文件路径分析图片

    Args:
        filepath: 图片文件路径
        task_hint: 任务提示
        use_mock: 是否使用 mock

    Returns:
        ToolResult
    """
    from pathlib import Path

    path = Path(filepath)
    if not path.exists():
        return ToolResult(
            success=False,
            error=ToolError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"文件不存在: {filepath}",
            ),
        )

    image_bytes = path.read_bytes()
    return analyze_image(image_bytes, task_hint, use_mock)


# ============================================================
# Mock 实现（离线测试用）
# ============================================================


def _mock_text_analysis(text: str, hint: str = "") -> ToolResult:
    """Mock 文本分析 — 从文本关键词猜测器件类型"""
    text_lower = text.lower()

    # 简单关键词匹配
    part_number = ""
    category = ""
    if "tps54202" in text_lower:
        part_number = "TPS54202"
        category = "buck"
    elif "ams1117" in text_lower:
        part_number = "AMS1117"
        category = "ldo"
    elif "stm32" in text_lower:
        part_number = "STM32F103"
        category = "mcu"
    elif hint:
        part_number = hint
        category = "other"

    result = TextAnalysisResult(
        part_number=part_number,
        manufacturer="(Mock) Unknown",
        description=f"(Mock) 从文本分析得到的 {part_number or '未知'} 器件",
        category=category,
        confidence=0.5,
        raw_response="(mock response)",
        missing_fields=["pins", "specs", "package"],
        warnings=["这是 Mock 分析结果，仅用于离线测试"],
    )
    return ToolResult(success=True, data=result)


def _mock_image_analysis() -> ToolResult:
    """Mock 图片分析"""
    result = ImageAnalysisResult(
        pins=[
            {"name": "VIN", "number": "1", "type": "power", "description": "输入电压"},
            {"name": "GND", "number": "2", "type": "power", "description": "接地"},
            {
                "name": "VOUT",
                "number": "3",
                "type": "output",
                "description": "输出电压",
            },
        ],
        package="SOT-223",
        pin_count=3,
        confidence=0.4,
        raw_response="(mock response)",
        warnings=["这是 Mock 图片分析结果，仅用于离线测试"],
    )
    return ToolResult(success=True, data=result)
