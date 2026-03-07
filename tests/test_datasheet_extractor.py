"""Tests for schemaforge.ingest.datasheet_extractor"""

from __future__ import annotations

from pathlib import Path

from schemaforge.ingest.datasheet_extractor import (
    ExtractionResult,
    apply_user_answers,
    extract_from_image,
    _analysis_to_draft,
    _generate_questions,
)
from schemaforge.ingest.ai_analyzer import TextAnalysisResult
from schemaforge.library.validator import DeviceDraft, PinDraft


# ============================================================
# ExtractionResult
# ============================================================


class TestExtractionResult:
    """ExtractionResult 数据类"""

    def test_defaults(self) -> None:
        result = ExtractionResult()
        assert not result.success
        assert result.draft is None
        assert not result.needs_user_input

    def test_success_result(self) -> None:
        result = ExtractionResult(
            success=True,
            draft=DeviceDraft(part_number="IC1"),
        )
        assert result.success


# ============================================================
# extract_from_image (mock)
# ============================================================


class TestExtractFromImage:
    """图片提取 — mock 模式"""

    def test_mock_image_extraction(self, tmp_path: Path) -> None:
        """Mock 图片提取流程"""
        img_file = tmp_path / "test_pin.png"
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        result = extract_from_image(
            str(img_file),
            hint="AMS1117",
        )
        assert result.success
        assert result.draft is not None
        assert result.draft.part_number == "AMS1117"
        assert result.needs_user_input  # 图片识别总是需要补全

    def test_mock_image_bytes(self) -> None:
        """直接传入 bytes"""
        png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        result = extract_from_image(png_data, )
        assert result.success
        assert result.draft is not None
        assert len(result.draft.pins) > 0

    def test_image_has_questions(self, tmp_path: Path) -> None:
        """图片提取应生成追问"""
        img_file = tmp_path / "test.png"
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        result = extract_from_image(str(img_file), )
        assert result.needs_user_input
        assert len(result.questions) > 0


# ============================================================
# _analysis_to_draft
# ============================================================


class TestAnalysisToDraft:
    """AI 分析结果 → DeviceDraft 转换"""

    def test_basic_conversion(self) -> None:
        analysis = TextAnalysisResult(
            part_number="TPS54202",
            manufacturer="TI",
            category="buck",
            description="2A Buck Converter",
            package="SOT-23-6",
            pin_count=6,
            pins=[
                {"name": "BOOT", "number": "1", "type": "input"},
                {"name": "VIN", "number": "2", "type": "power"},
            ],
            specs={"v_in_max": "28V"},
            confidence=0.85,
        )
        draft = _analysis_to_draft(analysis, source_file="test.pdf")
        assert draft.part_number == "TPS54202"
        assert draft.manufacturer == "TI"
        assert draft.source == "pdf_parsed"
        assert len(draft.pins) == 2
        assert draft.pins[0].name == "BOOT"
        assert "test.pdf" in draft.evidence_refs

    def test_empty_analysis(self) -> None:
        analysis = TextAnalysisResult()
        draft = _analysis_to_draft(analysis)
        assert draft.part_number == ""
        assert draft.source == "pdf_parsed"

    def test_low_confidence_mapping(self) -> None:
        """低置信度应在 confidence_map 中标记"""
        analysis = TextAnalysisResult(
            part_number="IC1",
            category="other",
            confidence=0.4,
        )
        draft = _analysis_to_draft(analysis)
        assert "part_number" in draft.confidence_map
        assert draft.confidence_map["part_number"] == 0.4


# ============================================================
# _generate_questions
# ============================================================


class TestGenerateQuestions:
    """追问生成"""

    def test_missing_part_number(self) -> None:
        """缺少料号 → 生成料号追问"""
        draft = DeviceDraft(part_number="")
        analysis = TextAnalysisResult(confidence=0.5)
        questions = _generate_questions(draft, analysis)
        pn_questions = [q for q in questions if q["field_path"] == "part_number"]
        assert len(pn_questions) >= 1

    def test_low_confidence_confirmation(self) -> None:
        """低置信度 → 确认追问"""
        draft = DeviceDraft(part_number="IC1")
        analysis = TextAnalysisResult(
            part_number="IC1",
            confidence=0.3,
        )
        questions = _generate_questions(draft, analysis)
        confirm_q = [q for q in questions if q.get("answer_type") == "confirm"]
        assert len(confirm_q) >= 1

    def test_no_questions_when_complete(self) -> None:
        """信息完整 → 无追问"""
        draft = DeviceDraft(
            part_number="TPS54202",
            category="buck",
            pins=[PinDraft(name="P1", number="1")],
        )
        analysis = TextAnalysisResult(
            part_number="TPS54202",
            confidence=0.9,
        )
        questions = _generate_questions(draft, analysis)
        # 可能有 warning 相关的但不应有必填追问
        required_q = [q for q in questions if q.get("required", False)]
        assert len(required_q) == 0

    def test_ai_warnings_become_questions(self) -> None:
        """AI 警告 → 追问"""
        draft = DeviceDraft(part_number="IC1")
        analysis = TextAnalysisResult(
            part_number="IC1",
            confidence=0.8,
            warnings=["引脚 3 的功能描述不明确"],
        )
        questions = _generate_questions(draft, analysis)
        assert any("引脚 3" in q["text"] for q in questions)


# ============================================================
# apply_user_answers
# ============================================================


class TestApplyUserAnswers:
    """用户回答应用"""

    def test_apply_part_number(self) -> None:
        draft = DeviceDraft(
            part_number="",
            missing_fields=["part_number", "category"],
        )
        updated = apply_user_answers(draft, {"part_number": "TPS54202"})
        assert updated.part_number == "TPS54202"
        assert "part_number" not in updated.missing_fields
        assert "category" in updated.missing_fields  # 未回答的保留

    def test_apply_multiple_fields(self) -> None:
        draft = DeviceDraft(
            part_number="",
            category="",
            manufacturer="",
            missing_fields=["part_number", "category", "manufacturer"],
        )
        updated = apply_user_answers(draft, {
            "part_number": "AMS1117",
            "category": "ldo",
            "manufacturer": "AMS",
        })
        assert updated.part_number == "AMS1117"
        assert updated.category == "ldo"
        assert updated.manufacturer == "AMS"
        assert len(updated.missing_fields) == 0

    def test_empty_answer_ignored(self) -> None:
        draft = DeviceDraft(part_number="IC1")
        updated = apply_user_answers(draft, {"part_number": ""})
        assert updated.part_number == "IC1"  # 空回答不覆盖

    def test_returns_new_object(self) -> None:
        draft = DeviceDraft(part_number="IC1")
        updated = apply_user_answers(draft, {"category": "ldo"})
        assert updated is not draft  # 新对象
        assert updated.category == "ldo"
