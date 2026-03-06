"""SchemaForge 参考设计库测试

测试 ReferenceDesign 模型和 ReferenceDesignStore 的功能：
- 加载、获取、搜索、最佳匹配、保存等操作
- 所有内置5个参考设计的内容验证
"""

from __future__ import annotations

from pathlib import Path

import pytest

from schemaforge.library.reference_models import ReferenceDesign, ReferenceDesignStore


# ============================================================
# 测试夹具
# ============================================================

STORE_DIR = Path(__file__).parent.parent / "schemaforge" / "store" / "reference_designs"


@pytest.fixture
def store() -> ReferenceDesignStore:
    return ReferenceDesignStore(STORE_DIR)


@pytest.fixture
def tmp_store(tmp_path: Path) -> ReferenceDesignStore:
    return ReferenceDesignStore(tmp_path)


def _make_design(
    ref_id: str = "ref_test",
    name: str = "测试参考设计",
    categories: list[str] | None = None,
    roles: list[str] | None = None,
    scenarios: list[str] | None = None,
    tags: list[str] | None = None,
    design_notes: list[str] | None = None,
) -> ReferenceDesign:
    return ReferenceDesign(
        ref_id=ref_id,
        name=name,
        description="测试用参考设计",
        applicable_categories=categories or ["test"],
        applicable_roles=roles or ["test_role"],
        applicable_scenarios=scenarios or ["测试场景"],
        tags=tags or ["测试"],
        design_notes=design_notes or ["测试注意事项"],
        topology_template={"modules": [{"role": "test_role", "category": "test"}]},
    )


# ============================================================
# 1. 加载所有参考设计
# ============================================================


def test_load_all_returns_five_designs(store: ReferenceDesignStore) -> None:
    designs = store.load_all()
    assert len(designs) == 6


def test_load_all_ids(store: ReferenceDesignStore) -> None:
    designs = store.load_all()
    ids = {d.ref_id for d in designs}
    assert ids == {
        "ref_ldo_basic",
        "ref_led_indicator",
        "ref_voltage_divider",
        "ref_rc_filter",
        "ref_ldo_led_combo",
        "ref_buck_basic",
    }


def test_load_all_twice_clears_cache(store: ReferenceDesignStore) -> None:
    first = store.load_all()
    second = store.load_all()
    assert len(first) == len(second) == 6


# ============================================================
# 2. get() 按ID获取
# ============================================================


def test_get_ldo_basic(store: ReferenceDesignStore) -> None:
    d = store.get("ref_ldo_basic")
    assert d is not None
    assert d.ref_id == "ref_ldo_basic"
    assert d.name == "基础LDO稳压电路"


def test_get_led_indicator(store: ReferenceDesignStore) -> None:
    d = store.get("ref_led_indicator")
    assert d is not None
    assert d.ref_id == "ref_led_indicator"


def test_get_nonexistent_returns_none(store: ReferenceDesignStore) -> None:
    result = store.get("ref_nonexistent")
    assert result is None


def test_get_empty_string_returns_none(store: ReferenceDesignStore) -> None:
    result = store.get("")
    assert result is None


# ============================================================
# 3. search() 按条件搜索
# ============================================================


def test_search_by_category_ldo(store: ReferenceDesignStore) -> None:
    results = store.search(category="ldo")
    ids = {d.ref_id for d in results}
    assert "ref_ldo_basic" in ids
    assert "ref_ldo_led_combo" in ids


def test_search_by_category_led(store: ReferenceDesignStore) -> None:
    results = store.search(category="led")
    ids = {d.ref_id for d in results}
    assert "ref_led_indicator" in ids
    assert "ref_ldo_led_combo" in ids


def test_search_by_category_ldo_count(store: ReferenceDesignStore) -> None:
    results = store.search(category="ldo")
    assert len(results) == 2


def test_search_by_role_main_regulator(store: ReferenceDesignStore) -> None:
    results = store.search(role="main_regulator")
    ids = {d.ref_id for d in results}
    assert "ref_ldo_basic" in ids
    assert "ref_ldo_led_combo" in ids


def test_search_by_role_power_led(store: ReferenceDesignStore) -> None:
    results = store.search(role="power_led")
    ids = {d.ref_id for d in results}
    assert "ref_led_indicator" in ids
    assert "ref_ldo_led_combo" in ids


def test_search_by_scenario_noise_filter(store: ReferenceDesignStore) -> None:
    results = store.search(scenario="噪声滤波")
    ids = {d.ref_id for d in results}
    assert "ref_rc_filter" in ids


def test_search_by_scenario_battery(store: ReferenceDesignStore) -> None:
    results = store.search(scenario="电池供电")
    ids = {d.ref_id for d in results}
    assert "ref_ldo_basic" in ids


def test_search_with_empty_criteria_returns_all(store: ReferenceDesignStore) -> None:
    results = store.search()
    assert len(results) == 6


def test_search_no_match_returns_empty(store: ReferenceDesignStore) -> None:
    results = store.search(category="nonexistent_category")
    assert results == []


def test_search_combined_category_and_role(store: ReferenceDesignStore) -> None:
    results = store.search(category="ldo", role="main_regulator")
    ids = {d.ref_id for d in results}
    assert "ref_ldo_basic" in ids
    assert "ref_ldo_led_combo" in ids


# ============================================================
# 4. find_best_match()
# ============================================================


def test_find_best_match_ldo_only(store: ReferenceDesignStore) -> None:
    result = store.find_best_match(categories=["ldo"], roles=[])
    assert result is not None
    assert result.ref_id in ("ref_ldo_basic", "ref_ldo_led_combo")


def test_find_best_match_ldo_and_led_prefers_combo(store: ReferenceDesignStore) -> None:
    result = store.find_best_match(categories=["ldo", "led"], roles=[])
    assert result is not None
    assert result.ref_id == "ref_ldo_led_combo"


def test_find_best_match_no_match_returns_none(store: ReferenceDesignStore) -> None:
    result = store.find_best_match(categories=["nonexistent"], roles=[])
    assert result is None


def test_find_best_match_with_roles(store: ReferenceDesignStore) -> None:
    result = store.find_best_match(categories=["rc_filter"], roles=["input_filter"])
    assert result is not None
    assert result.ref_id == "ref_rc_filter"


def test_find_best_match_empty_inputs_returns_none(store: ReferenceDesignStore) -> None:
    result = store.find_best_match(categories=[], roles=[])
    assert result is None


# ============================================================
# 5. save() 与 reload 往返
# ============================================================


def test_save_creates_json_file(
    tmp_store: ReferenceDesignStore, tmp_path: Path
) -> None:
    design = _make_design(ref_id="ref_save_test")
    path = tmp_store.save(design)
    assert path.exists()
    assert path.name == "ref_save_test.json"


def test_save_and_get_roundtrip(tmp_store: ReferenceDesignStore) -> None:
    design = _make_design(ref_id="ref_roundtrip", name="往返测试")
    tmp_store.save(design)
    loaded = tmp_store.get("ref_roundtrip")
    assert loaded is not None
    assert loaded.name == "往返测试"
    assert loaded.ref_id == "ref_roundtrip"


def test_save_and_reload_from_disk(
    tmp_store: ReferenceDesignStore, tmp_path: Path
) -> None:
    design = _make_design(ref_id="ref_disk_test", name="磁盘往返")
    tmp_store.save(design)
    fresh_store = ReferenceDesignStore(tmp_path)
    designs = fresh_store.load_all()
    assert len(designs) == 1
    assert designs[0].ref_id == "ref_disk_test"


# ============================================================
# 6. JSON 序列化往返
# ============================================================


def test_reference_design_json_roundtrip() -> None:
    original = _make_design(
        ref_id="ref_json_test",
        categories=["ldo", "led"],
        roles=["main_regulator", "power_led"],
        design_notes=["note1", "note2"],
    )
    serialized = original.model_dump_json()
    restored = ReferenceDesign.model_validate_json(serialized)
    assert restored.ref_id == original.ref_id
    assert restored.applicable_categories == original.applicable_categories
    assert restored.design_notes == original.design_notes


def test_reference_design_dict_roundtrip() -> None:
    design = _make_design()
    d = design.model_dump()
    restored = ReferenceDesign.model_validate(d)
    assert restored.ref_id == design.ref_id
    assert restored.tags == design.tags


# ============================================================
# 7. 所有5个内置设计的内容验证
# ============================================================


def test_all_designs_have_nonempty_name(store: ReferenceDesignStore) -> None:
    for design in store.load_all():
        assert design.name, f"{design.ref_id} 缺少名称"


def test_all_designs_have_nonempty_description(store: ReferenceDesignStore) -> None:
    for design in store.load_all():
        assert design.description, f"{design.ref_id} 缺少描述"


def test_all_designs_have_nonempty_applicable_categories(
    store: ReferenceDesignStore,
) -> None:
    for design in store.load_all():
        assert design.applicable_categories, f"{design.ref_id} 缺少适用类别"


def test_all_designs_have_nonempty_tags(store: ReferenceDesignStore) -> None:
    for design in store.load_all():
        assert design.tags, f"{design.ref_id} 缺少标签"


def test_all_designs_have_nonempty_design_notes(store: ReferenceDesignStore) -> None:
    for design in store.load_all():
        assert design.design_notes, f"{design.ref_id} 缺少设计注意事项"


def test_all_designs_topology_template_has_modules(store: ReferenceDesignStore) -> None:
    for design in store.load_all():
        assert "modules" in design.topology_template, (
            f"{design.ref_id} topology_template缺少modules"
        )


# ============================================================
# 8. 特定设计内容验证
# ============================================================


def test_ldo_basic_required_components(store: ReferenceDesignStore) -> None:
    d = store.get("ref_ldo_basic")
    assert d is not None
    assert len(d.required_components) >= 2
    components_text = " ".join(d.required_components)
    assert "电容" in components_text


def test_ldo_led_combo_has_two_module_roles(store: ReferenceDesignStore) -> None:
    d = store.get("ref_ldo_led_combo")
    assert d is not None
    assert len(d.module_roles) == 2
    assert "main_regulator" in d.module_roles
    assert "power_led" in d.module_roles


def test_ldo_basic_replaceable_devices_populated(store: ReferenceDesignStore) -> None:
    d = store.get("ref_ldo_basic")
    assert d is not None
    assert "main_regulator" in d.replaceable_devices
    assert len(d.replaceable_devices["main_regulator"]) > 0


def test_ldo_led_combo_replaceable_devices_populated(
    store: ReferenceDesignStore,
) -> None:
    d = store.get("ref_ldo_led_combo")
    assert d is not None
    assert "main_regulator" in d.replaceable_devices


def test_rc_filter_applicable_categories(store: ReferenceDesignStore) -> None:
    d = store.get("ref_rc_filter")
    assert d is not None
    assert "rc_filter" in d.applicable_categories


def test_voltage_divider_constraints(store: ReferenceDesignStore) -> None:
    d = store.get("ref_voltage_divider")
    assert d is not None
    assert "max_input_voltage" in d.constraints


def test_led_indicator_required_components(store: ReferenceDesignStore) -> None:
    d = store.get("ref_led_indicator")
    assert d is not None
    assert len(d.required_components) >= 1
    assert "限流电阻" in d.required_components[0]


def test_ldo_basic_confidence_is_one(store: ReferenceDesignStore) -> None:
    d = store.get("ref_ldo_basic")
    assert d is not None
    assert d.confidence == 1.0


def test_all_designs_source_is_manual(store: ReferenceDesignStore) -> None:
    for design in store.load_all():
        assert design.source == "manual", f"{design.ref_id} source不是manual"


# ============================================================
# 9. 从 design 模块导出验证
# ============================================================


def test_import_from_design_module() -> None:
    from schemaforge.design import ReferenceDesign as RD
    from schemaforge.design import ReferenceDesignStore as RDS

    assert RD is ReferenceDesign
    assert RDS is ReferenceDesignStore
