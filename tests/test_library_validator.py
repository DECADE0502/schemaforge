"""Tests for schemaforge.library.validator"""

from __future__ import annotations

import pytest

from schemaforge.library.validator import (
    DeviceDraft,
    PinDraft,
    Severity,
    ValidationReport,
    draft_to_device_model_dict,
    validate_draft,
)


# ============================================================
# DeviceDraft 模型测试
# ============================================================


class TestDeviceDraft:
    """DeviceDraft 数据模型"""

    def test_empty_draft(self) -> None:
        draft = DeviceDraft()
        assert draft.part_number == ""
        assert draft.pins == []
        assert draft.specs == {}
        assert draft.source == "manual"
        assert draft.confidence == 1.0

    def test_full_draft(self) -> None:
        draft = DeviceDraft(
            part_number="TPS54202",
            manufacturer="Texas Instruments",
            description="4.5V to 28V Input, 2A, Synchronous Step-Down Converter",
            category="buck",
            package="SOT-23-6",
            pin_count=6,
            pins=[
                PinDraft(name="BOOT", number="1", pin_type="input"),
                PinDraft(name="VIN", number="2", pin_type="power"),
                PinDraft(name="EN", number="3", pin_type="input"),
                PinDraft(name="FB", number="4", pin_type="input"),
                PinDraft(name="GND", number="5", pin_type="power"),
                PinDraft(name="SW", number="6", pin_type="output"),
            ],
            source="easyeda",
        )
        assert draft.part_number == "TPS54202"
        assert len(draft.pins) == 6
        assert draft.pins[0].name == "BOOT"

    def test_pin_draft_defaults(self) -> None:
        pin = PinDraft()
        assert pin.name == ""
        assert pin.number == ""
        assert pin.pin_type == ""
        assert pin.side == ""


# ============================================================
# validate_draft() 测试
# ============================================================


class TestValidateDraft:
    """校验函数"""

    def test_valid_minimal_draft(self) -> None:
        """最小有效草稿：只需要料号"""
        draft = DeviceDraft(part_number="R100K")
        report = validate_draft(draft)
        assert report.is_valid  # 无 error
        # 应有 warning/info (缺少 category, description, manufacturer)

    def test_empty_part_number_fails(self) -> None:
        """空料号 → error"""
        draft = DeviceDraft(part_number="")
        report = validate_draft(draft)
        assert not report.is_valid
        assert any(
            e.field_path == "part_number" and e.severity == Severity.ERROR
            for e in report.issues
        )

    def test_whitespace_part_number_fails(self) -> None:
        """纯空格料号 → error"""
        draft = DeviceDraft(part_number="   ")
        report = validate_draft(draft)
        assert not report.is_valid

    def test_unknown_category_warning(self) -> None:
        """未知类别 → warning"""
        draft = DeviceDraft(part_number="X1", category="unknown_widget")
        report = validate_draft(draft)
        assert report.is_valid  # warning 不阻塞
        assert any(
            e.field_path == "category" and e.severity == Severity.WARNING
            for e in report.issues
        )

    def test_valid_category_no_warning(self) -> None:
        """已知类别 → 不产生 category warning"""
        draft = DeviceDraft(part_number="X1", category="ldo")
        report = validate_draft(draft)
        category_issues = [
            i for i in report.issues
            if i.field_path == "category" and "未知" in i.message
        ]
        assert len(category_issues) == 0

    def test_duplicate_pin_names_error(self) -> None:
        """引脚名重复 → error"""
        draft = DeviceDraft(
            part_number="IC1",
            pins=[
                PinDraft(name="VCC", number="1"),
                PinDraft(name="VCC", number="2"),  # 重复
                PinDraft(name="GND", number="3"),
            ],
        )
        report = validate_draft(draft)
        assert not report.is_valid
        assert any("引脚名重复" in e.message for e in report.errors)

    def test_duplicate_pin_numbers_error(self) -> None:
        """引脚编号重复 → error"""
        draft = DeviceDraft(
            part_number="IC1",
            pins=[
                PinDraft(name="VCC", number="1"),
                PinDraft(name="GND", number="1"),  # 重复编号
            ],
        )
        report = validate_draft(draft)
        assert not report.is_valid
        assert any("引脚编号重复" in e.message for e in report.errors)

    def test_no_pins_warning_for_active(self) -> None:
        """有源器件无引脚 → warning"""
        draft = DeviceDraft(part_number="IC1", category="mcu")
        report = validate_draft(draft)
        assert report.is_valid  # warning 不阻塞
        pin_warnings = [
            i for i in report.issues
            if i.field_path == "pins" and i.severity == Severity.WARNING
        ]
        assert len(pin_warnings) > 0

    def test_no_pins_ok_for_passive(self) -> None:
        """无源器件无引脚 → 不产生引脚 warning"""
        draft = DeviceDraft(part_number="R1", category="resistor")
        report = validate_draft(draft)
        pin_warnings = [
            i for i in report.issues
            if i.field_path == "pins" and "未定义引脚" in i.message
        ]
        assert len(pin_warnings) == 0

    def test_package_pin_count_mismatch(self) -> None:
        """封装与引脚数不一致 → warning"""
        draft = DeviceDraft(
            part_number="IC1",
            package="SOT-23-5",
            pins=[
                PinDraft(name="A", number="1"),
                PinDraft(name="B", number="2"),
                PinDraft(name="C", number="3"),
            ],  # SOT-23-5 应有 5 个引脚
        )
        report = validate_draft(draft)
        pkg_issues = [
            i for i in report.issues
            if i.field_path == "package"
        ]
        assert len(pkg_issues) > 0

    def test_package_pin_count_match(self) -> None:
        """封装与引脚数一致 → 无封装 warning"""
        draft = DeviceDraft(
            part_number="IC1",
            package="SOT-23-5",
            pins=[PinDraft(name=f"P{i}", number=str(i)) for i in range(1, 6)],
        )
        report = validate_draft(draft)
        pkg_issues = [
            i for i in report.issues
            if i.field_path == "package"
        ]
        assert len(pkg_issues) == 0

    def test_pin_count_mismatch_warning(self) -> None:
        """声明引脚数与实际不一致 → warning"""
        draft = DeviceDraft(
            part_number="IC1",
            pin_count=8,
            pins=[PinDraft(name=f"P{i}", number=str(i)) for i in range(1, 5)],
        )
        report = validate_draft(draft)
        assert any("声明引脚数" in i.message for i in report.issues)

    def test_unknown_pin_type_warning(self) -> None:
        """未知引脚类型 → warning"""
        draft = DeviceDraft(
            part_number="IC1",
            pins=[PinDraft(name="X", number="1", pin_type="unknown_type")],
        )
        report = validate_draft(draft)
        assert any("未知引脚类型" in i.message for i in report.issues)

    def test_easyeda_source_without_id_warning(self) -> None:
        """EasyEDA 来源但无 ID → warning"""
        draft = DeviceDraft(
            part_number="IC1",
            source="easyeda",
            easyeda_id="",
        )
        report = validate_draft(draft)
        assert any("EasyEDA ID" in i.message for i in report.issues)

    def test_regulator_missing_voltage_info(self) -> None:
        """稳压器缺少电压参数 → info"""
        draft = DeviceDraft(
            part_number="AMS1117",
            category="ldo",
            specs={},
        )
        report = validate_draft(draft)
        info_issues = [
            i for i in report.suggestions
            if "电压" in i.message
        ]
        assert len(info_issues) > 0


# ============================================================
# ValidationReport 测试
# ============================================================


class TestValidationReport:
    """ValidationReport 属性"""

    def test_empty_report_is_valid(self) -> None:
        report = ValidationReport()
        assert report.is_valid
        assert len(report.errors) == 0
        assert len(report.warnings) == 0
        assert len(report.suggestions) == 0

    def test_to_dict(self) -> None:
        draft = DeviceDraft(part_number="IC1")
        report = validate_draft(draft)
        d = report.to_dict()
        assert "is_valid" in d
        assert "error_count" in d
        assert "issues" in d


# ============================================================
# draft_to_device_model_dict() 测试
# ============================================================


class TestDraftConversion:
    """DeviceDraft → DeviceModel 转换"""

    def test_basic_conversion(self) -> None:
        draft = DeviceDraft(
            part_number="TPS54202",
            manufacturer="TI",
            category="buck",
            package="SOT-23-6",
            pins=[
                PinDraft(name="BOOT", number="1", pin_type="input", side="left"),
                PinDraft(name="VIN", number="2", pin_type="power", side="left"),
                PinDraft(name="EN", number="3", pin_type="input", side="left"),
                PinDraft(name="FB", number="4", pin_type="input", side="right"),
                PinDraft(name="GND", number="5", pin_type="power", side="bottom"),
                PinDraft(name="SW", number="6", pin_type="output", side="right"),
            ],
        )
        d = draft_to_device_model_dict(draft)
        assert d["part_number"] == "TPS54202"
        assert d["manufacturer"] == "TI"
        assert d["category"] == "buck"
        assert d["symbol"] is not None
        assert len(d["symbol"].pins) == 6

    def test_passive_no_symbol(self) -> None:
        """无引脚 → symbol 为 None"""
        draft = DeviceDraft(part_number="R100K", category="resistor")
        d = draft_to_device_model_dict(draft)
        assert d["symbol"] is None

    def test_pin_type_mapping(self) -> None:
        """引脚类型正确映射"""
        from schemaforge.core.models import PinType
        draft = DeviceDraft(
            part_number="IC1",
            pins=[
                PinDraft(name="IN", number="1", pin_type="input"),
                PinDraft(name="OUT", number="2", pin_type="output"),
                PinDraft(name="VCC", number="3", pin_type="power"),
                PinDraft(name="NC", number="4", pin_type="nc"),
            ],
        )
        d = draft_to_device_model_dict(draft)
        pins = d["symbol"].pins
        assert pins[0].pin_type == PinType.INPUT
        assert pins[1].pin_type == PinType.OUTPUT
        assert pins[2].pin_type == PinType.POWER_IN
        assert pins[3].pin_type == PinType.NO_CONNECT  # nc → NO_CONNECT
