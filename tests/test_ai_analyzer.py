"""Tests for schemaforge.ingest.ai_analyzer"""

from __future__ import annotations

from pathlib import Path

from schemaforge.ingest.ai_analyzer import (
    ImageAnalysisResult,
    TextAnalysisResult,
    analyze_datasheet_text,
    analyze_image,
    analyze_image_file,
)


# ============================================================
# TextAnalysisResult 模型测试
# ============================================================


class TestTextAnalysisResult:
    """TextAnalysisResult 数据类"""

    def test_defaults(self) -> None:
        result = TextAnalysisResult()
        assert result.part_number == ""
        assert result.confidence == 0.0
        assert result.pins == []
        assert result.specs == {}

    def test_full_result(self) -> None:
        result = TextAnalysisResult(
            part_number="TPS54202",
            manufacturer="Texas Instruments",
            category="buck",
            pins=[{"name": "VIN", "number": "1"}],
            confidence=0.85,
        )
        assert result.part_number == "TPS54202"
        assert len(result.pins) == 1


class TestImageAnalysisResult:
    """ImageAnalysisResult 数据类"""

    def test_defaults(self) -> None:
        result = ImageAnalysisResult()
        assert result.pins == []
        assert result.pin_count == 0

    def test_full_result(self) -> None:
        result = ImageAnalysisResult(
            pins=[{"name": "VIN", "number": "1", "type": "power"}],
            package="SOT-223",
            pin_count=3,
            confidence=0.7,
        )
        assert len(result.pins) == 1
        assert result.package == "SOT-223"


# ============================================================
# analyze_datasheet_text (mock)
# ============================================================


class TestAnalyzeDatasheetText:
    """文本分析 — mock 模式"""

    def test_mock_with_known_part(self) -> None:
        """已知器件型号 mock 分析"""
        result = analyze_datasheet_text(
            "TPS54202 datasheet ... 4.5V to 28V ...",
        )
        assert result.success
        data: TextAnalysisResult = result.data
        assert data.part_number == "TPS54202"
        assert data.category == "buck"

    def test_mock_with_hint(self) -> None:
        """带 hint 的 mock 分析"""
        result = analyze_datasheet_text(
            "some unknown text content",
            hint="LM7805",
        )
        assert result.success
        data: TextAnalysisResult = result.data
        assert data.part_number == "LM7805"

    def test_mock_ams1117(self) -> None:
        """AMS1117 mock 分析"""
        result = analyze_datasheet_text(
            "AMS1117 series ... low dropout regulator ...",
        )
        assert result.success
        data: TextAnalysisResult = result.data
        assert data.part_number == "AMS1117"
        assert data.category == "ldo"

    def test_empty_text_fails(self) -> None:
        """空文本 → 失败"""
        result = analyze_datasheet_text("", )
        assert not result.success
        assert result.error is not None

    def test_whitespace_text_fails(self) -> None:
        """纯空格文本 → 失败"""
        result = analyze_datasheet_text("   \n  ", )
        assert not result.success

    def test_analysis_has_result(self) -> None:
        """分析结果应返回成功"""
        result = analyze_datasheet_text("some text", hint="IC1", )
        assert result.success
        data: TextAnalysisResult = result.data
        assert data is not None

    def test_analysis_returns_fields(self) -> None:
        """分析结果应包含字段"""
        result = analyze_datasheet_text("TPS54202", )
        assert result.success
        data: TextAnalysisResult = result.data
        assert len(data.missing_fields) > 0


# ============================================================
# analyze_image (mock)
# ============================================================


class TestAnalyzeImage:
    """图片分析 — mock 模式"""

    def test_mock_image_analysis(self) -> None:
        """Mock 图片分析"""
        # 创建最小有效 PNG
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        result = analyze_image(png_header, )
        assert result.success
        data: ImageAnalysisResult = result.data
        assert len(data.pins) == 3
        assert data.package == "SOT-223"

    def test_empty_image_fails(self) -> None:
        """空图片 → 失败"""
        result = analyze_image(b"", )
        assert not result.success

    def test_image_analysis_returns_result(self) -> None:
        """图片分析返回结果"""
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        result = analyze_image(png_header, )
        assert result.success
        data: ImageAnalysisResult = result.data
        assert data is not None


# ============================================================
# analyze_image_file
# ============================================================


class TestAnalyzeImageFile:
    """文件路径分析"""

    def test_file_not_found(self) -> None:
        result = analyze_image_file("/nonexistent/file.png", )
        assert not result.success
        assert result.error is not None
        assert "不存在" in result.error.message

    def test_valid_file(self, tmp_path: Path) -> None:
        """有效文件 mock 分析"""
        img_file = tmp_path / "test.png"
        # 写一个最小 PNG
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        result = analyze_image_file(str(img_file), )
        assert result.success
