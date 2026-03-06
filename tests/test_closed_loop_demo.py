"""Step 4: LDO+LED full pipeline closed-loop demo.

Exercises every stage of the new main chain in a single test scenario:
  clarify → candidate solve → review → render → patch → reference reuse

This is the "全链路闭环" described in docs/full_review_new_guide.md §9-Step4.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from schemaforge.core.models import ParameterDef, PinType
from schemaforge.design.candidate_solver import CandidateSolver, SolverResult
from schemaforge.design.clarifier import ClarificationResult, RequirementClarifier
from schemaforge.design.ir import (
    DesignIR,
    DesignReview,
    ModuleReview,
    ReviewSeverity,
)
from schemaforge.design.planner import DesignPlanner
from schemaforge.design.review import DesignReviewEngine, ModuleReviewInput
from schemaforge.library.models import (
    DeviceModel,
    ExternalComponent,
    SymbolDef,
    SymbolPin,
    TopologyConnection,
    TopologyDef,
)
from schemaforge.library.reference_models import ReferenceDesignStore
from schemaforge.library.store import ComponentStore
from schemaforge.workflows.design_session import DesignSession
from schemaforge.workflows.ir_patch import IRPatchEngine, IRPatchOp


# ============================================================
# Test store setup
# ============================================================


def _build_ldo() -> DeviceModel:
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


def _build_led() -> DeviceModel:
    return DeviceModel(
        part_number="LED_INDICATOR",
        description="LED指示灯电路",
        category="led",
        topology=TopologyDef(circuit_type="led_driver"),
    )


_REF_LDO_LED_COMBO = {
    "ref_id": "ref_ldo_led_combo",
    "name": "LDO+LED组合参考设计",
    "description": "LDO 稳压 + LED 电源指示灯的典型组合",
    "applicable_categories": ["ldo", "led"],
    "applicable_roles": ["main_regulator", "power_led"],
    "applicable_scenarios": ["低压差稳压", "电源指示"],
    "constraints": {"v_in_range": "3.5V-15V", "v_out": "3.3V"},
    "module_roles": ["main_regulator", "power_led"],
    "required_components": ["输入电容10uF", "输出电容22uF", "LED限流电阻"],
    "design_notes": ["输入输出电容需紧贴IC", "LED限流电阻靠近LED放置"],
    "layout_tips": ["电容距离IC引脚<2mm"],
    "bringup_tips": ["先测LDO输出再接LED"],
    "confidence": 1.0,
    "source": "manual",
    "tags": ["ldo", "led", "combo"],
}


@pytest.fixture
def store_dir():
    tmp = Path(tempfile.mkdtemp())
    store = ComponentStore(tmp)
    store.save_device(_build_ldo())
    store.save_device(_build_led())

    ref_dir = tmp / "reference_designs"
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "ref_ldo_led_combo.json").write_text(
        json.dumps(_REF_LDO_LED_COMBO, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


USER_QUERY = "5V转3.3V稳压电路，带绿色LED指示灯"


# ============================================================
# Stage 1: Clarify
# ============================================================


class TestStage1Clarify:
    def test_clarifier_produces_structured_result(self):
        planner = DesignPlanner(use_mock=True)
        plan = planner.plan(USER_QUERY)
        clarifier = RequirementClarifier(use_mock=True)
        result = clarifier.clarify(USER_QUERY, plan)

        assert isinstance(result, ClarificationResult)
        assert result.can_proceed
        assert len(result.known_constraints) >= 1
        assert len(result.assumptions) >= 1
        assert result.confidence > 0

    def test_clarifier_identifies_ldo_constraints(self):
        planner = DesignPlanner(use_mock=True)
        plan = planner.plan(USER_QUERY)
        clarifier = RequirementClarifier(use_mock=True)
        result = clarifier.clarify(USER_QUERY, plan)

        constraint_names = {c.name for c in result.known_constraints}
        assert "v_in" in constraint_names or "v_out" in constraint_names

    def test_plan_has_ldo_and_led_modules(self):
        planner = DesignPlanner(use_mock=True)
        plan = planner.plan(USER_QUERY)

        categories = {m.category for m in plan.modules}
        assert "ldo" in categories
        assert len(plan.modules) >= 2


# ============================================================
# Stage 2: Candidate Solve
# ============================================================


class TestStage2CandidateSolve:
    def test_solver_generates_multiple_candidates_for_ldo(self, store_dir):
        store = ComponentStore(store_dir)
        solver = CandidateSolver(store, use_mock=True)
        planner = DesignPlanner(use_mock=True)
        plan = planner.plan(USER_QUERY)

        ldo_mods = [m for m in plan.modules if m.category == "ldo"]
        assert len(ldo_mods) >= 1

        for mod_req in ldo_mods:
            solver_result = solver.solve(mod_req, max_candidates=3)
            assert isinstance(solver_result, SolverResult)
            assert len(solver_result.candidates) >= 1
            assert solver_result.recommended is not None
            assert solver_result.recommended.total_score > 0

    def test_solver_candidates_have_6_score_dimensions(self, store_dir):
        store = ComponentStore(store_dir)
        solver = CandidateSolver(store, use_mock=True)
        planner = DesignPlanner(use_mock=True)
        plan = planner.plan(USER_QUERY)

        ldo_mod = next(m for m in plan.modules if m.category == "ldo")
        solver_result = solver.solve(ldo_mod)
        candidate = solver_result.candidates[0]
        assert len(candidate.scores) == 6

        dim_names = {s.name for s in candidate.scores}
        expected = {
            "constraint_satisfaction",
            "device_match",
            "electrical_reasonability",
            "bom_complexity",
            "thermal_risk",
            "user_preference_match",
        }
        assert dim_names == expected


# ============================================================
# Stage 3: Review
# ============================================================


class TestStage3Review:
    def test_review_engine_produces_issues_for_ldo(self, store_dir):
        device = _build_ldo()
        engine = DesignReviewEngine()
        module_input = ModuleReviewInput(
            role="main_regulator",
            category="ldo",
            device=device,
            parameters={"v_in": "5V", "v_out": "3.3V", "i_out": "0.5A"},
        )
        review = engine.review_module(module_input)

        assert isinstance(review, ModuleReview)
        assert len(review.issues) >= 1

        rule_ids = {i.rule_id for i in review.issues}
        assert len(rule_ids) >= 1

    def test_review_engine_cross_module_check(self, store_dir):
        engine = DesignReviewEngine()
        inputs = [
            ModuleReviewInput(
                role="main_regulator",
                category="ldo",
                device=_build_ldo(),
                parameters={"v_in": "5V", "v_out": "3.3V", "i_out": "0.5A"},
            ),
            ModuleReviewInput(
                role="power_led",
                category="led",
                device=_build_led(),
                parameters={"v_supply": "3.3V"},
            ),
        ]
        design_review = engine.review_design(inputs)
        assert isinstance(design_review, DesignReview)
        rule_ids = {i.rule_id for i in design_review.issues}
        assert "ground_path_check" in rule_ids

    def test_review_severity_levels_present(self, store_dir):
        engine = DesignReviewEngine()
        module_input = ModuleReviewInput(
            role="main_regulator",
            category="ldo",
            device=_build_ldo(),
            parameters={"v_in": "5V", "v_out": "3.3V"},
        )
        review = engine.review_module(module_input)
        severities = {i.severity for i in review.issues}
        assert len(severities) >= 1
        for sev in severities:
            assert sev in set(ReviewSeverity)


# ============================================================
# Stage 4: Render (via DesignSession)
# ============================================================


class TestStage4Render:
    def test_session_renders_svg_for_ldo_led(self, store_dir):
        session = DesignSession(store_dir=store_dir, use_mock=True)
        result = session.run(USER_QUERY)

        assert result.success, f"Pipeline failed: {result.error}"
        assert len(result.svg_paths) >= 1
        for svg in result.svg_paths:
            assert Path(svg).exists()
            assert Path(svg).stat().st_size > 0

    def test_session_produces_bom_and_spice(self, store_dir):
        session = DesignSession(store_dir=store_dir, use_mock=True)
        result = session.run(USER_QUERY)

        assert result.success
        assert result.bom_text
        assert result.spice_text


# ============================================================
# Stage 5: Patch (IR-level modification)
# ============================================================


class TestStage5Patch:
    def test_patch_update_constraint_on_session_ir(self, store_dir):
        session = DesignSession(store_dir=store_dir, use_mock=True)
        result = session.run(USER_QUERY)
        assert result.success

        ir: DesignIR = session.ir
        original_version = ir.version

        patch_engine = IRPatchEngine()
        ops = [
            IRPatchOp(
                op_type="update_constraint",
                field="v_in",
                value="12V",
                reason="用户修改输入电压为12V",
            ),
        ]

        patch_result = patch_engine.apply(ir, ops)
        assert patch_result.success
        assert patch_result.modified_ir is not None
        assert patch_result.modified_ir.version == original_version + 1

        updated_constraints = {
            c.name: c.value for c in patch_result.modified_ir.intent.known_constraints
        }
        assert updated_constraints.get("v_in") == "12V"

    def test_patch_replace_device(self, store_dir):
        session = DesignSession(store_dir=store_dir, use_mock=True)
        result = session.run(USER_QUERY)
        assert result.success

        ir: DesignIR = session.ir
        ldo_roles = [m.intent.role for m in ir.modules if m.intent.category == "ldo"]
        assert len(ldo_roles) >= 1

        patch_engine = IRPatchEngine()
        ops = [
            IRPatchOp(
                op_type="replace_device",
                target_module=ldo_roles[0],
                value="ME6211-3.3",
                reason="替换为更低压差的LDO",
            ),
        ]

        patch_result = patch_engine.apply(ir, ops)
        assert patch_result.success
        replaced_module = patch_result.modified_ir.get_module(ldo_roles[0])
        assert replaced_module is not None
        assert replaced_module.selection.selected.part_number == "ME6211-3.3"

    def test_patch_update_parameter(self, store_dir):
        session = DesignSession(store_dir=store_dir, use_mock=True)
        result = session.run(USER_QUERY)
        assert result.success

        ir: DesignIR = session.ir
        ldo_roles = [m.intent.role for m in ir.modules if m.intent.category == "ldo"]

        patch_engine = IRPatchEngine()
        ops = [
            IRPatchOp(
                op_type="update_parameter",
                target_module=ldo_roles[0],
                field="c_out",
                value="100uF",
                reason="增大输出电容提高稳定性",
            ),
        ]
        patch_result = patch_engine.apply(ir, ops)
        assert patch_result.success

        mod = patch_result.modified_ir.get_module(ldo_roles[0])
        assert mod.parameters.input_params.get("c_out") == "100uF"

    def test_patch_preserves_history(self, store_dir):
        session = DesignSession(store_dir=store_dir, use_mock=True)
        session.run(USER_QUERY)
        ir: DesignIR = session.ir

        patch_engine = IRPatchEngine()
        ops1 = [IRPatchOp(op_type="update_constraint", field="v_in", value="7V")]
        result1 = patch_engine.apply(ir, ops1)
        assert result1.success

        ops2 = [IRPatchOp(op_type="update_constraint", field="v_in", value="9V")]
        result2 = patch_engine.apply(result1.modified_ir, ops2)
        assert result2.success

        assert len(result2.modified_ir.history) == 2
        assert result2.modified_ir.version >= 3

    def test_patch_preview(self, store_dir):
        session = DesignSession(store_dir=store_dir, use_mock=True)
        session.run(USER_QUERY)
        ir: DesignIR = session.ir

        patch_engine = IRPatchEngine()
        ops = [
            IRPatchOp(op_type="update_constraint", field="v_in", value="12V"),
        ]
        descriptions = patch_engine.preview(ir, ops)
        assert len(descriptions) == 1
        assert "v_in" in descriptions[0]

    def test_patch_validate_catches_invalid_ops(self, store_dir):
        session = DesignSession(store_dir=store_dir, use_mock=True)
        session.run(USER_QUERY)
        ir: DesignIR = session.ir

        patch_engine = IRPatchEngine()
        ops = [
            IRPatchOp(op_type="remove_module", target_module="nonexistent_module"),
        ]
        errors = patch_engine.validate(ir, ops)
        assert len(errors) >= 1


# ============================================================
# Stage 6: Reference Design Reuse
# ============================================================


class TestStage6ReferenceDesign:
    def test_reference_store_finds_ldo_led_combo(self, store_dir):
        ref_dir = store_dir / "reference_designs"
        ref_store = ReferenceDesignStore(ref_dir)

        best = ref_store.find_best_match(
            categories=["ldo", "led"],
            roles=["main_regulator", "power_led"],
        )
        assert best is not None
        assert best.ref_id == "ref_ldo_led_combo"

    def test_reference_design_has_design_notes(self, store_dir):
        ref_dir = store_dir / "reference_designs"
        ref_store = ReferenceDesignStore(ref_dir)
        design = ref_store.get("ref_ldo_led_combo")
        assert design is not None
        assert len(design.design_notes) >= 1
        assert len(design.layout_tips) >= 1
        assert len(design.bringup_tips) >= 1

    def test_reference_design_matched_by_session(self, store_dir):
        session = DesignSession(store_dir=store_dir, use_mock=True)
        result = session.run(USER_QUERY)
        assert result.success
        assert result.reference_design is not None
        assert result.reference_design.ref_id == "ref_ldo_led_combo"

    def test_reference_design_applicable_categories_match(self, store_dir):
        session = DesignSession(store_dir=store_dir, use_mock=True)
        result = session.run(USER_QUERY)
        ref = result.reference_design
        assert ref is not None
        assert "ldo" in ref.applicable_categories


# ============================================================
# Full closed-loop: all 6 stages in one scenario
# ============================================================


class TestFullClosedLoop:
    def test_ldo_led_full_chain(self, store_dir):
        """Full pipeline: clarify → candidate → review → render → patch → reference.

        Given: user asks for "5V转3.3V稳压电路，带绿色LED指示灯"
        When:  the entire pipeline executes
        Then:  every stage produces valid output and data flows through IR
        """
        # --- 1. Clarify ---
        planner = DesignPlanner(use_mock=True)
        plan = planner.plan(USER_QUERY)
        clarifier = RequirementClarifier(use_mock=True)
        clarification = clarifier.clarify(USER_QUERY, plan)
        assert clarification.can_proceed
        assert len(plan.modules) >= 2

        # --- 2. Candidate Solve ---
        store = ComponentStore(store_dir)
        solver = CandidateSolver(store, use_mock=True)
        solver_results: dict[str, SolverResult] = {}
        for mod in plan.modules:
            solver_results[mod.role] = solver.solve(mod, max_candidates=3)
        ldo_solver = next(
            v for k, v in solver_results.items() if v.module_category == "ldo"
        )
        assert ldo_solver.recommended is not None
        assert ldo_solver.recommended.total_score > 0

        # --- 3. Review ---
        review_engine = DesignReviewEngine()
        ldo_device = _build_ldo()
        led_device = _build_led()
        review_inputs = [
            ModuleReviewInput(
                role="main_regulator",
                category="ldo",
                device=ldo_device,
                parameters={"v_in": "5V", "v_out": "3.3V", "i_out": "0.5A"},
            ),
            ModuleReviewInput(
                role="power_led",
                category="led",
                device=led_device,
                parameters={"v_supply": "3.3V"},
            ),
        ]
        design_review = review_engine.review_design(review_inputs)
        assert isinstance(design_review, DesignReview)
        assert len(design_review.issues) >= 1

        # --- 4. Render (via full DesignSession) ---
        session = DesignSession(store_dir=store_dir, use_mock=True)
        result = session.run(USER_QUERY)
        assert result.success, f"Render failed: {result.error}"
        assert len(result.svg_paths) >= 1

        ir: DesignIR = session.ir
        assert ir.success
        assert len(ir.modules) >= 2

        # Verify solver data flowed into IR
        for m in ir.modules:
            if m.selection.selected is not None:
                assert len(m.selection.candidates) >= 1
                assert m.selection.selection_reason

        # Verify review data flowed into IR
        assert ir.review is not None
        assert len(ir.review.issues) >= 1

        # --- 5. Patch ---
        patch_engine = IRPatchEngine()
        ops = [
            IRPatchOp(
                op_type="update_constraint",
                field="v_in",
                value="12V",
                reason="客户要求改为12V输入",
            ),
            IRPatchOp(
                op_type="update_parameter",
                target_module=ir.modules[0].intent.role,
                field="c_out",
                value="47uF",
                reason="增大输出电容",
            ),
        ]
        patch_result = patch_engine.apply(ir, ops)
        assert patch_result.success
        patched_ir = patch_result.modified_ir
        assert patched_ir.version > ir.version
        assert len(patched_ir.history) >= 1

        v_in_updated = next(
            (c.value for c in patched_ir.intent.known_constraints if c.name == "v_in"),
            None,
        )
        assert v_in_updated == "12V"

        # --- 6. Reference Design ---
        assert result.reference_design is not None
        ref = result.reference_design
        assert "ldo" in ref.applicable_categories
        assert ref.confidence >= 0.5

        # Verify IR serialization roundtrip preserves everything
        json_str = patched_ir.model_dump_json(indent=2)
        restored = DesignIR.model_validate_json(json_str)
        assert len(restored.modules) == len(patched_ir.modules)
        assert restored.version == patched_ir.version
        assert len(restored.history) == len(patched_ir.history)

    def test_ldo_only_closed_loop(self, store_dir):
        """Simpler scenario: LDO-only, still exercises full chain."""
        session = DesignSession(store_dir=store_dir, use_mock=True)
        result = session.run("5V转3.3V稳压电路")

        assert result.success
        ir: DesignIR = session.ir
        assert ir.success

        # Solver
        ldo_mods = [m for m in ir.modules if m.intent.category == "ldo"]
        assert len(ldo_mods) >= 1
        for m in ldo_mods:
            assert len(m.selection.candidates) >= 1

        # Review
        assert ir.review is not None
        assert len(ir.review.issues) >= 1

        # Patch
        patch_engine = IRPatchEngine()
        ops = [
            IRPatchOp(
                op_type="change_preference",
                field="cost_priority",
                value="low",
                reason="优先低成本",
            )
        ]
        pr = patch_engine.apply(ir, ops)
        assert pr.success

        # Reference
        assert result.reference_design is not None

    def test_ir_snapshot_and_rollback_after_patch(self, store_dir):
        """Demonstrate snapshot/rollback capability across the full chain."""
        session = DesignSession(store_dir=store_dir, use_mock=True)
        result = session.run(USER_QUERY)
        assert result.success

        ir: DesignIR = session.ir

        # Save snapshot before patch
        history = session.ir_history
        snap_id_before = history.save(ir, "before_patch")

        # Apply patch
        patch_engine = IRPatchEngine()
        ops = [IRPatchOp(op_type="update_constraint", field="v_in", value="24V")]
        pr = patch_engine.apply(ir, ops)
        assert pr.success
        patched_ir = pr.modified_ir

        # Save snapshot after patch
        snap_id_after = history.save(patched_ir, "after_patch")

        # Rollback to before
        rolled_back = history.restore(snap_id_before)
        assert rolled_back is not None
        v_in_before = next(
            (c.value for c in rolled_back.intent.known_constraints if c.name == "v_in"),
            None,
        )

        v_in_after = next(
            (c.value for c in patched_ir.intent.known_constraints if c.name == "v_in"),
            None,
        )

        assert v_in_after == "24V"
        # Original IR constraints should NOT have "24V"
        assert v_in_before != "24V" or v_in_before is None

        # Verify we can restore the patched version too
        restored_patched = history.restore(snap_id_after)
        assert restored_patched is not None
        v_in_restored = next(
            (
                c.value
                for c in restored_patched.intent.known_constraints
                if c.name == "v_in"
            ),
            None,
        )
        assert v_in_restored == "24V"
