"""Step 6: Buck converter — second complex circuit type.

Full pipeline tests: device load, template, clarifier, candidate scoring,
review rules (7 buck + 3 rc_filter), reference design, topology adapter,
and end-to-end pipeline integration.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from schemaforge.core.templates import TEMPLATE_REGISTRY, get_template
from schemaforge.design.candidate_solver import CandidateSolver, SolverResult
from schemaforge.design.clarifier import RequirementClarifier
from schemaforge.design.ir import ReviewSeverity
from schemaforge.design.planner import DesignPlan, DesignPlanner, ModuleRequirement
from schemaforge.design.review import DesignReviewEngine, ModuleReviewInput
from schemaforge.library.models import DeviceModel
from schemaforge.library.reference_models import ReferenceDesignStore
from schemaforge.library.store import ComponentStore


# ============================================================
# Fixtures
# ============================================================

STORE_DIR = Path(__file__).resolve().parent.parent / "schemaforge" / "store"
DEVICE_DIR = STORE_DIR / "devices"
REF_DIR = STORE_DIR / "reference_designs"


def _build_buck_device() -> DeviceModel:
    json_path = DEVICE_DIR / "TPS5430.json"
    assert json_path.exists(), f"TPS5430.json not found at {json_path}"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    return DeviceModel(**data)


def _build_temp_store_with_buck() -> tuple[ComponentStore, str]:
    tmp = tempfile.mkdtemp()
    store = ComponentStore(store_dir=Path(tmp))
    device = _build_buck_device()
    store.save_device(device)
    return store, tmp


# ============================================================
# 1. Device Model Load
# ============================================================


class TestBuckDeviceModel:
    def test_load_from_json(self):
        device = _build_buck_device()
        assert device.part_number == "TPS5430"
        assert device.category == "buck"
        assert device.manufacturer == "TI"

    def test_symbol_has_6_pins(self):
        device = _build_buck_device()
        assert device.symbol is not None
        assert len(device.symbol.pins) == 6
        pin_names = {p.name for p in device.symbol.pins}
        assert pin_names == {"VIN", "EN", "BST", "SW", "FB", "GND"}

    def test_topology_has_7_external_components(self):
        device = _build_buck_device()
        assert device.topology is not None
        assert device.topology.circuit_type == "buck"
        assert len(device.topology.external_components) == 7
        roles = {c.role for c in device.topology.external_components}
        expected = {
            "input_cap",
            "output_cap",
            "inductor",
            "boot_cap",
            "fb_upper",
            "fb_lower",
            "catch_diode",
        }
        assert roles == expected

    def test_topology_connections(self):
        device = _build_buck_device()
        assert device.topology is not None
        net_names = {c.net_name for c in device.topology.connections}
        assert "VIN" in net_names
        assert "SW" in net_names
        assert "VOUT" in net_names
        assert "FB" in net_names
        assert "GND" in net_names

    def test_8_design_knowledge_fields(self):
        device = _build_buck_device()
        assert len(device.design_roles) >= 2
        assert len(device.selection_hints) >= 3
        assert len(device.anti_patterns) >= 3
        assert len(device.required_companions) >= 4
        assert len(device.operating_constraints) >= 3
        assert len(device.layout_hints) >= 4
        assert len(device.failure_modes) >= 4
        assert len(device.review_rules) >= 3

    def test_specs_contain_key_params(self):
        device = _build_buck_device()
        assert "v_in_max" in device.specs
        assert "i_out_max" in device.specs
        assert "fsw" in device.specs

    def test_topology_parameters(self):
        device = _build_buck_device()
        assert device.topology is not None
        param_names = set(device.topology.parameters.keys())
        assert "v_in" in param_names
        assert "v_out" in param_names
        assert "i_out_max" in param_names
        assert "fsw" in param_names

    def test_topology_calculations(self):
        device = _build_buck_device()
        assert device.topology is not None
        assert "duty_cycle" in device.topology.calculations
        assert "fb_ratio" in device.topology.calculations


# ============================================================
# 2. Template
# ============================================================


class TestBuckTemplate:
    def test_buck_converter_registered(self):
        assert "buck_converter" in TEMPLATE_REGISTRY

    def test_template_components(self):
        tpl = get_template("buck_converter")
        assert tpl is not None
        assert len(tpl.components) == 7
        ref_prefixes = [c.ref_prefix for c in tpl.components]
        assert "U" in ref_prefixes
        assert "L" in ref_prefixes
        assert "D" in ref_prefixes

    def test_template_parameters(self):
        tpl = get_template("buck_converter")
        assert tpl is not None
        assert "v_in" in tpl.parameters
        assert "v_out" in tpl.parameters
        assert "i_out_max" in tpl.parameters
        assert "ic_model" in tpl.parameters
        assert "l_value" in tpl.parameters

    def test_template_nets(self):
        tpl = get_template("buck_converter")
        assert tpl is not None
        net_names = {n.name for n in tpl.net_template}
        assert "VIN" in net_names
        assert "SW" in net_names
        assert "VOUT" in net_names
        assert "FB" in net_names
        assert "GND" in net_names

    def test_template_calculations(self):
        tpl = get_template("buck_converter")
        assert tpl is not None
        assert "duty_cycle" in tpl.calculations
        assert "r_fb_upper" in tpl.calculations
        assert "r_fb_lower" in tpl.calculations

    def test_template_category_is_power(self):
        tpl = get_template("buck_converter")
        assert tpl is not None
        assert tpl.category == "power"


# ============================================================
# 3. Clarifier
# ============================================================


class TestBuckClarifier:
    def _make_plan(self, params: dict[str, str]) -> DesignPlan:
        return DesignPlan(
            modules=[
                ModuleRequirement(
                    role="main_buck",
                    category="buck",
                    description="Buck降压电路",
                    parameters=params,
                )
            ],
        )

    def test_complete_params_can_proceed(self):
        clarifier = RequirementClarifier()
        plan = self._make_plan({"v_in": "12V", "v_out": "3.3V"})
        result = clarifier.clarify("12V转3.3V降压", plan)
        assert result.can_proceed

    def test_missing_v_in_blocks(self):
        clarifier = RequirementClarifier()
        plan = self._make_plan({"v_out": "3.3V"})
        result = clarifier.clarify("转3.3V降压", plan)
        assert not result.can_proceed
        fields = {q.field for q in result.missing_required}
        assert "v_in" in fields

    def test_missing_v_out_blocks(self):
        clarifier = RequirementClarifier()
        plan = self._make_plan({"v_in": "12V"})
        result = clarifier.clarify("12V降压", plan)
        assert not result.can_proceed
        fields = {q.field for q in result.missing_required}
        assert "v_out" in fields

    def test_optional_assumptions_generated(self):
        clarifier = RequirementClarifier()
        plan = self._make_plan({"v_in": "12V", "v_out": "3.3V"})
        result = clarifier.clarify("12V转3.3V", plan)
        assumption_fields = {a.field for a in result.assumptions}
        assert "i_out_max" in assumption_fields
        assert "fsw" in assumption_fields
        assert "efficiency_target" in assumption_fields

    def test_confidence_complete(self):
        clarifier = RequirementClarifier()
        plan = self._make_plan({"v_in": "12V", "v_out": "3.3V"})
        result = clarifier.clarify("12V转3.3V", plan)
        assert result.confidence > 0.5


# ============================================================
# 4. Candidate Solver
# ============================================================


class TestBuckCandidateSolver:
    def test_solver_finds_buck_device(self):
        store, tmp = _build_temp_store_with_buck()
        try:
            solver = CandidateSolver(store)
            req = ModuleRequirement(
                role="main_buck",
                category="buck",
                description="12V转3.3V降压",
                parameters={"v_in": "12", "v_out": "3.3", "i_out_max": "1"},
            )
            result = solver.solve(req, max_candidates=3)
            assert isinstance(result, SolverResult)
            assert result.module_category == "buck"
            assert len(result.candidates) >= 1
            assert result.recommended is not None
            assert result.recommended.device.part_number == "TPS5430"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_solver_scores_positive(self):
        store, tmp = _build_temp_store_with_buck()
        try:
            solver = CandidateSolver(store)
            req = ModuleRequirement(
                role="main_buck",
                category="buck",
                description="Buck降压",
                parameters={"v_in": "12", "v_out": "3.3", "i_out_max": "1"},
            )
            result = solver.solve(req)
            assert result.recommended is not None
            assert result.recommended.total_score > 0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_solver_constraint_satisfaction_buck(self):
        store, tmp = _build_temp_store_with_buck()
        try:
            solver = CandidateSolver(store)
            req = ModuleRequirement(
                role="main_buck",
                category="buck",
                description="Buck降压",
                parameters={"v_in": "12", "v_out": "3.3", "i_out_max": "2"},
            )
            result = solver.solve(req)
            candidate = result.recommended
            assert candidate is not None
            cs = next(
                s for s in candidate.scores if s.name == "constraint_satisfaction"
            )
            assert cs.score > 0.5
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_solver_thermal_risk_buck(self):
        store, tmp = _build_temp_store_with_buck()
        try:
            solver = CandidateSolver(store)
            req = ModuleRequirement(
                role="main_buck",
                category="buck",
                description="Buck降压",
                parameters={"v_in": "24", "v_out": "3.3", "i_out_max": "3"},
            )
            result = solver.solve(req)
            candidate = result.recommended
            assert candidate is not None
            tr = next(s for s in candidate.scores if s.name == "thermal_risk")
            assert tr.score > 0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ============================================================
# 5. Review Engine — Buck Rules
# ============================================================


class TestBuckReviewRules:
    def _make_input(self, params: dict[str, str]) -> ModuleReviewInput:
        device = _build_buck_device()
        return ModuleReviewInput(
            role="main_buck",
            category="buck",
            device=device,
            parameters=params,
        )

    def test_inductor_saturation_rule_present(self):
        engine = DesignReviewEngine()
        module = self._make_input({"v_in": "12V", "v_out": "3.3V", "i_out_max": "2A"})
        result = engine.review_module(module)
        rule_ids = {i.rule_id for i in result.issues}
        assert "buck_inductor_saturation" in rule_ids

    def test_input_cap_rms_high_current(self):
        engine = DesignReviewEngine()
        module = self._make_input(
            {
                "v_in": "24V",
                "v_out": "5V",
                "i_out_max": "3A",
            }
        )
        result = engine.review_module(module)
        rule_ids = {i.rule_id for i in result.issues}
        assert "buck_input_cap_rms" in rule_ids

    def test_output_ripple_present(self):
        engine = DesignReviewEngine()
        module = self._make_input(
            {
                "v_in": "12V",
                "v_out": "3.3V",
                "fsw": "500kHz",
            }
        )
        result = engine.review_module(module)
        rule_ids = {i.rule_id for i in result.issues}
        assert "buck_output_ripple" in rule_ids

    def test_feedback_accuracy_present(self):
        engine = DesignReviewEngine()
        module = self._make_input({"v_out": "3.3V"})
        result = engine.review_module(module)
        rule_ids = {i.rule_id for i in result.issues}
        assert "buck_feedback_accuracy" in rule_ids

    def test_max_vin_exceeded_blocking(self):
        engine = DesignReviewEngine()
        module = self._make_input({"v_in": "48V", "v_out": "3.3V"})
        result = engine.review_module(module)
        blocking = [
            i
            for i in result.issues
            if i.rule_id == "buck_max_vin_exceeded"
            and i.severity == ReviewSeverity.BLOCKING
        ]
        assert len(blocking) == 1
        assert not result.passed

    def test_normal_vin_no_exceeded(self):
        engine = DesignReviewEngine()
        module = self._make_input({"v_in": "12V", "v_out": "3.3V"})
        result = engine.review_module(module)
        exceeded = [i for i in result.issues if i.rule_id == "buck_max_vin_exceeded"]
        assert len(exceeded) == 0

    def test_bootstrap_cap_recommendation(self):
        device = _build_buck_device()
        no_boot_comps = [
            c for c in device.topology.external_components if c.role != "boot_cap"
        ]
        device_no_boot = device.model_copy(
            update={
                "topology": device.topology.model_copy(
                    update={"external_components": no_boot_comps}
                )
            }
        )
        module = ModuleReviewInput(
            role="main_buck",
            category="buck",
            device=device_no_boot,
            parameters={},
        )
        engine = DesignReviewEngine()
        result = engine.review_module(module)
        rule_ids = {i.rule_id for i in result.issues}
        assert "buck_bootstrap_cap" in rule_ids

    def test_layout_note_present(self):
        engine = DesignReviewEngine()
        module = self._make_input({"v_in": "12V", "v_out": "3.3V"})
        result = engine.review_module(module)
        layout = [i for i in result.issues if i.severity == ReviewSeverity.LAYOUT_NOTE]
        assert len(layout) >= 1
        rule_ids = {i.rule_id for i in layout}
        assert "buck_layout_loop" in rule_ids

    def test_bringup_note_present(self):
        engine = DesignReviewEngine()
        module = self._make_input({"v_in": "12V", "v_out": "3.3V"})
        result = engine.review_module(module)
        bringup = [
            i for i in result.issues if i.severity == ReviewSeverity.BRINGUP_NOTE
        ]
        assert len(bringup) >= 1
        rule_ids = {i.rule_id for i in bringup}
        assert "buck_bringup_softstart" in rule_ids

    def test_review_passes_normal_design(self):
        engine = DesignReviewEngine()
        module = self._make_input(
            {
                "v_in": "12V",
                "v_out": "3.3V",
                "i_out_max": "1A",
            }
        )
        result = engine.review_module(module)
        blocking = [i for i in result.issues if i.severity == ReviewSeverity.BLOCKING]
        blocking_ids = {i.rule_id for i in blocking}
        blocking_ids.discard("buck_inductor_saturation")
        blocking_ids.discard("buck_feedback_accuracy")
        assert len(blocking_ids) == 0


# ============================================================
# 6. RC Filter Review Rules (deepening existing type)
# ============================================================


class TestRCFilterReviewRules:
    def _make_rc_input(self, params: dict[str, str]) -> ModuleReviewInput:
        data = json.loads((DEVICE_DIR / "RC_LOWPASS.json").read_text(encoding="utf-8"))
        device = DeviceModel(**data)
        return ModuleReviewInput(
            role="filter_1",
            category="rc_filter",
            device=device,
            parameters=params,
        )

    def test_high_impedance_warning(self):
        engine = DesignReviewEngine()
        module = self._make_rc_input({"r_value": "200000"})
        result = engine.review_module(module)
        rule_ids = {i.rule_id for i in result.issues}
        assert "rc_filter_impedance_mismatch" in rule_ids

    def test_low_impedance_warning(self):
        engine = DesignReviewEngine()
        module = self._make_rc_input({"r_value": "50"})
        result = engine.review_module(module)
        rule_ids = {i.rule_id for i in result.issues}
        assert "rc_filter_impedance_mismatch" in rule_ids

    def test_normal_impedance_no_warning(self):
        engine = DesignReviewEngine()
        module = self._make_rc_input({"r_value": "10000"})
        result = engine.review_module(module)
        impedance_warnings = [
            i for i in result.issues if i.rule_id == "rc_filter_impedance_mismatch"
        ]
        assert len(impedance_warnings) == 0

    def test_high_cutoff_recommendation(self):
        engine = DesignReviewEngine()
        module = self._make_rc_input({"f_cutoff": "5000000"})
        result = engine.review_module(module)
        rule_ids = {i.rule_id for i in result.issues}
        assert "rc_filter_cutoff_range" in rule_ids

    def test_load_effect_always_present(self):
        engine = DesignReviewEngine()
        module = self._make_rc_input({})
        result = engine.review_module(module)
        rule_ids = {i.rule_id for i in result.issues}
        assert "rc_filter_load_effect" in rule_ids


# ============================================================
# 7. Reference Design
# ============================================================


class TestBuckReferenceDesign:
    def test_ref_buck_basic_exists(self):
        ref_path = REF_DIR / "ref_buck_basic.json"
        assert ref_path.exists()
        data = json.loads(ref_path.read_text(encoding="utf-8"))
        assert data["ref_id"] == "ref_buck_basic"
        assert "buck" in data["applicable_categories"]

    def test_ref_design_store_finds_buck(self):
        store = ReferenceDesignStore(str(REF_DIR))
        results = store.search(category="buck")
        assert len(results) >= 1
        assert any(r.ref_id == "ref_buck_basic" for r in results)

    def test_ref_design_has_replaceable_devices(self):
        ref_path = REF_DIR / "ref_buck_basic.json"
        data = json.loads(ref_path.read_text(encoding="utf-8"))
        assert "main_regulator" in data.get("replaceable_devices", {})
        devices = data["replaceable_devices"]["main_regulator"]
        assert "TPS5430" in devices

    def test_ref_design_has_required_components(self):
        ref_path = REF_DIR / "ref_buck_basic.json"
        data = json.loads(ref_path.read_text(encoding="utf-8"))
        assert len(data.get("required_components", [])) >= 4


# ============================================================
# 8. Topology Adapter
# ============================================================


class TestBuckTopologyAdapter:
    def test_adapter_maps_buck_to_buck_converter(self):
        from schemaforge.design.topology_adapter import TopologyAdapter

        device = _build_buck_device()
        adapter = TopologyAdapter()
        adapted = adapter.adapt_single(
            device, parameters={"v_in": "12", "v_out": "3.3"}, role="main_buck"
        )
        spec = adapted.to_design_spec_module()
        assert spec["template"] == "buck_converter"
        assert spec["instance_name"] == "main_buck"

    def test_adapter_merges_parameters(self):
        from schemaforge.design.topology_adapter import TopologyAdapter

        device = _build_buck_device()
        adapter = TopologyAdapter()
        adapted = adapter.adapt_single(
            device, parameters={"v_in": "24", "v_out": "5"}, role="dc_dc"
        )
        assert adapted.render_params.get("v_in") == "24"
        assert adapted.render_params.get("v_out") == "5"


# ============================================================
# 9. Full Pipeline E2E
# ============================================================


class TestBuckE2EPipeline:
    def test_clarify_to_review_pipeline(self):
        store, tmp = _build_temp_store_with_buck()
        try:
            planner = DesignPlanner()
            plan = planner.plan("12V转3.3V Buck降压电路")
            assert len(plan.modules) >= 1

            buck_mod = None
            for m in plan.modules:
                if m.category == "buck":
                    buck_mod = m
                    break
            if buck_mod is None:
                buck_mod = ModuleRequirement(
                    role="main_buck",
                    category="buck",
                    description="Buck降压",
                    parameters={"v_in": "12", "v_out": "3.3"},
                )

            clarifier = RequirementClarifier()
            clar_result = clarifier.clarify(
                "12V转3.3V Buck降压",
                DesignPlan(
                    modules=[buck_mod],
                ),
            )
            if not clar_result.can_proceed:
                for a in clar_result.assumptions:
                    buck_mod.parameters.setdefault(a.field, a.assumed_value)
                buck_mod.parameters.setdefault("v_in", "12")
                buck_mod.parameters.setdefault("v_out", "3.3")

            solver = CandidateSolver(store)
            solver_result = solver.solve(buck_mod)
            assert solver_result.recommended is not None

            device = solver_result.recommended.device
            engine = DesignReviewEngine()
            review_input = ModuleReviewInput(
                role="main_buck",
                category="buck",
                device=device,
                parameters={"v_in": "12V", "v_out": "3.3V", "i_out_max": "1A"},
            )
            review = engine.review_module(review_input)
            assert len(review.issues) > 0

            ref_store = ReferenceDesignStore(str(REF_DIR))
            refs = ref_store.search(category="buck")
            assert len(refs) >= 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_cross_module_buck_plus_led(self):
        engine = DesignReviewEngine()
        buck_device = _build_buck_device()
        led_data = json.loads(
            (DEVICE_DIR / "LED_INDICATOR.json").read_text(encoding="utf-8")
        )
        led_device = DeviceModel(**led_data)

        buck_input = ModuleReviewInput(
            role="dc_dc",
            category="buck",
            device=buck_device,
            parameters={"v_in": "24V", "v_out": "3.3V", "i_out_max": "2A"},
        )
        led_input = ModuleReviewInput(
            role="indicator",
            category="led",
            device=led_device,
            parameters={"v_supply": "3.3V", "led_vf": "2.2V", "led_current": "0.01"},
        )

        review = engine.review_design([buck_input, led_input])
        rule_ids = {i.rule_id for i in review.issues}
        assert "buck_inductor_saturation" in rule_ids
        assert "ground_path_check" in rule_ids

    def test_total_template_count_is_5(self):
        assert len(TEMPLATE_REGISTRY) == 5
        assert "buck_converter" in TEMPLATE_REGISTRY
        assert "ldo_regulator" in TEMPLATE_REGISTRY
        assert "voltage_divider" in TEMPLATE_REGISTRY
        assert "led_indicator" in TEMPLATE_REGISTRY
        assert "rc_lowpass" in TEMPLATE_REGISTRY
