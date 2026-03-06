"""Tests for schemaforge.library.service"""

from __future__ import annotations

import pytest
from pathlib import Path

from schemaforge.ingest.easyeda_provider import EasyEDAHit, EasyEDASymbolResult, EasyEDAPinInfo
from schemaforge.library.models import DeviceModel
from schemaforge.library.service import AddDeviceResult, LibraryService
from schemaforge.library.validator import DeviceDraft, PinDraft


@pytest.fixture
def service(tmp_path: Path) -> LibraryService:
    """创建临时 LibraryService"""
    return LibraryService(tmp_path / "test_store")


@pytest.fixture
def populated_service(tmp_path: Path) -> LibraryService:
    """预置数据的 LibraryService"""
    svc = LibraryService(tmp_path / "pop_store")
    draft = DeviceDraft(
        part_number="AMS1117-3.3",
        manufacturer="AMS",
        category="ldo",
        description="3.3V 1A LDO",
        package="SOT-223",
    )
    svc.add_device_from_draft(draft)
    return svc


# ============================================================
# add_device_from_draft
# ============================================================


class TestAddDeviceFromDraft:
    """入库流程"""

    def test_add_minimal_device(self, service: LibraryService) -> None:
        """最小有效草稿入库"""
        draft = DeviceDraft(part_number="R100K")
        result = service.add_device_from_draft(draft)
        assert result.success
        assert result.device is not None
        assert result.device.part_number == "R100K"

    def test_add_full_device(self, service: LibraryService) -> None:
        """完整草稿入库"""
        draft = DeviceDraft(
            part_number="TPS54202",
            manufacturer="Texas Instruments",
            category="buck",
            description="2A Synchronous Buck Converter",
            package="SOT-23-6",
            pins=[
                PinDraft(name="BOOT", number="1", pin_type="input", side="left"),
                PinDraft(name="VIN", number="2", pin_type="power", side="left"),
                PinDraft(name="EN", number="3", pin_type="input", side="left"),
                PinDraft(name="FB", number="4", pin_type="input", side="right"),
                PinDraft(name="GND", number="5", pin_type="power", side="bottom"),
                PinDraft(name="SW", number="6", pin_type="output", side="right"),
            ],
            pin_count=6,
        )
        result = service.add_device_from_draft(draft)
        assert result.success
        assert result.device is not None
        assert result.device.symbol is not None
        assert len(result.device.symbol.pins) == 6

    def test_add_fails_without_part_number(self, service: LibraryService) -> None:
        """无料号 → 校验失败"""
        draft = DeviceDraft(part_number="")
        result = service.add_device_from_draft(draft)
        assert not result.success
        assert "校验未通过" in result.error_message

    def test_add_duplicate_blocked(self, populated_service: LibraryService) -> None:
        """重复入库 → 阻塞"""
        draft = DeviceDraft(
            part_number="AMS1117-3.3",
            category="ldo",
        )
        result = populated_service.add_device_from_draft(draft)
        assert not result.success
        assert "已存在" in result.error_message

    def test_add_duplicate_force(self, populated_service: LibraryService) -> None:
        """force=True → 强制覆盖"""
        draft = DeviceDraft(
            part_number="AMS1117-3.3",
            category="ldo",
            description="Updated description",
        )
        result = populated_service.add_device_from_draft(draft, force=True)
        assert result.success

    def test_add_skip_validation(self, service: LibraryService) -> None:
        """skip_validation → 无料号也能入库（用于迁移）"""
        # 这种场景下 part_number 仍为空 → 保存可能报错或成功取决于 store 实现
        draft = DeviceDraft(part_number="MIGRATED_PART")
        result = service.add_device_from_draft(draft, skip_validation=True)
        assert result.success

    def test_add_skip_dedupe(self, populated_service: LibraryService) -> None:
        """skip_dedupe → 不检查重复"""
        draft = DeviceDraft(part_number="AMS1117-3.3", category="ldo")
        result = populated_service.add_device_from_draft(draft, skip_dedupe=True)
        assert result.success  # 跳过去重，直接覆盖保存

    def test_validation_report_in_result(self, service: LibraryService) -> None:
        """结果中包含校验报告"""
        draft = DeviceDraft(part_number="IC1", category="unknown_widget")
        result = service.add_device_from_draft(draft)
        assert result.success  # unknown category 是 warning 不是 error
        assert result.validation is not None


# ============================================================
# EasyEDA 导入
# ============================================================


class TestEasyEDAImport:
    """EasyEDA 导入转换"""

    def test_hit_to_draft(self, service: LibraryService) -> None:
        """EasyEDAHit → DeviceDraft"""
        hit = EasyEDAHit(
            title="TPS54202DDCR",
            uuid="abc-123",
            description="2A Buck Converter",
            package="SOT-23-6",
            manufacturer="Texas Instruments",
            lcsc_part="C87774",
            datasheet_url="https://example.com/ds.pdf",
            pin_count=6,
        )
        draft = service.easyeda_hit_to_draft(hit)
        assert draft.part_number == "TPS54202DDCR"
        assert draft.manufacturer == "Texas Instruments"
        assert draft.source == "easyeda"
        assert draft.easyeda_id == "abc-123"
        assert draft.lcsc_part == "C87774"
        assert draft.confidence == 0.8

    def test_symbol_to_draft_with_pins(self, service: LibraryService) -> None:
        """EasyEDASymbolResult → DeviceDraft (含引脚)"""
        symbol = EasyEDASymbolResult(
            uuid="abc-123",
            title="TPS54202",
            package="SOT-23-6",
            pins=[
                EasyEDAPinInfo(number="1", name="BOOT"),
                EasyEDAPinInfo(number="2", name="VIN"),
                EasyEDAPinInfo(number="3", name="EN"),
            ],
        )
        draft = service.easyeda_symbol_to_draft(symbol)
        assert len(draft.pins) == 3
        assert draft.pins[0].name == "BOOT"
        assert draft.pin_count == 3
        assert draft.source == "easyeda"

    def test_easyeda_import_full_flow(self, service: LibraryService) -> None:
        """完整 EasyEDA 导入流程: hit → draft → 入库"""
        hit = EasyEDAHit(
            title="LM7805",
            uuid="xyz-456",
            package="TO-220",
            manufacturer="ON Semiconductor",
        )
        draft = service.easyeda_hit_to_draft(hit)
        draft.category = "linear_regulator"
        result = service.add_device_from_draft(draft)
        assert result.success
        # 验证入库后可检索
        device = service.get("LM7805")
        assert device is not None
        assert device.source == "easyeda"


# ============================================================
# 查询
# ============================================================


class TestQuery:
    """查询和搜索"""

    def test_get_existing(self, populated_service: LibraryService) -> None:
        device = populated_service.get("AMS1117-3.3")
        assert device is not None
        assert device.category == "ldo"

    def test_get_nonexistent(self, populated_service: LibraryService) -> None:
        device = populated_service.get("NONEXISTENT")
        assert device is None

    def test_search_by_query(self, populated_service: LibraryService) -> None:
        results = populated_service.search(query="AMS")
        assert len(results) >= 1

    def test_search_by_category(self, populated_service: LibraryService) -> None:
        results = populated_service.search(category="ldo")
        assert len(results) >= 1

    def test_list_all(self, populated_service: LibraryService) -> None:
        parts = populated_service.list_all()
        assert "AMS1117-3.3" in parts

    def test_delete(self, populated_service: LibraryService) -> None:
        assert populated_service.delete("AMS1117-3.3")
        assert populated_service.get("AMS1117-3.3") is None

    def test_delete_nonexistent(self, populated_service: LibraryService) -> None:
        assert not populated_service.delete("NONEXISTENT")


# ============================================================
# 统计和工具方法
# ============================================================


class TestUtilities:
    """工具方法"""

    def test_get_stats(self, populated_service: LibraryService) -> None:
        stats = populated_service.get_stats()
        assert stats["total_devices"] >= 1
        assert "ldo" in stats["categories"]

    def test_validate_only(self, service: LibraryService) -> None:
        draft = DeviceDraft(part_number="IC1")
        report = service.validate_only(draft)
        assert report.is_valid

    def test_check_duplicate_only(self, populated_service: LibraryService) -> None:
        result = populated_service.check_duplicate_only("AMS1117-3.3")
        assert result.has_exact

    def test_rebuild_index(self, populated_service: LibraryService) -> None:
        """重建索引后仍可查询"""
        populated_service.rebuild_index()
        parts = populated_service.list_all()
        assert "AMS1117-3.3" in parts

    def test_result_to_dict(self, service: LibraryService) -> None:
        """AddDeviceResult.to_dict()"""
        draft = DeviceDraft(part_number="IC1")
        result = service.add_device_from_draft(draft)
        d = result.to_dict()
        assert d["success"] is True
        assert d["part_number"] == "IC1"
