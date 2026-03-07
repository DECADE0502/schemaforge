"""Tests for DesignReviewEngine — engineering-level design review layer."""

from __future__ import annotations

import pytest

from schemaforge.design.ir import (
    DesignReview,
    ModuleReview,
    ReviewSeverity,
)
from schemaforge.design.review import (
    DesignReviewEngine,
    ModuleReviewInput,
    _parse_numeric,
)
from schemaforge.library.models import DeviceModel, ExternalComponent, TopologyDef


# ============================================================
# Fixtures
# ============================================================


def make_ldo_device(
    part_number: str = "AMS1117-3.3",
    v_out: str = "3.3V",
    v_dropout: str = "1.1V",
    i_out_max: str = "1A",
    v_in_max: str = "15V",
    with_input_cap: bool = True,
    with_output_cap: bool = True,
) -> DeviceModel:
    external = []
    if with_input_cap:
        external.append(
            ExternalComponent(role="input_cap", ref_prefix="C", default_value="10uF")
        )
    if with_output_cap:
        external.append(
            ExternalComponent(role="output_cap", ref_prefix="C", default_value="22uF")
        )

    return DeviceModel(
        part_number=part_number,
        category="ldo",
        specs={
            "v_out": v_out,
            "v_dropout": v_dropout,
            "i_out_max": i_out_max,
            "v_in_max": v_in_max,
        },
        topology=TopologyDef(
            circuit_type="ldo",
            external_components=external,
        ),
    )


def make_led_device(part_number: str = "LED-RED") -> DeviceModel:
    return DeviceModel(
        part_number=part_number,
        category="led",
        specs={"v_f": "2.0V", "i_max": "20mA"},
        topology=TopologyDef(
            circuit_type="led_driver",
            external_components=[
                ExternalComponent(role="r_limit", ref_prefix="R", default_value="120R"),
            ],
        ),
    )


def make_divider_device(part_number: str = "VDIV-001") -> DeviceModel:
    return DeviceModel(
        part_number=part_number,
        category="voltage_divider",
        specs={},
        topology=TopologyDef(
            circuit_type="voltage_divider",
            external_components=[
                ExternalComponent(role="r_top", ref_prefix="R", default_value="10k"),
                ExternalComponent(role="r_bot", ref_prefix="R", default_value="10k"),
            ],
        ),
    )


@pytest.fixture
def engine() -> DesignReviewEngine:
    return DesignReviewEngine()


@pytest.fixture
def ldo_device() -> DeviceModel:
    return make_ldo_device()


@pytest.fixture
def led_device() -> DeviceModel:
    return make_led_device()


# ============================================================
# _parse_numeric helper tests
# ============================================================


def test_parse_numeric_plain_float():
    assert _parse_numeric("3.3") == pytest.approx(3.3)


def test_parse_numeric_with_volt_unit():
    assert _parse_numeric("3.3V") == pytest.approx(3.3)


def test_parse_numeric_milliamps():
    assert _parse_numeric("500mA") == pytest.approx(0.5)


def test_parse_numeric_microfarads():
    assert _parse_numeric("10uF") == pytest.approx(10e-6)


def test_parse_numeric_kilo():
    assert _parse_numeric("4.7k") == pytest.approx(4700.0)


def test_parse_numeric_mega():
    assert _parse_numeric("1M") == pytest.approx(1e6)


def test_parse_numeric_empty_returns_none():
    assert _parse_numeric("") is None


def test_parse_numeric_non_numeric_returns_none():
    assert _parse_numeric("abc") is None


# ============================================================
# LDO: dropout margin
# ============================================================


def test_ldo_dropout_margin_satisfied_no_blocking(engine, ldo_device):
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "5V", "v_out": "3.3V", "i_out": "0.5A"},
    )
    result = engine.review_module(module)
    blocking = [
        i
        for i in result.issues
        if i.severity == ReviewSeverity.BLOCKING and i.rule_id == "ldo_dropout_margin"
    ]
    assert blocking == []


def test_ldo_dropout_margin_violated_blocking(engine):
    device = make_ldo_device(v_dropout="1.1V")
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=device,
        parameters={"v_in": "3.5V", "v_out": "3.3V", "i_out": "0.1A"},
    )
    result = engine.review_module(module)
    blocking = [i for i in result.issues if i.rule_id == "ldo_dropout_margin"]
    assert len(blocking) == 1
    assert blocking[0].severity == ReviewSeverity.BLOCKING


def test_ldo_dropout_margin_violated_sets_passed_false(engine):
    device = make_ldo_device(v_dropout="1.1V")
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=device,
        parameters={"v_in": "3.5V", "v_out": "3.3V", "i_out": "0.1A"},
    )
    result = engine.review_module(module)
    assert result.passed is False
    assert result.has_blocking is True


# ============================================================
# LDO: thermal dissipation
# ============================================================


def test_ldo_thermal_blocking_high_power(engine, ldo_device):
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "12V", "v_out": "3.3V", "i_out": "1A"},
    )
    result = engine.review_module(module)
    thermal_blocking = [
        i
        for i in result.issues
        if i.rule_id == "ldo_thermal_dissipation"
        and i.severity == ReviewSeverity.BLOCKING
    ]
    assert len(thermal_blocking) == 1
    assert (
        "8.7" in thermal_blocking[0].evidence or "8.70" in thermal_blocking[0].evidence
    )


def test_ldo_thermal_warning_moderate_power(engine, ldo_device):
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "5V", "v_out": "3.3V", "i_out": "0.3A"},
    )
    result = engine.review_module(module)
    thermal_warnings = [
        i
        for i in result.issues
        if i.rule_id == "ldo_thermal_dissipation"
        and i.severity == ReviewSeverity.WARNING
    ]
    assert len(thermal_warnings) == 1


def test_ldo_thermal_no_issue_low_power(engine, ldo_device):
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "3.6V", "v_out": "3.3V", "i_out": "0.1A"},
    )
    result = engine.review_module(module)
    thermal_issues = [
        i for i in result.issues if i.rule_id == "ldo_thermal_dissipation"
    ]
    assert thermal_issues == []


# ============================================================
# LDO: max vin exceeded
# ============================================================


def test_ldo_max_vin_exceeded_blocking(engine):
    device = make_ldo_device(v_in_max="15V")
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=device,
        parameters={"v_in": "20V", "v_out": "3.3V", "i_out": "0.1A"},
    )
    result = engine.review_module(module)
    vin_issues = [i for i in result.issues if i.rule_id == "ldo_max_vin_exceeded"]
    assert len(vin_issues) == 1
    assert vin_issues[0].severity == ReviewSeverity.BLOCKING


def test_ldo_max_vin_not_exceeded_no_issue(engine, ldo_device):
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "5V", "v_out": "3.3V", "i_out": "0.1A"},
    )
    result = engine.review_module(module)
    vin_issues = [i for i in result.issues if i.rule_id == "ldo_max_vin_exceeded"]
    assert vin_issues == []


# ============================================================
# LDO: capacitor presence
# ============================================================


def test_ldo_input_cap_present_no_warning(engine, ldo_device):
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "5V", "v_out": "3.3V"},
    )
    result = engine.review_module(module)
    cap_issues = [i for i in result.issues if i.rule_id == "ldo_input_cap_present"]
    assert cap_issues == []


def test_ldo_input_cap_missing_warning(engine):
    device = make_ldo_device(with_input_cap=False, with_output_cap=True)
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=device,
        parameters={"v_in": "5V", "v_out": "3.3V"},
    )
    result = engine.review_module(module)
    cap_issues = [i for i in result.issues if i.rule_id == "ldo_input_cap_present"]
    assert len(cap_issues) == 1
    assert cap_issues[0].severity == ReviewSeverity.WARNING


def test_ldo_output_cap_missing_warning(engine):
    device = make_ldo_device(with_input_cap=True, with_output_cap=False)
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=device,
        parameters={"v_in": "5V", "v_out": "3.3V"},
    )
    result = engine.review_module(module)
    cap_issues = [i for i in result.issues if i.rule_id == "ldo_output_cap_present"]
    assert len(cap_issues) == 1
    assert cap_issues[0].severity == ReviewSeverity.WARNING


def test_ldo_output_cap_esr_recommendation(engine, ldo_device):
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "5V", "v_out": "3.3V"},
    )
    result = engine.review_module(module)
    esr_issues = [i for i in result.issues if i.rule_id == "ldo_output_cap_esr"]
    assert len(esr_issues) == 1
    assert esr_issues[0].severity == ReviewSeverity.RECOMMENDATION
    assert "ESR" in esr_issues[0].message


# ============================================================
# LED rules
# ============================================================


def test_led_current_excessive_warning(engine, led_device):
    module = ModuleReviewInput(
        role="power_led",
        category="led",
        device=led_device,
        parameters={"v_supply": "3.3V", "led_vf": "2.0V", "led_current": "30mA"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "led_current_excessive"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.WARNING


def test_led_supply_too_low_blocking(engine, led_device):
    module = ModuleReviewInput(
        role="power_led",
        category="led",
        device=led_device,
        parameters={"v_supply": "1.5V", "led_vf": "2.0V", "led_current": "10mA"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "led_supply_too_low"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.BLOCKING
    assert result.passed is False


def test_led_normal_no_blocking(engine, led_device):
    module = ModuleReviewInput(
        role="power_led",
        category="led",
        device=led_device,
        parameters={"v_supply": "3.3V", "led_vf": "2.0V", "led_current": "10mA"},
    )
    result = engine.review_module(module)
    blocking = [i for i in result.issues if i.severity == ReviewSeverity.BLOCKING]
    assert blocking == []
    assert result.passed is True


def test_led_resistor_power_warning(engine, led_device):
    module = ModuleReviewInput(
        role="power_led",
        category="led",
        device=led_device,
        parameters={"v_supply": "12V", "led_vf": "2.0V", "led_current": "30mA"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "led_resistor_power"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.WARNING


# ============================================================
# Voltage divider rules
# ============================================================


def test_divider_load_impedance_recommendation(engine):
    device = make_divider_device()
    module = ModuleReviewInput(
        role="vdiv",
        category="voltage_divider",
        device=device,
        parameters={"v_in": "5V", "r_top": "10k", "r_bot": "10k"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "divider_load_impedance"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.RECOMMENDATION


def test_divider_current_excessive_warning(engine):
    device = make_divider_device()
    module = ModuleReviewInput(
        role="vdiv",
        category="voltage_divider",
        device=device,
        parameters={"v_in": "5V", "r_top": "100", "r_bot": "100"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "divider_current_excessive"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.WARNING


# ============================================================
# Layout notes
# ============================================================


def test_ldo_layout_note_generated(engine, ldo_device):
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "5V", "v_out": "3.3V"},
    )
    result = engine.review_module(module)
    layout = [i for i in result.issues if i.severity == ReviewSeverity.LAYOUT_NOTE]
    assert len(layout) >= 1
    rule_ids = [i.rule_id for i in layout]
    assert "ldo_layout_caps_close" in rule_ids


def test_led_layout_note_generated(engine, led_device):
    module = ModuleReviewInput(
        role="power_led",
        category="led",
        device=led_device,
        parameters={"v_supply": "3.3V", "led_vf": "2.0V", "led_current": "10mA"},
    )
    result = engine.review_module(module)
    layout = [i for i in result.issues if i.severity == ReviewSeverity.LAYOUT_NOTE]
    assert len(layout) >= 1
    rule_ids = [i.rule_id for i in layout]
    assert "led_layout_resistor_close" in rule_ids


# ============================================================
# Bringup notes
# ============================================================


def test_ldo_bringup_note_generated(engine, ldo_device):
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "5V", "v_out": "3.3V"},
    )
    result = engine.review_module(module)
    bringup = [i for i in result.issues if i.severity == ReviewSeverity.BRINGUP_NOTE]
    assert len(bringup) >= 1
    rule_ids = [i.rule_id for i in bringup]
    assert "ldo_bringup_startup" in rule_ids


def test_led_bringup_note_generated(engine, led_device):
    module = ModuleReviewInput(
        role="power_led",
        category="led",
        device=led_device,
        parameters={"v_supply": "3.3V", "led_vf": "2.0V", "led_current": "10mA"},
    )
    result = engine.review_module(module)
    bringup = [i for i in result.issues if i.severity == ReviewSeverity.BRINGUP_NOTE]
    assert len(bringup) >= 1
    rule_ids = [i.rule_id for i in bringup]
    assert "led_bringup_polarity" in rule_ids


# ============================================================
# Cross-module checks
# ============================================================


def test_cross_module_power_budget_warning(engine):
    ldo1 = make_ldo_device("LDO1")
    ldo2 = make_ldo_device("LDO2")
    modules = [
        ModuleReviewInput(
            role="ldo1",
            category="ldo",
            device=ldo1,
            parameters={"v_in": "12V", "v_out": "3.3V", "i_out": "1A"},
        ),
        ModuleReviewInput(
            role="ldo2",
            category="ldo",
            device=ldo2,
            parameters={"v_in": "12V", "v_out": "5V", "i_out": "1A"},
        ),
    ]
    review = engine.review_design(modules)
    budget_issues = [i for i in review.issues if i.rule_id == "power_budget_check"]
    assert len(budget_issues) == 1
    assert budget_issues[0].severity == ReviewSeverity.WARNING


def test_cross_module_ground_path_recommendation(engine, ldo_device):
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "5V", "v_out": "3.3V", "i_out": "0.1A"},
    )
    review = engine.review_design([module])
    gnd_issues = [i for i in review.issues if i.rule_id == "ground_path_check"]
    assert len(gnd_issues) == 1
    assert gnd_issues[0].severity == ReviewSeverity.RECOMMENDATION


def test_cross_module_no_power_warning_low_power(engine, ldo_device):
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "5V", "v_out": "3.3V", "i_out": "0.1A"},
    )
    review = engine.review_design([module])
    budget_issues = [i for i in review.issues if i.rule_id == "power_budget_check"]
    assert budget_issues == []


# ============================================================
# Empty module list
# ============================================================


def test_review_design_empty_modules_no_issues(engine):
    review = engine.review_design([])
    assert review.overall_passed is True
    assert review.issues == []


# ============================================================
# Return types and structure
# ============================================================


def test_review_module_returns_module_review_type(engine, ldo_device):
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "5V", "v_out": "3.3V"},
    )
    result = engine.review_module(module)
    assert isinstance(result, ModuleReview)


def test_review_design_returns_design_review_type(engine, ldo_device):
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "5V", "v_out": "3.3V"},
    )
    review = engine.review_design([module])
    assert isinstance(review, DesignReview)


def test_module_review_passed_reflects_blocking(engine):
    device = make_ldo_device(v_dropout="1.1V")
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=device,
        parameters={"v_in": "3.5V", "v_out": "3.3V", "i_out": "0.1A"},
    )
    result = engine.review_module(module)
    assert result.has_blocking is True
    assert result.passed is False


def test_module_review_passed_true_no_blocking(engine, ldo_device):
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "5V", "v_out": "3.3V", "i_out": "0.1A"},
    )
    result = engine.review_module(module)
    assert result.has_blocking is False
    assert result.passed is True


def test_design_review_overall_passed_reflects_blocking(engine):
    device = make_ldo_device(v_dropout="1.1V")
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=device,
        parameters={"v_in": "3.5V", "v_out": "3.3V", "i_out": "0.1A"},
    )
    review = engine.review_design([module])
    assert review.overall_passed is False


def test_design_review_overall_passed_true_when_no_blocking(engine, ldo_device):
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "5V", "v_out": "3.3V", "i_out": "0.1A"},
    )
    review = engine.review_design([module])
    assert review.overall_passed is True


def test_all_rule_ids_non_empty(engine, ldo_device):
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "5V", "v_out": "3.3V", "i_out": "0.3A"},
    )
    result = engine.review_module(module)
    for issue in result.issues:
        assert issue.rule_id != "", f"Empty rule_id found: {issue}"


def test_all_messages_non_empty_chinese_strings(engine, ldo_device):
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "5V", "v_out": "3.3V", "i_out": "0.3A"},
    )
    result = engine.review_module(module)
    for issue in result.issues:
        assert issue.message != "", f"Empty message found: {issue}"
        has_chinese = any("\u4e00" <= c <= "\u9fff" for c in issue.message)
        assert has_chinese, f"Non-Chinese message: {issue.message}"


def test_review_returns_correct_severity_counts(engine, ldo_device):
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "5V", "v_out": "3.3V", "i_out": "0.3A"},
    )
    result = engine.review_module(module)
    assert len(result.issues) > 0
    blocking_count = sum(
        1 for i in result.issues if i.severity == ReviewSeverity.BLOCKING
    )
    warning_count = sum(
        1 for i in result.issues if i.severity == ReviewSeverity.WARNING
    )
    rec_count = sum(
        1 for i in result.issues if i.severity == ReviewSeverity.RECOMMENDATION
    )
    layout_count = sum(
        1 for i in result.issues if i.severity == ReviewSeverity.LAYOUT_NOTE
    )
    bringup_count = sum(
        1 for i in result.issues if i.severity == ReviewSeverity.BRINGUP_NOTE
    )
    total = blocking_count + warning_count + rec_count + layout_count + bringup_count
    assert total == len(result.issues)


def test_module_role_set_on_issues(engine, ldo_device):
    module = ModuleReviewInput(
        role="my_ldo_role",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "5V", "v_out": "3.3V"},
    )
    result = engine.review_module(module)
    for issue in result.issues:
        assert issue.module_role == "my_ldo_role"


def test_design_review_has_reviewed_at_timestamp(engine, ldo_device):
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "5V", "v_out": "3.3V"},
    )
    review = engine.review_design([module])
    assert review.reviewed_at != ""


def test_ldo_thermal_dissipation_evidence_contains_formula(engine, ldo_device):
    module = ModuleReviewInput(
        role="main_ldo",
        category="ldo",
        device=ldo_device,
        parameters={"v_in": "12V", "v_out": "3.3V", "i_out": "1A"},
    )
    result = engine.review_module(module)
    thermal = [i for i in result.issues if i.rule_id == "ldo_thermal_dissipation"]
    assert len(thermal) >= 1
    assert "P" in thermal[0].evidence


def test_review_design_aggregates_all_module_issues(engine):
    ldo_dev = make_ldo_device()
    led_dev = make_led_device()
    modules = [
        ModuleReviewInput(
            role="main_ldo",
            category="ldo",
            device=ldo_dev,
            parameters={"v_in": "5V", "v_out": "3.3V", "i_out": "0.1A"},
        ),
        ModuleReviewInput(
            role="power_led",
            category="led",
            device=led_dev,
            parameters={"v_supply": "3.3V", "led_vf": "2.0V", "led_current": "10mA"},
        ),
    ]
    review = engine.review_design(modules)
    ldo_issues = [i for i in review.issues if i.module_role == "main_ldo"]
    led_issues = [i for i in review.issues if i.module_role == "power_led"]
    assert len(ldo_issues) > 0
    assert len(led_issues) > 0


# ============================================================
# Fixtures for new categories
# ============================================================


def make_boost_device(
    part_number: str = "TPS61021A",
    v_in_max: str = "5.5V",
    fsw: str = "500k",
) -> DeviceModel:
    return DeviceModel(
        part_number=part_number,
        category="boost",
        specs={"v_in_max": v_in_max, "fsw": fsw},
        topology=TopologyDef(
            circuit_type="boost",
            external_components=[
                ExternalComponent(role="inductor", ref_prefix="L", default_value="22uH"),
                ExternalComponent(role="output_cap", ref_prefix="C", default_value="47uF"),
                ExternalComponent(role="input_cap", ref_prefix="C", default_value="10uF"),
            ],
        ),
    )


def make_flyback_device(
    part_number: str = "TNY288PG",
    v_in_max: str = "700V",
    fsw: str = "132k",
    v_isolation: str = "3750V",
) -> DeviceModel:
    external = [
        ExternalComponent(role="transformer", ref_prefix="T", default_value=""),
        ExternalComponent(role="output_cap", ref_prefix="C", default_value="470uF"),
    ]
    return DeviceModel(
        part_number=part_number,
        category="flyback",
        specs={"v_in_max": v_in_max, "fsw": fsw, "v_isolation": v_isolation},
        topology=TopologyDef(
            circuit_type="flyback",
            external_components=external,
        ),
    )


def make_flyback_device_no_snubber(
    part_number: str = "TNY288PG-NS",
) -> DeviceModel:
    """Flyback without snubber for testing snubber detection."""
    return DeviceModel(
        part_number=part_number,
        category="flyback",
        specs={"v_in_max": "700V", "fsw": "132k"},
        topology=TopologyDef(
            circuit_type="flyback",
            external_components=[
                ExternalComponent(role="transformer", ref_prefix="T", default_value=""),
            ],
        ),
    )


def make_sepic_device(
    part_number: str = "LM3478",
    v_in_max: str = "40V",
    fsw: str = "300k",
) -> DeviceModel:
    return DeviceModel(
        part_number=part_number,
        category="sepic",
        specs={"v_in_max": v_in_max, "fsw": fsw},
        topology=TopologyDef(
            circuit_type="sepic",
            external_components=[
                ExternalComponent(role="inductor_1", ref_prefix="L", default_value="22uH"),
                ExternalComponent(role="inductor_2", ref_prefix="L", default_value="22uH"),
                ExternalComponent(role="coupling_cap", ref_prefix="C", default_value="1uF"),
                ExternalComponent(role="output_cap", ref_prefix="C", default_value="47uF"),
            ],
        ),
    )


def make_opamp_device(
    part_number: str = "OPA2277",
    v_cm_min: str = "-12V",
    v_cm_max: str = "12V",
    gbw: str = "1M",
) -> DeviceModel:
    return DeviceModel(
        part_number=part_number,
        category="opamp",
        specs={"v_cm_min": v_cm_min, "v_cm_max": v_cm_max, "gbw": gbw},
        topology=TopologyDef(
            circuit_type="opamp",
            external_components=[
                ExternalComponent(role="bypass_cap", ref_prefix="C", default_value="100nF"),
                ExternalComponent(role="r_feedback", ref_prefix="R", default_value="100k"),
                ExternalComponent(role="r_input", ref_prefix="R", default_value="10k"),
            ],
        ),
    )


@pytest.fixture
def boost_device() -> DeviceModel:
    return make_boost_device()


@pytest.fixture
def flyback_device() -> DeviceModel:
    return make_flyback_device()


@pytest.fixture
def sepic_device() -> DeviceModel:
    return make_sepic_device()


@pytest.fixture
def opamp_device() -> DeviceModel:
    return make_opamp_device()


# ============================================================
# Boost: vin/vout relation
# ============================================================


def test_boost_vin_vout_relation_violated_blocking(engine, boost_device):
    """Boost: V_in >= V_out should be BLOCKING."""
    module = ModuleReviewInput(
        role="boost_5v",
        category="boost",
        device=boost_device,
        parameters={"v_in": "12V", "v_out": "5V", "i_out": "0.5A"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "boost_vin_vout_relation"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.BLOCKING
    assert result.passed is False


def test_boost_vin_vout_relation_ok_no_blocking(engine, boost_device):
    """Boost: V_in < V_out should not trigger."""
    module = ModuleReviewInput(
        role="boost_12v",
        category="boost",
        device=boost_device,
        parameters={"v_in": "3.3V", "v_out": "12V", "i_out": "0.2A"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "boost_vin_vout_relation"]
    assert issues == []


# ============================================================
# Boost: duty cycle
# ============================================================


def test_boost_duty_cycle_too_high_blocking(engine, boost_device):
    """D > 0.85 should be BLOCKING."""
    module = ModuleReviewInput(
        role="boost_high",
        category="boost",
        device=boost_device,
        parameters={"v_in": "1V", "v_out": "12V", "i_out": "0.1A"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "boost_duty_cycle"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.BLOCKING


def test_boost_duty_cycle_too_low_warning(engine, boost_device):
    """D < 0.1 should be WARNING."""
    module = ModuleReviewInput(
        role="boost_low",
        category="boost",
        device=boost_device,
        parameters={"v_in": "4.8V", "v_out": "5V", "i_out": "0.5A"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "boost_duty_cycle"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.WARNING


def test_boost_duty_cycle_normal_no_issue(engine, boost_device):
    """D in [0.1, 0.85] should not trigger."""
    module = ModuleReviewInput(
        role="boost_ok",
        category="boost",
        device=boost_device,
        parameters={"v_in": "3.3V", "v_out": "5V", "i_out": "0.5A"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "boost_duty_cycle"]
    assert issues == []


# ============================================================
# Boost: inductor ripple
# ============================================================


def test_boost_inductor_ripple_warning(engine):
    """High ripple ratio triggers WARNING."""
    device = make_boost_device(fsw="50k")
    module = ModuleReviewInput(
        role="boost_ripple",
        category="boost",
        device=device,
        parameters={"v_in": "3.3V", "v_out": "12V", "i_out": "0.5A", "fsw": "50k"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "boost_inductor_ripple"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.WARNING


# ============================================================
# Boost: output capacitor ripple
# ============================================================


def test_boost_output_cap_ripple_warning(engine):
    """High output ripple triggers WARNING."""
    device = make_boost_device(fsw="50k")
    module = ModuleReviewInput(
        role="boost_ripple_out",
        category="boost",
        device=device,
        parameters={"v_in": "3.3V", "v_out": "12V", "i_out": "2A", "fsw": "50k"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "boost_output_cap_ripple"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.WARNING


# ============================================================
# Boost: max vin exceeded
# ============================================================


def test_boost_max_vin_exceeded_blocking(engine):
    device = make_boost_device(v_in_max="5.5V")
    module = ModuleReviewInput(
        role="boost_vin",
        category="boost",
        device=device,
        parameters={"v_in": "6V", "v_out": "12V", "i_out": "0.2A"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "boost_max_vin_exceeded"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.BLOCKING


def test_boost_max_vin_not_exceeded(engine, boost_device):
    module = ModuleReviewInput(
        role="boost_vin_ok",
        category="boost",
        device=boost_device,
        parameters={"v_in": "3.3V", "v_out": "12V", "i_out": "0.2A"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "boost_max_vin_exceeded"]
    assert issues == []


# ============================================================
# Boost: layout and bringup notes
# ============================================================


def test_boost_layout_note(engine, boost_device):
    module = ModuleReviewInput(
        role="boost_layout",
        category="boost",
        device=boost_device,
        parameters={"v_in": "3.3V", "v_out": "12V"},
    )
    result = engine.review_module(module)
    layout = [i for i in result.issues if i.severity == ReviewSeverity.LAYOUT_NOTE]
    assert len(layout) >= 1
    rule_ids = [i.rule_id for i in layout]
    assert "boost_layout_loop" in rule_ids


def test_boost_bringup_note(engine, boost_device):
    module = ModuleReviewInput(
        role="boost_bringup",
        category="boost",
        device=boost_device,
        parameters={"v_in": "3.3V", "v_out": "12V"},
    )
    result = engine.review_module(module)
    bringup = [i for i in result.issues if i.severity == ReviewSeverity.BRINGUP_NOTE]
    assert len(bringup) >= 1
    rule_ids = [i.rule_id for i in bringup]
    assert "boost_bringup_startup" in rule_ids


# ============================================================
# Flyback: turns ratio
# ============================================================


def test_flyback_turns_ratio_too_low_blocking(engine, flyback_device):
    module = ModuleReviewInput(
        role="flyback_1",
        category="flyback",
        device=flyback_device,
        parameters={"v_in": "310V", "v_out": "12V", "turns_ratio": "0.3"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "flyback_turns_ratio"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.BLOCKING


def test_flyback_turns_ratio_too_high_blocking(engine, flyback_device):
    module = ModuleReviewInput(
        role="flyback_2",
        category="flyback",
        device=flyback_device,
        parameters={"v_in": "310V", "v_out": "12V", "turns_ratio": "25"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "flyback_turns_ratio"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.BLOCKING


def test_flyback_turns_ratio_high_warning(engine, flyback_device):
    module = ModuleReviewInput(
        role="flyback_3",
        category="flyback",
        device=flyback_device,
        parameters={"v_in": "310V", "v_out": "12V", "turns_ratio": "15"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "flyback_turns_ratio"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.WARNING


def test_flyback_turns_ratio_normal_no_issue(engine, flyback_device):
    module = ModuleReviewInput(
        role="flyback_4",
        category="flyback",
        device=flyback_device,
        parameters={"v_in": "310V", "v_out": "12V", "turns_ratio": "5"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "flyback_turns_ratio"]
    assert issues == []


# ============================================================
# Flyback: transformer saturation
# ============================================================


def test_flyback_transformer_saturation_warning(engine, flyback_device):
    """High B_max triggers WARNING."""
    module = ModuleReviewInput(
        role="flyback_sat",
        category="flyback",
        device=flyback_device,
        parameters={
            "v_in": "310V",
            "v_out": "12V",
            "turns_ratio": "5",
            "d_max": "0.45",
            "ae": "50",  # mm²
            "fsw": "132k",
        },
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "flyback_transformer_saturation"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.WARNING


# ============================================================
# Flyback: snubber requirement
# ============================================================


def test_flyback_snubber_required_recommendation(engine):
    device = make_flyback_device_no_snubber()
    module = ModuleReviewInput(
        role="flyback_snub",
        category="flyback",
        device=device,
        parameters={"v_in": "310V", "v_out": "12V"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "flyback_snubber_required"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.RECOMMENDATION


# ============================================================
# Flyback: isolation voltage
# ============================================================


def test_flyback_isolation_voltage_low_blocking(engine):
    device = make_flyback_device(v_isolation="1000V")
    module = ModuleReviewInput(
        role="flyback_iso",
        category="flyback",
        device=device,
        parameters={"v_in": "310V", "v_out": "12V", "v_isolation": "1000"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "flyback_isolation_voltage"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.BLOCKING


def test_flyback_isolation_voltage_ok(engine, flyback_device):
    module = ModuleReviewInput(
        role="flyback_iso_ok",
        category="flyback",
        device=flyback_device,
        parameters={"v_in": "310V", "v_out": "12V", "v_isolation": "3000"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "flyback_isolation_voltage"]
    assert issues == []


# ============================================================
# Flyback: layout and bringup notes
# ============================================================


def test_flyback_layout_note(engine, flyback_device):
    module = ModuleReviewInput(
        role="flyback_layout",
        category="flyback",
        device=flyback_device,
        parameters={"v_in": "310V", "v_out": "12V"},
    )
    result = engine.review_module(module)
    layout = [i for i in result.issues if i.severity == ReviewSeverity.LAYOUT_NOTE]
    assert len(layout) >= 1
    rule_ids = [i.rule_id for i in layout]
    assert "flyback_layout_transformer" in rule_ids


def test_flyback_bringup_note(engine, flyback_device):
    module = ModuleReviewInput(
        role="flyback_bringup",
        category="flyback",
        device=flyback_device,
        parameters={"v_in": "310V", "v_out": "12V"},
    )
    result = engine.review_module(module)
    bringup = [i for i in result.issues if i.severity == ReviewSeverity.BRINGUP_NOTE]
    assert len(bringup) >= 1
    rule_ids = [i.rule_id for i in bringup]
    assert "flyback_bringup_leakage" in rule_ids


# ============================================================
# SEPIC: coupling capacitor voltage
# ============================================================


def test_sepic_coupling_cap_voltage_warning(engine, sepic_device):
    """Coupling cap rating < 1.5 × V_in triggers WARNING."""
    module = ModuleReviewInput(
        role="sepic_cap",
        category="sepic",
        device=sepic_device,
        parameters={"v_in": "12V", "v_out": "5V", "coupling_cap_rating": "15"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "sepic_coupling_cap_voltage"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.WARNING


def test_sepic_coupling_cap_voltage_ok(engine, sepic_device):
    """Coupling cap rating >= 1.5 × V_in: no warning."""
    module = ModuleReviewInput(
        role="sepic_cap_ok",
        category="sepic",
        device=sepic_device,
        parameters={"v_in": "12V", "v_out": "5V", "coupling_cap_rating": "25"},
    )
    result = engine.review_module(module)
    issues = [
        i
        for i in result.issues
        if i.rule_id == "sepic_coupling_cap_voltage"
        and i.severity == ReviewSeverity.WARNING
    ]
    assert issues == []


def test_sepic_coupling_cap_no_rating_recommendation(engine, sepic_device):
    """No coupling_cap_rating → recommendation."""
    module = ModuleReviewInput(
        role="sepic_cap_rec",
        category="sepic",
        device=sepic_device,
        parameters={"v_in": "12V", "v_out": "5V"},
    )
    result = engine.review_module(module)
    issues = [
        i
        for i in result.issues
        if i.rule_id == "sepic_coupling_cap_voltage"
        and i.severity == ReviewSeverity.RECOMMENDATION
    ]
    assert len(issues) == 1


# ============================================================
# SEPIC: coupled inductor polarity
# ============================================================


def test_sepic_coupled_inductor_polarity_recommendation(engine, sepic_device):
    module = ModuleReviewInput(
        role="sepic_pol",
        category="sepic",
        device=sepic_device,
        parameters={"v_in": "12V", "v_out": "5V"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "sepic_coupled_inductor_polarity"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.RECOMMENDATION


# ============================================================
# SEPIC: output ripple
# ============================================================


def test_sepic_output_ripple_warning(engine):
    """High output ripple triggers WARNING."""
    device = make_sepic_device(fsw="50k")
    module = ModuleReviewInput(
        role="sepic_ripple",
        category="sepic",
        device=device,
        parameters={"v_in": "12V", "v_out": "5V", "i_out": "2A", "fsw": "50k"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "sepic_output_ripple"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.WARNING


def test_sepic_output_ripple_low_no_issue(engine, sepic_device):
    """Low output ripple → no warning."""
    module = ModuleReviewInput(
        role="sepic_ripple_ok",
        category="sepic",
        device=sepic_device,
        parameters={"v_in": "12V", "v_out": "5V", "i_out": "0.1A", "fsw": "500k"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "sepic_output_ripple"]
    assert issues == []


# ============================================================
# SEPIC: layout and bringup notes
# ============================================================


def test_sepic_layout_note(engine, sepic_device):
    module = ModuleReviewInput(
        role="sepic_layout",
        category="sepic",
        device=sepic_device,
        parameters={"v_in": "12V", "v_out": "5V"},
    )
    result = engine.review_module(module)
    layout = [i for i in result.issues if i.severity == ReviewSeverity.LAYOUT_NOTE]
    assert len(layout) >= 1
    rule_ids = [i.rule_id for i in layout]
    assert "sepic_layout_coupling_cap" in rule_ids


def test_sepic_bringup_note(engine, sepic_device):
    module = ModuleReviewInput(
        role="sepic_bringup",
        category="sepic",
        device=sepic_device,
        parameters={"v_in": "12V", "v_out": "5V"},
    )
    result = engine.review_module(module)
    bringup = [i for i in result.issues if i.severity == ReviewSeverity.BRINGUP_NOTE]
    assert len(bringup) >= 1
    rule_ids = [i.rule_id for i in bringup]
    assert "sepic_bringup_coupling" in rule_ids


# ============================================================
# OpAmp: output swing
# ============================================================


def test_opamp_output_swing_warning(engine, opamp_device):
    """Output voltage exceeds available swing."""
    module = ModuleReviewInput(
        role="opamp_swing",
        category="opamp",
        device=opamp_device,
        parameters={"v_supply": "5V", "v_out": "4.5V"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "opamp_output_swing"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.WARNING


def test_opamp_output_swing_ok(engine, opamp_device):
    """Output voltage within available swing."""
    module = ModuleReviewInput(
        role="opamp_swing_ok",
        category="opamp",
        device=opamp_device,
        parameters={"v_supply": "12V", "v_out": "5V"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "opamp_output_swing"]
    assert issues == []


# ============================================================
# OpAmp: input common-mode range
# ============================================================


def test_opamp_input_cm_below_min_blocking(engine, opamp_device):
    module = ModuleReviewInput(
        role="opamp_cm_low",
        category="opamp",
        device=opamp_device,
        parameters={"v_supply": "15V", "v_cm": "-15V"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "opamp_input_cm_range"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.BLOCKING


def test_opamp_input_cm_above_max_blocking(engine, opamp_device):
    module = ModuleReviewInput(
        role="opamp_cm_high",
        category="opamp",
        device=opamp_device,
        parameters={"v_supply": "15V", "v_cm": "15V"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "opamp_input_cm_range"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.BLOCKING


def test_opamp_input_cm_ok(engine, opamp_device):
    module = ModuleReviewInput(
        role="opamp_cm_ok",
        category="opamp",
        device=opamp_device,
        parameters={"v_supply": "15V", "v_cm": "5V"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "opamp_input_cm_range"]
    assert issues == []


# ============================================================
# OpAmp: gain-bandwidth product
# ============================================================


def test_opamp_gbw_insufficient_warning(engine, opamp_device):
    """Required GBW > spec GBW triggers WARNING."""
    module = ModuleReviewInput(
        role="opamp_gbw",
        category="opamp",
        device=opamp_device,
        parameters={"v_supply": "15V", "gain": "100", "f_signal": "100k"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "opamp_gbw_check"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.WARNING


def test_opamp_gbw_sufficient_no_issue(engine, opamp_device):
    """Required GBW <= spec GBW: no issue."""
    # GBW spec=1MHz, gain=10, f_signal=10 → f_hz=10kHz → required=100kHz < 1MHz
    module = ModuleReviewInput(
        role="opamp_gbw_ok",
        category="opamp",
        device=opamp_device,
        parameters={"v_supply": "15V", "gain": "10", "f_signal": "10"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "opamp_gbw_check"]
    assert issues == []


# ============================================================
# OpAmp: stability / high gain
# ============================================================


def test_opamp_stability_high_gain_warning(engine, opamp_device):
    module = ModuleReviewInput(
        role="opamp_hg",
        category="opamp",
        device=opamp_device,
        parameters={"v_supply": "15V", "gain": "200"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "opamp_stability_high_gain"]
    assert len(issues) == 1
    assert issues[0].severity == ReviewSeverity.WARNING


def test_opamp_stability_moderate_gain_no_issue(engine, opamp_device):
    module = ModuleReviewInput(
        role="opamp_mg",
        category="opamp",
        device=opamp_device,
        parameters={"v_supply": "15V", "gain": "50"},
    )
    result = engine.review_module(module)
    issues = [i for i in result.issues if i.rule_id == "opamp_stability_high_gain"]
    assert issues == []


# ============================================================
# OpAmp: layout and bringup notes
# ============================================================


def test_opamp_layout_note(engine, opamp_device):
    module = ModuleReviewInput(
        role="opamp_layout",
        category="opamp",
        device=opamp_device,
        parameters={"v_supply": "15V"},
    )
    result = engine.review_module(module)
    layout = [i for i in result.issues if i.severity == ReviewSeverity.LAYOUT_NOTE]
    assert len(layout) >= 1
    rule_ids = [i.rule_id for i in layout]
    assert "opamp_layout_decoupling" in rule_ids


def test_opamp_bringup_note(engine, opamp_device):
    module = ModuleReviewInput(
        role="opamp_bringup",
        category="opamp",
        device=opamp_device,
        parameters={"v_supply": "15V"},
    )
    result = engine.review_module(module)
    bringup = [i for i in result.issues if i.severity == ReviewSeverity.BRINGUP_NOTE]
    assert len(bringup) >= 1
    rule_ids = [i.rule_id for i in bringup]
    assert "opamp_bringup_offset" in rule_ids


# ============================================================
# New categories: messages are Chinese
# ============================================================


def test_boost_messages_chinese(engine, boost_device):
    module = ModuleReviewInput(
        role="boost_cn",
        category="boost",
        device=boost_device,
        parameters={"v_in": "3.3V", "v_out": "12V", "i_out": "0.5A"},
    )
    result = engine.review_module(module)
    for issue in result.issues:
        assert issue.message != ""
        has_chinese = any("\u4e00" <= c <= "\u9fff" for c in issue.message)
        assert has_chinese, f"Non-Chinese message: {issue.message}"


def test_flyback_messages_chinese(engine, flyback_device):
    module = ModuleReviewInput(
        role="flyback_cn",
        category="flyback",
        device=flyback_device,
        parameters={"v_in": "310V", "v_out": "12V", "turns_ratio": "5"},
    )
    result = engine.review_module(module)
    for issue in result.issues:
        assert issue.message != ""
        has_chinese = any("\u4e00" <= c <= "\u9fff" for c in issue.message)
        assert has_chinese, f"Non-Chinese message: {issue.message}"


def test_sepic_messages_chinese(engine, sepic_device):
    module = ModuleReviewInput(
        role="sepic_cn",
        category="sepic",
        device=sepic_device,
        parameters={"v_in": "12V", "v_out": "5V", "i_out": "0.5A"},
    )
    result = engine.review_module(module)
    for issue in result.issues:
        assert issue.message != ""
        has_chinese = any("\u4e00" <= c <= "\u9fff" for c in issue.message)
        assert has_chinese, f"Non-Chinese message: {issue.message}"


def test_opamp_messages_chinese(engine, opamp_device):
    module = ModuleReviewInput(
        role="opamp_cn",
        category="opamp",
        device=opamp_device,
        parameters={"v_supply": "15V", "gain": "200"},
    )
    result = engine.review_module(module)
    for issue in result.issues:
        assert issue.message != ""
        has_chinese = any("\u4e00" <= c <= "\u9fff" for c in issue.message)
        assert has_chinese, f"Non-Chinese message: {issue.message}"


# ============================================================
# New categories: module role set on issues
# ============================================================


def test_boost_module_role_set(engine, boost_device):
    module = ModuleReviewInput(
        role="my_boost",
        category="boost",
        device=boost_device,
        parameters={"v_in": "3.3V", "v_out": "12V"},
    )
    result = engine.review_module(module)
    for issue in result.issues:
        assert issue.module_role == "my_boost"


def test_opamp_module_role_set(engine, opamp_device):
    module = ModuleReviewInput(
        role="my_opamp",
        category="opamp",
        device=opamp_device,
        parameters={"v_supply": "15V"},
    )
    result = engine.review_module(module)
    for issue in result.issues:
        assert issue.module_role == "my_opamp"


# ============================================================
# Cross-module: boost in power budget
# ============================================================


def test_cross_module_boost_power_budget(engine):
    """Boost contributes to power budget."""
    boost_dev = make_boost_device()
    modules = [
        ModuleReviewInput(
            role="boost_1",
            category="boost",
            device=boost_dev,
            parameters={"v_in": "3.3V", "v_out": "12V", "i_out": "1A"},
        ),
        ModuleReviewInput(
            role="boost_2",
            category="boost",
            device=boost_dev,
            parameters={"v_in": "3.3V", "v_out": "12V", "i_out": "1A"},
        ),
    ]
    review = engine.review_design(modules)
    budget_issues = [i for i in review.issues if i.rule_id == "power_budget_check"]
    assert len(budget_issues) == 1
    assert budget_issues[0].severity == ReviewSeverity.WARNING
