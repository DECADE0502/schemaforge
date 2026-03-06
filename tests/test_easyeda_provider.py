"""Tests for schemaforge.ingest.easyeda_provider

所有 HTTP 调用均通过 mock 替代，不发起真实网络请求。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from schemaforge.ingest.easyeda_provider import (
    ELECTRIC_TYPE_MAP,
    PIN_TYPE_MAP,
    EasyEDAHit,
    EasyEDAPinInfo,
    EasyEDASymbolResult,
    _format_price_range,
    _parse_easyeda_product,
    _parse_jlcpcb_response,
    _safe_int,
    fetch_easyeda_symbol,
    parse_easyeda_pins,
    search_easyeda,
    search_jlcpcb,
)


# ============================================================
# 测试数据
# ============================================================

SAMPLE_JLCPCB_RESPONSE = {
    "data": {
        "componentPageInfo": {
            "list": [
                {
                    "componentCode": "C82899",
                    "componentModelEn": "ESP32-WROOM-32-N4",
                    "componentBrandEn": "Espressif",
                    "componentSpecificationEn": "SMD-38",
                    "stockCount": 12500,
                    "componentPrices": [
                        {"startNumber": 1, "endNumber": 9, "productPrice": 3.50},
                        {"startNumber": 10, "endNumber": 99, "productPrice": 2.80},
                        {"startNumber": 100, "endNumber": 999, "productPrice": 2.10},
                    ],
                    "describe": "WiFi+BLE 双模模组",
                    "dataManualUrl": "https://example.com/esp32.pdf",
                    "componentTypeEn": "WiFi Modules",
                    "componentLibraryType": "expand",
                },
                {
                    "componentCode": "C123456",
                    "componentModelEn": "ESP32-S3-WROOM-1",
                    "componentBrandEn": "Espressif",
                    "componentSpecificationEn": "SMD-40",
                    "stockCount": 0,
                    "componentPrices": [],
                    "describe": "WiFi+BLE5.0 模组",
                    "dataManualUrl": "",
                    "componentTypeEn": "WiFi Modules",
                    "componentLibraryType": "base",
                },
            ]
        }
    }
}

SAMPLE_EASYEDA_RESPONSE = {
    "result": [
        {
            "uuid": "abc123-def456",
            "title": "ESP32-WROOM-32",
            "dataStr": {
                "head": {
                    "c_para": {
                        "name": "ESP32-WROOM-32-N4",
                        "package": "SMD-38",
                        "BOM_Supplier Part": "C82899",
                        "BOM_Manufacturer": "Espressif Systems",
                        "pre": "U?",
                    }
                },
                "shape": [
                    "P~show~1~100~200~150~200~1~0~gge1~0~GND~12~120~210~gge1~0~5~100~200~1~1~1~",
                    "P~show~2~100~300~150~300~2~0~gge2~0~VCC~12~120~310~gge2~0~5~100~300~1~1~2~",
                    "P~show~1~400~200~350~200~3~0~gge3~0~IO0~12~370~210~gge3~0~3~400~200~1~1~3~",
                    "R~100~100~200~400~~#000~1~0~none~gge10~",
                    "T~some text~200~300~0~#fff~",
                    "P~show~0~400~300~350~300~4~0~gge4~0~EN~12~370~310~gge4~0~1~400~300~1~1~4~",
                ],
            },
        }
    ]
}

# 真实世界的 P~ 行示例
REAL_PIN_SHAPES = [
    "P~show~0~470~290~520~290~1~180~gge7~0~GND~8~478~287~gge7~0~4~470~290~1~1~1~",
    "P~show~1~470~310~520~310~2~180~gge8~0~VOUT~8~478~307~gge8~0~2~470~310~1~1~2~",
    "P~show~4~260~290~210~290~3~0~gge9~0~VIN~8~252~287~gge9~0~5~260~290~1~1~3~",
    "P~show~3~260~310~210~310~4~0~gge10~0~SDA~8~252~307~gge10~0~3~260~310~1~1~4~",
]


# ============================================================
# parse_easyeda_pins
# ============================================================


class TestParseEasyedaPins:
    """引脚解析测试"""

    def test_basic_pin_extraction(self) -> None:
        """从 P~ 行中提取引脚编号和名称"""
        shapes = [
            "P~show~1~100~200~150~200~1~0~gge1~0~GND~12~120~210~gge1~0~5~100~200~1~1~1~",
        ]
        pins = parse_easyeda_pins(shapes)
        assert len(pins) == 1
        assert pins[0].number == "1"
        assert pins[0].name == "GND"

    def test_multiple_pins(self) -> None:
        """多个 P~ 行"""
        pins = parse_easyeda_pins(REAL_PIN_SHAPES)
        assert len(pins) == 4
        assert pins[0].number == "1"
        assert pins[0].name == "GND"
        assert pins[1].number == "2"
        assert pins[1].name == "VOUT"
        assert pins[2].number == "3"
        assert pins[2].name == "VIN"
        assert pins[3].number == "4"
        assert pins[3].name == "SDA"

    def test_non_pin_shapes_ignored(self) -> None:
        """非 P~ 行被跳过"""
        shapes = [
            "R~100~100~200~400~~#000~1~0~none~gge10~",
            "T~some text~200~300~0~#fff~",
            "P~show~0~100~200~150~200~1~0~gge1~0~VCC~12~120~210~gge1~0~0~100~200~1~1~1~",
        ]
        pins = parse_easyeda_pins(shapes)
        assert len(pins) == 1
        assert pins[0].name == "VCC"

    def test_empty_shapes(self) -> None:
        """空列表"""
        assert parse_easyeda_pins([]) == []

    def test_short_pin_line_skipped(self) -> None:
        """字段不足的 P~ 行被跳过"""
        shapes = ["P~show~1~100~200"]
        pins = parse_easyeda_pins(shapes)
        assert len(pins) == 0

    def test_non_string_items_skipped(self) -> None:
        """非字符串元素被跳过"""
        shapes = [123, None, {"a": 1}]  # type: ignore[list-item]
        pins = parse_easyeda_pins(shapes)
        assert len(pins) == 0

    def test_electric_type_mapping(self) -> None:
        """电气类型映射 (index 2)"""
        # electric=1 → input
        shapes = [
            "P~show~1~100~200~150~200~1~0~gge1~0~IN~12~120~210~gge1~0~0~100~200~1~1~1~",
        ]
        pins = parse_easyeda_pins(shapes)
        assert pins[0].electric_type == "input"

        # electric=2 → output
        shapes2 = [
            "P~show~2~100~200~150~200~1~0~gge1~0~OUT~12~120~210~gge1~0~0~100~200~1~1~1~",
        ]
        pins2 = parse_easyeda_pins(shapes2)
        assert pins2[0].electric_type == "output"

    def test_pin_type_mapping(self) -> None:
        """引脚类型映射 (index 17)"""
        # pinType=5 → power_in
        shapes = [
            "P~show~0~100~200~150~200~1~0~gge1~0~VCC~12~120~210~gge1~0~5~100~200~1~1~1~",
        ]
        pins = parse_easyeda_pins(shapes)
        assert pins[0].pin_type == "power_in"

        # pinType=1 → input
        shapes2 = [
            "P~show~0~100~200~150~200~1~0~gge1~0~IN~12~120~210~gge1~0~1~100~200~1~1~1~",
        ]
        pins2 = parse_easyeda_pins(shapes2)
        assert pins2[0].pin_type == "input"

        # pinType=8 → nc
        shapes3 = [
            "P~show~0~100~200~150~200~1~0~gge1~0~NC~12~120~210~gge1~0~8~100~200~1~1~1~",
        ]
        pins3 = parse_easyeda_pins(shapes3)
        assert pins3[0].pin_type == "nc"

    def test_sample_easyeda_shapes(self) -> None:
        """使用 SAMPLE_EASYEDA_RESPONSE 中的 shape"""
        shapes = SAMPLE_EASYEDA_RESPONSE["result"][0]["dataStr"]["shape"]
        pins = parse_easyeda_pins(shapes)
        # 4 P~ lines in the sample
        assert len(pins) == 4
        names = {p.name for p in pins}
        assert "GND" in names
        assert "VCC" in names
        assert "IO0" in names
        assert "EN" in names


# ============================================================
# 电气/引脚类型映射表
# ============================================================


class TestTypeMaps:
    """类型映射表完整性"""

    def test_electric_type_map_keys(self) -> None:
        """ELECTRIC_TYPE_MAP 包含 0~5"""
        for k in range(6):
            assert k in ELECTRIC_TYPE_MAP

    def test_pin_type_map_keys(self) -> None:
        """PIN_TYPE_MAP 包含 0~8"""
        for k in range(9):
            assert k in PIN_TYPE_MAP

    def test_electric_type_values(self) -> None:
        """电气类型值合法"""
        valid = {"passive", "input", "output", "bidirectional", "power_in"}
        for v in ELECTRIC_TYPE_MAP.values():
            assert v in valid

    def test_pin_type_values(self) -> None:
        """引脚类型值合法"""
        valid = {
            "passive", "input", "output", "bidirectional",
            "power_in", "open_collector", "open_emitter", "nc",
        }
        for v in PIN_TYPE_MAP.values():
            assert v in valid


# ============================================================
# _safe_int
# ============================================================


class TestSafeInt:
    """安全整数解析"""

    def test_valid_int(self) -> None:
        assert _safe_int("42") == 42

    def test_zero(self) -> None:
        assert _safe_int("0") == 0

    def test_invalid_string(self) -> None:
        assert _safe_int("abc") == 0

    def test_empty_string(self) -> None:
        assert _safe_int("") == 0

    def test_float_string(self) -> None:
        assert _safe_int("3.14") == 0


# ============================================================
# _format_price_range
# ============================================================


class TestFormatPriceRange:
    """价格范围格式化"""

    def test_multiple_prices(self) -> None:
        prices = [
            {"productPrice": 3.50},
            {"productPrice": 2.80},
            {"productPrice": 2.10},
        ]
        result = _format_price_range(prices)
        assert result == "¥2.10~¥3.50"

    def test_single_price(self) -> None:
        prices = [{"productPrice": 1.50}]
        result = _format_price_range(prices)
        assert result == "¥1.50"

    def test_empty_prices(self) -> None:
        assert _format_price_range([]) == ""

    def test_invalid_prices(self) -> None:
        prices = [{"productPrice": "invalid"}]
        assert _format_price_range(prices) == ""

    def test_same_price(self) -> None:
        prices = [
            {"productPrice": 2.00},
            {"productPrice": 2.00},
        ]
        assert _format_price_range(prices) == "¥2.00"


# ============================================================
# _parse_jlcpcb_response
# ============================================================


class TestParseJlcpcbResponse:
    """JLCPCB 搜索响应解析"""

    def test_valid_response(self) -> None:
        """正常 JLCPCB 响应"""
        hits = _parse_jlcpcb_response(SAMPLE_JLCPCB_RESPONSE)
        assert len(hits) == 2

        h0 = hits[0]
        assert h0.lcsc_part == "C82899"
        assert h0.title == "ESP32-WROOM-32-N4"
        assert h0.manufacturer == "Espressif"
        assert h0.package == "SMD-38"
        assert h0.stock == 12500
        assert h0.price_range == "¥2.10~¥3.50"
        assert h0.category_name == "WiFi Modules"
        assert h0.library_type == "expand"
        assert h0.datasheet_url == "https://example.com/esp32.pdf"

    def test_zero_stock(self) -> None:
        """库存为 0"""
        hits = _parse_jlcpcb_response(SAMPLE_JLCPCB_RESPONSE)
        assert hits[1].stock == 0
        assert hits[1].price_range == ""

    def test_empty_response(self) -> None:
        """空响应"""
        assert _parse_jlcpcb_response({}) == []
        assert _parse_jlcpcb_response({"data": {}}) == []
        assert _parse_jlcpcb_response({"data": {"componentPageInfo": {}}}) == []

    def test_limit(self) -> None:
        """限制返回数"""
        hits = _parse_jlcpcb_response(SAMPLE_JLCPCB_RESPONSE, limit=1)
        assert len(hits) == 1

    def test_non_dict_data(self) -> None:
        """data 不是 dict"""
        assert _parse_jlcpcb_response({"data": "invalid"}) == []

    def test_non_list_items(self) -> None:
        """list 中包含非 dict 项"""
        data = {
            "data": {
                "componentPageInfo": {
                    "list": ["not_a_dict", 42, None]
                }
            }
        }
        hits = _parse_jlcpcb_response(data)
        assert len(hits) == 0


# ============================================================
# _parse_easyeda_product
# ============================================================


class TestParseEasyedaProduct:
    """EasyEDA 产品响应解析"""

    def test_valid_response(self) -> None:
        """正常 EasyEDA 产品响应"""
        result = _parse_easyeda_product(SAMPLE_EASYEDA_RESPONSE, "C82899")
        assert result is not None
        assert result.uuid == "abc123-def456"
        assert result.title == "ESP32-WROOM-32-N4"  # 从 c_para.name
        assert result.package == "SMD-38"
        assert len(result.pins) == 4
        assert result.attributes["lcsc_part"] == "C82899"
        assert result.attributes["BOM_Manufacturer"] == "Espressif Systems"

    def test_empty_result(self) -> None:
        """空 result"""
        assert _parse_easyeda_product({"result": []}, "C1") is None
        assert _parse_easyeda_product({"result": None}, "C1") is None
        assert _parse_easyeda_product({}, "C1") is None

    def test_result_as_dict(self) -> None:
        """result 为单个 dict (非列表)"""
        data = {
            "result": {
                "uuid": "single-uuid",
                "title": "SingleComp",
                "dataStr": {
                    "head": {"c_para": {"name": "SC1", "package": "DIP-8"}},
                    "shape": [],
                },
            }
        }
        result = _parse_easyeda_product(data, "C999")
        assert result is not None
        assert result.uuid == "single-uuid"
        assert result.title == "SC1"
        assert result.package == "DIP-8"

    def test_data_str_as_json_string(self) -> None:
        """dataStr 为 JSON 字符串"""
        inner = {
            "head": {"c_para": {"name": "Chip1", "package": "QFN-16"}},
            "shape": [
                "P~show~0~100~200~150~200~1~0~g~0~A1~12~120~210~g~0~0~100~200~1~1~1~",
            ],
        }
        data = {
            "result": [{
                "uuid": "str-ds",
                "title": "Chip1",
                "dataStr": json.dumps(inner),
            }]
        }
        result = _parse_easyeda_product(data, "C55")
        assert result is not None
        assert len(result.pins) == 1
        assert result.pins[0].name == "A1"


# ============================================================
# search_jlcpcb (mocked HTTP)
# ============================================================


class TestSearchJlcpcb:
    """JLCPCB 搜索 (mock HTTP)"""

    def test_empty_keyword(self) -> None:
        """空关键词返回 INVALID_FORMAT"""
        result = search_jlcpcb("")
        assert not result.success
        assert result.error is not None
        assert result.error.code.value == "invalid_format"

    def test_whitespace_keyword(self) -> None:
        """纯空格关键词"""
        result = search_jlcpcb("   ")
        assert not result.success

    @patch("urllib.request.urlopen")
    def test_successful_search(self, mock_urlopen: MagicMock) -> None:
        """成功搜索"""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(SAMPLE_JLCPCB_RESPONSE).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = search_jlcpcb("ESP32")
        assert result.success
        assert len(result.data) == 2
        assert result.data[0].lcsc_part == "C82899"

    @patch("urllib.request.urlopen")
    def test_no_results(self, mock_urlopen: MagicMock) -> None:
        """无结果"""
        empty_resp = {"data": {"componentPageInfo": {"list": []}}}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(empty_resp).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = search_jlcpcb("NONEXISTENT_PART_XYZ")
        assert not result.success
        assert result.error is not None
        assert result.error.code.value == "easyeda_not_found"

    @patch("urllib.request.urlopen")
    def test_network_error(self, mock_urlopen: MagicMock) -> None:
        """网络异常"""
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")

        result = search_jlcpcb("ESP32")
        assert not result.success
        assert result.error is not None
        assert result.error.code.value == "easyeda_timeout"
        assert result.error.retriable is True

    @patch("urllib.request.urlopen")
    def test_json_parse_error(self, mock_urlopen: MagicMock) -> None:
        """非 JSON 响应"""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<html>error</html>"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = search_jlcpcb("ESP32")
        assert not result.success


# ============================================================
# search_easyeda (backward compat wrapper)
# ============================================================


class TestSearchEasyeda:
    """search_easyeda 向后兼容测试"""

    def test_delegates_to_jlcpcb(self) -> None:
        """search_easyeda 委托给 search_jlcpcb"""
        with patch(
            "schemaforge.ingest.easyeda_provider.search_jlcpcb"
        ) as mock_jlcpcb:
            mock_jlcpcb.return_value = MagicMock(success=True)
            result = search_easyeda("test", limit=5)
            mock_jlcpcb.assert_called_once_with("test", limit=5)
            assert result.success

    def test_empty_keyword(self) -> None:
        """空关键词"""
        result = search_easyeda("")
        assert not result.success


# ============================================================
# fetch_easyeda_symbol (mocked HTTP)
# ============================================================


class TestFetchEasyedaSymbol:
    """EasyEDA 符号获取 (mock HTTP)"""

    def test_empty_lcsc(self) -> None:
        """空 LCSC 编号"""
        result = fetch_easyeda_symbol("")
        assert not result.success
        assert result.error is not None
        assert result.error.code.value == "invalid_format"

    @patch("urllib.request.urlopen")
    def test_successful_fetch(self, mock_urlopen: MagicMock) -> None:
        """成功获取符号"""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(SAMPLE_EASYEDA_RESPONSE).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = fetch_easyeda_symbol("C82899")
        assert result.success
        sym: EasyEDASymbolResult = result.data
        assert sym.uuid == "abc123-def456"
        assert len(sym.pins) == 4
        pin_names = {p.name for p in sym.pins}
        assert "GND" in pin_names
        assert "VCC" in pin_names

    @patch("urllib.request.urlopen")
    def test_not_found(self, mock_urlopen: MagicMock) -> None:
        """器件未找到"""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"result": []}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = fetch_easyeda_symbol("C999999")
        assert not result.success
        assert result.error is not None
        assert result.error.code.value == "easyeda_not_found"

    @patch("urllib.request.urlopen")
    def test_network_error(self, mock_urlopen: MagicMock) -> None:
        """网络异常"""
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("timeout")

        result = fetch_easyeda_symbol("C82899")
        assert not result.success
        assert result.error is not None
        assert result.error.retriable is True


# ============================================================
# 数据模型
# ============================================================


class TestDataModels:
    """数据模型基础测试"""

    def test_easyeda_pin_info_defaults(self) -> None:
        """EasyEDAPinInfo 默认值"""
        pin = EasyEDAPinInfo()
        assert pin.number == ""
        assert pin.name == ""
        assert pin.pin_type == ""
        assert pin.electric_type == ""
        assert pin.description == ""

    def test_easyeda_hit_defaults(self) -> None:
        """EasyEDAHit 默认值"""
        hit = EasyEDAHit()
        assert hit.title == ""
        assert hit.stock == 0
        assert hit.price_range == ""
        assert hit.category_name == ""
        assert hit.library_type == ""

    def test_easyeda_hit_new_fields(self) -> None:
        """EasyEDAHit 新增字段"""
        hit = EasyEDAHit(
            title="Chip1",
            lcsc_part="C123",
            stock=5000,
            price_range="¥1.00~¥2.00",
            category_name="MCU",
            library_type="base",
        )
        assert hit.stock == 5000
        assert hit.price_range == "¥1.00~¥2.00"
        assert hit.category_name == "MCU"
        assert hit.library_type == "base"

    def test_easyeda_symbol_result_defaults(self) -> None:
        """EasyEDASymbolResult 默认值"""
        sym = EasyEDASymbolResult()
        assert sym.uuid == ""
        assert sym.pins == []
        assert sym.symbol_data == {}
        assert sym.attributes == {}


# ============================================================
# 集成级: 解析 → 完整数据流
# ============================================================


class TestIntegration:
    """端到端数据流 (纯解析，无 HTTP)"""

    def test_jlcpcb_to_hits(self) -> None:
        """JLCPCB 响应 → EasyEDAHit 列表"""
        hits = _parse_jlcpcb_response(SAMPLE_JLCPCB_RESPONSE)
        assert all(isinstance(h, EasyEDAHit) for h in hits)
        assert hits[0].lcsc_part == "C82899"

    def test_easyeda_to_symbol(self) -> None:
        """EasyEDA 响应 → EasyEDASymbolResult"""
        sym = _parse_easyeda_product(SAMPLE_EASYEDA_RESPONSE, "C82899")
        assert sym is not None
        assert isinstance(sym, EasyEDASymbolResult)
        assert len(sym.pins) == 4
        # 验证引脚类型
        pin_by_name = {p.name: p for p in sym.pins}
        assert pin_by_name["GND"].pin_type == "power_in"
        assert pin_by_name["VCC"].pin_type == "power_in"
        assert pin_by_name["IO0"].pin_type == "bidirectional"
        assert pin_by_name["EN"].pin_type == "input"

    def test_full_round_trip(self) -> None:
        """完整数据流: 搜索结果 → 详情获取 → 引脚解析"""
        # 模拟搜索
        hits = _parse_jlcpcb_response(SAMPLE_JLCPCB_RESPONSE)
        first_hit = hits[0]
        assert first_hit.lcsc_part == "C82899"

        # 模拟详情
        sym = _parse_easyeda_product(SAMPLE_EASYEDA_RESPONSE, first_hit.lcsc_part)
        assert sym is not None
        assert len(sym.pins) > 0
        assert sym.attributes["lcsc_part"] == "C82899"
