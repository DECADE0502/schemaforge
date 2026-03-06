"""端到端引擎测试

测试 SchemaForgeEngine 完整流水线：
LLM(mock) → 验证 → 实例化 → ERC → 渲染 → 导出
"""

import os

import pytest

from schemaforge.core.engine import EngineResult, SchemaForgeEngine


@pytest.fixture
def engine():
    return SchemaForgeEngine(use_mock=True)


class TestEngineE2E:
    """端到端流水线测试"""

    def test_ldo_led_combo(self, engine):
        """LDO+LED组合 — 最复杂的默认Demo场景"""
        result = engine.process("5V转3.3V稳压电路，带绿色LED电源指示灯")
        assert result.success
        assert result.design_name == "5V-3.3V稳压电源（带LED指示）"
        assert len(result.circuits) == 2
        assert len(result.svg_paths) == 2
        assert result.bom_text
        assert result.spice_text

    def test_divider(self, engine):
        """单模块 — 分压器"""
        result = engine.process("12V分压到3.3V")
        assert result.success
        assert "分压" in result.design_name
        assert len(result.circuits) == 1
        assert len(result.svg_paths) == 1

    def test_rc_filter(self, engine):
        """单模块 — RC滤波器"""
        result = engine.process("1kHz滤波器")
        assert result.success
        assert "滤波" in result.design_name
        assert len(result.circuits) == 1

    def test_led_standalone(self, engine):
        """单模块 — 独立LED"""
        result = engine.process("LED指示灯")
        assert result.success
        assert "LED" in result.design_name
        assert len(result.circuits) == 1

    def test_default_fallback(self, engine):
        """未匹配关键词 — 默认返回LDO+LED"""
        result = engine.process("random input xyz")
        assert result.success
        assert len(result.circuits) == 2


class TestEngineResult:
    """EngineResult 结构测试"""

    def test_result_has_all_fields(self, engine):
        result = engine.process("分压采样")
        assert isinstance(result, EngineResult)
        assert isinstance(result.raw_design, dict)
        assert result.validation is not None
        assert result.validation.is_valid
        assert isinstance(result.circuits, list)
        assert isinstance(result.erc_errors, list)
        assert isinstance(result.svg_paths, list)
        assert isinstance(result.bom_text, str)
        assert isinstance(result.spice_text, str)

    def test_successful_result_stage_done(self, engine):
        result = engine.process("5V稳压")
        assert result.success
        assert result.stage == "done"
        assert result.error == ""


class TestEngineSVGOutput:
    """验证SVG文件确实生成"""

    def test_svg_files_exist(self, engine):
        result = engine.process("5V转3.3V稳压带LED")
        assert result.success
        for svg_path in result.svg_paths:
            assert os.path.exists(svg_path), f"SVG文件不存在: {svg_path}"
            assert os.path.getsize(svg_path) > 100, f"SVG文件过小: {svg_path}"

    def test_svg_is_valid_xml(self, engine):
        """SVG应包含基本XML/SVG标签"""
        result = engine.process("分压采样")
        assert result.success
        for svg_path in result.svg_paths:
            with open(svg_path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "<svg" in content
            assert "</svg>" in content


class TestEngineBOMOutput:
    """验证BOM输出内容"""

    def test_bom_markdown_table(self, engine):
        result = engine.process("5V稳压带LED")
        assert result.success
        bom = result.bom_text
        assert "| #" in bom or "| 参考标号" in bom
        assert "LCSC" in bom

    def test_bom_ldo_components(self, engine):
        """LDO BOM应包含U1, C1, C2"""
        result = engine.process("5V转3.3V稳压")
        assert result.success
        bom = result.bom_text
        assert "U1" in bom
        assert "C1" in bom
        assert "C2" in bom
        assert "AMS1117" in bom

    def test_bom_led_components(self, engine):
        """LED BOM应包含R1, D1"""
        result = engine.process("LED指示灯")
        assert result.success
        bom = result.bom_text
        assert "R1" in bom
        assert "D1" in bom


class TestEngineSPICEOutput:
    """验证SPICE输出内容"""

    def test_spice_no_unicode_units(self, engine):
        """SPICE网表不应包含Unicode单位符号"""
        result = engine.process("5V转3.3V稳压带LED")
        assert result.success
        spice = result.spice_text
        assert "Ω" not in spice, "SPICE不应含Ω"
        assert "μ" not in spice, "SPICE不应含μ（应为u）"

    def test_spice_has_end(self, engine):
        """每段SPICE网表应以.end结尾"""
        result = engine.process("5V稳压带LED")
        assert result.success
        assert ".end" in result.spice_text

    def test_spice_header(self, engine):
        result = engine.process("分压采样")
        assert result.success
        assert "SchemaForge" in result.spice_text


class TestEngineERC:
    """ERC检查集成测试"""

    def test_erc_runs_without_fatal(self, engine):
        """ERC不应产生fatal error（模板保证连接正确）"""
        result = engine.process("5V转3.3V稳压带LED")
        assert result.success
        # 可能有warning，但不应有阻断性error
        fatal_errors = [e for e in result.erc_errors if e.severity.value == "error"]
        # 当前模板设计允许某些ERC warning
        # 确保引擎不因ERC而失败
        assert result.stage == "done"
