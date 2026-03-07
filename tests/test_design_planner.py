"""设计规划器测试"""

from __future__ import annotations

from schemaforge.design.planner import (
    DesignPlanner,
    ModuleRequirement,
    _extract_voltages,
)


class TestDesignPlanner:
    """规划器 Mock 模式测试"""

    def setup_method(self) -> None:
        self.planner = DesignPlanner(use_mock=True)

    # --- LDO 识别 ---

    def test_ldo_basic(self) -> None:
        plan = self.planner.plan("5V转3.3V稳压电路")
        assert len(plan.modules) >= 1
        ldo_mod = plan.modules[0]
        assert ldo_mod.category == "ldo"
        assert ldo_mod.parameters.get("v_in") == "5"
        assert ldo_mod.parameters.get("v_out") == "3.3"

    def test_ldo_english(self) -> None:
        plan = self.planner.plan("LDO 12V to 3.3V")
        assert len(plan.modules) >= 1
        assert plan.modules[0].category == "ldo"
        assert plan.modules[0].parameters.get("v_in") == "12"
        assert plan.modules[0].parameters.get("v_out") == "3.3"

    # --- LED 识别 ---

    def test_led_standalone(self) -> None:
        plan = self.planner.plan("绿色LED指示灯")
        assert any(m.category == "led" for m in plan.modules)

    def test_led_with_ldo(self) -> None:
        plan = self.planner.plan("5V转3.3V稳压电路，带绿色LED指示灯")
        cats = [m.category for m in plan.modules]
        assert "ldo" in cats
        assert "led" in cats
        # LED 应该连接到 LDO
        led_mod = next(m for m in plan.modules if m.category == "led")
        assert len(led_mod.connections_to) > 0

    def test_led_color_extraction(self) -> None:
        plan = self.planner.plan("红色LED指示灯")
        led_mod = next(m for m in plan.modules if m.category == "led")
        assert led_mod.parameters.get("led_color") == "red"

    # --- 分压器 ---

    def test_divider(self) -> None:
        plan = self.planner.plan("12V到3.3V分压采样电路")
        assert len(plan.modules) >= 1
        assert plan.modules[0].category == "voltage_divider"

    # --- RC 滤波 ---

    def test_rc_filter(self) -> None:
        plan = self.planner.plan("1kHz RC滤波器")
        assert any(m.category == "rc_filter" for m in plan.modules)

    # --- Buck ---

    def test_buck(self) -> None:
        plan = self.planner.plan("12V到3.3V Buck降压电路")
        assert plan.modules[0].category == "buck"

    # --- 料号+类别合并 ---

    def test_part_number_merged_with_buck_category(self) -> None:
        """用 TPS54202 搭 DCDC → 应生成 1 个模块，category=buck，part_number=TPS54202"""
        plan = self.planner.plan("用 TPS54202 搭一个 20V转5V的DCDC电路")
        # 只有 1 个主模块（料号与电路类别合并，不拆成两个模块）
        regulator_modules = [
            m for m in plan.modules if m.role == "main_regulator"
        ]
        assert len(regulator_modules) == 1
        mod = regulator_modules[0]
        assert mod.part_number == "TPS54202"
        assert mod.category == "buck"
        assert mod.parameters.get("v_in") == "20"
        assert mod.parameters.get("v_out") == "5"

    def test_part_number_merged_with_ldo_category(self) -> None:
        """指定料号 + 稳压 → 应合并为 1 个 ldo 模块"""
        plan = self.planner.plan("用 AMS1117 做一个 5V转3.3V稳压电路")
        regulator_modules = [
            m for m in plan.modules if m.role == "main_regulator"
        ]
        assert len(regulator_modules) == 1
        mod = regulator_modules[0]
        assert mod.part_number == "AMS1117"
        assert mod.category == "ldo"

    def test_part_number_unknown_category_falls_back_to_other(self) -> None:
        """料号无法推断类别时 category=other"""
        plan = self.planner.plan("用 W25Q32JV 做一个电路")
        pn_modules = [m for m in plan.modules if m.part_number == "W25Q32JV"]
        assert len(pn_modules) == 1
        assert pn_modules[0].category == "other"

    # --- 回退 ---

    def test_fallback_to_ldo(self) -> None:
        plan = self.planner.plan("做个电路")
        assert len(plan.modules) >= 1
        # 默认回退到 LDO
        assert plan.modules[0].category == "ldo"

    # --- DesignPlan 属性 ---

    def test_plan_name(self) -> None:
        plan = self.planner.plan("5V转3.3V稳压电路")
        assert plan.name  # 非空

    def test_plan_to_dict(self) -> None:
        plan = self.planner.plan("5V转3.3V稳压电路")
        d = plan.to_dict()
        assert "name" in d
        assert "modules" in d
        assert isinstance(d["modules"], list)

    def test_plan_raw_input(self) -> None:
        plan = self.planner.plan("测试输入")
        assert plan.raw_input == "测试输入"


class TestModuleRequirement:
    """ModuleRequirement 测试"""

    def test_to_device_requirement(self) -> None:
        req = ModuleRequirement(
            role="main_regulator",
            category="ldo",
            description="LDO稳压器",
            parameters={"v_in": "5", "v_out": "3.3", "c_in": "10uF"},
        )
        dev_req = req.to_device_requirement()
        assert dev_req.role == "main_regulator"
        assert dev_req.category == "ldo"
        # ldo 属于有拓扑定义的分类，所以 must_have_topology=True
        assert dev_req.must_have_topology is True
        # c_in 不在 spec_keys 中，不应出现
        assert "c_in" not in dev_req.specs
        assert dev_req.specs.get("v_out") == "3.3"

    def test_to_device_requirement_no_topology_for_other(self) -> None:
        """category='other' 的器件不应要求拓扑定义"""
        req = ModuleRequirement(
            role="user_specified",
            category="other",
            description="用户指定器件",
            part_number="W25Q32JV",
        )
        dev_req = req.to_device_requirement()
        assert dev_req.must_have_topology is False
        assert dev_req.part_number == "W25Q32JV"

    def test_to_device_requirement_no_topology_for_memory(self) -> None:
        """category='memory' 的器件不应要求拓扑定义"""
        req = ModuleRequirement(
            role="flash_storage",
            category="memory",
            description="SPI Flash存储",
        )
        dev_req = req.to_device_requirement()
        assert dev_req.must_have_topology is False

    def test_to_dict(self) -> None:
        req = ModuleRequirement(role="test", category="ldo")
        d = req.to_dict()
        assert d["role"] == "test"
        assert d["category"] == "ldo"


class TestExtractVoltages:
    """电压提取测试"""

    def test_chinese_format(self) -> None:
        v_in, v_out = _extract_voltages("5V转3.3V")
        assert v_in == "5"
        assert v_out == "3.3"

    def test_arrow_format(self) -> None:
        v_in, v_out = _extract_voltages("12V→3.3V")
        assert v_in == "12"
        assert v_out == "3.3"

    def test_english_format(self) -> None:
        v_in, v_out = _extract_voltages("12V to 3.3V")
        assert v_in == "12"
        assert v_out == "3.3"

    def test_input_output_format(self) -> None:
        v_in, v_out = _extract_voltages("输入5V 输出3.3V")
        assert v_in == "5"
        assert v_out == "3.3"

    def test_no_voltage(self) -> None:
        v_in, v_out = _extract_voltages("做一个电路")
        assert v_in == ""
        assert v_out == ""

    def test_decimal(self) -> None:
        v_in, v_out = _extract_voltages("3.3V到1.8V")
        assert v_in == "3.3"
        assert v_out == "1.8"
