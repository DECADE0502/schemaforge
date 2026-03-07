"""SchemaForge 新工作台会话测试。"""

from __future__ import annotations

from pathlib import Path

from schemaforge.design.synthesis import (
    DesignRecipeSynthesizer,
    ExactPartResolver,
    UserDesignRequest,
    parse_design_request,
)
from schemaforge.library.models import (
    DesignRecipe,
    DeviceModel,
    ExternalComponent,
    RecipeComponent,
    RecipeEvidence,
    RecipeFormula,
    TopologyConnection,
    TopologyDef,
)
from schemaforge.library.store import ComponentStore
from schemaforge.workflows.schemaforge_session import SchemaForgeSession


def test_parse_design_request_extracts_exact_part_and_voltages() -> None:
    req = parse_design_request("帮我用 TPS54202 搭一个 20V 转 5V 的 DCDC 电路")
    assert req.part_number == "TPS54202"
    assert req.category == "buck"
    assert req.v_in == "20"
    assert req.v_out == "5"


def test_exact_part_resolver_supports_alias(tmp_path: Path) -> None:
    store = ComponentStore(tmp_path / "store")
    store.save_device(
        DeviceModel(
            part_number="TPS54202RGTR",
            aliases=["TPS54202"],
            category="buck",
        )
    )
    resolver = ExactPartResolver(store)
    hit = resolver.resolve("TPS54202")
    assert hit is not None
    assert hit.part_number == "TPS54202RGTR"


def test_session_returns_needs_asset_for_missing_exact_part(tmp_path: Path) -> None:
    session = SchemaForgeSession(tmp_path / "store", use_mock=True)
    result = session.start("帮我用 TPS54202 搭一个 20V 转 5V 的 DCDC 电路")
    assert result.status == "needs_asset"
    assert result.missing_part_number == "TPS54202"


def test_session_import_and_generate_buck_design(tmp_path: Path) -> None:
    session = SchemaForgeSession(tmp_path / "store", use_mock=True)
    start = session.start("帮我用 TPS54202 搭一个 20V 转 5V 的 DCDC 电路")
    assert start.status == "needs_asset"

    image = tmp_path / "tps54202.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 128)
    preview = session.ingest_asset(str(image))
    assert preview.status == "needs_confirmation"

    generated = session.confirm_import(
        {
            "part_number": "TPS54202",
            "manufacturer": "TI",
            "description": "2A Buck Converter",
            "category": "buck",
            "package": "SOT-23-6",
            "datasheet_url": "https://example.com/tps54202.pdf",
            "pins": [
                {"name": "BOOT", "number": "1", "type": "passive"},
                {"name": "VIN", "number": "2", "type": "power"},
                {"name": "EN", "number": "3", "type": "input"},
                {"name": "GND", "number": "4", "type": "power"},
                {"name": "FB", "number": "5", "type": "input"},
                {"name": "SW", "number": "6", "type": "output"},
            ],
            "specs": {
                "v_in_max": "28V",
                "i_out_max": "2A",
                "fsw": "500kHz",
                "v_ref": "0.8V",
            },
        }
    )
    assert generated.status == "generated"
    assert generated.bundle is not None
    assert generated.bundle.device.part_number == "TPS54202"
    assert generated.bundle.parameters["v_in"] == "20"
    assert generated.bundle.parameters["v_out"] == "5"
    assert generated.bundle.parameters["c_out"] == "22uF"
    assert generated.bundle.parameters["l_value"].endswith("uH")
    assert Path(generated.bundle.svg_path).exists()
    assert "TPS54202" in generated.bundle.bom_text
    # SPICE 网表现在从拓扑自动生成，反馈电阻为 R1/R2 而非硬编码 RFB1
    assert "R1 VOUT FB" in generated.bundle.spice_text
    assert "R2 FB 0" in generated.bundle.spice_text


def test_session_revision_updates_output_cap_and_led(tmp_path: Path) -> None:
    session = SchemaForgeSession(tmp_path / "store", use_mock=True)
    store = ComponentStore(tmp_path / "store")
    store.save_device(
        DeviceModel(
            part_number="TPS5430",
            category="buck",
            specs={"v_in_max": "36V", "i_out_max": "3A", "fsw": "500kHz"},
        )
    )

    created = session.start("用 TPS5430 搭一个 12V 转 5V 的降压电路")
    assert created.status == "generated"

    revised = session.revise("换个 22μF 的输出电容，并加个绿色 LED")
    assert revised.status == "generated"
    assert revised.bundle is not None
    assert revised.bundle.parameters["c_out"] == "22uF"
    assert revised.bundle.parameters["power_led"] == "true"
    assert "DLED1" in revised.bundle.bom_text


# ============================================================
# 回归测试: confirm_import 安全落库
# ============================================================


def test_confirm_import_rejects_incomplete_draft_and_store_stays_empty(
    tmp_path: Path,
) -> None:
    """confirm_import() 对不完整草稿（空 part_number）应返回 needs_confirmation，
    且 ComponentStore 中不应有脏数据残留。"""
    from schemaforge.library.validator import DeviceDraft

    session = SchemaForgeSession(tmp_path / "store", use_mock=True)
    session.start("帮我用 TPS54202 搭一个 20V 转 5V 的 DCDC 电路")

    # 直接注入一个空草稿，模拟 ingest_asset 解析出的不完整结果
    session._pending_draft = DeviceDraft()  # part_number=""

    # 不提供任何补充信息 → 校验应拦截（part_number 为空是 ERROR 级）
    result = session.confirm_import({})
    assert result.status in ("needs_confirmation", "error")

    # 即使失败，store 里也不应该有任何器件
    store = ComponentStore(tmp_path / "store")
    assert store.list_devices() == []


# ============================================================
# 回归测试: 同一器件不同工况必须重新计算
# ============================================================


def test_different_requests_produce_different_parameters() -> None:
    """同一 TPS5430，两次不同 Vin/Vout 请求应该产生不同的 r_fb_upper 和 l_value。"""
    device = DeviceModel(
        part_number="TPS5430",
        category="buck",
        specs={"v_in_max": "36V", "i_out_max": "3A", "fsw": "500kHz", "v_ref": "1.221V"},
    )
    synthesizer = DesignRecipeSynthesizer()

    req_12_to_5 = UserDesignRequest(raw_text="12V转5V", v_in="12", v_out="5", category="buck")
    _, recipe_a = synthesizer.prepare_device(device, req_12_to_5)

    req_24_to_3 = UserDesignRequest(raw_text="24V转3.3V", v_in="24", v_out="3.3", category="buck")
    _, recipe_b = synthesizer.prepare_device(device, req_24_to_3)

    # 输出电压不同 → 反馈电阻上拉值必须不同
    assert recipe_a.default_parameters["r_fb_upper"] != recipe_b.default_parameters["r_fb_upper"]
    # 两次设计来自同一个 prepare_device，不是复用缓存 → v_in 不同
    assert recipe_a.default_parameters["v_in"] != recipe_b.default_parameters["v_in"]
    assert recipe_a.default_parameters["v_out"] != recipe_b.default_parameters["v_out"]


# ============================================================
# 回归测试: 默认工况不能用绝对最大值
# ============================================================


def test_buck_does_not_use_absolute_max_as_design_point() -> None:
    """只有 v_in_max 和 i_out_max 时，默认设计工况不能等于绝对最大值。"""
    device = DeviceModel(
        part_number="TPS5430",
        category="buck",
        specs={"v_in_max": "36V", "i_out_max": "3A", "fsw": "500kHz"},
    )
    synthesizer = DesignRecipeSynthesizer()
    request = UserDesignRequest(raw_text="降压电路", category="buck")

    _, recipe = synthesizer.prepare_device(device, request)
    v_in_design = float(recipe.default_parameters["v_in"])
    i_out_design = float(recipe.default_parameters["i_out_max"])

    # 不应等于绝对最大值
    assert v_in_design < 36.0, f"v_in={v_in_design} 不应等于 v_in_max=36"
    assert i_out_design < 3.0, f"i_out={i_out_design} 不应等于 i_out_max=3"
    # 但也不应为零
    assert v_in_design > 0
    assert i_out_design > 0


# ============================================================
# 回归测试: SPICE 网表按拓扑映射
# ============================================================


def test_spice_uses_topology_connections_for_buck() -> None:
    """带 topology 的 buck 器件，SPICE 网表应从拓扑连接自动生成，
    包含正确的 VIN/VOUT/SW/FB 网络名。"""
    from schemaforge.design.synthesis import _render_spice

    device = DeviceModel(
        part_number="TPS5430",
        category="buck",
        specs={"v_in_max": "36V", "i_out_max": "3A", "fsw": "500kHz"},
    )
    synthesizer = DesignRecipeSynthesizer()
    request = UserDesignRequest(raw_text="12V转5V", v_in="12", v_out="5", category="buck")
    enriched, recipe = synthesizer.prepare_device(device, request)

    params = dict(recipe.default_parameters)
    params["v_in"] = "12"
    params["v_out"] = "5"

    spice = _render_spice(enriched, params)
    # 主 IC 行必须包含器件型号
    assert "TPS5430" in spice
    # 必须有 VIN 电源
    assert "V1 VIN 0 DC" in spice
    # 外围元件必须包含正确的网络名
    assert "VOUT" in spice
    assert "FB" in spice
    # 必须以 .end 结尾
    assert spice.strip().endswith(".end")


def test_spice_with_device_spice_model_template() -> None:
    """当器件带有 spice_model 模板时，SPICE 输出应使用该模板映射引脚。"""
    from schemaforge.design.synthesis import _render_spice

    device = DeviceModel(
        part_number="TPS5430",
        category="buck",
        specs={"v_in_max": "36V", "i_out_max": "3A", "fsw": "500kHz"},
        spice_model="XU{ref} {VIN} {GND} {EN} {BST} {SW} {FB} TPS5430",
    )
    synthesizer = DesignRecipeSynthesizer()
    request = UserDesignRequest(raw_text="12V转5V", v_in="12", v_out="5", category="buck")
    enriched, recipe = synthesizer.prepare_device(device, request)

    params = dict(recipe.default_parameters)
    params["v_in"] = "12"
    params["v_out"] = "5"

    spice = _render_spice(enriched, params)
    # spice_model 模板应被展开，型号保留
    assert "TPS5430" in spice
    # IC 行应包含 XU1
    assert "XU1" in spice
    # 未映射的 {EN} 应被替换为 0（默认）
    assert "XU1 VIN 0 0 BST SW FB TPS5430" in spice


# ============================================================
# P3: Datasheet 驱动的公式求解
# ============================================================


def test_recipe_driven_calculation_uses_formula_eval() -> None:
    """当器件自带含可求解公式的 design_recipe 时，应用 FormulaEvaluator 计算参数"""
    device = DeviceModel(
        part_number="TEST_BUCK_DS",
        category="buck",
        specs={"fsw": "500kHz", "v_ref": "0.8"},
        topology=TopologyDef(
            circuit_type="buck",
            external_components=[
                ExternalComponent(role="input_cap", ref_prefix="C",
                                  default_value="10uF", value_expression="{c_in}",
                                  schemdraw_element="Capacitor"),
                ExternalComponent(role="output_cap", ref_prefix="C",
                                  default_value="22uF", value_expression="{c_out}",
                                  schemdraw_element="Capacitor"),
                ExternalComponent(role="inductor", ref_prefix="L",
                                  default_value="10uH", value_expression="{l_value}",
                                  schemdraw_element="Inductor2"),
            ],
            connections=[
                TopologyConnection(net_name="VIN", device_pin="VIN",
                                   external_refs=["input_cap.1"], is_power=True),
                TopologyConnection(net_name="VOUT",
                                   external_refs=["inductor.2", "output_cap.1"],
                                   is_power=True),
                TopologyConnection(net_name="GND", device_pin="GND",
                                   external_refs=["input_cap.2", "output_cap.2"],
                                   is_ground=True),
            ],
        ),
        design_recipe=DesignRecipe(
            topology_family="buck",
            summary="Datasheet-driven Buck recipe with formulas",
            formulas=[
                RecipeFormula(
                    name="duty",
                    expression="D = v_out / v_in",
                    rationale="占空比",
                ),
                RecipeFormula(
                    name="delta_il",
                    expression="delta_il = i_out * 0.3",
                    rationale="30% 纹波",
                ),
            ],
            sizing_components=[
                RecipeComponent(
                    role="inductor",
                    formula="L = v_out * (1 - duty) / (fsw * delta_il)",
                    rationale="按纹波设计",
                ),
                RecipeComponent(
                    role="output_cap",
                    formula="Cout = delta_il / (8 * fsw * 0.01 * v_out)",
                    rationale="按 1% 纹波",
                ),
                RecipeComponent(
                    role="input_cap",
                    value="10uF",
                    formula="Cin ≥ 10μF",
                    rationale="推荐最小值",
                ),
            ],
            default_parameters={"v_in": "12", "v_out": "5"},
            evidence=[
                RecipeEvidence(source_type="datasheet", summary="从 datasheet 提取"),
            ],
        ),
    )

    synthesizer = DesignRecipeSynthesizer()
    request = UserDesignRequest(raw_text="12V转5V", v_in="12", v_out="5", category="buck")
    enriched, recipe = synthesizer.prepare_device(device, request)

    # 应走公式驱动路径
    assert any(e.source_type == "formula_eval" for e in recipe.evidence)

    # 参数应被公式计算覆盖
    params = recipe.default_parameters
    assert "l_value" in params or "inductor" in params
    # 电感值应是计算出来的，不是默认的 "10uH"
    l_key = "l_value" if "l_value" in params else "inductor"
    assert params[l_key] != "10uH"  # 不应是原始默认值

    # 输入电容应保持推荐值
    assert params.get("c_in") == "10uF"

    # ic_model 应被设置
    assert params.get("ic_model") == "TEST_BUCK_DS"


def test_recipe_driven_falls_back_when_no_evaluable_formulas() -> None:
    """当 recipe 只有常量（无可求解公式）时，应回退到硬编码计算"""
    device = DeviceModel(
        part_number="TEST_LDO_CONST",
        category="ldo",
        specs={"v_out": "3.3"},
        topology=TopologyDef(
            circuit_type="ldo",
            external_components=[
                ExternalComponent(role="input_cap", ref_prefix="C",
                                  default_value="10uF", value_expression="{c_in}",
                                  schemdraw_element="Capacitor"),
                ExternalComponent(role="output_cap", ref_prefix="C",
                                  default_value="22uF", value_expression="{c_out}",
                                  schemdraw_element="Capacitor"),
            ],
            connections=[
                TopologyConnection(net_name="VIN", device_pin="VIN",
                                   external_refs=["input_cap.1"], is_power=True),
                TopologyConnection(net_name="VOUT", device_pin="VOUT",
                                   external_refs=["output_cap.1"], is_power=True),
                TopologyConnection(net_name="GND", device_pin="GND",
                                   external_refs=["input_cap.2", "output_cap.2"],
                                   is_ground=True),
            ],
        ),
        design_recipe=DesignRecipe(
            topology_family="ldo",
            summary="LDO with constants only",
            sizing_components=[
                RecipeComponent(role="input_cap", value="10uF",
                                formula="Cin ≥ 10μF"),
                RecipeComponent(role="output_cap", value="22uF",
                                formula="Cout ≥ 22μF"),
            ],
            default_parameters={"v_in": "5", "v_out": "3.3", "c_in": "10uF", "c_out": "22uF"},
        ),
    )

    synthesizer = DesignRecipeSynthesizer()
    request = UserDesignRequest(raw_text="5V转3.3V稳压", v_in="5", v_out="3.3", category="ldo")
    _, recipe = synthesizer.prepare_device(device, request)

    # 应回退到硬编码 LDO 计算（无 formula_eval evidence）
    assert not any(e.source_type == "formula_eval" for e in recipe.evidence)
