"""SchemaForge 器件库存储测试

测试 ComponentStore 的初始化、CRUD、搜索、索引重建等功能。
所有测试使用 tmp_path 夹具确保隔离。
"""

from __future__ import annotations

from pathlib import Path


from schemaforge.core.models import PinType
from schemaforge.library.models import (
    DeviceModel,
    ExternalComponent,
    PinSide,
    SymbolDef,
    SymbolPin,
    TopologyConnection,
    TopologyDef,
)
from schemaforge.library.store import ComponentStore


# ============================================================
# 测试辅助
# ============================================================

def _make_device(
    part_number: str = "TEST-001",
    category: str = "test",
    manufacturer: str = "TestCo",
    description: str = "测试器件",
    package: str = "0805",
    lcsc_part: str = "",
    source: str = "manual",
) -> DeviceModel:
    """创建测试用 DeviceModel"""
    return DeviceModel(
        part_number=part_number,
        manufacturer=manufacturer,
        description=description,
        category=category,
        package=package,
        lcsc_part=lcsc_part,
        source=source,
    )


def _make_ldo_device() -> DeviceModel:
    """创建带符号和拓扑的 LDO DeviceModel"""
    return DeviceModel(
        part_number="AMS1117-3.3",
        manufacturer="AMS",
        description="LDO 3.3V 1A",
        category="ldo",
        specs={"v_out": "3.3V"},
        symbol=SymbolDef(
            pins=[
                SymbolPin(name="VIN", side=PinSide.LEFT, pin_type=PinType.POWER_IN),
                SymbolPin(name="VOUT", side=PinSide.RIGHT, pin_type=PinType.POWER_OUT),
                SymbolPin(name="GND", side=PinSide.BOTTOM, pin_type=PinType.GROUND),
            ],
            size=(4, 3),
        ),
        topology=TopologyDef(
            circuit_type="ldo",
            external_components=[
                ExternalComponent(
                    role="input_cap",
                    ref_prefix="C",
                    default_value="10uF",
                    schemdraw_element="Capacitor",
                ),
            ],
            connections=[
                TopologyConnection(
                    net_name="VIN",
                    device_pin="VIN",
                    external_refs=["input_cap.1"],
                    is_power=True,
                ),
            ],
        ),
        lcsc_part="C347222",
        package="SOT-223",
    )


# ============================================================
# 初始化测试
# ============================================================

class TestStoreInit:
    """ComponentStore 初始化测试"""

    def test_creates_directories(self, tmp_path: Path) -> None:
        """初始化时创建目录结构"""
        store_dir = tmp_path / "store"
        store = ComponentStore(store_dir)
        assert store.devices_dir.exists()
        assert store.devices_dir.is_dir()

    def test_creates_database(self, tmp_path: Path) -> None:
        """初始化时创建 SQLite 数据库"""
        store = ComponentStore(tmp_path / "store")
        assert store.db_path.exists()

    def test_idempotent_init(self, tmp_path: Path) -> None:
        """多次初始化不报错"""
        store_dir = tmp_path / "store"
        store1 = ComponentStore(store_dir)
        store2 = ComponentStore(store_dir)
        assert store1.db_path == store2.db_path


# ============================================================
# save_device + get_device 测试
# ============================================================

class TestSaveAndGet:
    """保存和加载测试"""

    def test_save_and_get_basic(self, tmp_path: Path) -> None:
        """保存后加载基本器件"""
        store = ComponentStore(tmp_path / "store")
        device = _make_device()
        path = store.save_device(device)

        assert path.exists()
        assert path.suffix == ".json"

        loaded = store.get_device("TEST-001")
        assert loaded is not None
        assert loaded.part_number == "TEST-001"
        assert loaded.category == "test"
        assert loaded.manufacturer == "TestCo"

    def test_save_and_get_with_symbol(self, tmp_path: Path) -> None:
        """保存后加载带符号的器件"""
        store = ComponentStore(tmp_path / "store")
        device = _make_ldo_device()
        store.save_device(device)

        loaded = store.get_device("AMS1117-3.3")
        assert loaded is not None
        assert loaded.symbol is not None
        assert len(loaded.symbol.pins) == 3
        assert loaded.topology is not None
        assert loaded.topology.circuit_type == "ldo"

    def test_get_nonexistent(self, tmp_path: Path) -> None:
        """加载不存在的器件返回 None"""
        store = ComponentStore(tmp_path / "store")
        result = store.get_device("DOES_NOT_EXIST")
        assert result is None

    def test_overwrite_device(self, tmp_path: Path) -> None:
        """相同料号二次保存会覆盖"""
        store = ComponentStore(tmp_path / "store")
        dev1 = _make_device(description="版本1")
        dev2 = _make_device(description="版本2")

        store.save_device(dev1)
        store.save_device(dev2)

        loaded = store.get_device("TEST-001")
        assert loaded is not None
        assert loaded.description == "版本2"

    def test_save_special_characters(self, tmp_path: Path) -> None:
        """料号含特殊字符时文件名安全"""
        store = ComponentStore(tmp_path / "store")
        device = _make_device(part_number="IC/REG-3.3V:1A")
        path = store.save_device(device)
        assert path.exists()
        assert "/" not in path.name
        assert ":" not in path.name

        loaded = store.get_device("IC/REG-3.3V:1A")
        assert loaded is not None
        assert loaded.part_number == "IC/REG-3.3V:1A"


# ============================================================
# search_devices 测试
# ============================================================

class TestSearchDevices:
    """搜索测试"""

    def test_search_by_category(self, tmp_path: Path) -> None:
        """按类别搜索"""
        store = ComponentStore(tmp_path / "store")
        store.save_device(_make_device(part_number="R1", category="resistor"))
        store.save_device(_make_device(part_number="C1", category="capacitor"))
        store.save_device(_make_device(part_number="R2", category="resistor"))

        results = store.search_devices(category="resistor")
        assert len(results) == 2
        pns = {r.part_number for r in results}
        assert pns == {"R1", "R2"}

    def test_search_by_query(self, tmp_path: Path) -> None:
        """关键字搜索"""
        store = ComponentStore(tmp_path / "store")
        store.save_device(_make_device(part_number="AMS1117", description="LDO稳压器"))
        store.save_device(_make_device(part_number="TPS54202", description="Buck降压器"))
        store.save_device(_make_device(part_number="LM7805", description="线性稳压器"))

        results = store.search_devices(query="稳压器")
        pns = {r.part_number for r in results}
        assert "AMS1117" in pns
        assert "LM7805" in pns

    def test_search_by_category_and_query(self, tmp_path: Path) -> None:
        """同时按类别和关键字搜索"""
        store = ComponentStore(tmp_path / "store")
        store.save_device(_make_device(part_number="A", category="ldo", description="LDO器件"))
        store.save_device(_make_device(part_number="B", category="buck", description="LDO错误"))
        store.save_device(_make_device(part_number="C", category="ldo", description="其他器件"))

        results = store.search_devices(category="ldo", query="LDO")
        assert len(results) == 1
        assert results[0].part_number == "A"

    def test_search_no_results(self, tmp_path: Path) -> None:
        """搜索无结果返回空列表"""
        store = ComponentStore(tmp_path / "store")
        store.save_device(_make_device())
        results = store.search_devices(category="nonexistent")
        assert results == []

    def test_search_by_filter(self, tmp_path: Path) -> None:
        """按额外字段筛选"""
        store = ComponentStore(tmp_path / "store")
        store.save_device(_make_device(part_number="D1", package="SOT-223"))
        store.save_device(_make_device(part_number="D2", package="0805"))

        results = store.search_devices(package="SOT-223")
        assert len(results) == 1
        assert results[0].part_number == "D1"


# ============================================================
# list_devices 测试
# ============================================================

class TestListDevices:
    """列表测试"""

    def test_list_empty(self, tmp_path: Path) -> None:
        """空库返回空列表"""
        store = ComponentStore(tmp_path / "store")
        assert store.list_devices() == []

    def test_list_multiple(self, tmp_path: Path) -> None:
        """列出多个器件"""
        store = ComponentStore(tmp_path / "store")
        store.save_device(_make_device(part_number="A"))
        store.save_device(_make_device(part_number="B"))
        store.save_device(_make_device(part_number="C"))

        result = store.list_devices()
        assert len(result) == 3
        assert result == ["A", "B", "C"]  # 按字母排序


# ============================================================
# delete_device 测试
# ============================================================

class TestDeleteDevice:
    """删除测试"""

    def test_delete_existing(self, tmp_path: Path) -> None:
        """删除已存在的器件"""
        store = ComponentStore(tmp_path / "store")
        store.save_device(_make_device())

        assert store.delete_device("TEST-001") is True
        assert store.get_device("TEST-001") is None
        assert store.list_devices() == []

    def test_delete_nonexistent(self, tmp_path: Path) -> None:
        """删除不存在的器件返回 False"""
        store = ComponentStore(tmp_path / "store")
        assert store.delete_device("NOPE") is False

    def test_delete_removes_json(self, tmp_path: Path) -> None:
        """删除操作同时移除 JSON 文件"""
        store = ComponentStore(tmp_path / "store")
        path = store.save_device(_make_device())
        assert path.exists()

        store.delete_device("TEST-001")
        assert not path.exists()


# ============================================================
# rebuild_index 测试
# ============================================================

class TestRebuildIndex:
    """索引重建测试"""

    def test_rebuild_from_json(self, tmp_path: Path) -> None:
        """从 JSON 文件重建索引"""
        store = ComponentStore(tmp_path / "store")
        store.save_device(_make_device(part_number="X1", category="ic"))
        store.save_device(_make_device(part_number="X2", category="passive"))

        # 手动清空索引
        import sqlite3
        con = sqlite3.connect(str(store.db_path))
        con.execute("DELETE FROM devices")
        con.commit()
        con.close()

        # 确认索引已清空
        assert store.list_devices() == []

        # 重建
        store.rebuild_index()
        result = store.list_devices()
        assert len(result) == 2
        assert "X1" in result
        assert "X2" in result

    def test_rebuild_skips_invalid(self, tmp_path: Path) -> None:
        """重建时跳过无效的 JSON 文件"""
        store = ComponentStore(tmp_path / "store")
        store.save_device(_make_device(part_number="GOOD"))

        # 写入一个无效的 JSON
        bad_file = store.devices_dir / "bad_device.json"
        bad_file.write_text("{ invalid json !!!", encoding="utf-8")

        store.rebuild_index()
        result = store.list_devices()
        assert result == ["GOOD"]


# ============================================================
# _sanitize_filename 测试
# ============================================================

class TestSanitizeFilename:
    """文件名清理测试"""

    def test_simple_name(self) -> None:
        """简单名称不变"""
        assert ComponentStore._sanitize_filename("AMS1117-3.3") == "AMS1117-3.3"

    def test_slash(self) -> None:
        """斜杠替换为下划线"""
        assert ComponentStore._sanitize_filename("IC/REG") == "IC_REG"

    def test_multiple_special(self) -> None:
        """多种特殊字符"""
        result = ComponentStore._sanitize_filename('A:B*C?"D')
        assert ":" not in result
        assert "*" not in result
        assert "?" not in result
        assert '"' not in result

    def test_spaces(self) -> None:
        """空格替换为下划线"""
        assert ComponentStore._sanitize_filename("Part Number 1") == "Part_Number_1"
