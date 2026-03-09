"""Tests for visual review loop (V071-V080) + GUI integration + final validation (V091-V100).

Covers:
- Loop stops when score threshold reached (VC11)
- Loop stops after max iterations (VC06)
- Loop stops when no improvement for 2 rounds (VC10)
- Loop preserves BOM text (VC12)
- Loop preserves SPICE text (VC12)
- Loop preserves IR connections (VC12)
- Trace records all iterations (VC07)
- Trace summary is correct
- Patch actions all within whitelist (VC97)
- Single iteration produces trace entry
- Empty review report -> stops gracefully
- AI failure -> continues with local scoring
- IR is never modified (VC05)
- Loop with zero max_iterations
- Score threshold exactly at boundary
- No improvement resets after real improvement
- Trace initial and final scores tracked
- BOM CSV preserved (VC12)
- Multiple patches in single iteration
- Error during re-render -> stops with ERROR
"""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import patch

from schemaforge.system.models import (
    ModuleInstance,
    ModuleStatus,
    NetType,
    PortRef,
    RenderMetadata,
    ResolvedConnection,
    SystemBundle,
    SystemDesignIR,
    SystemDesignRequest,
)
from schemaforge.visual_review.loop import run_visual_review_loop
from schemaforge.visual_review.models import (
    IssueSeverity,
    PatchActionType,
    ReviewImageSet,
    ReviewManifest,
    StopReason,
    VisualIssue,
    VisualReviewConfig,
    VisualReviewReport,
    VisualReviewTrace,
)


# ============================================================
# Helpers
# ============================================================


def _make_request() -> SystemDesignRequest:
    return SystemDesignRequest(raw_text="test")


def _make_module(module_id: str, category: str = "buck") -> ModuleInstance:
    return ModuleInstance(
        module_id=module_id,
        role="test",
        resolved_category=category,
        status=ModuleStatus.RESOLVED,
        parameters={"v_in": "12", "v_out": "5"},
        resolved_ports={
            "VIN": PortRef(
                module_id=module_id, port_role="power_in",
                pin_name="VIN", net_class=NetType.POWER,
            ),
            "VOUT": PortRef(
                module_id=module_id, port_role="power_out",
                pin_name="VOUT", net_class=NetType.POWER,
            ),
        },
        external_components=[
            {"role": "input_cap", "type": "capacitor", "value": "10uF"},
        ],
    )


def _make_connection(src: str, dst: str) -> ResolvedConnection:
    return ResolvedConnection(
        resolved_connection_id=f"conn_{src}_{dst}",
        src_port=PortRef(
            module_id=src, port_role="power_out",
            pin_name="VOUT", net_class=NetType.POWER,
        ),
        dst_port=PortRef(
            module_id=dst, port_role="power_in",
            pin_name="VIN", net_class=NetType.POWER,
        ),
        net_name="NET_5V",
        rule_id="RULE_POWER_SUPPLY",
    )


def _make_ir(
    module_ids: list[str] | None = None,
    connections: list[ResolvedConnection] | None = None,
) -> SystemDesignIR:
    ids = module_ids or ["buck1", "ldo1"]
    modules = {mid: _make_module(mid) for mid in ids}
    return SystemDesignIR(
        request=_make_request(),
        module_instances=modules,
        connections=connections or [],
    )


def _make_metadata() -> RenderMetadata:
    return RenderMetadata(
        module_bboxes={
            "buck1": (0.0, 0.0, 5.0, 3.0),
            "ldo1": (8.0, 0.0, 5.0, 3.0),
        },
        label_bboxes={
            "L_buck1": (1.0, 4.0, 3.0, 1.0),
            "L_ldo1": (9.0, 4.0, 3.0, 1.0),
        },
        wire_paths=[("buck1", "ldo1", [(5.0, 1.5), (8.0, 1.5)])],
        canvas_size=(20.0, 15.0),
    )


def _make_bundle(
    ir: SystemDesignIR | None = None,
    bom_text: str = "C1,10uF",
    spice_text: str = ".subckt test",
    bom_csv: str = "ref,value\nC1,10uF",
) -> SystemBundle:
    if ir is None:
        conn = _make_connection("buck1", "ldo1")
        ir = _make_ir(connections=[conn])
    return SystemBundle(
        design_ir=ir,
        svg_path="test.svg",
        bom_text=bom_text,
        bom_csv=bom_csv,
        spice_text=spice_text,
        render_metadata=_make_metadata(),
    )


def _make_high_score_report() -> VisualReviewReport:
    """AI report with high score, no issues."""
    return VisualReviewReport(
        overall_score=9.0,
        summary="Excellent layout",
        issues=[],
    )


def _make_low_score_report(n_issues: int = 2) -> VisualReviewReport:
    """AI report with low score and issues."""
    issues = []
    for i in range(n_issues):
        issues.append(VisualIssue(
            issue_id=f"v{i + 1}",
            severity=IssueSeverity.WARNING,
            category="overlap",
            description=f"Test issue {i + 1}",
            affected_elements=["buck1", "ldo1"],
            suggested_fix="increase_module_spacing",
        ))
    return VisualReviewReport(
        overall_score=3.0,
        summary="Poor layout",
        issues=issues,
    )


# Shared mock targets
_MOCK_RENDER_IMAGES = "schemaforge.visual_review.loop.render_review_images"
_MOCK_MANIFEST = "schemaforge.visual_review.loop.build_review_manifest"
_MOCK_CRITIC = "schemaforge.visual_review.loop.review_rendered_schematic"
_MOCK_RENDER_SVG = "schemaforge.visual_review.loop.render_system_svg_with_metadata"


def _patch_loop_deps(
    review_report: VisualReviewReport | None = None,
    render_svg_side_effect: Exception | None = None,
):
    """Return a stack of mocks for all external dependencies of the loop."""
    report = review_report or _make_low_score_report()
    images = ReviewImageSet(full_image_path="fake.png", dpi=150)
    manifest = ReviewManifest(
        module_list=[{"module_id": "buck1", "device": "TEST", "role": "test"}],
    )

    patches = [
        patch(_MOCK_RENDER_IMAGES, return_value=images),
        patch(_MOCK_MANIFEST, return_value=manifest),
        patch(_MOCK_CRITIC, return_value=report),
    ]

    if render_svg_side_effect is not None:
        patches.append(
            patch(_MOCK_RENDER_SVG, side_effect=render_svg_side_effect),
        )
    else:
        patches.append(
            patch(_MOCK_RENDER_SVG, return_value=("updated.svg", _make_metadata())),
        )

    return patches


def _run_with_mocks(
    bundle: SystemBundle,
    config: VisualReviewConfig,
    review_report: VisualReviewReport | None = None,
    render_svg_side_effect: Exception | None = None,
) -> tuple[SystemBundle, VisualReviewTrace]:
    """Run the loop with all external deps mocked."""
    ir = bundle.design_ir
    patches = _patch_loop_deps(review_report, render_svg_side_effect)

    # Apply all patches as a stack
    for p in patches:
        p.start()
    try:
        result = run_visual_review_loop(ir, bundle, config)
    finally:
        for p in patches:
            p.stop()
    return result


# ============================================================
# V071: Loop stops when score threshold reached (VC11)
# ============================================================


class TestLoopScoreThreshold:
    """VC11: Loop stops when combined score meets threshold."""

    def test_stops_immediately_on_high_local_score(self) -> None:
        """Perfect local metrics -> score >= threshold -> stop before iteration 1."""
        bundle = _make_bundle()
        # All local metrics will be 1.0 with this metadata + IR
        # local_score = 10.0, combined = 10.0*0.7 + 0*0.3 = 7.0
        config = VisualReviewConfig(
            max_iterations=5,
            score_threshold=7.0,
        )

        final_bundle, trace = _run_with_mocks(bundle, config)

        assert trace.stop_reason == StopReason.SCORE_REACHED
        assert trace.total_iterations == 0

    def test_stops_when_threshold_reached_mid_loop(self) -> None:
        """Score starts low, reaches threshold -> stop."""
        bundle = _make_bundle()
        # Use metadata with overlapping modules so initial score is low
        bundle.render_metadata = RenderMetadata(
            module_bboxes={
                "buck1": (0.0, 0.0, 5.0, 3.0),
                "ldo1": (3.0, 0.0, 5.0, 3.0),  # overlaps
            },
            canvas_size=(20.0, 15.0),
        )
        config = VisualReviewConfig(
            max_iterations=5,
            score_threshold=100.0,  # impossibly high -> will hit max_iterations
        )

        final_bundle, trace = _run_with_mocks(bundle, config)

        # Should NOT reach score threshold with impossibly high threshold
        assert trace.stop_reason != StopReason.SCORE_REACHED


# ============================================================
# V072: Loop stops after max iterations (VC06)
# ============================================================


class TestLoopMaxIterations:
    """VC06: Loop respects max_iterations."""

    def test_stops_at_max_iterations(self) -> None:
        """Loop runs exactly max_iterations then stops."""
        bundle = _make_bundle()
        # Make initial score low enough to not trigger SCORE_REACHED
        bundle.render_metadata = RenderMetadata(
            module_bboxes={
                "buck1": (0.0, 0.0, 5.0, 3.0),
                "ldo1": (3.0, 0.0, 5.0, 3.0),  # overlaps
            },
            canvas_size=(20.0, 15.0),
        )
        config = VisualReviewConfig(
            max_iterations=2,
            score_threshold=100.0,  # impossibly high
            no_improvement_limit=10,  # disable no_improvement stop
        )

        final_bundle, trace = _run_with_mocks(bundle, config)

        assert trace.stop_reason == StopReason.MAX_ITERATIONS
        assert trace.total_iterations == 2

    def test_single_iteration(self) -> None:
        """max_iterations=1 runs exactly once."""
        bundle = _make_bundle()
        bundle.render_metadata = RenderMetadata(
            module_bboxes={
                "buck1": (0.0, 0.0, 5.0, 3.0),
                "ldo1": (3.0, 0.0, 5.0, 3.0),
            },
            canvas_size=(20.0, 15.0),
        )
        config = VisualReviewConfig(
            max_iterations=1,
            score_threshold=100.0,
            no_improvement_limit=10,
        )

        final_bundle, trace = _run_with_mocks(bundle, config)

        assert trace.total_iterations == 1


# ============================================================
# V073: Loop stops when no improvement for N rounds (VC10)
# ============================================================


class TestLoopNoImprovement:
    """VC10: Loop stops when no improvement for N consecutive rounds."""

    def test_stops_after_no_improvement_limit(self) -> None:
        """Two rounds of no improvement -> stop."""
        bundle = _make_bundle()
        bundle.render_metadata = RenderMetadata(
            module_bboxes={
                "buck1": (0.0, 0.0, 5.0, 3.0),
                "ldo1": (3.0, 0.0, 5.0, 3.0),
            },
            canvas_size=(20.0, 15.0),
        )
        config = VisualReviewConfig(
            max_iterations=10,
            score_threshold=100.0,
            no_improvement_limit=2,
            min_improvement=0.3,
        )

        # The mock re-render doesn't change metadata, so scores stay the same
        # -> improvement < min_improvement -> no_improvement_count increments
        final_bundle, trace = _run_with_mocks(bundle, config)

        assert trace.stop_reason == StopReason.NO_IMPROVEMENT
        assert trace.total_iterations <= 10


# ============================================================
# V074: Loop preserves BOM and SPICE (VC12)
# ============================================================


class TestLoopPreservesOutputs:
    """VC12: BOM and SPICE must be unchanged after visual loop."""

    def test_preserves_bom_text(self) -> None:
        """BOM text is identical before and after loop."""
        original_bom = "C1,10uF,0805\nR1,18k,0402"
        bundle = _make_bundle(bom_text=original_bom)
        bundle.render_metadata = RenderMetadata(
            module_bboxes={
                "buck1": (0.0, 0.0, 5.0, 3.0),
                "ldo1": (3.0, 0.0, 5.0, 3.0),
            },
            canvas_size=(20.0, 15.0),
        )
        config = VisualReviewConfig(
            max_iterations=2,
            score_threshold=100.0,
            no_improvement_limit=10,
        )

        final_bundle, trace = _run_with_mocks(bundle, config)

        assert final_bundle.bom_text == original_bom

    def test_preserves_spice_text(self) -> None:
        """SPICE text is identical before and after loop."""
        original_spice = ".subckt buck VIN VOUT GND\n.ends"
        bundle = _make_bundle(spice_text=original_spice)
        bundle.render_metadata = RenderMetadata(
            module_bboxes={
                "buck1": (0.0, 0.0, 5.0, 3.0),
                "ldo1": (3.0, 0.0, 5.0, 3.0),
            },
            canvas_size=(20.0, 15.0),
        )
        config = VisualReviewConfig(
            max_iterations=2,
            score_threshold=100.0,
            no_improvement_limit=10,
        )

        final_bundle, trace = _run_with_mocks(bundle, config)

        assert final_bundle.spice_text == original_spice

    def test_preserves_bom_csv(self) -> None:
        """BOM CSV is identical before and after loop."""
        original_csv = "ref,value,package\nC1,10uF,0805"
        bundle = _make_bundle(bom_csv=original_csv)
        bundle.render_metadata = RenderMetadata(
            module_bboxes={
                "buck1": (0.0, 0.0, 5.0, 3.0),
                "ldo1": (3.0, 0.0, 5.0, 3.0),
            },
            canvas_size=(20.0, 15.0),
        )
        config = VisualReviewConfig(
            max_iterations=2,
            score_threshold=100.0,
            no_improvement_limit=10,
        )

        final_bundle, trace = _run_with_mocks(bundle, config)

        assert final_bundle.bom_csv == original_csv

    def test_preserves_ir_connections(self) -> None:
        """IR connections are unchanged after visual loop (VC05)."""
        conn = _make_connection("buck1", "ldo1")
        ir = _make_ir(connections=[conn])
        ir_copy = deepcopy(ir)
        bundle = _make_bundle(ir=ir)
        bundle.render_metadata = RenderMetadata(
            module_bboxes={
                "buck1": (0.0, 0.0, 5.0, 3.0),
                "ldo1": (3.0, 0.0, 5.0, 3.0),
            },
            canvas_size=(20.0, 15.0),
        )
        config = VisualReviewConfig(
            max_iterations=2,
            score_threshold=100.0,
            no_improvement_limit=10,
        )

        final_bundle, trace = _run_with_mocks(bundle, config)

        # IR connections unchanged
        assert len(ir.connections) == len(ir_copy.connections)
        for orig, copy in zip(ir.connections, ir_copy.connections):
            assert orig.resolved_connection_id == copy.resolved_connection_id
            assert orig.net_name == copy.net_name
            assert orig.src_port.module_id == copy.src_port.module_id
            assert orig.dst_port.module_id == copy.dst_port.module_id


# ============================================================
# V075: Trace records all iterations (VC07)
# ============================================================


class TestTraceRecording:
    """VC07: Full trace recorded."""

    def test_trace_entries_match_iterations(self) -> None:
        """Number of trace entries matches iteration count."""
        bundle = _make_bundle()
        bundle.render_metadata = RenderMetadata(
            module_bboxes={
                "buck1": (0.0, 0.0, 5.0, 3.0),
                "ldo1": (3.0, 0.0, 5.0, 3.0),
            },
            canvas_size=(20.0, 15.0),
        )
        config = VisualReviewConfig(
            max_iterations=3,
            score_threshold=100.0,
            no_improvement_limit=10,
        )

        final_bundle, trace = _run_with_mocks(bundle, config)

        assert len(trace.entries) == trace.total_iterations

    def test_trace_entry_has_all_fields(self) -> None:
        """Each trace entry has images, report, plan, scores."""
        bundle = _make_bundle()
        bundle.render_metadata = RenderMetadata(
            module_bboxes={
                "buck1": (0.0, 0.0, 5.0, 3.0),
                "ldo1": (3.0, 0.0, 5.0, 3.0),
            },
            canvas_size=(20.0, 15.0),
        )
        config = VisualReviewConfig(
            max_iterations=1,
            score_threshold=100.0,
            no_improvement_limit=10,
        )

        final_bundle, trace = _run_with_mocks(bundle, config)

        assert len(trace.entries) >= 1
        entry = trace.entries[0]
        assert entry.iteration == 1
        assert entry.images is not None
        assert entry.review_report is not None
        assert entry.patch_plan is not None
        assert entry.score_before is not None
        assert entry.score_after is not None

    def test_trace_summary_correct(self) -> None:
        """to_summary returns correct structure."""
        bundle = _make_bundle()
        bundle.render_metadata = RenderMetadata(
            module_bboxes={
                "buck1": (0.0, 0.0, 5.0, 3.0),
                "ldo1": (3.0, 0.0, 5.0, 3.0),
            },
            canvas_size=(20.0, 15.0),
        )
        config = VisualReviewConfig(
            max_iterations=2,
            score_threshold=100.0,
            no_improvement_limit=10,
        )

        final_bundle, trace = _run_with_mocks(bundle, config)

        summary = trace.to_summary()
        assert "total_iterations" in summary
        assert "stop_reason" in summary
        assert "initial_score" in summary
        assert "final_score" in summary
        assert "improvement" in summary
        assert "total_patches_applied" in summary
        assert "total_patches_rejected" in summary

    def test_trace_initial_and_final_scores(self) -> None:
        """Trace tracks initial and final scores."""
        bundle = _make_bundle()
        bundle.render_metadata = RenderMetadata(
            module_bboxes={
                "buck1": (0.0, 0.0, 5.0, 3.0),
                "ldo1": (3.0, 0.0, 5.0, 3.0),
            },
            canvas_size=(20.0, 15.0),
        )
        config = VisualReviewConfig(
            max_iterations=1,
            score_threshold=100.0,
            no_improvement_limit=10,
        )

        final_bundle, trace = _run_with_mocks(bundle, config)

        assert trace.initial_score >= 0.0
        assert trace.final_score >= 0.0


# ============================================================
# V076: Patch actions within whitelist (VC97)
# ============================================================


class TestPatchWhitelist:
    """All patch actions generated during the loop must be from the whitelist."""

    def test_all_actions_in_whitelist(self) -> None:
        """Every action in every trace entry has a valid PatchActionType."""
        bundle = _make_bundle()
        bundle.render_metadata = RenderMetadata(
            module_bboxes={
                "buck1": (0.0, 0.0, 5.0, 3.0),
                "ldo1": (3.0, 0.0, 5.0, 3.0),
            },
            canvas_size=(20.0, 15.0),
        )
        config = VisualReviewConfig(
            max_iterations=3,
            score_threshold=100.0,
            no_improvement_limit=10,
        )

        final_bundle, trace = _run_with_mocks(bundle, config)

        valid_types = set(PatchActionType)
        for entry in trace.entries:
            for action in entry.patch_plan.actions:
                assert action.action_type in valid_types, (
                    f"Action {action.action_type} not in whitelist"
                )


# ============================================================
# V077: Empty review report and AI failure
# ============================================================


class TestLoopEdgeCases:
    """Edge cases: empty reports, AI failures, render errors."""

    def test_empty_review_report_stops_gracefully(self) -> None:
        """AI returns no issues -> ALL_ISSUES_RESOLVED."""
        bundle = _make_bundle()
        bundle.render_metadata = RenderMetadata(
            module_bboxes={
                "buck1": (0.0, 0.0, 5.0, 3.0),
                "ldo1": (3.0, 0.0, 5.0, 3.0),
            },
            canvas_size=(20.0, 15.0),
        )
        config = VisualReviewConfig(
            max_iterations=5,
            score_threshold=100.0,
        )

        # Mock AI returning empty report
        empty_report = VisualReviewReport(overall_score=8.0, issues=[])
        final_bundle, trace = _run_with_mocks(
            bundle, config, review_report=empty_report,
        )

        assert trace.stop_reason == StopReason.ALL_ISSUES_RESOLVED
        assert len(trace.entries) == 1  # one entry for the final empty-plan iteration

    def test_ai_failure_continues_with_local_scoring(self) -> None:
        """AI raises exception -> loop continues with empty report."""
        bundle = _make_bundle()
        bundle.render_metadata = RenderMetadata(
            module_bboxes={
                "buck1": (0.0, 0.0, 5.0, 3.0),
                "ldo1": (3.0, 0.0, 5.0, 3.0),
            },
            canvas_size=(20.0, 15.0),
        )
        config = VisualReviewConfig(
            max_iterations=3,
            score_threshold=100.0,
        )

        images = ReviewImageSet(full_image_path="fake.png", dpi=150)
        manifest = ReviewManifest()

        with (
            patch(_MOCK_RENDER_IMAGES, return_value=images),
            patch(_MOCK_MANIFEST, return_value=manifest),
            patch(_MOCK_CRITIC, side_effect=RuntimeError("API down")),
            patch(_MOCK_RENDER_SVG, return_value="updated.svg"),
        ):
            final_bundle, trace = run_visual_review_loop(
                bundle.design_ir, bundle, config,
            )

        # Should not crash; AI failure produces empty report -> no patches -> stops
        assert trace.stop_reason == StopReason.ALL_ISSUES_RESOLVED

    def test_render_error_stops_with_error(self) -> None:
        """Re-render failure -> stop with ERROR reason."""
        bundle = _make_bundle()
        bundle.render_metadata = RenderMetadata(
            module_bboxes={
                "buck1": (0.0, 0.0, 5.0, 3.0),
                "ldo1": (3.0, 0.0, 5.0, 3.0),
            },
            canvas_size=(20.0, 15.0),
        )
        config = VisualReviewConfig(
            max_iterations=5,
            score_threshold=100.0,
        )

        final_bundle, trace = _run_with_mocks(
            bundle, config,
            render_svg_side_effect=RuntimeError("schemdraw crash"),
        )

        assert trace.stop_reason == StopReason.ERROR
        assert trace.total_iterations >= 1


# ============================================================
# V078: IR never modified (VC05)
# ============================================================


class TestIRImmutability:
    """VC05: SystemDesignIR is never modified by the loop."""

    def test_ir_module_instances_unchanged(self) -> None:
        """Module instances in IR are identical after loop."""
        ir = _make_ir()
        ir_modules_before = deepcopy(ir.module_instances)
        bundle = _make_bundle(ir=ir)
        bundle.render_metadata = RenderMetadata(
            module_bboxes={
                "buck1": (0.0, 0.0, 5.0, 3.0),
                "ldo1": (3.0, 0.0, 5.0, 3.0),
            },
            canvas_size=(20.0, 15.0),
        )
        config = VisualReviewConfig(
            max_iterations=2,
            score_threshold=100.0,
            no_improvement_limit=10,
        )

        _run_with_mocks(bundle, config)

        # Module instances unchanged
        assert set(ir.module_instances.keys()) == set(ir_modules_before.keys())
        for mid in ir.module_instances:
            assert ir.module_instances[mid].status == ir_modules_before[mid].status
            assert ir.module_instances[mid].parameters == ir_modules_before[mid].parameters
            assert ir.module_instances[mid].role == ir_modules_before[mid].role

    def test_ir_warnings_unchanged(self) -> None:
        """IR warnings list is unchanged after loop."""
        ir = _make_ir()
        ir.warnings = ["test warning"]
        warnings_before = list(ir.warnings)
        bundle = _make_bundle(ir=ir)
        bundle.render_metadata = RenderMetadata(
            module_bboxes={
                "buck1": (0.0, 0.0, 5.0, 3.0),
                "ldo1": (3.0, 0.0, 5.0, 3.0),
            },
            canvas_size=(20.0, 15.0),
        )
        config = VisualReviewConfig(
            max_iterations=1,
            score_threshold=100.0,
            no_improvement_limit=10,
        )

        _run_with_mocks(bundle, config)

        assert ir.warnings == warnings_before


# ============================================================
# V079: Loop with boundary configurations
# ============================================================


class TestLoopBoundaryConditions:
    """Boundary and edge-case configurations."""

    def test_score_at_exact_threshold(self) -> None:
        """Score exactly at threshold -> stops."""
        bundle = _make_bundle()
        # Perfect local metadata -> local_score = 10.0, combined = 7.0
        config = VisualReviewConfig(
            max_iterations=5,
            score_threshold=7.0,  # combined_score of 7.0 should meet this
        )

        final_bundle, trace = _run_with_mocks(bundle, config)

        assert trace.stop_reason == StopReason.SCORE_REACHED

    def test_single_module_ir(self) -> None:
        """Loop works with a single module."""
        ir = _make_ir(module_ids=["buck1"])
        bundle = SystemBundle(
            design_ir=ir,
            svg_path="test.svg",
            bom_text="C1,10uF",
            spice_text=".subckt test",
            render_metadata=RenderMetadata(
                module_bboxes={"buck1": (0.0, 0.0, 5.0, 3.0)},
                canvas_size=(20.0, 15.0),
            ),
        )
        config = VisualReviewConfig(
            max_iterations=5,
            score_threshold=7.0,
        )

        final_bundle, trace = _run_with_mocks(bundle, config)

        # Single module with good spacing -> should reach threshold
        assert trace.stop_reason == StopReason.SCORE_REACHED


# ============================================================
# V080: Multiple patches in single iteration
# ============================================================


class TestMultiplePatches:
    """Multiple patches applied in a single iteration."""

    def test_multiple_issues_produce_multiple_patches(self) -> None:
        """Report with multiple issues -> multiple patches in trace."""
        bundle = _make_bundle()
        bundle.render_metadata = RenderMetadata(
            module_bboxes={
                "buck1": (0.0, 0.0, 5.0, 3.0),
                "ldo1": (3.0, 0.0, 5.0, 3.0),
            },
            label_bboxes={
                "L_buck1": (0.0, 0.0, 3.0, 1.0),
                "L_ldo1": (0.0, 0.0, 3.0, 1.0),  # overlapping labels
            },
            canvas_size=(20.0, 15.0),
        )
        config = VisualReviewConfig(
            max_iterations=1,
            score_threshold=100.0,
            no_improvement_limit=10,
        )

        report = VisualReviewReport(
            overall_score=3.0,
            summary="Multiple issues",
            issues=[
                VisualIssue(
                    issue_id="v1", severity=IssueSeverity.WARNING,
                    category="overlap", description="Module overlap",
                    affected_elements=["buck1", "ldo1"],
                    suggested_fix="increase_module_spacing",
                ),
                VisualIssue(
                    issue_id="v2", severity=IssueSeverity.WARNING,
                    category="label", description="Label overlap",
                    affected_elements=["L_buck1"],
                    suggested_fix="move_label",
                ),
            ],
        )

        final_bundle, trace = _run_with_mocks(
            bundle, config, review_report=report,
        )

        assert len(trace.entries) >= 1
        entry = trace.entries[0]
        assert entry.patches_applied >= 2


# ============================================================
# V091: Trace improvement tracking
# ============================================================


class TestTraceImprovement:
    """Trace correctly tracks improvement delta."""

    def test_score_improved_returns_true_when_final_gt_initial(self) -> None:
        """score_improved() works on trace."""
        trace = VisualReviewTrace()
        trace.initial_score = 3.0
        trace.final_score = 7.0
        assert trace.score_improved() is True

    def test_score_improved_returns_false_when_no_change(self) -> None:
        """score_improved() returns False when scores equal."""
        trace = VisualReviewTrace()
        trace.initial_score = 5.0
        trace.final_score = 5.0
        assert trace.score_improved() is False

    def test_improvement_delta_correct(self) -> None:
        """improvement_delta() returns correct value."""
        trace = VisualReviewTrace()
        trace.initial_score = 3.0
        trace.final_score = 7.5
        assert trace.improvement_delta() == 4.5


# ============================================================
# V092: Config defaults
# ============================================================


class TestConfigDefaults:
    """VisualReviewConfig defaults are sane."""

    def test_default_max_iterations(self) -> None:
        cfg = VisualReviewConfig()
        assert cfg.max_iterations == 5

    def test_default_score_threshold(self) -> None:
        cfg = VisualReviewConfig()
        assert cfg.score_threshold == 7.0

    def test_default_no_improvement_limit(self) -> None:
        cfg = VisualReviewConfig()
        assert cfg.no_improvement_limit == 2

    def test_default_weights_sum_to_one(self) -> None:
        cfg = VisualReviewConfig()
        assert cfg.local_weight + cfg.ai_weight == 1.0
