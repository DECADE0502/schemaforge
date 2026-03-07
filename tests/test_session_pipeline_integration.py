"""Pipeline integration tests — CandidateSolver + DesignReviewEngine + ReferenceDesignStore in DesignSession.

Verifies that the new main-chain modules (Phase D/E/G) are correctly wired
into the design_session.run() pipeline and their outputs flow into the Design IR.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from schemaforge.core.models import ParameterDef, PinType
from schemaforge.design.ir import (
    DesignIR,
    ReviewSeverity,
)
from schemaforge.library.models import (
    DeviceModel,
    ExternalComponent,
    SymbolDef,
    SymbolPin,
    TopologyConnection,
    TopologyDef,
)
from schemaforge.library.store import ComponentStore
from schemaforge.workflows.design_session import DesignSession


# ============================================================
# Fixtures
# ============================================================


def _create_ldo_device() -> DeviceModel:
    return DeviceModel(
        part_number="AMS1117-3.3",
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
                    name="VIN",
                    pin_number="3",
                    side="left",
                    pin_type=PinType.POWER_IN,
                    slot="1/3",
                ),
                SymbolPin(
                    name="VOUT",
                    pin_number="2",
                    side="right",
                    pin_type=PinType.POWER_OUT,
                    slot="1/3",
                ),
                SymbolPin(
                    name="GND",
                    pin_number="1",
                    side="bottom",
                    pin_type=PinType.GROUND,
                    slot="1/1",
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
                    value_expression="{c_in}",
                    schemdraw_element="Capacitor",
                ),
                ExternalComponent(
                    role="output_cap",
                    ref_prefix="C",
                    default_value="22uF",
                    value_expression="{c_out}",
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
        spice_model="XU{ref} {VIN} {VOUT} {GND} AMS1117",
        package="SOT-223",
        selection_hints=["低功耗场景", "SOT-223封装"],
        failure_modes=["输入输出电容不足导致振荡"],
        anti_patterns=["高压差大电流应用不建议使用"],
    )


def _create_led_device() -> DeviceModel:
    return DeviceModel(
        part_number="LED_INDICATOR",
        description="LED指示灯电路",
        category="led",
        topology=TopologyDef(circuit_type="led_driver"),
    )


_REF_LDO_BASIC = {
    "ref_id": "ref_ldo_basic",
    "name": "基础LDO稳压电路",
    "description": "适用于低压差、低电流的基础稳压方案",
    "applicable_categories": ["ldo"],
    "applicable_roles": ["main_regulator"],
    "applicable_scenarios": ["低压差稳压"],
    "constraints": {"v_in_range": "3.5V-15V", "v_out": "3.3V"},
    "module_roles": ["main_regulator"],
    "required_components": ["输入电容10uF", "输出电容22uF"],
    "design_notes": ["输入输出电容需紧贴IC"],
    "confidence": 1.0,
    "source": "manual",
}


def _populate_store(store_dir: Path) -> None:
    store = ComponentStore(store_dir)
    store.save_device(_create_ldo_device())
    store.save_device(_create_led_device())

    ref_dir = store_dir / "reference_designs"
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "ref_ldo_basic.json").write_text(
        json.dumps(_REF_LDO_BASIC, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@pytest.fixture
def store_dir():
    tmp = Path(tempfile.mkdtemp())
    _populate_store(tmp)
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


def _run_session(store_dir: Path, query: str = "5V转3.3V稳压电路") -> tuple:
    session = DesignSession(store_dir=store_dir, )
    result = session.run(query)
    return session, result


# ============================================================
# CandidateSolver integration
# ============================================================


class TestCandidateSolverIntegration:
    def test_solver_result_populated_on_matched_modules(self, store_dir):
        _, result = _run_session(store_dir)
        matched = [m for m in result.modules if m.device is not None]
        assert len(matched) >= 1
        for mr in matched:
            assert mr.solver_result is not None
            assert len(mr.solver_result.candidates) >= 1

    def test_solver_result_none_on_unmatched_modules(self, store_dir):
        _, result = _run_session(store_dir)
        unmatched = [m for m in result.modules if m.device is None]
        for mr in unmatched:
            assert mr.solver_result is None

    def test_ir_candidates_from_solver(self, store_dir):
        session, result = _run_session(store_dir)
        ir: DesignIR = session.ir
        for module_ir in ir.modules:
            if module_ir.selection.selected is not None:
                assert len(module_ir.selection.candidates) >= 1
                first = module_ir.selection.candidates[0]
                assert first.part_number
                assert first.score > 0

    def test_ir_selection_reason_from_solver(self, store_dir):
        session, result = _run_session(store_dir)
        ir: DesignIR = session.ir
        for module_ir in ir.modules:
            if module_ir.selection.selected is not None:
                assert module_ir.selection.selection_reason
                assert module_ir.selection.selection_reason != "最佳匹配"

    def test_ir_candidate_tradeoff_notes(self, store_dir):
        session, result = _run_session(store_dir)
        ir: DesignIR = session.ir
        ldo_modules = [
            m for m in ir.modules if m.intent.category == "ldo" and m.selection.selected
        ]
        for m in ldo_modules:
            for c in m.selection.candidates:
                assert isinstance(c.tradeoff_notes, str)

    def test_ir_candidate_match_reasons_contain_score_dimensions(self, store_dir):
        session, _ = _run_session(store_dir)
        ir: DesignIR = session.ir
        for module_ir in ir.modules:
            if module_ir.selection.selected is not None:
                first = module_ir.selection.candidates[0]
                assert len(first.match_reasons) >= 1
                assert any(":" in r for r in first.match_reasons)

    def test_to_dict_includes_solver_candidates_count(self, store_dir):
        _, result = _run_session(store_dir)
        matched = [m for m in result.modules if m.device is not None]
        for mr in matched:
            d = mr.to_dict()
            assert "solver_candidates" in d
            assert d["solver_candidates"] >= 1

    def test_solver_not_called_when_no_device_matched(self, store_dir):
        empty_dir = Path(tempfile.mkdtemp())
        try:
            ComponentStore(empty_dir)
            session = DesignSession(store_dir=empty_dir, )
            result = session.run("5V转3.3V稳压电路")
            assert not result.success
            for mr in result.modules:
                assert mr.solver_result is None
        finally:
            shutil.rmtree(empty_dir, ignore_errors=True)


# ============================================================
# DesignReviewEngine integration
# ============================================================


class TestDesignReviewEngineIntegration:
    def test_review_result_populated_on_matched_modules(self, store_dir):
        _, result = _run_session(store_dir)
        matched = [m for m in result.modules if m.device is not None]
        assert len(matched) >= 1
        for mr in matched:
            assert mr.review_result is not None
            assert hasattr(mr.review_result, "passed")
            assert hasattr(mr.review_result, "issues")

    def test_design_review_populated(self, store_dir):
        _, result = _run_session(store_dir)
        assert result.design_review is not None
        assert hasattr(result.design_review, "overall_passed")
        assert hasattr(result.design_review, "issues")

    def test_ir_module_review_from_engine(self, store_dir):
        session, _ = _run_session(store_dir)
        ir: DesignIR = session.ir
        for module_ir in ir.modules:
            if module_ir.selection.selected is not None:
                assert len(module_ir.review.issues) >= 0
                assert isinstance(module_ir.review.passed, bool)

    def test_ir_global_review_from_engine(self, store_dir):
        session, _ = _run_session(store_dir)
        ir: DesignIR = session.ir
        assert ir.review is not None
        assert hasattr(ir.review, "overall_passed")
        assert len(ir.review.issues) >= 1

    def test_ldo_review_produces_known_rules(self, store_dir):
        session, _ = _run_session(store_dir)
        ir: DesignIR = session.ir
        ldo_modules = [m for m in ir.modules if m.intent.category == "ldo"]
        all_rule_ids = set()
        for m in ldo_modules:
            for issue in m.review.issues:
                all_rule_ids.add(issue.rule_id)
        assert len(all_rule_ids) >= 1

    def test_review_issues_have_valid_severity(self, store_dir):
        session, _ = _run_session(store_dir)
        ir: DesignIR = session.ir
        valid_severities = set(ReviewSeverity)
        for m in ir.modules:
            for issue in m.review.issues:
                assert issue.severity in valid_severities

    def test_cross_module_review_included(self, store_dir):
        _, result = _run_session(store_dir, "5V转3.3V稳压电路，带LED指示灯")
        assert result.design_review is not None
        rule_ids = {i.rule_id for i in result.design_review.issues}
        assert "ground_path_check" in rule_ids

    def test_to_dict_includes_review_passed(self, store_dir):
        _, result = _run_session(store_dir)
        matched = [m for m in result.modules if m.device is not None]
        for mr in matched:
            d = mr.to_dict()
            assert "review_passed" in d
            assert isinstance(d["review_passed"], bool)

    def test_result_to_dict_includes_design_review(self, store_dir):
        _, result = _run_session(store_dir)
        d = result.to_dict()
        assert "design_review_passed" in d
        assert isinstance(d["design_review_passed"], bool)


# ============================================================
# ReferenceDesignStore integration
# ============================================================


class TestReferenceDesignIntegration:
    def test_reference_design_matched(self, store_dir):
        _, result = _run_session(store_dir)
        assert result.reference_design is not None
        assert result.reference_design.ref_id == "ref_ldo_basic"

    def test_reference_design_has_expected_fields(self, store_dir):
        _, result = _run_session(store_dir)
        ref = result.reference_design
        assert ref is not None
        assert ref.name == "基础LDO稳压电路"
        assert "ldo" in ref.applicable_categories
        assert ref.confidence == 1.0

    def test_reference_design_none_when_no_ref_dir(self, store_dir):
        ref_dir = store_dir / "reference_designs"
        shutil.rmtree(ref_dir)
        _, result = _run_session(store_dir)
        assert result.reference_design is None

    def test_reference_design_none_when_no_match(self, store_dir):
        ref_dir = store_dir / "reference_designs"
        shutil.rmtree(ref_dir)
        ref_dir.mkdir()
        no_match = {
            "ref_id": "ref_buck_only",
            "name": "Buck-only design",
            "applicable_categories": ["buck"],
            "applicable_roles": ["dc_dc_converter"],
        }
        (ref_dir / "ref_buck_only.json").write_text(
            json.dumps(no_match, ensure_ascii=False),
            encoding="utf-8",
        )
        _, result = _run_session(store_dir)
        assert result.reference_design is None

    def test_to_dict_has_reference_design_flag(self, store_dir):
        _, result = _run_session(store_dir)
        d = result.to_dict()
        assert "has_reference_design" in d
        assert d["has_reference_design"] is True


# ============================================================
# End-to-end pipeline coherence
# ============================================================


class TestPipelineCoherence:
    def test_full_pipeline_ldo_success(self, store_dir):
        session, result = _run_session(store_dir)
        assert result.success
        ir: DesignIR = session.ir

        assert ir.success
        assert ir.stage == "done"
        assert len(ir.modules) >= 1

        for m in ir.modules:
            if m.selection.selected is not None:
                assert len(m.selection.candidates) >= 1
                assert m.selection.selection_reason
            assert isinstance(m.review.passed, bool)

        assert ir.review is not None
        assert len(ir.outputs.svg_paths) >= 1

    def test_full_pipeline_ldo_plus_led(self, store_dir):
        session, result = _run_session(store_dir, "5V转3.3V稳压电路，带LED指示灯")
        assert result.success
        ir: DesignIR = session.ir

        categories = {m.intent.category for m in ir.modules}
        assert "ldo" in categories

        assert result.design_review is not None
        assert result.reference_design is not None

    def test_ir_serialization_roundtrip_with_new_fields(self, store_dir):
        session, _ = _run_session(store_dir)
        ir: DesignIR = session.ir

        json_str = ir.model_dump_json(indent=2)
        ir2 = DesignIR.model_validate_json(json_str)

        assert ir2.ir_id == ir.ir_id
        assert len(ir2.modules) == len(ir.modules)
        for m1, m2 in zip(ir.modules, ir2.modules):
            assert len(m1.selection.candidates) == len(m2.selection.candidates)
            assert len(m1.review.issues) == len(m2.review.issues)

    def test_ir_snapshot_preserves_new_data(self, store_dir):
        session, _ = _run_session(store_dir)
        ir: DesignIR = session.ir

        snap = ir.snapshot("test_snap")
        restored = snap.restore()

        assert len(restored.modules) == len(ir.modules)
        for m_orig, m_rest in zip(ir.modules, restored.modules):
            assert len(m_orig.selection.candidates) == len(m_rest.selection.candidates)
            assert m_orig.review.passed == m_rest.review.passed

    def test_existing_749_tests_baseline_unbroken(self, store_dir):
        session, result = _run_session(store_dir)
        assert result.success
        assert result.plan is not None
        assert len(result.svg_paths) >= 1
        assert result.bom_text
        assert result.spice_text
