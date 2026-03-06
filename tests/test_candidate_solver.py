"""候选方案求解器测试"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path


from schemaforge.core.models import ParameterDef, PinType
from schemaforge.design.candidate_solver import (
    CandidateSolver,
    ScoreDimension,
    SolverResult,
    _count_external_components,
    _estimate_cost,
    _estimate_power,
    _parse_float,
)
from schemaforge.design.planner import ModuleRequirement
from schemaforge.library.models import (
    DeviceModel,
    ExternalComponent,
    SymbolDef,
    SymbolPin,
    TopologyConnection,
    TopologyDef,
)
from schemaforge.library.store import ComponentStore


# ============================================================
# 测试夹具
# ============================================================

SCORE_DIMENSION_NAMES = {
    "constraint_satisfaction",
    "device_match",
    "electrical_reasonability",
    "bom_complexity",
    "thermal_risk",
    "user_preference_match",
}


def _make_ldo_device(part_number: str = "AMS1117-3.3") -> DeviceModel:
    return DeviceModel(
        part_number=part_number,
        manufacturer="AMS",
        description="LDO线性稳压器 3.3V 1A SOT-223",
        category="ldo",
        specs={
            "v_out": "3.3V",
            "v_dropout": "1.1V",
            "i_out_max": "1A",
            "v_in_max": "15V",
        },
        symbol=SymbolDef(
            pins=[
                SymbolPin(
                    name="VIN", pin_number="3", side="left", pin_type=PinType.POWER_IN
                ),
                SymbolPin(
                    name="VOUT",
                    pin_number="2",
                    side="right",
                    pin_type=PinType.POWER_OUT,
                ),
                SymbolPin(
                    name="GND", pin_number="1", side="bottom", pin_type=PinType.GROUND
                ),
            ]
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
                ExternalComponent(
                    role="output_cap",
                    ref_prefix="C",
                    default_value="22uF",
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
                TopologyConnection(
                    net_name="VOUT",
                    device_pin="VOUT",
                    external_refs=["output_cap.1"],
                    is_power=True,
                ),
                TopologyConnection(
                    net_name="GND",
                    device_pin="GND",
                    external_refs=["input_cap.2", "output_cap.2"],
                    is_ground=True,
                ),
            ],
            parameters={
                "v_in": ParameterDef(name="v_in", default="5", unit="V"),
                "c_in": ParameterDef(name="c_in", default="10uF"),
                "c_out": ParameterDef(name="c_out", default="22uF"),
            },
        ),
        package="SOT-223",
        design_roles=["main_regulator", "aux_regulator"],
        selection_hints=["低压差应用", "电池供电设备", "低噪声应用"],
        anti_patterns=["输入输出压差小于1.1V时不适用", "高效率要求场景优先选Buck"],
        failure_modes=["输入电压过低导致掉压", "输出电容ESR过高导致振荡"],
        operating_constraints={"min_v_dropout": "1.1V", "max_power_dissipation": "1W"},
    )


def _make_led_device() -> DeviceModel:
    return DeviceModel(
        part_number="LED_GREEN",
        description="绿色LED指示灯",
        category="led",
        specs={"v_f": "2.1V", "i_f": "20mA", "color": "green"},
        topology=TopologyDef(
            circuit_type="led_driver",
            external_components=[
                ExternalComponent(
                    role="limit_resistor",
                    ref_prefix="R",
                    default_value="330R",
                    schemdraw_element="Resistor",
                ),
            ],
        ),
        selection_hints=["电源指示", "状态指示"],
        anti_patterns=["不可直接接电源，需限流电阻"],
        failure_modes=["过流烧毁LED"],
    )


def _make_voltage_divider_device() -> DeviceModel:
    return DeviceModel(
        part_number="VOLTAGE_DIVIDER",
        description="通用电压分压器",
        category="voltage_divider",
        topology=TopologyDef(
            circuit_type="voltage_divider",
            external_components=[
                ExternalComponent(
                    role="r1",
                    ref_prefix="R",
                    default_value="10k",
                    schemdraw_element="Resistor",
                ),
                ExternalComponent(
                    role="r2",
                    ref_prefix="R",
                    default_value="10k",
                    schemdraw_element="Resistor",
                ),
            ],
        ),
        selection_hints=["电压采样", "ADC输入分压"],
        anti_patterns=["不适合大电流负载"],
    )


def _make_second_ldo_device() -> DeviceModel:
    return DeviceModel(
        part_number="LM1117-3.3",
        manufacturer="TI",
        description="LDO线性稳压器 3.3V 0.8A",
        category="ldo",
        specs={
            "v_out": "3.3V",
            "v_dropout": "1.2V",
            "i_out_max": "0.8A",
            "v_in_max": "15V",
        },
        topology=TopologyDef(
            circuit_type="ldo",
            external_components=[
                ExternalComponent(
                    role="input_cap",
                    ref_prefix="C",
                    default_value="10uF",
                    schemdraw_element="Capacitor",
                ),
                ExternalComponent(
                    role="output_cap",
                    ref_prefix="C",
                    default_value="10uF",
                    schemdraw_element="Capacitor",
                ),
            ],
        ),
        package="SOT-223",
        selection_hints=["低成本应用"],
        anti_patterns=["电流超0.8A时不适用"],
    )


def _make_store(tmp: Path) -> ComponentStore:
    store = ComponentStore(tmp)
    store.save_device(_make_ldo_device())
    store.save_device(_make_led_device())
    store.save_device(_make_voltage_divider_device())
    return store


def _make_store_with_two_ldos(tmp: Path) -> ComponentStore:
    store = ComponentStore(tmp)
    store.save_device(_make_ldo_device())
    store.save_device(_make_second_ldo_device())
    return store


def _ldo_requirement(v_in: str = "5", v_out: str = "3.3") -> ModuleRequirement:
    return ModuleRequirement(
        role="main_regulator",
        category="ldo",
        description="LDO稳压器",
        parameters={"v_in": v_in, "v_out": v_out, "i_out_max": "0.5"},
    )


def _led_requirement() -> ModuleRequirement:
    return ModuleRequirement(
        role="power_led",
        category="led",
        description="LED电源指示灯",
        parameters={"v_supply": "3.3", "led_color": "green"},
    )


def _divider_requirement() -> ModuleRequirement:
    return ModuleRequirement(
        role="voltage_sampler",
        category="voltage_divider",
        description="电压分压采样",
        parameters={"v_in": "5", "v_out": "3.3"},
    )


# ============================================================
# 测试类：基本 LDO 场景
# ============================================================


class TestCandidateSolverLDO:
    def setup_method(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.store = _make_store(self.tmp)
        self.solver = CandidateSolver(self.store, use_mock=True)

    def teardown_method(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ldo_returns_at_least_one_candidate(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        assert len(result.candidates) >= 1

    def test_ldo_candidate_total_score_above_half(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        top = result.candidates[0]
        assert top.total_score > 0.5

    def test_ldo_candidate_has_correct_key_params(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        top = result.candidates[0]
        assert "v_out" in top.key_params
        assert "3.3" in top.key_params["v_out"]

    def test_ldo_candidate_scores_contain_all_six_dimensions(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        top = result.candidates[0]
        dim_names = {s.name for s in top.scores}
        assert dim_names == SCORE_DIMENSION_NAMES

    def test_ldo_total_score_is_weighted_sum(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        top = result.candidates[0]
        expected = sum(s.score * s.weight for s in top.scores)
        assert abs(top.total_score - expected) < 1e-4

    def test_ldo_candidate_device_is_ams1117(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        top = result.candidates[0]
        assert top.device.part_number == "AMS1117-3.3"

    def test_ldo_result_module_role(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        assert result.module_role == "main_regulator"

    def test_ldo_result_module_category(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        assert result.module_category == "ldo"

    def test_ldo_recommended_equals_first_candidate(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        assert result.recommended is result.candidates[0]

    def test_ldo_recommendation_reason_populated(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        assert result.recommendation_reason
        assert len(result.recommendation_reason) > 5

    def test_ldo_estimated_cost_populated(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        top = result.candidates[0]
        assert top.estimated_cost in ("low", "medium", "high")

    def test_ldo_estimated_power_populated(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        top = result.candidates[0]
        assert top.estimated_power

    def test_ldo_bom_complexity_reflects_external_components(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        top = result.candidates[0]
        assert top.bom_complexity == 2

    def test_ldo_risk_summary_populated_from_failure_modes(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        top = result.candidates[0]
        assert top.risk_summary
        assert top.risk_summary != "无已知重大风险"

    def test_ldo_tradeoff_notes_populated_from_anti_patterns(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        top = result.candidates[0]
        assert top.tradeoff_notes
        assert "Buck" in top.tradeoff_notes or "LDO" in top.tradeoff_notes

    def test_ldo_suitable_for_from_selection_hints(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        top = result.candidates[0]
        assert len(top.suitable_for) >= 1
        hints_combined = " ".join(top.suitable_for)
        assert "应用" in hints_combined or "设备" in hints_combined


# ============================================================
# 测试类：LED 场景
# ============================================================


class TestCandidateSolverLED:
    def setup_method(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.store = _make_store(self.tmp)
        self.solver = CandidateSolver(self.store, use_mock=True)

    def teardown_method(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_led_returns_candidate(self) -> None:
        result = self.solver.solve(_led_requirement())
        assert len(result.candidates) >= 1

    def test_led_candidate_bom_complexity_ge_one(self) -> None:
        result = self.solver.solve(_led_requirement())
        top = result.candidates[0]
        assert top.bom_complexity >= 1

    def test_led_module_category(self) -> None:
        result = self.solver.solve(_led_requirement())
        assert result.module_category == "led"

    def test_led_candidate_has_all_score_dimensions(self) -> None:
        result = self.solver.solve(_led_requirement())
        top = result.candidates[0]
        dim_names = {s.name for s in top.scores}
        assert dim_names == SCORE_DIMENSION_NAMES

    def test_led_estimated_cost_is_low(self) -> None:
        result = self.solver.solve(_led_requirement())
        top = result.candidates[0]
        assert top.estimated_cost == "low"


# ============================================================
# 测试类：电压分压器场景
# ============================================================


class TestCandidateSolverVoltageDivider:
    def setup_method(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.store = _make_store(self.tmp)
        self.solver = CandidateSolver(self.store, use_mock=True)

    def teardown_method(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_divider_returns_candidate(self) -> None:
        result = self.solver.solve(_divider_requirement())
        assert len(result.candidates) >= 1

    def test_divider_bom_complexity_is_two(self) -> None:
        result = self.solver.solve(_divider_requirement())
        top = result.candidates[0]
        assert top.bom_complexity == 2

    def test_divider_module_category(self) -> None:
        result = self.solver.solve(_divider_requirement())
        assert result.module_category == "voltage_divider"


# ============================================================
# 测试类：多候选和排序
# ============================================================


class TestCandidateSolverMultiple:
    def setup_method(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.store = _make_store_with_two_ldos(self.tmp)
        self.solver = CandidateSolver(self.store, use_mock=True)

    def teardown_method(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_multiple_devices_return_multiple_candidates(self) -> None:
        result = self.solver.solve(_ldo_requirement(), max_candidates=3)
        assert len(result.candidates) >= 2

    def test_candidates_sorted_by_total_score_descending(self) -> None:
        result = self.solver.solve(_ldo_requirement(), max_candidates=3)
        scores = [c.total_score for c in result.candidates]
        assert scores == sorted(scores, reverse=True)

    def test_recommended_is_highest_scored(self) -> None:
        result = self.solver.solve(_ldo_requirement(), max_candidates=3)
        assert result.recommended is not None
        assert result.recommended.total_score == max(
            c.total_score for c in result.candidates
        )


# ============================================================
# 测试类：空 store
# ============================================================


class TestCandidateSolverEmptyStore:
    def setup_method(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.store = ComponentStore(self.tmp)
        self.solver = CandidateSolver(self.store, use_mock=True)

    def teardown_method(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_store_returns_solver_result(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        assert isinstance(result, SolverResult)

    def test_empty_store_returns_empty_candidates(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        assert result.candidates == []

    def test_empty_store_recommended_is_none(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        assert result.recommended is None


# ============================================================
# 测试类：评分权重
# ============================================================


class TestScoreWeights:
    def setup_method(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        store = _make_store(self.tmp)
        self.solver = CandidateSolver(store, use_mock=True)

    def teardown_method(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_score_dimension_weights_sum_to_one(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        top = result.candidates[0]
        total_weight = sum(s.weight for s in top.scores)
        assert abs(total_weight - 1.0) < 1e-9

    def test_each_score_dimension_in_0_to_1(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        top = result.candidates[0]
        for dim in top.scores:
            assert 0.0 <= dim.score <= 1.0, (
                f"维度 {dim.name} 得分 {dim.score} 超出 [0, 1]"
            )

    def test_total_score_in_0_to_1(self) -> None:
        result = self.solver.solve(_ldo_requirement())
        top = result.candidates[0]
        assert 0.0 <= top.total_score <= 1.0


# ============================================================
# 测试类：辅助函数
# ============================================================


class TestHelperFunctions:
    def test_parse_float_voltage(self) -> None:
        assert _parse_float("3.3V") == 3.3

    def test_parse_float_current(self) -> None:
        assert _parse_float("1A") == 1.0

    def test_parse_float_plain(self) -> None:
        assert _parse_float("5") == 5.0

    def test_parse_float_empty(self) -> None:
        assert _parse_float("") is None

    def test_parse_float_no_number(self) -> None:
        assert _parse_float("abc") is None

    def test_count_external_components_no_topology(self) -> None:
        device = DeviceModel(part_number="X", category="ldo")
        assert _count_external_components(device) == 0

    def test_count_external_components_two(self) -> None:
        device = _make_ldo_device()
        assert _count_external_components(device) == 2

    def test_estimate_cost_led_is_low(self) -> None:
        device = _make_led_device()
        assert _estimate_cost(device, "led", 1) == "low"

    def test_estimate_cost_ldo_minimal_bom_is_low(self) -> None:
        device = _make_ldo_device()
        assert _estimate_cost(device, "ldo", 2) == "low"

    def test_estimate_power_ldo(self) -> None:
        device = _make_ldo_device()
        result = _estimate_power(
            device, "ldo", {"v_in": "5", "v_out": "3.3", "i_out_max": "1"}
        )
        assert "W" in result
        assert "1.70" in result

    def test_estimate_power_led(self) -> None:
        device = _make_led_device()
        result = _estimate_power(device, "led", {})
        assert "0.1" in result


# ============================================================
# 测试类：数据模型结构
# ============================================================


class TestDataModels:
    def test_score_dimension_fields(self) -> None:
        dim = ScoreDimension(
            name="constraint_satisfaction",
            score=0.8,
            weight=0.30,
            detail="测试",
        )
        assert dim.name == "constraint_satisfaction"
        assert dim.score == 0.8
        assert dim.weight == 0.30
        assert dim.detail == "测试"

    def test_solver_result_fields(self) -> None:
        result = SolverResult(
            module_role="main_regulator",
            module_category="ldo",
            candidates=[],
            recommended=None,
        )
        assert result.module_role == "main_regulator"
        assert result.candidates == []
        assert result.recommended is None
        assert result.recommendation_reason == ""

    def test_candidate_solution_name_format(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            store = _make_store(tmp)
            solver = CandidateSolver(store, use_mock=True)
            result = solver.solve(_ldo_requirement())
            top = result.candidates[0]
            assert "AMS1117-3.3" in top.name
            assert "LDO" in top.name or "ldo" in top.name.lower()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
