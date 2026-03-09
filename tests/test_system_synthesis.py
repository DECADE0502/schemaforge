"""Tests for schemaforge.system.synthesis (T062-T070).

纯确定性模块综合：Buck/LDO/MCU/LED 外围元件计算与依赖传播。
"""

from __future__ import annotations

from schemaforge.system.models import (
    ModuleInstance,
    ModuleStatus,
    NetType,
    PortRef,
    ResolvedConnection,
    SystemDesignIR,
    SystemDesignRequest,
)
from schemaforge.system.synthesis import (
    propagate_supply_constraints,
    recompute_dependent_modules,
    synthesize_all_modules,
    synthesize_buck_module,
    synthesize_generic_module,
    synthesize_led_indicator,
    synthesize_ldo_module,
    synthesize_mcu_minimum_system,
)


# ============================================================
# Helpers
# ============================================================


def _make_instance(
    module_id: str,
    category: str,
    parameters: dict[str, str] | None = None,
    ports: dict[str, PortRef] | None = None,
    status: ModuleStatus = ModuleStatus.RESOLVED,
) -> ModuleInstance:
    """创建测试用 ModuleInstance。"""
    if ports is None:
        ports = {
            "VIN": PortRef(
                module_id=module_id,
                port_role="power_in",
                pin_name="VIN",
                net_class=NetType.POWER,
            ),
            "VOUT": PortRef(
                module_id=module_id,
                port_role="power_out",
                pin_name="VOUT",
                net_class=NetType.POWER,
            ),
            "GND": PortRef(
                module_id=module_id,
                port_role="ground",
                pin_name="GND",
                net_class=NetType.GROUND,
            ),
        }
    return ModuleInstance(
        module_id=module_id,
        role=f"test_{category}",
        resolved_category=category,
        parameters=dict(parameters or {}),
        resolved_ports=ports,
        status=status,
    )


def _make_supply_connection(
    src_module: str, dst_module: str,
) -> ResolvedConnection:
    """创建电源链连接。"""
    return ResolvedConnection(
        resolved_connection_id=f"{src_module}_to_{dst_module}",
        src_port=PortRef(
            module_id=src_module,
            port_role="power_out",
            pin_name="VOUT",
            net_class=NetType.POWER,
        ),
        dst_port=PortRef(
            module_id=dst_module,
            port_role="power_in",
            pin_name="VIN",
            net_class=NetType.POWER,
        ),
        net_name="NET_SUPPLY",
        rule_id="supply_chain",
    )


def _make_ir(
    modules: dict[str, ModuleInstance],
    connections: list[ResolvedConnection] | None = None,
) -> SystemDesignIR:
    """创建测试用 SystemDesignIR。"""
    return SystemDesignIR(
        request=SystemDesignRequest(raw_text="test"),
        module_instances=modules,
        connections=connections or [],
    )


# ============================================================
# T062: Buck 模块综合
# ============================================================


class TestSynthesizeBuck:
    """T062: Buck 转换器综合。"""

    def test_buck_12v_to_5v_status(self) -> None:
        """12V->5V Buck 综合后 status=SYNTHESIZED。"""
        inst = _make_instance("buck1", "buck", {"v_in": "12", "v_out": "5", "i_out": "2"})
        result = synthesize_buck_module(inst)
        assert result.status == ModuleStatus.SYNTHESIZED

    def test_buck_12v_to_5v_inductor(self) -> None:
        """12V->5V Buck 电感值在合理范围 (1uH-100uH)。"""
        inst = _make_instance("buck1", "buck", {"v_in": "12", "v_out": "5", "i_out": "2"})
        result = synthesize_buck_module(inst)
        l_val = result.parameters["l_value"]
        assert "uH" in l_val or "mH" in l_val
        # 解析数值检查范围
        num = float(l_val.replace("uH", "").replace("mH", ""))
        if "mH" in l_val:
            num *= 1000  # 转为 uH
        assert 1.0 <= num <= 100.0

    def test_buck_12v_to_5v_caps(self) -> None:
        """12V->5V Buck 有输入电容和输出电容。"""
        inst = _make_instance("buck1", "buck", {"v_in": "12", "v_out": "5", "i_out": "2"})
        result = synthesize_buck_module(inst)
        assert "c_in" in result.parameters
        assert "c_out" in result.parameters
        assert "uF" in result.parameters["c_in"]
        assert "uF" in result.parameters["c_out"]

    def test_buck_12v_to_5v_feedback(self) -> None:
        """12V->5V Buck 反馈电阻网络正确。"""
        inst = _make_instance("buck1", "buck", {"v_in": "12", "v_out": "5", "i_out": "2"})
        result = synthesize_buck_module(inst)
        assert "r_fb_upper" in result.parameters
        assert "r_fb_lower" in result.parameters
        # 下拉 10k
        assert result.parameters["r_fb_lower"] == "10k\u03a9"

    def test_buck_external_components(self) -> None:
        """Buck 外围元件列表含 inductor/input_cap/output_cap/boot_cap/fb_upper/fb_lower/diode。"""
        inst = _make_instance("buck1", "buck", {"v_in": "12", "v_out": "5", "i_out": "2"})
        result = synthesize_buck_module(inst)
        roles = {c["role"] for c in result.external_components}
        assert "inductor" in roles
        assert "input_cap" in roles
        assert "output_cap" in roles
        assert "boot_cap" in roles
        assert "fb_upper" in roles
        assert "fb_lower" in roles
        assert "diode" in roles

    def test_buck_evidence(self) -> None:
        """Buck 综合产出 evidence 记录（C47）。"""
        inst = _make_instance("buck1", "buck", {"v_in": "12", "v_out": "5", "i_out": "2"})
        result = synthesize_buck_module(inst)
        assert any("Buck" in e for e in result.evidence)

    def test_buck_each_component_has_formula(self) -> None:
        """每个外围元件都有 formula 和 evidence 字段（C56, C57）。"""
        inst = _make_instance("buck1", "buck", {"v_in": "24", "v_out": "3.3", "i_out": "1"})
        result = synthesize_buck_module(inst)
        for comp in result.external_components:
            assert "formula" in comp, f"{comp['role']} 缺少 formula"
            assert "evidence" in comp, f"{comp['role']} 缺少 evidence"

    def test_buck_24v_to_3v3_different_params(self) -> None:
        """24V->3.3V 与 12V->5V 产生不同参数（C49: 无缓存污染）。"""
        inst_a = _make_instance("a", "buck", {"v_in": "12", "v_out": "5", "i_out": "2"})
        inst_b = _make_instance("b", "buck", {"v_in": "24", "v_out": "3.3", "i_out": "1"})
        result_a = synthesize_buck_module(inst_a)
        result_b = synthesize_buck_module(inst_b)
        # 不同工况必须产生不同电感值
        assert result_a.parameters["l_value"] != result_b.parameters["l_value"] or \
            result_a.parameters["r_fb_upper"] != result_b.parameters["r_fb_upper"]

    def test_buck_boot_cap_100nf(self) -> None:
        """自举电容固定 100nF。"""
        inst = _make_instance("buck1", "buck", {"v_in": "12", "v_out": "5", "i_out": "2"})
        result = synthesize_buck_module(inst)
        assert result.parameters["c_boot"] == "100nF"


# ============================================================
# T063: LDO 模块综合
# ============================================================


class TestSynthesizeLdo:
    """T063: LDO 综合。"""

    def test_ldo_5v_to_3v3_status(self) -> None:
        """5V->3.3V LDO 综合后 status=SYNTHESIZED。"""
        inst = _make_instance("ldo1", "ldo", {"v_in": "5", "v_out": "3.3"})
        result = synthesize_ldo_module(inst)
        assert result.status == ModuleStatus.SYNTHESIZED

    def test_ldo_cin_cout(self) -> None:
        """LDO 有正确的 C_in 和 C_out。"""
        inst = _make_instance("ldo1", "ldo", {"v_in": "5", "v_out": "3.3"})
        result = synthesize_ldo_module(inst)
        assert result.parameters["c_in"] == "10uF"
        assert result.parameters["c_out"] == "22uF"

    def test_ldo_external_components(self) -> None:
        """LDO 外围元件含 input_cap 和 output_cap。"""
        inst = _make_instance("ldo1", "ldo", {"v_in": "5", "v_out": "3.3"})
        result = synthesize_ldo_module(inst)
        roles = {c["role"] for c in result.external_components}
        assert roles == {"input_cap", "output_cap"}

    def test_ldo_evidence(self) -> None:
        """LDO 综合产出 evidence。"""
        inst = _make_instance("ldo1", "ldo", {"v_in": "5", "v_out": "3.3"})
        result = synthesize_ldo_module(inst)
        assert any("LDO" in e for e in result.evidence)


# ============================================================
# T064: MCU 最小系统
# ============================================================


class TestSynthesizeMcu:
    """T064: MCU 最小系统综合。"""

    def test_mcu_has_decoupling(self) -> None:
        """MCU 综合产出去耦电容。"""
        ports = {
            "VDD_1": PortRef(
                module_id="mcu1", port_role="power_in",
                pin_name="VDD_1", net_class=NetType.POWER,
            ),
            "VDD_2": PortRef(
                module_id="mcu1", port_role="power_in",
                pin_name="VDD_2", net_class=NetType.POWER,
            ),
            "VDDA": PortRef(
                module_id="mcu1", port_role="power_in",
                pin_name="VDDA", net_class=NetType.POWER,
            ),
            "VSS_1": PortRef(
                module_id="mcu1", port_role="ground",
                pin_name="VSS_1", net_class=NetType.GROUND,
            ),
        }
        inst = _make_instance("mcu1", "mcu", ports=ports)
        result = synthesize_mcu_minimum_system(inst)
        assert result.status == ModuleStatus.SYNTHESIZED

        # 3 个 VDD 引脚 -> 3 个 100nF 去耦 + 1 个 10uF
        decoupling = [c for c in result.external_components if "decoupling" in c["role"]]
        bulk = [c for c in result.external_components if c["role"] == "bulk_cap"]
        assert len(decoupling) == 3
        assert len(bulk) == 1
        for c in decoupling:
            assert c["value"] == "100nF"
        assert bulk[0]["value"] == "10uF"

    def test_mcu_single_vdd(self) -> None:
        """单 VDD MCU 至少 1 个去耦 + 1 个储能。"""
        inst = _make_instance("mcu1", "mcu")
        # 默认 ports 有 VIN(power_in) -> 1 个
        result = synthesize_mcu_minimum_system(inst)
        assert len(result.external_components) == 2  # 1 decoupling + 1 bulk

    def test_mcu_evidence(self) -> None:
        """MCU 综合产出 evidence。"""
        inst = _make_instance("mcu1", "mcu")
        result = synthesize_mcu_minimum_system(inst)
        assert any("MCU" in e for e in result.evidence)


# ============================================================
# T065: LED 指示灯
# ============================================================


class TestSynthesizeLed:
    """T065: LED 指示灯综合。"""

    def test_led_green_3v3(self) -> None:
        """3.3V 绿色 LED 限流电阻正确。"""
        inst = _make_instance(
            "led1", "led",
            {"led_color": "green", "v_supply": "3.3"},
        )
        result = synthesize_led_indicator(inst)
        assert result.status == ModuleStatus.SYNTHESIZED

        # R = (3.3 - 2.2) / 0.01 = 110 -> E24 nearest = 110
        r_val = result.parameters["r_value"]
        assert "\u03a9" in r_val  # 有欧姆符号
        # 解析数值
        num_str = r_val.replace("k\u03a9", "").replace("\u03a9", "")
        num = float(num_str)
        if "k" in r_val:
            num *= 1000
        assert 100 <= num <= 120  # E24: 110

    def test_led_red_5v(self) -> None:
        """5V 红色 LED 限流电阻正确。"""
        inst = _make_instance(
            "led1", "led",
            {"led_color": "red", "v_supply": "5"},
        )
        result = synthesize_led_indicator(inst)

        # R = (5 - 2.0) / 0.01 = 300 -> E24 nearest = 300
        r_val = result.parameters["r_value"]
        num_str = r_val.replace("k\u03a9", "").replace("\u03a9", "")
        num = float(num_str)
        if "k" in r_val:
            num *= 1000
        assert 270 <= num <= 330  # E24: 300

    def test_led_blue_3v3(self) -> None:
        """3.3V 蓝色 LED: Vf=3.0 -> 很小的压降。"""
        inst = _make_instance(
            "led1", "led",
            {"led_color": "blue", "v_supply": "3.3"},
        )
        result = synthesize_led_indicator(inst)
        # R = (3.3 - 3.0) / 0.01 = 30, 但 min 保护 = max(0.3/0.01, 10) = 30
        # E24 nearest of 30 = 30
        assert result.status == ModuleStatus.SYNTHESIZED

    def test_led_drive_voltage_param(self) -> None:
        """drive_voltage 参数传递给函数。"""
        inst = _make_instance("led1", "led", {"led_color": "green"})
        result = synthesize_led_indicator(inst, drive_voltage=5.0)
        # R = (5.0 - 2.2) / 0.01 = 280 -> E24: 270
        assert result.parameters["v_supply"] == "5"

    def test_led_external_components(self) -> None:
        """LED 外围元件只有 led_limit。"""
        inst = _make_instance("led1", "led", {"led_color": "green", "v_supply": "3.3"})
        result = synthesize_led_indicator(inst)
        assert len(result.external_components) == 1
        assert result.external_components[0]["role"] == "led_limit"

    def test_led_custom_current(self) -> None:
        """自定义 LED 电流。"""
        inst = _make_instance(
            "led1", "led",
            {"led_color": "green", "v_supply": "3.3", "led_current": "0.002"},
        )
        result = synthesize_led_indicator(inst)
        # R = (3.3 - 2.2) / 0.002 = 550 -> E24 nearest = 560
        r_val = result.parameters["r_value"]
        num_str = r_val.replace("k\u03a9", "").replace("\u03a9", "")
        num = float(num_str)
        if "k" in r_val:
            num *= 1000
        assert 510 <= num <= 620


# ============================================================
# T066: Generic 模块
# ============================================================


class TestSynthesizeGeneric:
    """T066: 通用占位模块。"""

    def test_generic_synthesized(self) -> None:
        """未知类别模块标记为 SYNTHESIZED。"""
        inst = _make_instance("x1", "sensor")
        result = synthesize_generic_module(inst)
        assert result.status == ModuleStatus.SYNTHESIZED
        assert result.external_components == []

    def test_generic_evidence(self) -> None:
        """通用模块有 evidence 记录。"""
        inst = _make_instance("x1", "unknown_thing")
        result = synthesize_generic_module(inst)
        assert any("通用" in e for e in result.evidence)


# ============================================================
# T067: 供电约束传播
# ============================================================


class TestPropagateSupplyConstraints:
    """T067: 电压域传播。"""

    def test_buck_vout_to_ldo_vin(self) -> None:
        """Buck.v_out=5 传播到 LDO.v_in。"""
        buck = _make_instance("buck1", "buck", {"v_out": "5"})
        ldo = _make_instance("ldo1", "ldo", {"v_in": "12"})  # 旧值
        conn = _make_supply_connection("buck1", "ldo1")
        ir = _make_ir({"buck1": buck, "ldo1": ldo}, [conn])

        result = propagate_supply_constraints(ir)
        assert result.get_module("ldo1").parameters["v_in"] == "5"

    def test_no_propagation_without_connection(self) -> None:
        """无连接时不传播。"""
        buck = _make_instance("buck1", "buck", {"v_out": "5"})
        ldo = _make_instance("ldo1", "ldo", {"v_in": "12"})
        ir = _make_ir({"buck1": buck, "ldo1": ldo})  # 无连接

        result = propagate_supply_constraints(ir)
        assert result.get_module("ldo1").parameters["v_in"] == "12"

    def test_chain_propagation(self) -> None:
        """三级链: buck->ldo1->ldo2 传播。"""
        buck = _make_instance("buck1", "buck", {"v_out": "5"})
        ldo1 = _make_instance("ldo1", "ldo", {"v_in": "12", "v_out": "3.3"})
        ldo2 = _make_instance("ldo2", "ldo", {"v_in": "9"})
        conns = [
            _make_supply_connection("buck1", "ldo1"),
            _make_supply_connection("ldo1", "ldo2"),
        ]
        ir = _make_ir({"buck1": buck, "ldo1": ldo1, "ldo2": ldo2}, conns)

        result = propagate_supply_constraints(ir)
        assert result.get_module("ldo1").parameters["v_in"] == "5"
        assert result.get_module("ldo2").parameters["v_in"] == "3.3"


# ============================================================
# T068: 依赖模块重算
# ============================================================


class TestRecomputeDependentModules:
    """T068: 依赖重算。"""

    def test_recompute_after_buck_change(self) -> None:
        """Buck v_out 变化后，下游 LDO 被重算。"""
        buck = _make_instance("buck1", "buck", {"v_out": "5"})
        buck.status = ModuleStatus.SYNTHESIZED
        ldo = _make_instance("ldo1", "ldo", {"v_in": "12", "v_out": "3.3"})
        ldo.status = ModuleStatus.SYNTHESIZED  # 已综合
        conn = _make_supply_connection("buck1", "ldo1")
        ir = _make_ir({"buck1": buck, "ldo1": ldo}, [conn])

        result = recompute_dependent_modules(ir, {"buck1"})
        # LDO 应被重新综合
        ldo_result = result.get_module("ldo1")
        assert ldo_result.status == ModuleStatus.SYNTHESIZED
        assert ldo_result.parameters["v_in"] == "5"

    def test_unrelated_module_not_recomputed(self) -> None:
        """不相关模块不被重算（C58）。"""
        buck = _make_instance("buck1", "buck", {"v_out": "5"})
        buck.status = ModuleStatus.SYNTHESIZED
        led = _make_instance("led1", "led", {"led_color": "green", "v_supply": "3.3"})
        led.status = ModuleStatus.SYNTHESIZED
        led.evidence = ["原始 evidence"]
        # 无连接 -> led 不依赖 buck
        ir = _make_ir({"buck1": buck, "led1": led})

        result = recompute_dependent_modules(ir, {"buck1"})
        led_result = result.get_module("led1")
        # evidence 不应被改变（未重算）
        assert led_result.evidence == ["原始 evidence"]


# ============================================================
# synthesize_all_modules 集成
# ============================================================


class TestSynthesizeAllModules:
    """synthesize_all_modules 集成测试。"""

    def test_all_resolved_get_synthesized(self) -> None:
        """所有 RESOLVED 模块被综合。"""
        buck = _make_instance("buck1", "buck", {"v_in": "12", "v_out": "5", "i_out": "2"})
        ldo = _make_instance("ldo1", "ldo", {"v_in": "5", "v_out": "3.3"})
        ir = _make_ir({"buck1": buck, "ldo1": ldo})

        result = synthesize_all_modules(ir)
        assert result.get_module("buck1").status == ModuleStatus.SYNTHESIZED
        assert result.get_module("ldo1").status == ModuleStatus.SYNTHESIZED

    def test_pending_skipped(self) -> None:
        """PENDING 模块不被综合。"""
        pending = _make_instance("x1", "buck", status=ModuleStatus.PENDING)
        ir = _make_ir({"x1": pending})

        result = synthesize_all_modules(ir)
        assert result.get_module("x1").status == ModuleStatus.PENDING

    def test_propagation_before_synthesis(self) -> None:
        """综合前先传播电压域。"""
        buck = _make_instance("buck1", "buck", {"v_in": "12", "v_out": "5", "i_out": "2"})
        ldo = _make_instance("ldo1", "ldo", {"v_in": "99", "v_out": "3.3"})
        conn = _make_supply_connection("buck1", "ldo1")
        ir = _make_ir({"buck1": buck, "ldo1": ldo}, [conn])

        result = synthesize_all_modules(ir)
        # LDO v_in 应从 99 变为 5（传播后）
        ldo_result = result.get_module("ldo1")
        assert ldo_result.parameters["v_in"] == "5"

    def test_unknown_category_gets_generic(self) -> None:
        """未知类别走 generic 路径。"""
        inst = _make_instance("sensor1", "thermocouple")
        ir = _make_ir({"sensor1": inst})

        result = synthesize_all_modules(ir)
        assert result.get_module("sensor1").status == ModuleStatus.SYNTHESIZED
        assert result.get_module("sensor1").external_components == []
