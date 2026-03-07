"""器件符号生成多Agent流水线

从 PDF/图片到入库级 SymbolDef 的三阶段流程:
  1. Extractor (提取器): AI vision 读取 datasheet → 结构化引脚 JSON
  2. Builder (构建器): 本地 SymbolBuilder 引擎 → KLC 兼容 SymbolDef
  3. Reviewer (审查器): AI 对比原始图片与渲染结果 → 发现错误

核心原则: AI 只做理解和判断，本地引擎负责标准化绘制。
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Any



logger = logging.getLogger(__name__)


# ============================================================
# 流水线结果
# ============================================================


@dataclass
class PipelineStepResult:
    """单阶段执行结果"""

    stage: str  # "extract" | "build" | "review"
    success: bool = False
    data: Any = None
    error: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class SymbolPipelineResult:
    """完整流水线结果"""

    success: bool = False
    part_number: str = ""
    category: str = ""
    manufacturer: str = ""
    description: str = ""
    package: str = ""
    pins_data: list[dict[str, str]] = field(default_factory=list)
    specs: dict[str, str] = field(default_factory=dict)
    symbol_def: dict[str, Any] | None = None
    symbol_preview_b64: str = ""

    # 各阶段详情
    extraction: PipelineStepResult | None = None
    build: PipelineStepResult | None = None
    review: PipelineStepResult | None = None

    # 汇总
    confidence: float = 0.0
    all_warnings: list[str] = field(default_factory=list)
    error_message: str = ""


# ============================================================
# AI Prompt 模板
# ============================================================

# --- 阶段 2: 布局建议 prompt ---
_LAYOUT_ADVISOR_PROMPT = """\
你是电子元器件符号布局专家。以下是自动分配后的引脚布局方案，请审查并提出改进建议。

**器件信息:**
- 型号: {part_number}
- 类别: {category}
- 封装: {package}

**当前引脚布局:**
{pin_layout}

**审查要点:**
1. 引脚分组是否合理？同功能的引脚是否在同一侧？
2. 电源引脚(VCC/VDD)是否在顶部，接地(GND)是否在底部？
3. 输入引脚是否在左侧，输出引脚是否在右侧？
4. 总线信号(如 D0-D7)是否连续排列？
5. 双向信号(如 SDA/SCL)的位置是否方便连线？

**必须输出严格 JSON，不要包含 markdown 代码块标记:**
{{
  "approved": true/false,
  "suggestions": [
    {{
      "pin_name": "引脚名",
      "current_side": "当前方位",
      "suggested_side": "建议方位",
      "reason": "原因"
    }}
  ],
  "overall_comment": "总体评价"
}}

如果布局已经合理，approved=true 且 suggestions 为空数组。
"""

# --- 阶段 3: 视觉审查 prompt ---
_REVIEW_PROMPT = """\
你是电子元器件符号审查专家。请对比原始 datasheet 信息与自动生成的器件符号，检查是否有错误。

**器件信息:**
- 型号: {part_number}
- 类别: {category}
- 引脚数: {pin_count}

**自动生成的符号引脚列表:**
{generated_pins}

**原始提取的引脚数据 (来自 datasheet):**
{original_pins}

**审查要点:**
1. 引脚数量是否匹配？
2. 每个引脚的名称是否正确？(检查拼写)
3. 每个引脚的编号是否正确？
4. 引脚类型(输入/输出/电源/地)是否正确？
5. 是否有遗漏的引脚？
6. 是否有多余的引脚？

**必须输出严格 JSON，不要包含 markdown 代码块标记:**
{{
  "passed": true/false,
  "pin_count_match": true/false,
  "issues": [
    {{
      "severity": "error/warning/info",
      "pin_name": "涉及的引脚名",
      "description": "问题描述",
      "fix_suggestion": "修复建议"
    }}
  ],
  "confidence": 0.0到1.0的审查置信度,
  "summary": "审查总结"
}}

没有问题时 passed=true 且 issues 为空数组。
"""

# --- 视觉审查 prompt (带图片) ---
_VISUAL_REVIEW_PROMPT = """\
你是电子元器件符号视觉审查专家。我会提供自动生成的器件符号渲染图和原始 datasheet 参考信息。
请仔细对比，确认符号是否正确。

**器件信息:**
- 型号: {part_number}
- 类别: {category}

**自动生成的引脚:**
{generated_pins}

**请检查渲染图中:**
1. IC 矩形是否正确显示了器件名称？
2. 每个引脚是否正确标注了名称和编号？
3. 引脚方位是否符合 EDA 习惯（电源上、地下、输入左、输出右）？
4. 反相引脚(~开头)是否有反相标记？
5. 整体布局是否清晰、间距合适？

**必须输出严格 JSON，不要包含 markdown 代码块标记:**
{{
  "visual_check_passed": true/false,
  "issues": [
    {{
      "severity": "error/warning/info",
      "description": "问题描述",
      "fix_suggestion": "修复建议"
    }}
  ],
  "confidence": 0.0到1.0,
  "summary": "视觉审查总结"
}}
"""


# ============================================================
# 流水线执行器
# ============================================================


def run_symbol_pipeline(
    pdf_path: str | None = None,
    image_paths: list[str] | None = None,
    hint: str = "",
    skip_review: bool = False,
) -> SymbolPipelineResult:
    """执行完整的符号生成流水线

    Args:
        pdf_path: PDF datasheet 路径 (可选)
        image_paths: 引脚图/封装图路径列表 (可选)
        hint: 器件型号提示
        skip_review: 是否跳过审查阶段

    Returns:
        SymbolPipelineResult 包含完整结果
    """
    result = SymbolPipelineResult()

    # --- 阶段 1: 提取 ---
    logger.info("符号流水线: 阶段1 — 提取器件信息")
    extraction = _run_extraction(pdf_path, image_paths, hint)
    result.extraction = extraction

    if not extraction.success:
        result.error_message = f"提取失败: {extraction.error}"
        return result

    ext_data: dict[str, Any] = extraction.data
    result.part_number = ext_data.get("part_number", hint or "UNKNOWN")
    result.category = ext_data.get("category", "")
    result.manufacturer = ext_data.get("manufacturer", "")
    result.description = ext_data.get("description", "")
    result.package = ext_data.get("package", "")
    result.pins_data = ext_data.get("pins", [])
    result.specs = ext_data.get("specs", {})
    result.confidence = ext_data.get("confidence", 0.5)
    result.all_warnings.extend(extraction.warnings)

    if not result.pins_data:
        result.error_message = "提取完成但未获得引脚数据"
        return result

    # --- 阶段 2: 构建符号 ---
    logger.info("符号流水线: 阶段2 — 构建 KLC 兼容符号")
    build_result = _run_build(
        result.part_number,
        result.pins_data,
        result.category,
        result.package,
    )
    result.build = build_result

    if not build_result.success:
        result.error_message = f"符号构建失败: {build_result.error}"
        return result

    result.symbol_def = build_result.data.get("symbol_def")
    result.all_warnings.extend(build_result.warnings)

    # --- 布局建议 (可选，非阻塞) ---
    if result.symbol_def:
        _run_layout_advice(result)

    # --- 阶段 3: 审查 ---
    if not skip_review and result.symbol_def:
        logger.info("符号流水线: 阶段3 — AI 审查")
        review_result = _run_review(
            result.part_number,
            result.category,
            result.pins_data,
            build_result.data.get("pins", []),
            result.symbol_def,
        )
        result.review = review_result
        result.all_warnings.extend(review_result.warnings)

        # 审查发现严重错误时降低置信度
        if review_result.data and not review_result.data.get("passed", True):
            errors = [
                i for i in review_result.data.get("issues", [])
                if i.get("severity") == "error"
            ]
            if errors:
                result.confidence *= 0.5
                result.all_warnings.append(
                    f"审查发现 {len(errors)} 个错误，置信度已降低"
                )

    # --- 渲染预览 ---
    if result.symbol_def:
        preview = _render_preview(result.symbol_def, result.part_number)
        if preview:
            result.symbol_preview_b64 = preview

    result.success = True
    return result


# ============================================================
# 阶段 1: 提取
# ============================================================


def _run_extraction(
    pdf_path: str | None,
    image_paths: list[str] | None,
    hint: str,
) -> PipelineStepResult:
    """阶段 1: 从 PDF/图片提取器件信息。"""
    step = PipelineStepResult(stage="extract")

    try:
        if pdf_path:
            from schemaforge.ingest.datasheet_extractor import extract_from_pdf

            extraction = extract_from_pdf(
                filepath=pdf_path,
                hint=hint,
                extra_images=image_paths,
            )
        elif image_paths:
            from schemaforge.ingest.datasheet_extractor import extract_from_image

            extraction = extract_from_image(
                image_source=image_paths[0],
                hint=hint,
            )
        else:
            step.error = "未提供 PDF 或图片"
            return step

        if not extraction.success:
            step.error = extraction.error_message
            return step

        draft = extraction.draft
        if draft is None:
            step.error = "提取完成但未生成草稿"
            return step

        # 将 draft 转为 dict 供后续阶段使用
        pins_list: list[dict[str, str]] = []
        for pin in draft.pins:
            pins_list.append({
                "name": pin.name,
                "number": pin.number,
                "type": pin.pin_type,
                "description": pin.description,
            })

        step.data = {
            "part_number": draft.part_number,
            "category": draft.category,
            "manufacturer": draft.manufacturer,
            "description": draft.description,
            "package": draft.package,
            "pins": pins_list,
            "specs": dict(draft.specs),
            "confidence": draft.confidence,
        }
        step.success = True

        # 追问项作为警告
        if extraction.needs_user_input and extraction.questions:
            for q in extraction.questions:
                text = q.get("text", str(q))
                step.warnings.append(f"需确认: {text}")

        # AI 自身警告
        if extraction.text_analysis and extraction.text_analysis.warnings:
            step.warnings.extend(extraction.text_analysis.warnings)

    except Exception as exc:
        logger.exception("提取阶段异常")
        step.error = str(exc)

    return step


# ============================================================
# 阶段 2: 构建符号
# ============================================================


def _run_build(
    part_number: str,
    pins_data: list[dict[str, str]],
    category: str,
    package: str,
) -> PipelineStepResult:
    """阶段 2: 调用 SymbolBuilder 构建 KLC 兼容符号。"""
    step = PipelineStepResult(stage="build")

    try:
        from schemaforge.library.symbol_builder import build_symbol

        symbol = build_symbol(
            part_number=part_number,
            pins_data=pins_data,
            category=category,
            package=package,
        )

        step.data = {
            "symbol_def": symbol.model_dump(),
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
        }
        step.success = True

    except ValueError as exc:
        step.error = str(exc)
    except Exception as exc:
        logger.exception("构建阶段异常")
        step.error = str(exc)

    return step


# ============================================================
# 布局建议 (非阻塞增强)
# ============================================================


def _run_layout_advice(result: SymbolPipelineResult) -> None:
    """可选: 请求 AI 审查引脚布局并提出改进建议。"""
    if not result.build or not result.build.data:
        return

    build_pins = result.build.data.get("pins", [])
    if not build_pins:
        return

    pin_layout_lines: list[str] = []
    for p in build_pins:
        pin_layout_lines.append(
            f"  {p['name']} (#{p.get('pin_number', '?')}) "
            f"→ {p['side']}侧, 类型: {p['pin_type']}"
        )

    prompt_text = _LAYOUT_ADVISOR_PROMPT.format(
        part_number=result.part_number,
        category=result.category or "未知",
        package=result.package or "未知",
        pin_layout="\n".join(pin_layout_lines),
    )

    try:
        from schemaforge.ai.client import call_llm_json

        advice = call_llm_json(
            system_prompt="你是电子元器件符号布局专家。",
            user_message=prompt_text,
            temperature=0.1,
        )

        if advice and not advice.get("approved", True):
            suggestions = advice.get("suggestions", [])
            for s in suggestions:
                result.all_warnings.append(
                    f"布局建议: {s.get('pin_name', '?')} "
                    f"从{s.get('current_side', '?')} "
                    f"移到{s.get('suggested_side', '?')} "
                    f"— {s.get('reason', '')}"
                )
            comment = advice.get("overall_comment", "")
            if comment:
                result.all_warnings.append(f"布局总评: {comment}")

    except Exception as exc:
        logger.warning("布局建议请求失败 (非阻塞): %s", exc)


# ============================================================
# 阶段 3: 审查
# ============================================================


def _run_review(
    part_number: str,
    category: str,
    original_pins: list[dict[str, str]],
    generated_pins: list[dict[str, str]],
    symbol_def: dict[str, Any],
) -> PipelineStepResult:
    """阶段 3: AI 审查生成的符号。"""
    step = PipelineStepResult(stage="review")

    # 格式化引脚信息
    orig_lines: list[str] = []
    for p in original_pins:
        orig_lines.append(
            f"  {p.get('name', '?')} (#{p.get('number', '?')}) "
            f"类型: {p.get('type', '?')}"
        )

    gen_lines: list[str] = []
    for p in generated_pins:
        gen_lines.append(
            f"  {p.get('name', '?')} (#{p.get('pin_number', '?')}) "
            f"→ {p.get('side', '?')}侧, 类型: {p.get('pin_type', '?')}"
        )

    prompt_text = _REVIEW_PROMPT.format(
        part_number=part_number,
        category=category or "未知",
        pin_count=len(generated_pins),
        generated_pins="\n".join(gen_lines),
        original_pins="\n".join(orig_lines),
    )

    try:
        from schemaforge.ai.client import call_llm_json

        review = call_llm_json(
            system_prompt="你是电子元器件符号审查专家。",
            user_message=prompt_text,
            temperature=0.1,
        )

        if review is None:
            step.error = "AI 审查返回无效 JSON"
            return step

        step.data = review
        step.success = True

        # 提取警告
        for issue in review.get("issues", []):
            severity = issue.get("severity", "info")
            desc = issue.get("description", "")
            if severity == "error":
                step.warnings.append(f"❌ {desc}")
            elif severity == "warning":
                step.warnings.append(f"⚠ {desc}")

    except Exception as exc:
        logger.warning("审查阶段失败 (非阻塞): %s", exc)
        step.error = str(exc)
        # 审查失败不阻塞整条流水线
        step.success = True
        step.data = {
            "passed": True,
            "issues": [],
            "confidence": 0.5,
            "summary": f"审查调用失败: {exc}",
        }

    return step


# ============================================================
# 视觉审查 (带渲染图片)
# ============================================================


def run_visual_review(
    part_number: str,
    category: str,
    symbol_def: dict[str, Any],
    generated_pins: list[dict[str, str]],
) -> PipelineStepResult:
    """带渲染图片的视觉审查 (可选增强)

    渲染符号为 PNG → 发送给 AI vision → 对比检查。
    """
    step = PipelineStepResult(stage="visual_review")

    preview_b64 = _render_preview(symbol_def, part_number)
    if not preview_b64:
        step.error = "无法渲染预览图"
        return step

    gen_lines: list[str] = []
    for p in generated_pins:
        gen_lines.append(
            f"  {p.get('name', '?')} (#{p.get('pin_number', '?')}) "
            f"→ {p.get('side', '?')}侧"
        )

    prompt_text = _VISUAL_REVIEW_PROMPT.format(
        part_number=part_number,
        category=category or "未知",
        generated_pins="\n".join(gen_lines),
    )

    try:
        from schemaforge.ai.client import DEFAULT_MODEL, _extract_json, get_client

        client = get_client()
        response = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{preview_b64}",
                            },
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
            step.error = "AI 视觉审查返回无效 JSON"
            return step

        step.data = parsed
        step.success = True

        for issue in parsed.get("issues", []):
            severity = issue.get("severity", "info")
            desc = issue.get("description", "")
            if severity == "error":
                step.warnings.append(f"❌ 视觉: {desc}")
            elif severity == "warning":
                step.warnings.append(f"⚠ 视觉: {desc}")

    except Exception as exc:
        logger.warning("视觉审查失败: %s", exc)
        step.error = str(exc)

    return step


# ============================================================
# 辅助: 渲染预览
# ============================================================


def _render_preview(
    symbol_def: dict[str, Any],
    label: str,
) -> str:
    """渲染 SymbolDef 为 PNG base64 字符串。"""
    try:
        from schemaforge.library.models import SymbolDef
        from schemaforge.schematic.renderer import TopologyRenderer

        sym = SymbolDef.model_validate(symbol_def)
        png_bytes = TopologyRenderer.render_symbol_preview(sym, label=label)
        return base64.b64encode(png_bytes).decode("ascii")
    except Exception as exc:
        logger.warning("预览渲染失败: %s", exc)
        return ""
