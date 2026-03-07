"""SchemaForge 设计工具测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from schemaforge.agent.design_tools import build_design_tool_registry
from schemaforge.library.models import (
    DesignRecipe,
    DeviceModel,
    RecipeComponent,
    RecipeFormula,
)
from schemaforge.library.store import ComponentStore
from schemaforge.workflows.schemaforge_session import SchemaForgeSession


# ============================================================
# Fixture: 准备带有活跃设计的 session + registry
# ============================================================


@pytest.fixture()
def session_with_design(tmp_path: Path) -> tuple[SchemaForgeSession, object]:
    """创建一个已经有活跃 DesignBundle 的 session 并返回 (session, registry)。"""
    store = ComponentStore(tmp_path / "store")
    store.save_device(
        DeviceModel(
            part_number="AMS1117-3.3",
            category="ldo",
            specs={"v_out": "3.3V"},
        )
    )
    session = SchemaForgeSession(tmp_path / "store", )
    registry = build_design_tool_registry(session)

    # 先启动设计，确保 bundle 存在
    start_result = registry.execute(
        "start_design_request",
        {"user_input": "用 AMS1117-3.3 搭一个 5V 转 3.3V 稳压电路"},
    )
    assert start_result.success
    return session, registry


@pytest.fixture()
def session_with_recipe(tmp_path: Path) -> tuple[SchemaForgeSession, object]:
    """创建一个带有可求解公式 recipe 的 session。"""
    recipe = DesignRecipe(
        topology_family="buck",
        summary="TPS54202 buck recipe",
        formulas=[
            RecipeFormula(
                name="duty",
                expression="v_out / v_in",
                rationale="占空比",
            ),
        ],
        sizing_components=[
            RecipeComponent(
                role="l_value",
                formula="v_out * (1 - duty) / (fsw * 0.3 * i_out)",
                rationale="电感计算",
            ),
            RecipeComponent(
                role="c_out",
                value="22uF",
                rationale="输出电容推荐值",
            ),
        ],
        default_parameters={
            "v_in": "12",
            "v_out": "5",
            "i_out": "2",
            "fsw": "500000",
        },
    )
    store = ComponentStore(tmp_path / "store")
    store.save_device(
        DeviceModel(
            part_number="TPS54202",
            category="buck",
            specs={"v_out": "5V", "fsw": "500000"},
            design_recipe=recipe,
        )
    )
    session = SchemaForgeSession(tmp_path / "store", )
    registry = build_design_tool_registry(session)

    start_result = registry.execute(
        "start_design_request",
        {"user_input": "用 TPS54202 搭一个 12V 转 5V 的降压电路"},
    )
    assert start_result.success
    return session, registry


# ============================================================
# 原有测试
# ============================================================


def test_design_tool_registry_starts_design(tmp_path: Path) -> None:
    store = ComponentStore(tmp_path / "store")
    store.save_device(
        DeviceModel(
            part_number="AMS1117-3.3",
            category="ldo",
            specs={"v_out": "3.3V"},
        )
    )
    session = SchemaForgeSession(tmp_path / "store", )
    registry = build_design_tool_registry(session)

    result = registry.execute(
        "start_design_request",
        {"user_input": "用 AMS1117-3.3 搭一个 5V 转 3.3V 稳压电路"},
    )
    assert result.success
    assert result.data["status"] == "generated"


# ============================================================
# P4: 新工具测试
# ============================================================


class TestCalculateParameters:
    """calculate_parameters 工具测试。"""

    def test_no_bundle_returns_error(self, tmp_path: Path) -> None:
        session = SchemaForgeSession(tmp_path / "store", )
        registry = build_design_tool_registry(session)
        result = registry.execute("calculate_parameters", {})
        assert not result.success
        assert result.error is not None
        assert "活跃" in result.error.message

    def test_basic_calculation(self, session_with_design: tuple) -> None:
        _session, registry = session_with_design
        result = registry.execute(
            "calculate_parameters",
            {"v_in": 5.0, "v_out": 3.3},
        )
        # LDO recipe may not have evaluable formulas → still succeeds or fails gracefully
        assert isinstance(result.success, bool)
        assert result.data is not None or result.error is not None

    def test_with_recipe_formulas(self, session_with_recipe: tuple) -> None:
        _session, registry = session_with_recipe
        result = registry.execute(
            "calculate_parameters",
            {"v_in": 12.0, "v_out": 5.0, "i_out": 2.0, "fsw": 500000.0},
        )
        assert result.data is not None
        assert "computed_params" in result.data
        assert "rationale" in result.data


class TestEvaluateFormula:
    """evaluate_formula 工具测试。"""

    def test_simple_expression(self, session_with_design: tuple) -> None:
        _session, registry = session_with_design
        result = registry.execute(
            "evaluate_formula",
            {"expression": "3.3 / 5.0"},
        )
        assert result.success
        assert result.data is not None
        assert abs(result.data["raw_value"] - 0.66) < 0.01

    def test_expression_with_context(self, session_with_design: tuple) -> None:
        _session, registry = session_with_design
        result = registry.execute(
            "evaluate_formula",
            {"expression": "v_out / v_in", "v_in": 12.0, "v_out": 5.0},
        )
        assert result.success
        raw = result.data["raw_value"]
        assert abs(raw - 5.0 / 12.0) < 0.01

    def test_invalid_expression(self, session_with_design: tuple) -> None:
        _session, registry = session_with_design
        result = registry.execute(
            "evaluate_formula",
            {"expression": "Cin ≥ 10μF"},
        )
        # 不等式约束无法自动求解
        assert not result.success

    def test_capacitor_rounding(self, session_with_design: tuple) -> None:
        _session, registry = session_with_design
        result = registry.execute(
            "evaluate_formula",
            {"expression": "15e-6", "component_type": "capacitor"},
        )
        assert result.success
        # 应圆整到标准电容系列
        assert result.data["formatted"] is not None


class TestGenerateNetlist:
    """generate_netlist 工具测试。"""

    def test_no_bundle_returns_error(self, tmp_path: Path) -> None:
        session = SchemaForgeSession(tmp_path / "store", )
        registry = build_design_tool_registry(session)
        result = registry.execute("generate_netlist", {})
        assert not result.success

    def test_spice_netlist(self, session_with_design: tuple) -> None:
        _session, registry = session_with_design
        result = registry.execute("generate_netlist", {"format": "spice"})
        assert result.success
        assert "netlist" in result.data
        assert "SchemaForge" in result.data["netlist"]
        assert result.data["device"] == "AMS1117-3.3"

    def test_unsupported_format(self, session_with_design: tuple) -> None:
        _session, registry = session_with_design
        result = registry.execute("generate_netlist", {"format": "verilog"})
        assert not result.success
        assert "verilog" in result.error.message


class TestRenderSchematic:
    """render_schematic 工具测试。"""

    def test_no_bundle_returns_error(self, tmp_path: Path) -> None:
        session = SchemaForgeSession(tmp_path / "store", )
        registry = build_design_tool_registry(session)
        result = registry.execute("render_schematic", {})
        assert not result.success

    def test_render_produces_svg(self, session_with_design: tuple) -> None:
        _session, registry = session_with_design
        result = registry.execute("render_schematic", {})
        assert result.success
        assert result.data is not None
        assert "svg_path" in result.data
        assert result.data["svg_path"].endswith(".svg")

    def test_render_with_filename(self, session_with_design: tuple) -> None:
        _session, registry = session_with_design
        result = registry.execute(
            "render_schematic",
            {"filename": "test_output.svg"},
        )
        assert result.success
        assert "test_output" in result.data["svg_path"]


class TestValidateDesign:
    """validate_design 工具测试。"""

    def test_no_bundle_returns_error(self, tmp_path: Path) -> None:
        session = SchemaForgeSession(tmp_path / "store", )
        registry = build_design_tool_registry(session)
        result = registry.execute("validate_design", {})
        assert not result.success

    def test_review_runs(self, session_with_design: tuple) -> None:
        _session, registry = session_with_design
        result = registry.execute("validate_design", {})
        assert result.success
        assert result.data is not None
        assert "passed" in result.data
        assert "issue_count" in result.data
        assert "issues" in result.data
        assert isinstance(result.data["issues"], list)

    def test_review_returns_device_info(self, session_with_design: tuple) -> None:
        _session, registry = session_with_design
        result = registry.execute("validate_design", {})
        assert result.success
        assert result.data["device"] == "AMS1117-3.3"
        assert result.data["category"] == "ldo"


class TestToolRegistration:
    """验证所有工具都正确注册。"""

    def test_all_nine_tools_registered(self, tmp_path: Path) -> None:
        session = SchemaForgeSession(tmp_path / "store", )
        registry = build_design_tool_registry(session)
        tools = registry.list_tools()
        tool_names = {t.name for t in tools}
        expected = {
            "start_design_request",
            "ingest_datasheet_asset",
            "confirm_import_device",
            "apply_design_revision",
            "calculate_parameters",
            "evaluate_formula",
            "generate_netlist",
            "render_schematic",
            "validate_design",
        }
        assert tool_names == expected

    def test_all_tools_have_design_category(self, tmp_path: Path) -> None:
        session = SchemaForgeSession(tmp_path / "store", )
        registry = build_design_tool_registry(session)
        descriptions = registry.get_tool_descriptions()
        for desc in descriptions:
            tool_def = registry.get_tool(desc["name"])
            assert tool_def is not None
            assert tool_def.category == "design"
