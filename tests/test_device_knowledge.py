"""设计知识字段测试

测试 DeviceModel 设计知识扩展字段、store 角色搜索、retrieval 角色评分。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from schemaforge.library.models import DeviceModel
from schemaforge.library.store import ComponentStore
from schemaforge.design.retrieval import DeviceRequirement, DeviceRetriever


# ============================================================
# 路径常量
# ============================================================

STORE_DEVICES_DIR = Path(__file__).parent.parent / "schemaforge" / "store" / "devices"


# ============================================================
# DeviceModel 字段测试
# ============================================================


def test_device_model_with_design_knowledge_fields():
    device = DeviceModel(
        part_number="TEST-LDO",
        design_roles=["main_regulator"],
        selection_hints=["低压差应用"],
        anti_patterns=["压差不足"],
        required_companions=["输入电容10uF"],
        operating_constraints={"min_v_dropout": "1.1V"},
        layout_hints=["电容紧贴IC"],
        failure_modes=["热关断"],
        review_rules=["check_ldo_dropout"],
    )
    assert device.design_roles == ["main_regulator"]
    assert device.selection_hints == ["低压差应用"]
    assert device.anti_patterns == ["压差不足"]
    assert device.required_companions == ["输入电容10uF"]
    assert device.operating_constraints == {"min_v_dropout": "1.1V"}
    assert device.layout_hints == ["电容紧贴IC"]
    assert device.failure_modes == ["热关断"]
    assert device.review_rules == ["check_ldo_dropout"]


def test_device_model_without_design_knowledge_fields_backward_compat():
    device = DeviceModel(part_number="LEGACY-001")
    assert device.design_roles == []
    assert device.selection_hints == []
    assert device.anti_patterns == []
    assert device.required_companions == []
    assert device.operating_constraints == {}
    assert device.layout_hints == []
    assert device.failure_modes == []
    assert device.review_rules == []


def test_device_model_json_roundtrip():
    original = DeviceModel(
        part_number="TEST-ROUND",
        design_roles=["voltage_sampler", "feedback_network"],
        selection_hints=["ADC分压"],
        anti_patterns=["大电流负载"],
        required_companions=[],
        operating_constraints={"max_input_voltage": "5V"},
        layout_hints=["靠近采样点"],
        failure_modes=["阻抗拉偏"],
        review_rules=["check_divider_ratio"],
    )
    json_str = original.model_dump_json()
    restored = DeviceModel.model_validate_json(json_str)
    assert restored.design_roles == original.design_roles
    assert restored.selection_hints == original.selection_hints
    assert restored.anti_patterns == original.anti_patterns
    assert restored.required_companions == original.required_companions
    assert restored.operating_constraints == original.operating_constraints
    assert restored.layout_hints == original.layout_hints
    assert restored.failure_modes == original.failure_modes
    assert restored.review_rules == original.review_rules


def test_device_model_load_from_json_without_design_fields():
    minimal_json = json.dumps(
        {
            "part_number": "MINIMAL-001",
            "manufacturer": "TestCo",
            "description": "最小化器件",
            "category": "test",
        }
    )
    device = DeviceModel.model_validate_json(minimal_json)
    assert device.part_number == "MINIMAL-001"
    assert device.design_roles == []
    assert device.operating_constraints == {}


# ============================================================
# store search_by_role 测试
# ============================================================


def test_search_by_role_returns_matching_devices(tmp_path):
    store = ComponentStore(tmp_path)
    store.rebuild_index()

    ldo = DeviceModel(
        part_number="LDO-001",
        design_roles=["main_regulator", "aux_regulator"],
    )
    other = DeviceModel(
        part_number="FILTER-001",
        design_roles=["input_filter"],
    )
    store.save_device(ldo)
    store.save_device(other)

    results = store.search_by_role("main_regulator")
    part_numbers = [d.part_number for d in results]
    assert "LDO-001" in part_numbers
    assert "FILTER-001" not in part_numbers


def test_search_by_role_returns_empty_for_nonmatching_role(tmp_path):
    store = ComponentStore(tmp_path)
    device = DeviceModel(
        part_number="TEST-001",
        design_roles=["input_filter"],
    )
    store.save_device(device)
    results = store.search_by_role("nonexistent_role")
    assert results == []


def test_search_by_role_with_real_store():
    real_store_dir = Path(__file__).parent.parent / "schemaforge" / "store"
    store = ComponentStore(real_store_dir)
    store.rebuild_index()

    results = store.search_by_role("main_regulator")
    part_numbers = [d.part_number for d in results]
    assert "AMS1117-3.3" in part_numbers

    results_led = store.search_by_role("power_led")
    part_numbers_led = [d.part_number for d in results_led]
    assert "LED_INDICATOR" in part_numbers_led


# ============================================================
# AMS1117-3.3 JSON 字段验证
# ============================================================


def test_ams1117_has_correct_design_roles():
    fp = STORE_DEVICES_DIR / "AMS1117-3.3.json"
    device = DeviceModel.model_validate_json(fp.read_text(encoding="utf-8"))
    assert "main_regulator" in device.design_roles
    assert "aux_regulator" in device.design_roles


def test_led_indicator_has_correct_design_roles():
    fp = STORE_DEVICES_DIR / "LED_INDICATOR.json"
    device = DeviceModel.model_validate_json(fp.read_text(encoding="utf-8"))
    assert "power_led" in device.design_roles
    assert "status_led" in device.design_roles
    assert "indicator" in device.design_roles


# ============================================================
# 所有4个器件的字段完整性
# ============================================================

ALL_DEVICE_FILES = [
    "AMS1117-3.3.json",
    "VOLTAGE_DIVIDER.json",
    "LED_INDICATOR.json",
    "RC_LOWPASS.json",
]


@pytest.mark.parametrize("filename", ALL_DEVICE_FILES)
def test_all_devices_have_nonempty_design_roles(filename):
    fp = STORE_DEVICES_DIR / filename
    device = DeviceModel.model_validate_json(fp.read_text(encoding="utf-8"))
    assert len(device.design_roles) > 0, f"{filename} has empty design_roles"


@pytest.mark.parametrize("filename", ALL_DEVICE_FILES)
def test_all_devices_have_nonempty_selection_hints(filename):
    fp = STORE_DEVICES_DIR / filename
    device = DeviceModel.model_validate_json(fp.read_text(encoding="utf-8"))
    assert len(device.selection_hints) > 0, f"{filename} has empty selection_hints"


@pytest.mark.parametrize("filename", ALL_DEVICE_FILES)
def test_all_devices_have_nonempty_anti_patterns(filename):
    fp = STORE_DEVICES_DIR / filename
    device = DeviceModel.model_validate_json(fp.read_text(encoding="utf-8"))
    assert len(device.anti_patterns) > 0, f"{filename} has empty anti_patterns"


@pytest.mark.parametrize("filename", ALL_DEVICE_FILES)
def test_all_devices_have_nonempty_review_rules(filename):
    fp = STORE_DEVICES_DIR / filename
    device = DeviceModel.model_validate_json(fp.read_text(encoding="utf-8"))
    assert len(device.review_rules) > 0, f"{filename} has empty review_rules"


@pytest.mark.parametrize("filename", ALL_DEVICE_FILES)
def test_all_devices_operating_constraints_is_dict(filename):
    fp = STORE_DEVICES_DIR / filename
    device = DeviceModel.model_validate_json(fp.read_text(encoding="utf-8"))
    assert isinstance(device.operating_constraints, dict), (
        f"{filename} operating_constraints is not a dict"
    )


# ============================================================
# retrieval 角色评分测试
# ============================================================


def test_retrieval_role_scoring_bonus(tmp_path):
    store = ComponentStore(tmp_path)
    ldo = DeviceModel(
        part_number="LDO-ROLE",
        category="ldo",
        description="LDO稳压器",
        design_roles=["main_regulator"],
    )
    other = DeviceModel(
        part_number="LDO-NOROLE",
        category="ldo",
        description="LDO稳压器",
        design_roles=[],
    )
    store.save_device(ldo)
    store.save_device(other)

    retriever = DeviceRetriever(store)
    results = retriever.search(category="ldo", role="main_regulator")

    role_match = next((r for r in results if r.device.part_number == "LDO-ROLE"), None)
    no_role_match = next(
        (r for r in results if r.device.part_number == "LDO-NOROLE"), None
    )
    assert role_match is not None
    assert no_role_match is not None
    assert role_match.score > no_role_match.score
    assert any("设计角色匹配" in reason for reason in role_match.match_reasons)


def test_retrieval_search_by_requirement_with_role(tmp_path):
    store = ComponentStore(tmp_path)
    device = DeviceModel(
        part_number="LED-ROLE",
        category="led",
        description="LED指示灯",
        design_roles=["power_led", "status_led"],
    )
    store.save_device(device)

    retriever = DeviceRetriever(store)
    req = DeviceRequirement(category="led", role="power_led")
    results = retriever.search_by_requirement(req)
    assert len(results) > 0
    assert results[0].device.part_number == "LED-ROLE"
    assert any("设计角色匹配" in r for r in results[0].match_reasons)
