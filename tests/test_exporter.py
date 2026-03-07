"""测试BOM和SPICE导出"""

import pytest
from schemaforge.core.engine import SchemaForgeEngine
from schemaforge.core.exporter import _spice_value


@pytest.fixture
def engine():
    return SchemaForgeEngine()


class TestBOM:
    def test_bom_contains_all_components(self, engine):
        result = engine.process("5V转3.3V稳压电路，带LED")
        assert result.success
        bom = result.bom_text
        assert "U1" in bom
        assert "C1" in bom
        assert "R1" in bom
        assert "D1" in bom

    def test_bom_has_table_format(self, engine):
        result = engine.process("分压采样")
        assert result.success
        bom = result.bom_text
        assert "参考标号" in bom
        assert "器件名称" in bom
        assert "LCSC" in bom

    def test_bom_divider(self, engine):
        result = engine.process("12V分压到3.3V")
        assert result.success
        bom = result.bom_text
        assert "R1" in bom
        assert "R2" in bom


class TestSPICE:
    def test_spice_has_header(self, engine):
        result = engine.process("分压采样")
        assert result.success
        spice = result.spice_text
        assert "SchemaForge" in spice
        assert ".end" in spice

    def test_spice_divider(self, engine):
        result = engine.process("12V分压到3.3V")
        assert result.success
        spice = result.spice_text
        assert "R1" in spice
        assert "R2" in spice
        assert "V1" in spice

    def test_spice_ldo(self, engine):
        result = engine.process("5V转3.3V稳压")
        assert result.success
        spice = result.spice_text
        assert "XU1" in spice
        assert "AMS1117" in spice

    def test_spice_led(self, engine):
        result = engine.process("LED指示灯")
        assert result.success
        spice = result.spice_text
        assert "D1" in spice
        assert "LED_GREEN" in spice

    def test_spice_rc(self, engine):
        result = engine.process("1kHz滤波器")
        assert result.success
        spice = result.spice_text
        assert "R1" in spice
        assert "C1" in spice
        assert ".ac" in spice


class TestSpiceValue:
    """测试SPICE值格式化函数"""

    def test_ohm_stripped(self):
        assert _spice_value("110Ω") == "110"

    def test_kohm_to_k(self):
        assert _spice_value("10kΩ") == "10k"

    def test_mohm_to_meg(self):
        assert _spice_value("4.7MΩ") == "4.7Meg"

    def test_uf_to_u(self):
        assert _spice_value("10μF") == "10u"

    def test_nf_to_n(self):
        assert _spice_value("100nF") == "100n"

    def test_pf_to_p(self):
        assert _spice_value("22pF") == "22p"

    def test_plain_number_unchanged(self):
        assert _spice_value("120") == "120"

    def test_spice_no_omega_in_led_netlist(self, engine):
        """验证LED SPICE网表中电阻值不含Ω符号"""
        result = engine.process("LED指示灯")
        assert result.success
        spice = result.spice_text
        assert "Ω" not in spice, f"SPICE网表中不应出现Ω符号: {spice}"

    def test_spice_no_omega_in_divider_netlist(self, engine):
        """验证分压器SPICE网表中电阻值不含Ω符号"""
        result = engine.process("12V分压到3.3V")
        assert result.success
        spice = result.spice_text
        assert "Ω" not in spice, f"SPICE网表中不应出现Ω符号: {spice}"
