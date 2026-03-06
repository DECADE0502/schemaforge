"""器件检索模块测试"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from schemaforge.design.retrieval import (
    DeviceRequirement,
    DeviceRetriever,
    _extract_numeric,
)
from schemaforge.library.models import (
    DeviceModel,
    SymbolDef,
    SymbolPin,
    TopologyDef,
)
from schemaforge.library.store import ComponentStore


def _make_store(tmp: Path) -> ComponentStore:
    """创建带预置器件的测试 store"""
    store = ComponentStore(tmp)

    # AMS1117-3.3 (LDO)
    store.save_device(DeviceModel(
        part_number="AMS1117-3.3",
        manufacturer="AMS",
        description="LDO线性稳压器 3.3V 1A SOT-223",
        category="ldo",
        specs={"v_out": "3.3V", "v_dropout": "1.1V", "i_out_max": "1A", "v_in_max": "15V"},
        symbol=SymbolDef(pins=[
            SymbolPin(name="VIN", pin_number="3"),
            SymbolPin(name="VOUT", pin_number="2"),
            SymbolPin(name="GND", pin_number="1"),
        ]),
        topology=TopologyDef(circuit_type="ldo"),
        package="SOT-223",
    ))

    # VOLTAGE_DIVIDER (无源)
    store.save_device(DeviceModel(
        part_number="VOLTAGE_DIVIDER",
        description="通用电压分压器",
        category="voltage_divider",
        topology=TopologyDef(circuit_type="voltage_divider"),
    ))

    # LED_INDICATOR
    store.save_device(DeviceModel(
        part_number="LED_INDICATOR",
        description="LED指示灯电路",
        category="led",
        topology=TopologyDef(circuit_type="led_driver"),
    ))

    # TPS54202 (Buck, 无拓扑)
    store.save_device(DeviceModel(
        part_number="TPS54202",
        manufacturer="TI",
        description="Buck降压转换器",
        category="buck",
        specs={"v_in_max": "28V", "i_out_max": "2A"},
        package="SOT-23-6",
    ))

    return store


class TestDeviceRetriever:
    """检索器测试"""

    def setup_method(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.store = _make_store(self.tmp)
        self.retriever = DeviceRetriever(self.store)

    def teardown_method(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- 精确料号匹配 ---

    def test_exact_part_number_match(self) -> None:
        results = self.retriever.search(part_number="AMS1117-3.3")
        assert len(results) == 1
        assert results[0].device.part_number == "AMS1117-3.3"
        assert results[0].score >= 1.0

    def test_exact_part_number_not_found_degrades(self) -> None:
        results = self.retriever.search(part_number="NOT_EXIST")
        # 降级到关键字搜索
        assert isinstance(results, list)

    # --- 分类匹配 ---

    def test_search_by_category(self) -> None:
        results = self.retriever.search(category="ldo")
        assert len(results) >= 1
        assert results[0].device.category == "ldo"

    def test_category_alias_resolution(self) -> None:
        results = self.retriever.search(category="稳压")
        assert len(results) >= 1
        assert results[0].device.category == "ldo"

    # --- 关键字搜索 ---

    def test_search_by_query(self) -> None:
        results = self.retriever.search(query="LDO")
        assert len(results) >= 1
        pns = [r.device.part_number for r in results]
        assert "AMS1117-3.3" in pns

    def test_search_by_description(self) -> None:
        results = self.retriever.search(query="指示灯")
        assert len(results) >= 1
        assert any(r.device.part_number == "LED_INDICATOR" for r in results)

    # --- 规格匹配 ---

    def test_spec_matching(self) -> None:
        results = self.retriever.search(
            category="ldo",
            specs={"v_out": "3.3V"},
        )
        assert len(results) >= 1
        assert any("规格匹配" in r for result in results for r in result.match_reasons)

    # --- 拓扑过滤 ---

    def test_must_have_topology_filters(self) -> None:
        results = self.retriever.search(
            category="buck",
            must_have_topology=True,
        )
        # TPS54202 没有拓扑定义，应该被过滤
        pns = [r.device.part_number for r in results]
        assert "TPS54202" not in pns

    def test_without_topology_filter(self) -> None:
        results = self.retriever.search(category="buck")
        pns = [r.device.part_number for r in results]
        assert "TPS54202" in pns

    # --- 排序 ---

    def test_results_sorted_by_score(self) -> None:
        results = self.retriever.search()  # 返回全部
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    # --- DeviceRequirement ---

    def test_search_by_requirement(self) -> None:
        req = DeviceRequirement(
            role="main_regulator",
            category="ldo",
            query="3.3V稳压",
            must_have_topology=True,
        )
        results = self.retriever.search_by_requirement(req)
        assert len(results) >= 1
        assert results[0].device.part_number == "AMS1117-3.3"

    def test_get_best_match(self) -> None:
        req = DeviceRequirement(category="ldo", must_have_topology=True)
        best = self.retriever.get_best_match(req)
        assert best is not None
        assert best.device.part_number == "AMS1117-3.3"

    def test_get_best_match_no_result(self) -> None:
        req = DeviceRequirement(category="fpga", must_have_topology=True)
        best = self.retriever.get_best_match(req)
        assert best is None

    # --- 评分 ---

    def test_topology_bonus(self) -> None:
        results = self.retriever.search(query="AMS1117")
        assert len(results) >= 1
        assert any("有拓扑定义" in r for r in results[0].match_reasons)

    def test_symbol_bonus(self) -> None:
        results = self.retriever.search(part_number="AMS1117-3.3")
        assert any("有符号定义" in r for r in results[0].match_reasons)

    # --- to_dict ---

    def test_retrieval_result_to_dict(self) -> None:
        results = self.retriever.search(part_number="AMS1117-3.3")
        d = results[0].to_dict()
        assert d["part_number"] == "AMS1117-3.3"
        assert d["has_topology"] is True
        assert d["has_symbol"] is True
        assert isinstance(d["score"], float)

    # --- limit ---

    def test_limit_results(self) -> None:
        results = self.retriever.search(limit=2)
        assert len(results) <= 2


class TestExtractNumeric:
    """数值提取测试"""

    def test_voltage(self) -> None:
        assert _extract_numeric("3.3V") == 3.3

    def test_current(self) -> None:
        assert _extract_numeric("1A") == 1.0

    def test_capacitor(self) -> None:
        assert _extract_numeric("10uF") == 10.0

    def test_plain_number(self) -> None:
        assert _extract_numeric("42") == 42.0

    def test_no_number(self) -> None:
        assert _extract_numeric("abc") is None
