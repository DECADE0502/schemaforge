"""Datasheet 提取器 — 从 PDF/图片到 DeviceDraft 的完整流程

编排层：调用 pdf_parser、ai_analyzer，组装结果为 DeviceDraft。
支持两种输入:
1. PDF datasheet → 解析文本 → AI 文本分析 → DeviceDraft
2. 图片 (截图/拍照) → AI vision → DeviceDraft

流程中产生的中间结果和 AI 不确定字段通过回调通知 GUI，
GUI 可以展示追问卡片让用户补全。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from schemaforge.common.progress import ProgressTracker
from schemaforge.ingest.ai_analyzer import (
    ImageAnalysisResult,
    TextAnalysisResult,
    analyze_datasheet_text,
    analyze_image,
    analyze_image_file,
)
from schemaforge.ingest.pdf_parser import (
    PdfParseResult,
    parse_pdf,
)
from schemaforge.library.validator import DeviceDraft, PinDraft


# ============================================================
# 提取结果
# ============================================================

@dataclass
class ExtractionResult:
    """Datasheet 提取结果"""

    success: bool = False
    draft: DeviceDraft | None = None
    pdf_result: PdfParseResult | None = None
    text_analysis: TextAnalysisResult | None = None
    image_analysis: ImageAnalysisResult | None = None
    error_message: str = ""
    needs_user_input: bool = False  # 是否需要用户补全
    questions: list[dict[str, Any]] = field(default_factory=list)


# ============================================================
# PDF 提取流程
# ============================================================

def extract_from_pdf(
    filepath: str,
    hint: str = "",
    use_mock: bool = False,
    tracker: ProgressTracker | None = None,
    page_limit: int | None = 10,
) -> ExtractionResult:
    """从 PDF datasheet 提取器件信息

    流程:
    1. 解析 PDF 提取文本
    2. AI 分析文本 → 结构化信息
    3. 组装 DeviceDraft
    4. 标记缺失字段 (需用户补全)

    Args:
        filepath: PDF 文件路径
        hint: 用户提示 (如器件型号)
        use_mock: 是否使用 mock AI
        tracker: 进度跟踪器
        page_limit: 最大解析页数

    Returns:
        ExtractionResult
    """
    result = ExtractionResult()

    # 1. 解析 PDF
    if tracker:
        tracker.stage("解析PDF文件", 10)
    pdf_result = parse_pdf(filepath, page_limit=page_limit)
    if not pdf_result.success:
        err = pdf_result.error
        result.error_message = err.message if err else "PDF 解析失败"
        return result

    pdf_data: PdfParseResult = pdf_result.data
    result.pdf_result = pdf_data

    if tracker:
        tracker.log(f"PDF 解析完成: {pdf_data.summary}")
        tracker.stage("AI分析文本", 30)

    # 2. AI 分析文本
    text = pdf_data.full_text
    if not text.strip():
        result.error_message = "PDF 中未提取到文本内容"
        return result

    ai_result = analyze_datasheet_text(text, hint=hint, use_mock=use_mock)
    if not ai_result.success:
        err = ai_result.error
        result.error_message = err.message if err else "AI 分析失败"
        return result

    analysis: TextAnalysisResult = ai_result.data
    result.text_analysis = analysis

    if tracker:
        tracker.log(
            f"AI 分析完成: {analysis.part_number or '(未识别型号)'}, "
            f"置信度: {analysis.confidence:.0%}"
        )
        tracker.stage("组装器件草稿", 60)

    # 3. 组装 DeviceDraft
    draft = _analysis_to_draft(analysis, source_file=filepath)
    result.draft = draft

    # 4. 检查缺失字段
    questions = _generate_questions(draft, analysis)
    if questions:
        result.needs_user_input = True
        result.questions = questions

    if tracker:
        if questions:
            tracker.log(f"需要用户确认 {len(questions)} 项信息")
        else:
            tracker.log("信息完整，无需用户确认")
        tracker.stage("提取完成", 80)

    result.success = True
    return result


# ============================================================
# 图片提取流程
# ============================================================

def extract_from_image(
    image_source: str | bytes,
    hint: str = "",
    use_mock: bool = False,
    tracker: ProgressTracker | None = None,
) -> ExtractionResult:
    """从图片提取器件信息

    Args:
        image_source: 图片文件路径(str) 或 图片二进制数据(bytes)
        hint: 用户提示
        use_mock: 是否使用 mock AI
        tracker: 进度跟踪器

    Returns:
        ExtractionResult
    """
    result = ExtractionResult()

    if tracker:
        tracker.stage("分析图片", 20)

    # 调用 AI vision
    if isinstance(image_source, str):
        ai_result = analyze_image_file(image_source, task_hint=hint, use_mock=use_mock)
    else:
        ai_result = analyze_image(image_source, task_hint=hint, use_mock=use_mock)

    if not ai_result.success:
        err = ai_result.error
        result.error_message = err.message if err else "图片分析失败"
        return result

    img_analysis: ImageAnalysisResult = ai_result.data
    result.image_analysis = img_analysis

    if tracker:
        tracker.log(
            f"图片分析完成: {img_analysis.pin_count} 个引脚, "
            f"置信度: {img_analysis.confidence:.0%}"
        )
        tracker.stage("组装器件草稿", 50)

    # 组装 DeviceDraft (图片分析信息通常不完整)
    pins: list[PinDraft] = []
    for p in img_analysis.pins:
        pins.append(PinDraft(
            name=p.get("name", ""),
            number=p.get("number", ""),
            pin_type=p.get("type", ""),
            description=p.get("description", ""),
        ))

    draft = DeviceDraft(
        package=img_analysis.package,
        pin_count=img_analysis.pin_count or len(pins),
        pins=pins,
        source="image",
        confidence=img_analysis.confidence,
        notes="从图片识别提取",
        missing_fields=["part_number", "manufacturer", "category", "description", "specs"],
    )

    # 如果 hint 中有料号信息
    if hint:
        draft.part_number = hint

    result.draft = draft
    result.success = True
    result.needs_user_input = True  # 图片识别通常需要补全
    result.questions = _generate_questions_for_image(draft)

    if tracker:
        tracker.log(f"需要用户补全 {len(result.questions)} 项信息")
        tracker.stage("提取完成", 80)

    return result


# ============================================================
# 辅助函数
# ============================================================

def _analysis_to_draft(
    analysis: TextAnalysisResult,
    source_file: str = "",
) -> DeviceDraft:
    """将 AI 文本分析结果转换为 DeviceDraft"""
    pins: list[PinDraft] = []
    for p in analysis.pins:
        if isinstance(p, dict):
            pins.append(PinDraft(
                name=p.get("name", ""),
                number=p.get("number", ""),
                pin_type=p.get("type", ""),
                description=p.get("description", ""),
            ))

    missing = list(analysis.missing_fields)
    confidence_map: dict[str, float] = {}

    # 对低置信度字段标记
    if analysis.confidence < 0.7:
        if analysis.part_number:
            confidence_map["part_number"] = analysis.confidence
        if analysis.category:
            confidence_map["category"] = analysis.confidence

    return DeviceDraft(
        part_number=analysis.part_number,
        manufacturer=analysis.manufacturer,
        description=analysis.description,
        category=analysis.category,
        package=analysis.package,
        pin_count=analysis.pin_count or len(pins),
        pins=pins,
        specs=analysis.specs,
        source="pdf_parsed",
        confidence=analysis.confidence,
        notes=f"从 PDF 提取: {source_file}" if source_file else "",
        missing_fields=missing,
        confidence_map=confidence_map,
        evidence_refs=[source_file] if source_file else [],
    )


def _generate_questions(
    draft: DeviceDraft,
    analysis: TextAnalysisResult,
) -> list[dict[str, Any]]:
    """根据分析结果生成需要用户确认的问题"""
    questions: list[dict[str, Any]] = []
    q_idx = 0

    # 缺少料号
    if not draft.part_number:
        q_idx += 1
        questions.append({
            "question_id": f"q_{q_idx}",
            "text": "无法从 datasheet 中识别器件型号，请手动输入",
            "field_path": "part_number",
            "answer_type": "text",
            "required": True,
        })

    # 缺少类别
    if not draft.category:
        q_idx += 1
        questions.append({
            "question_id": f"q_{q_idx}",
            "text": "无法确定器件类别，请选择或输入",
            "field_path": "category",
            "answer_type": "text",
            "required": False,
        })

    # 低置信度料号
    if draft.part_number and analysis.confidence < 0.6:
        q_idx += 1
        questions.append({
            "question_id": f"q_{q_idx}",
            "text": f"AI 识别料号为 '{draft.part_number}'（置信度 {analysis.confidence:.0%}），请确认是否正确",
            "field_path": "part_number",
            "answer_type": "confirm",
            "default": draft.part_number,
        })

    # 缺少引脚定义
    if not draft.pins and draft.category not in ("resistor", "capacitor", "inductor", "passive"):
        q_idx += 1
        questions.append({
            "question_id": f"q_{q_idx}",
            "text": "未能从文本中提取引脚定义。建议上传引脚图截图，或手动填写",
            "field_path": "pins",
            "answer_type": "text",
            "required": False,
            "evidence": "PDF 文本中未发现明确的引脚表格",
        })

    # AI 自身标记的警告
    for warn in analysis.warnings:
        q_idx += 1
        questions.append({
            "question_id": f"q_{q_idx}",
            "text": warn,
            "field_path": "",
            "answer_type": "text",
            "required": False,
        })

    return questions


def _generate_questions_for_image(draft: DeviceDraft) -> list[dict[str, Any]]:
    """图片导入的补全问题"""
    questions: list[dict[str, Any]] = []

    if not draft.part_number:
        questions.append({
            "question_id": "img_q_1",
            "text": "请输入器件型号",
            "field_path": "part_number",
            "answer_type": "text",
            "required": True,
        })

    questions.append({
        "question_id": "img_q_2",
        "text": "请输入制造商名称",
        "field_path": "manufacturer",
        "answer_type": "text",
        "required": False,
    })

    questions.append({
        "question_id": "img_q_3",
        "text": "请选择或输入器件类别 (如 ldo, buck, mcu)",
        "field_path": "category",
        "answer_type": "text",
        "required": False,
    })

    return questions


def apply_user_answers(
    draft: DeviceDraft,
    answers: dict[str, str],
) -> DeviceDraft:
    """将用户回答应用到 DeviceDraft

    Args:
        draft: 当前草稿
        answers: {field_path: answer_value}

    Returns:
        更新后的 DeviceDraft (新对象)
    """
    data = draft.model_dump()

    for field_path, value in answers.items():
        if not field_path or not value:
            continue

        # 简单字段直接设置
        if field_path in data and isinstance(data[field_path], str):
            data[field_path] = value
        elif field_path == "pin_count" and value.isdigit():
            data["pin_count"] = int(value)

    # 从 missing_fields 中移除已回答的字段
    answered_fields = set(answers.keys())
    data["missing_fields"] = [
        f for f in data.get("missing_fields", [])
        if f not in answered_fields
    ]

    return DeviceDraft.model_validate(data)
