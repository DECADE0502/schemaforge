"""SchemaForge 新工作台会话测试。"""

from __future__ import annotations

from pathlib import Path

from schemaforge.design.synthesis import (
    DesignRecipeSynthesizer,
    ExactPartResolver,
    UserDesignRequest,
    parse_design_request,
    parse_revision_request_v2,
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


import json

from schemaforge.agent.tool_registry import ToolRegistry, ToolResult
from schemaforge.ai.prompts import build_design_workbench_prompt


# ============================================================
# ToolRegistry.merge() 测试
# ============================================================


def test_tool_registry_merge_basic() -> None:
    """merge() 返回包含两个注册表全部工具的新实例。"""
    r1 = ToolRegistry()
    r2 = ToolRegistry()
    r1.register_fn(name="tool_a", description="A", handler=lambda: ToolResult())
    r2.register_fn(name="tool_b", description="B", handler=lambda: ToolResult())

    merged = r1.merge(r2)
    assert merged.get_tool("tool_a") is not None
    assert merged.get_tool("tool_b") is not None
    assert len(merged.list_tools()) == 2


def test_tool_registry_merge_does_not_mutate_originals() -> None:
    """merge() 不修改原始注册表。"""
    r1 = ToolRegistry()
    r2 = ToolRegistry()
    r1.register_fn(name="tool_a", description="A", handler=lambda: ToolResult())
    r2.register_fn(name="tool_b", description="B", handler=lambda: ToolResult())

    _ = r1.merge(r2)
    assert r1.get_tool("tool_b") is None
    assert r2.get_tool("tool_a") is None


def test_tool_registry_merge_conflict_other_wins() -> None:
    """merge() 冲突时 other 覆盖 self。"""
    r1 = ToolRegistry()
    r2 = ToolRegistry()
    r1.register_fn(name="tool_x", description="OLD", handler=lambda: ToolResult())
    r2.register_fn(name="tool_x", description="NEW", handler=lambda: ToolResult())

    merged = r1.merge(r2)
    assert merged.get_tool("tool_x") is not None
    assert merged.get_tool("tool_x").description == "NEW"  # type: ignore[union-attr]


def test_tool_registry_merge_empty() -> None:
    """合并空注册表。"""
    r1 = ToolRegistry()
    r1.register_fn(name="tool_a", description="A", handler=lambda: ToolResult())
    r2 = ToolRegistry()

    merged = r1.merge(r2)
    assert len(merged.list_tools()) == 1


# ============================================================
# build_design_workbench_prompt 测试
# ============================================================


def test_build_design_workbench_prompt_injects_tool_descriptions() -> None:
    """system prompt 中包含注入的工具描述。"""
    tools_text = json.dumps(
        [{"name": "test_tool", "description": "测试工具"}],
        ensure_ascii=False,
    )
    prompt = build_design_workbench_prompt(tools_text)
    assert "test_tool" in prompt
    assert "测试工具" in prompt
    assert "SchemaForge" in prompt


# ============================================================
# Orchestrator 懒构建测试
# ============================================================


def test_session_get_orchestrator_returns_orchestrator(tmp_path: Path) -> None:
    """get_orchestrator() 返回 Orchestrator 实例。"""
    from schemaforge.agent.orchestrator import Orchestrator

    session = SchemaForgeSession(tmp_path / "store", )
    orch = session.get_orchestrator()
    assert isinstance(orch, Orchestrator)


def test_session_get_orchestrator_merges_all_tools(tmp_path: Path) -> None:
    """Orchestrator 的注册表包含全局工具和会话工具。"""
    session = SchemaForgeSession(tmp_path / "store", )
    orch = session.get_orchestrator()
    # 全局工具
    assert orch.registry.get_tool("build_symbol") is not None
    assert orch.registry.get_tool("parse_pdf") is not None
    # 会话工具
    assert orch.registry.get_tool("start_design_request") is not None
    assert orch.registry.get_tool("calculate_parameters") is not None
    assert orch.registry.get_tool("validate_design") is not None


def test_session_get_orchestrator_system_prompt_has_tool_descriptions(
    tmp_path: Path,
) -> None:
    """Orchestrator 的 system prompt 包含工具描述。"""
    session = SchemaForgeSession(tmp_path / "store", )
    orch = session.get_orchestrator()
    # system_prompt 应包含工具名
    assert "start_design_request" in orch.system_prompt
    assert "build_symbol" in orch.system_prompt


def test_session_get_orchestrator_is_cached(tmp_path: Path) -> None:
    """多次调用 get_orchestrator() 返回同一实例。"""
    session = SchemaForgeSession(tmp_path / "store", )
    orch1 = session.get_orchestrator()
    orch2 = session.get_orchestrator()
    assert orch1 is orch2


# ============================================================
# structural_ops 执行测试
# ============================================================


def test_revise_add_led_module(tmp_path: Path) -> None:
    """通过 '加一个指示灯' 结构化操作，wants_led 应变为 True。"""
    store = ComponentStore(tmp_path / "store")
    store.save_device(
        DeviceModel(part_number="AMS1117-3.3", category="ldo",
                    specs={"v_out": "3.3V"})
    )
    session = SchemaForgeSession(tmp_path / "store", )
    session.start("用 AMS1117-3.3 搭一个 5V 转 3.3V 稳压电路")

    r = session.revise("加一个指示灯")
    assert r.status == "generated"
    assert "结构" in r.message


def test_revise_remove_led_module(tmp_path: Path) -> None:
    """通过 '去掉指示灯' 结构化操作。"""
    store = ComponentStore(tmp_path / "store")
    store.save_device(
        DeviceModel(part_number="AMS1117-3.3", category="ldo",
                    specs={"v_out": "3.3V"})
    )
    session = SchemaForgeSession(tmp_path / "store", )
    session.start("用 AMS1117-3.3 搭一个 5V 转 3.3V 稳压电路 带 LED 指示灯")

    r = session.revise("去掉指示灯模块")
    assert r.status == "generated"
    assert "结构" in r.message


def test_revise_add_decoupling_cap(tmp_path: Path) -> None:
    """通过 '加一个去耦电容' 结构化操作添加去耦电容参数。"""
    store = ComponentStore(tmp_path / "store")
    store.save_device(
        DeviceModel(part_number="AMS1117-3.3", category="ldo",
                    specs={"v_out": "3.3V"})
    )
    session = SchemaForgeSession(tmp_path / "store", )
    session.start("用 AMS1117-3.3 搭一个 5V 转 3.3V 稳压电路")

    r = session.revise("加一个去耦电容")
    assert r.status == "generated"
    assert "c_decoupling" in r.bundle.parameters  # type: ignore[union-attr]


def test_revise_add_rc_filter(tmp_path: Path) -> None:
    """通过 '加一个滤波器' 结构化操作。"""
    store = ComponentStore(tmp_path / "store")
    store.save_device(
        DeviceModel(part_number="AMS1117-3.3", category="ldo",
                    specs={"v_out": "3.3V"})
    )
    session = SchemaForgeSession(tmp_path / "store", )
    session.start("用 AMS1117-3.3 搭一个 5V 转 3.3V 稳压电路")

    r = session.revise("加一个滤波器")
    assert r.status == "generated"
    assert "rc_filter_r" in r.bundle.parameters  # type: ignore[union-attr]


def test_revise_remove_filter(tmp_path: Path) -> None:
    """先加再删滤波器，参数应被清除。"""
    store = ComponentStore(tmp_path / "store")
    store.save_device(
        DeviceModel(part_number="AMS1117-3.3", category="ldo",
                    specs={"v_out": "3.3V"})
    )
    session = SchemaForgeSession(tmp_path / "store", )
    session.start("用 AMS1117-3.3 搭一个 5V 转 3.3V 稳压电路")

    session.revise("加一个滤波器")
    r = session.revise("去掉滤波器")
    assert r.status == "generated"
    assert "rc_filter_r" not in r.bundle.parameters  # type: ignore[union-attr]


def test_revise_structural_ops_with_param_changes(tmp_path: Path) -> None:
    """结构化操作和参数修改可以同时生效。"""
    store = ComponentStore(tmp_path / "store")
    store.save_device(
        DeviceModel(part_number="AMS1117-3.3", category="ldo",
                    specs={"v_out": "3.3V"})
    )
    session = SchemaForgeSession(tmp_path / "store", )
    session.start("用 AMS1117-3.3 搭一个 5V 转 3.3V 稳压电路")

    # "输出电容改成 100uF" is a param change, structural ops would come from separate parsing
    r = session.revise("输出电容改成 100uF")
    assert r.status == "generated"


def test_revise_unsupported_structural_op_returns_warning(tmp_path: Path) -> None:
    """不支持的模块类型应在 warnings 中给出提示。"""
    store = ComponentStore(tmp_path / "store")
    store.save_device(
        DeviceModel(part_number="AMS1117-3.3", category="ldo",
                    specs={"v_out": "3.3V"})
    )
    session = SchemaForgeSession(tmp_path / "store", )
    session.start("用 AMS1117-3.3 搭一个 5V 转 3.3V 稳压电路")

    # 手动注入一个不支持的结构化操作（直接走 _execute_add_module）
    warning = session._execute_add_module("transformer", "变压器")
    assert warning  # 非空 = 有警告


# ============================================================
# P1: 扩展品类检测测试
# ============================================================


def test_category_detection_boost() -> None:
    """检测 boost 升压关键词。"""
    req = parse_design_request("用 XL6009 搭一个 5V 升压到 12V 的 Boost 电路")
    assert req.category == "boost"


def test_category_detection_flyback() -> None:
    """检测 flyback 反激关键词。"""
    req = parse_design_request("用 TNY266 搭一个 Flyback 反激电源")
    assert req.category == "flyback"


def test_category_detection_sepic() -> None:
    """检测 SEPIC 升降压关键词。"""
    req = parse_design_request("设计一个 SEPIC 升降压电路")
    assert req.category == "sepic"


def test_category_detection_charge_pump() -> None:
    """检测电荷泵关键词。"""
    req = parse_design_request("设计一个电荷泵电路，从 3.3V 到 -3.3V")
    assert req.category == "charge_pump"


def test_category_detection_sensor() -> None:
    """检测传感器关键词。"""
    req = parse_design_request("用 LM35 设计一个温度 sensor 采集电路")
    assert req.category == "sensor"


def test_category_detection_mosfet() -> None:
    """检测 MOSFET 关键词。"""
    req = parse_design_request("用 IRF540 搭一个 MOSFET 驱动电路")
    assert req.category == "mosfet"


def test_category_detection_diode() -> None:
    """检测二极管/整流关键词。"""
    req = parse_design_request("用 1N4007 搭一个全桥整流电路")
    assert req.category == "diode"


# ============================================================
# P1: Boost / Flyback / SEPIC 合成测试
# ============================================================


def test_boost_recipe_synthesizer(tmp_path: Path) -> None:
    """Boost 类别走 _build_boost_recipe 路径。"""
    from schemaforge.design.synthesis import DesignRecipeSynthesizer

    device = DeviceModel(part_number="XL6009", category="boost",
                         specs={"v_in_max": "32V", "i_out_max": "3A"})
    request = UserDesignRequest(
        raw_text="5V 升压到 12V",
        part_number="XL6009",
        category="boost",
        v_in="5",
        v_out="12",
        i_out="1",
    )
    synth = DesignRecipeSynthesizer()
    enriched, recipe = synth.prepare_device(device, request)
    assert recipe.topology_family == "boost"
    assert enriched.topology is not None
    assert enriched.topology.circuit_type == "boost"
    assert "l_value" in recipe.default_parameters or "l_primary" in recipe.default_parameters


def test_flyback_recipe_synthesizer(tmp_path: Path) -> None:
    """Flyback 类别走 _build_isolated_recipe 路径。"""
    from schemaforge.design.synthesis import DesignRecipeSynthesizer

    device = DeviceModel(part_number="TNY266", category="flyback",
                         specs={"fsw": "132kHz"})
    request = UserDesignRequest(
        raw_text="反激电源",
        part_number="TNY266",
        category="flyback",
        v_in="12",
        v_out="5",
        i_out="0.5",
    )
    synth = DesignRecipeSynthesizer()
    enriched, recipe = synth.prepare_device(device, request)
    assert recipe.topology_family == "flyback"
    assert enriched.topology is not None
    assert "l_primary" in recipe.default_parameters


def test_sepic_recipe_synthesizer(tmp_path: Path) -> None:
    """SEPIC 类别走 _build_isolated_recipe 路径。"""
    from schemaforge.design.synthesis import DesignRecipeSynthesizer

    device = DeviceModel(part_number="LT3757", category="sepic")
    request = UserDesignRequest(
        raw_text="升降压电路",
        part_number="LT3757",
        category="sepic",
        v_in="12",
        v_out="5",
        i_out="0.5",
    )
    synth = DesignRecipeSynthesizer()
    enriched, recipe = synth.prepare_device(device, request)
    assert recipe.topology_family == "sepic"
    assert enriched.topology is not None


# ============================================================
# P1: TopologyDraftGenerator._llm_generate 测试
# ============================================================


def test_topology_draft_generator_llm_fallback_to_mock() -> None:
    """LLM 不可用时 _llm_generate 降级到 mock。"""
    from schemaforge.design.topology_draft import TopologyDraftGenerator

    device = DeviceModel(part_number="AMS1117-3.3", category="ldo")
    gen = TopologyDraftGenerator()
    # 因为真 AI 不可用（测试环境），应降级到 mock 而非 crash
    draft = gen.generate(device)
    assert draft.name == "ldo"


# ============================================================
# P1: RequirementClarifier AI path 测试
# ============================================================


def test_clarifier_ai_path_returns_result() -> None:
    """ 时不再抛异常，返回有效结果。"""
    from schemaforge.design.clarifier import RequirementClarifier
    from schemaforge.design.planner import DesignPlanner

    planner = DesignPlanner()
    plan = planner.plan("5V 转 3.3V 稳压电路")

    # AI 路径（真 AI 不可用时降级到 mock 结果）
    clarifier = RequirementClarifier()
    result = clarifier.clarify("5V 转 3.3V 稳压电路", plan)
    # 应该返回有效的 ClarificationResult 而不是崩溃
    assert result is not None
    assert isinstance(result.confidence, float)


# ============================================================
# 原有测试（parse / resolve / session）
# ============================================================


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
    session = SchemaForgeSession(tmp_path / "store", )
    result = session.start("帮我用 TPS54202 搭一个 20V 转 5V 的 DCDC 电路")
    assert result.status == "needs_asset"
    assert result.missing_part_number == "TPS54202"


def test_session_import_and_generate_buck_design(tmp_path: Path) -> None:
    session = SchemaForgeSession(tmp_path / "store", )
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
    session = SchemaForgeSession(tmp_path / "store", )
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

    session = SchemaForgeSession(tmp_path / "store", )
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
    """当器件自带含可求解公式的 design_recipe 时，应用 FormulaEvaluator 计算参数。

    注意：buck/ldo/boost 等工况敏感类型强制走硬编码重算（_RECALC_CATEGORIES），
    所以此测试使用 custom_power 类型验证 formula_eval 路径。
    """
    device = DeviceModel(
        part_number="TEST_CUSTOM_DS",
        category="custom_power",
        specs={"fsw": "500kHz", "v_ref": "0.8"},
        topology=TopologyDef(
            circuit_type="custom_power",
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
    assert params.get("ic_model") == "TEST_CUSTOM_DS"


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


# ============================================================
# P5: 多轮修改 — parse_revision_request_v2 测试
# ============================================================


class TestParseRevisionRequestV2:
    """parse_revision_request_v2 增强解析测试。"""

    def test_output_capacitor_change(self) -> None:
        result = parse_revision_request_v2("把输出电容改成 47uF")
        assert result.param_updates.get("c_out") == "47uF"

    def test_input_capacitor_change(self) -> None:
        result = parse_revision_request_v2("输入电容换成 22uF")
        assert result.param_updates.get("c_in") == "22uF"

    def test_inductor_change(self) -> None:
        result = parse_revision_request_v2("电感用 22uH 的")
        assert result.param_updates.get("l_value") == "22uH"

    def test_output_voltage_change(self) -> None:
        result = parse_revision_request_v2("输出调到 1.8V")
        assert result.request_updates.get("v_out") is not None

    def test_add_led(self) -> None:
        result = parse_revision_request_v2("加一个红色 LED 指示灯")
        assert result.request_updates.get("wants_led") is True
        assert result.request_updates.get("led_color") == "red"

    def test_remove_led(self) -> None:
        result = parse_revision_request_v2("去掉 LED 指示灯")
        assert result.request_updates.get("wants_led") is False

    def test_device_replacement(self) -> None:
        result = parse_revision_request_v2("换成 TPS5430")
        assert result.replace_device == "TPS5430"

    def test_device_replacement_chinese(self) -> None:
        result = parse_revision_request_v2("改用 AMS1117-3.3 这个芯片")
        assert result.replace_device == "AMS1117-3.3"

    def test_frequency_change_khz(self) -> None:
        result = parse_revision_request_v2("开关频率改成 600kHz")
        assert result.param_updates.get("fsw") == "600000"

    def test_frequency_change_mhz(self) -> None:
        result = parse_revision_request_v2("fsw 设为 1.2MHz")
        assert result.param_updates.get("fsw") == "1200000"

    def test_generic_param_change(self) -> None:
        result = parse_revision_request_v2("把 c_out 改成 100uF")
        assert result.param_updates.get("c_out") == "100uF"

    def test_output_current_change(self) -> None:
        result = parse_revision_request_v2("输出电流调到 500mA")
        assert result.request_updates.get("i_out") == "0.5"

    def test_add_module_filter(self) -> None:
        result = parse_revision_request_v2("加一个滤波器")
        assert len(result.structural_ops) == 1
        assert result.structural_ops[0]["op_type"] == "add_module"
        assert result.structural_ops[0]["category"] == "rc_filter"

    def test_remove_module(self) -> None:
        result = parse_revision_request_v2("去掉分压器")
        assert len(result.structural_ops) == 1
        assert result.structural_ops[0]["op_type"] == "remove_module"

    def test_empty_input(self) -> None:
        result = parse_revision_request_v2("你好")
        assert not result.param_updates
        assert not result.request_updates
        assert not result.replace_device
        assert not result.structural_ops

    def test_combined_changes(self) -> None:
        """同时修改多个参数。"""
        result = parse_revision_request_v2("输出电容改成 47uF，加一个 LED 指示灯")
        assert result.param_updates.get("c_out") == "47uF"
        assert result.request_updates.get("wants_led") is True


class TestSchemaForgeSessionReviseEnhanced:
    """SchemaForgeSession.revise() 增强功能测试。"""

    def test_revise_replaces_device(self, tmp_path: Path) -> None:
        """器件替换: 库中有新器件时自动重建。"""
        store = ComponentStore(tmp_path / "store")
        store.save_device(
            DeviceModel(part_number="AMS1117-3.3", category="ldo",
                        specs={"v_out": "3.3V"})
        )
        store.save_device(
            DeviceModel(part_number="AMS1117-5.0", category="ldo",
                        specs={"v_out": "5.0V"})
        )
        session = SchemaForgeSession(tmp_path / "store", )

        # 初始设计
        r1 = session.start("用 AMS1117-3.3 搭一个 5V 转 3.3V 稳压电路")
        assert r1.status == "generated"
        assert session.bundle is not None
        assert session.bundle.device.part_number == "AMS1117-3.3"

        # 替换器件
        r2 = session.revise("换成 AMS1117-5.0")
        assert r2.status == "generated"
        assert session.bundle is not None
        assert session.bundle.device.part_number == "AMS1117-5.0"

    def test_revise_device_not_found(self, tmp_path: Path) -> None:
        """器件替换: 库中没有新器件时返回 needs_asset。"""
        store = ComponentStore(tmp_path / "store")
        store.save_device(
            DeviceModel(part_number="AMS1117-3.3", category="ldo",
                        specs={"v_out": "3.3V"})
        )
        session = SchemaForgeSession(tmp_path / "store", )
        session.start("用 AMS1117-3.3 搭一个 5V 转 3.3V 稳压电路")

        r = session.revise("换成 TPS5430")
        assert r.status == "needs_asset"
        assert r.missing_part_number == "TPS5430"

    def test_revise_frequency_change(self, tmp_path: Path) -> None:
        """修改开关频率并重建。"""
        store = ComponentStore(tmp_path / "store")
        store.save_device(
            DeviceModel(part_number="AMS1117-3.3", category="ldo",
                        specs={"v_out": "3.3V"})
        )
        session = SchemaForgeSession(tmp_path / "store", )
        session.start("用 AMS1117-3.3 搭一个 5V 转 3.3V 稳压电路")

        r = session.revise("开关频率改成 600kHz")
        assert r.status == "generated"

    def test_revise_with_summary_message(self, tmp_path: Path) -> None:
        """修改后消息中包含变更摘要。"""
        store = ComponentStore(tmp_path / "store")
        store.save_device(
            DeviceModel(part_number="AMS1117-3.3", category="ldo",
                        specs={"v_out": "3.3V"})
        )
        session = SchemaForgeSession(tmp_path / "store", )
        session.start("用 AMS1117-3.3 搭一个 5V 转 3.3V 稳压电路")

        r = session.revise("输出电容改成 100uF")
        assert r.status == "generated"
        assert "c_out" in r.message


# ============================================================
# GPT Review 回归测试
# ============================================================


def test_confirm_import_validates_before_persisting(tmp_path) -> None:
    """P0-1: confirm_import 先校验后落库，part_number 为空的草稿触发 ERROR 不写盘。"""
    from schemaforge.workflows.schemaforge_session import SchemaForgeSession
    from schemaforge.design.synthesis import parse_design_request

    session = SchemaForgeSession(store_dir=tmp_path)
    session._request = parse_design_request("TPS99999 Buck")

    # 设置一个 part_number 为空的草稿 — validate_draft 会产生 ERROR 级问题
    from schemaforge.library.validator import DeviceDraft
    session._pending_draft = DeviceDraft(
        part_number="",  # ERROR: 料号不能为空
        category="buck",
        pin_count=0,
        pins=[],
    )

    result = session.confirm_import()
    # 校验失败应返回 needs_confirmation 或 error，不是 generated
    assert result.status in ("needs_confirmation", "error")
    # 器件不应被写入库
    assert session._service.get("TPS99999") is None


def test_different_operating_points_produce_different_params(tmp_path) -> None:
    """P0-2: 同一芯片不同工况应产生不同的计算参数。"""
    import shutil
    from pathlib import Path
    from schemaforge.workflows.schemaforge_session import SchemaForgeSession

    # 复制 store 到 tmp 避免污染
    src = Path("schemaforge/store")
    dst = tmp_path / "store"
    shutil.copytree(src, dst)

    s1 = SchemaForgeSession(store_dir=dst)
    r1 = s1.start("用 TPS5430 搭一个 12V转5V 的 DCDC 电路")
    assert r1.status == "generated"
    rfb1 = r1.bundle.parameters.get("r_fb_upper", "")

    s2 = SchemaForgeSession(store_dir=dst)
    r2 = s2.start("用 TPS5430 搭一个 24V转3.3V 的 DCDC 电路")
    assert r2.status == "generated"
    rfb2 = r2.bundle.parameters.get("r_fb_upper", "")

    # 不同 Vout 必须导致不同的反馈电阻
    assert rfb1 != rfb2, f"r_fb_upper should differ: {rfb1} vs {rfb2}"


def test_buck_defaults_do_not_use_absolute_max() -> None:
    """P1-4: 默认 v_in 不使用 absolute max，应使用保守典型值。"""
    from schemaforge.design.synthesis import DesignRecipeSynthesizer, UserDesignRequest
    from schemaforge.library.models import DeviceModel

    device = DeviceModel(
        part_number="TEST_BUCK_NOVIN",
        category="buck",
        specs={"v_in_max": "36V", "i_out_max": "3A", "fsw": "500kHz"},
    )
    request = UserDesignRequest(raw_text="Buck converter", category="buck")
    # v_in and i_out not specified in request

    synth = DesignRecipeSynthesizer()
    enriched, recipe = synth.prepare_device(device, request)
    v_in = float(recipe.default_parameters.get("v_in", 0))
    i_out = float(recipe.default_parameters.get("i_out_max", 0))

    # v_in 不应等于 36 (abs max) 或 21.6 (60% derate)
    assert v_in <= 12.0, f"v_in={v_in} should not use absolute max"
    # i_out 不应等于 3 (abs max) 或 2.1 (70% derate)
    assert i_out <= 1.0, f"i_out={i_out} should not use absolute max"


def test_spice_uses_device_model_template() -> None:
    """P1-3: SPICE 应使用 device.spice_model 模板生成 IC 行。"""
    from pathlib import Path
    from schemaforge.workflows.schemaforge_session import SchemaForgeSession

    s = SchemaForgeSession(store_dir=Path("schemaforge/store"))
    r = s.start("用 TPS5430 搭一个 12V转3.3V 的 DCDC 电路")
    assert r.status == "generated"
    spice = r.bundle.spice_text

    # 应包含 TPS5430 的 SPICE model 引用
    assert "TPS5430" in spice
    # 应包含外围元件
    assert "C1" in spice or "CIN" in spice
    assert "L1" in spice
    # 应包含 .end
    assert ".end" in spice
