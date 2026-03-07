"""器件库服务层

统一的器件库操作入口，封装 CRUD + 校验 + 去重 + EasyEDA 导入转换。
GUI 和 AI 工具层都通过此服务操作器件库。

职责:
- add_device_from_draft(): 从草稿入库（校验 → 去重 → 保存）
- import_from_easyeda(): EasyEDA 搜索结果 → DeviceDraft → 入库
- search() / get() / delete(): 查询和删除
- list_all(): 列出全部器件
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from schemaforge.ingest.easyeda_provider import (
    EasyEDAHit,
    EasyEDASymbolResult,
)
from schemaforge.library.dedupe import DuplicateCheckResult, check_duplicate
from schemaforge.library.models import DeviceModel, SymbolDef
from schemaforge.library.store import ComponentStore
from schemaforge.library.validator import (
    DeviceDraft,
    PinDraft,
    ValidationReport,
    draft_to_device_model_dict,
    validate_draft,
)


# ============================================================
# 入库结果
# ============================================================


@dataclass
class AddDeviceResult:
    """器件入库结果"""

    success: bool
    device: DeviceModel | None = None
    validation: ValidationReport | None = None
    duplicate_check: DuplicateCheckResult | None = None
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "part_number": self.device.part_number if self.device else "",
            "error": self.error_message,
            "validation": self.validation.to_dict() if self.validation else None,
            "duplicates": {
                "has_exact": self.duplicate_check.has_exact,
                "matches": len(self.duplicate_check.matches),
            }
            if self.duplicate_check
            else None,
        }


# ============================================================
# 器件库服务
# ============================================================


class LibraryService:
    """器件库服务层

    用法::

        service = LibraryService(store_dir=Path("schemaforge/store"))

        # 从草稿入库
        draft = DeviceDraft(part_number="TPS54202", category="buck", ...)
        result = service.add_device_from_draft(draft)
        if result.success:
            print(f"入库成功: {result.device.part_number}")

        # EasyEDA 搜索导入
        draft = service.easyeda_hit_to_draft(hit)
        result = service.add_device_from_draft(draft)

        # 搜索
        devices = service.search(query="TPS")
    """

    def __init__(self, store_dir: Path | str) -> None:
        self._store = ComponentStore(Path(store_dir))

    @property
    def store(self) -> ComponentStore:
        """暴露底层 store (用于直接操作，如 rebuild_index)"""
        return self._store

    # ----------------------------------------------------------
    # 入库
    # ----------------------------------------------------------

    def add_device_from_draft(
        self,
        draft: DeviceDraft,
        *,
        force: bool = False,
        skip_validation: bool = False,
        skip_dedupe: bool = False,
        persist: bool = True,
    ) -> AddDeviceResult:
        """从草稿创建器件并入库

        流程: validate → dedupe check → convert → save

        Args:
            draft: 器件草稿
            force: 强制入库（跳过重复检测阻塞）
            skip_validation: 跳过校验（仅用于迁移/修复）
            skip_dedupe: 跳过去重检测
            persist: 是否持久化到存储（False 仅做转换+校验，不写盘）

        Returns:
            AddDeviceResult
        """
        # 1. 校验
        validation: ValidationReport | None = None
        if not skip_validation:
            validation = validate_draft(draft)
            if not validation.is_valid:
                return AddDeviceResult(
                    success=False,
                    validation=validation,
                    error_message="校验未通过: "
                    + "; ".join(e.message for e in validation.errors),
                )

        # 2. 去重
        dup_result: DuplicateCheckResult | None = None
        if not skip_dedupe:
            dup_result = check_duplicate(
                self._store,
                part_number=draft.part_number,
                manufacturer=draft.manufacturer,
                package=draft.package,
            )
            if dup_result.has_exact and not force:
                return AddDeviceResult(
                    success=False,
                    validation=validation,
                    duplicate_check=dup_result,
                    error_message=f"器件 {draft.part_number} 已存在于库中",
                )

        # 3. 转换为 DeviceModel
        try:
            model_dict = draft_to_device_model_dict(draft)
            device = DeviceModel(**model_dict)
        except Exception as exc:
            return AddDeviceResult(
                success=False,
                validation=validation,
                duplicate_check=dup_result,
                error_message=f"器件模型转换失败: {exc}",
            )

        # 4. 保存（仅在 persist=True 时写盘）
        if persist:
            try:
                self._store.save_device(device)
            except Exception as exc:
                return AddDeviceResult(
                    success=False,
                    validation=validation,
                    duplicate_check=dup_result,
                    error_message=f"保存失败: {exc}",
                )

        return AddDeviceResult(
            success=True,
            device=device,
            validation=validation,
            duplicate_check=dup_result,
        )

    def update_device_symbol(
        self,
        part_number: str,
        symbol: SymbolDef,
    ) -> bool:
        device = self._store.get_device(part_number)
        if device is None:
            return False
        device.symbol = symbol
        self._store.save_device(device)
        return True

    # ----------------------------------------------------------
    # EasyEDA 导入
    # ----------------------------------------------------------

    def easyeda_hit_to_draft(self, hit: EasyEDAHit) -> DeviceDraft:
        """将 EasyEDA/JLCPCB 搜索命中转换为 DeviceDraft

        Args:
            hit: EasyEDA/JLCPCB 搜索结果

        Returns:
            DeviceDraft（部分字段填充，需用户确认后入库）
        """
        # 构建描述: 合并原始描述和库存/价格信息
        desc_parts: list[str] = []
        if hit.description:
            desc_parts.append(hit.description)
        if hit.category_name:
            desc_parts.append(f"分类: {hit.category_name}")

        notes_parts: list[str] = []
        if hit.stock > 0:
            notes_parts.append(f"库存: {hit.stock}")
        if hit.price_range:
            notes_parts.append(f"价格: {hit.price_range}")
        if hit.library_type:
            notes_parts.append(f"类型: {hit.library_type}")

        return DeviceDraft(
            part_number=hit.title or "",
            manufacturer=hit.manufacturer or "",
            description=" | ".join(desc_parts) if desc_parts else "",
            package=hit.package or "",
            pin_count=hit.pin_count,
            lcsc_part=hit.lcsc_part or "",
            datasheet_url=hit.datasheet_url or "",
            easyeda_id=hit.uuid or "",
            source="easyeda",
            confidence=0.8,
            notes="; ".join(notes_parts) if notes_parts else "",
        )

    def easyeda_symbol_to_draft(self, symbol: EasyEDASymbolResult) -> DeviceDraft:
        """将 EasyEDA 器件符号详情转换为 DeviceDraft (包含引脚)

        Args:
            symbol: EasyEDA 器件符号详情

        Returns:
            DeviceDraft（带引脚定义）
        """
        pins: list[PinDraft] = []
        for pin_info in symbol.pins:
            # 优先使用 pin_type，回退到 electric_type
            ptype = pin_info.pin_type or pin_info.electric_type or ""
            pins.append(
                PinDraft(
                    name=pin_info.name or "",
                    number=pin_info.number or "",
                    pin_type=ptype,
                    description=pin_info.description or "",
                )
            )

        # 从属性中提取附加信息
        lcsc = symbol.attributes.get("lcsc_part", "")
        manufacturer = symbol.attributes.get("BOM_Manufacturer", "")

        return DeviceDraft(
            part_number=symbol.title or "",
            manufacturer=manufacturer,
            package=symbol.package or "",
            pins=pins,
            pin_count=len(pins),
            lcsc_part=lcsc,
            easyeda_id=symbol.uuid or "",
            source="easyeda",
            confidence=0.9,
            notes=f"从 EasyEDA 导入 (UUID: {symbol.uuid}), 含 {len(pins)} 引脚",
        )

    # ----------------------------------------------------------
    # 查询
    # ----------------------------------------------------------

    def get(self, part_number: str) -> DeviceModel | None:
        """获取单个器件"""
        return self._store.get_device(part_number)

    def search(
        self,
        query: str = "",
        category: str = "",
        **filters: str,
    ) -> list[DeviceModel]:
        """搜索器件库"""
        return self._store.search_devices(
            category=category,
            query=query,
            **filters,
        )

    def list_all(self) -> list[str]:
        """列出所有料号"""
        return self._store.list_devices()

    def delete(self, part_number: str) -> bool:
        """删除器件"""
        return self._store.delete_device(part_number)

    def get_stats(self) -> dict[str, Any]:
        """获取器件库统计信息"""
        all_parts = self._store.list_devices()
        categories: dict[str, int] = {}
        for pn in all_parts:
            dev = self._store.get_device(pn)
            if dev:
                cat = dev.category or "未分类"
                categories[cat] = categories.get(cat, 0) + 1

        return {
            "total_devices": len(all_parts),
            "categories": categories,
        }

    # ----------------------------------------------------------
    # 批量操作
    # ----------------------------------------------------------

    def rebuild_index(self) -> None:
        """重建索引"""
        self._store.rebuild_index()

    def validate_only(self, draft: DeviceDraft) -> ValidationReport:
        """仅校验不入库"""
        return validate_draft(draft)

    def check_duplicate_only(
        self,
        part_number: str,
        manufacturer: str = "",
        package: str = "",
    ) -> DuplicateCheckResult:
        """仅检查重复不入库"""
        return check_duplicate(
            self._store,
            part_number=part_number,
            manufacturer=manufacturer,
            package=package,
        )
