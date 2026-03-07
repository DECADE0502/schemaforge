"""Visual review loop (V071-V080).

render -> screenshot -> AI review -> score -> patch -> re-render.

The loop ties together all visual review stages:
- scoring.py: local hard metrics
- screenshot.py: PNG rendering + cropping
- critic.py: AI visual review
- patch_planner.py: issue -> patch conversion
- patch_executor.py: patch application to layout state
- system/rendering.py: SVG re-rendering

Constraints:
- VC05: IR is never modified
- VC06: max iterations bounded
- VC07: full trace recorded
- VC08: local 70% + AI 30%
- VC10: stop on no improvement
- VC11: stop on score threshold
- VC12: BOM and SPICE unchanged
"""

from __future__ import annotations

import logging

from schemaforge.system.models import SystemBundle, SystemDesignIR
from schemaforge.system.rendering import render_system_svg
from schemaforge.visual_review.critic import review_rendered_schematic
from schemaforge.visual_review.models import (
    StopReason,
    VisualReviewConfig,
    VisualReviewReport,
    VisualReviewTrace,
    VisualReviewTraceEntry,
)
from schemaforge.visual_review.patch_executor import (
    apply_visual_patches,
    create_layout_state_from_metadata,
)
from schemaforge.visual_review.patch_planner import plan_visual_patches
from schemaforge.visual_review.scoring import score_render_quality
from schemaforge.visual_review.screenshot import (
    build_review_manifest,
    render_review_images,
)

logger = logging.getLogger(__name__)


def run_visual_review_loop(
    ir: SystemDesignIR,
    bundle: SystemBundle,
    config: VisualReviewConfig | None = None,
) -> tuple[SystemBundle, VisualReviewTrace]:
    """Run the visual review loop: render -> screenshot -> AI review -> score -> patch -> re-render.

    Args:
        ir: System design IR (read-only, never modified).
        bundle: Initial system bundle with SVG.
        config: Loop configuration (max iterations, thresholds, etc).

    Returns:
        (final_bundle, trace) -- improved bundle + full trace record.

    Loop logic:
    1. Score current render (local metrics)
    2. If score meets threshold -> stop (SCORE_REACHED)
    3. Take screenshots
    4. Send to AI critic
    5. Plan patches from AI review
    6. If no actionable patches -> stop (ALL_ISSUES_RESOLVED or NO_IMPROVEMENT)
    7. Apply patches to layout state
    8. Re-render with updated layout
    9. Score new render
    10. If no improvement for N rounds -> stop (NO_IMPROVEMENT)
    11. Record trace entry
    12. If max iterations reached -> stop (MAX_ITERATIONS)
    13. Loop back to step 3
    """
    cfg = config or VisualReviewConfig()
    trace = VisualReviewTrace()

    # Initialize layout state from current render metadata
    layout_state = create_layout_state_from_metadata(bundle.render_metadata)

    current_bundle = bundle
    no_improvement_count = 0
    prev_score = 0.0

    for iteration in range(1, cfg.max_iterations + 1):
        # Step 1: score current state (local metrics only, no AI yet)
        score_before = score_render_quality(
            current_bundle.render_metadata, ir,
        )

        if iteration == 1:
            trace.initial_score = score_before.combined_score

        # Step 2: check if already good enough (VC11)
        if score_before.meets_threshold(cfg.score_threshold):
            trace.stop_reason = StopReason.SCORE_REACHED
            trace.final_score = score_before.combined_score
            trace.total_iterations = iteration - 1
            break

        # Step 3: take screenshots
        images = render_review_images(current_bundle, cfg)
        manifest = build_review_manifest(ir, current_bundle.render_metadata)

        # Step 4: AI review
        try:
            review_report = review_rendered_schematic(images, manifest)
        except Exception as exc:
            logger.warning("AI review failed: %s", exc)
            # Use empty report, rely on local scoring
            review_report = VisualReviewReport()

        # Update score with AI input
        score_before.ai_score = review_report.overall_score

        # Step 5: plan patches
        patch_plan = plan_visual_patches(
            review_report, current_bundle.render_metadata, ir,
        )

        # Step 6: if no actionable patches -> stop
        if not patch_plan.has_actions:
            trace.stop_reason = StopReason.ALL_ISSUES_RESOLVED
            trace.final_score = score_before.combined_score
            trace.total_iterations = iteration
            # Record final entry
            entry = VisualReviewTraceEntry(
                iteration=iteration,
                images=images,
                review_report=review_report,
                patch_plan=patch_plan,
                score_before=score_before,
                score_after=score_before,
            )
            trace.entries.append(entry)
            break

        # Step 7: apply patches (VC05: does not modify IR)
        layout_state = apply_visual_patches(layout_state, patch_plan)

        # Step 8: re-render with updated layout
        try:
            new_svg = render_system_svg(ir)
            current_bundle = SystemBundle(
                design_ir=ir,
                svg_path=new_svg,
                bom_text=current_bundle.bom_text,      # VC12: BOM unchanged
                bom_csv=current_bundle.bom_csv,
                spice_text=current_bundle.spice_text,   # VC12: SPICE unchanged
                render_metadata=current_bundle.render_metadata,
            )
        except Exception as exc:
            logger.warning("Re-render failed: %s", exc)
            trace.stop_reason = StopReason.ERROR
            trace.final_score = score_before.combined_score
            trace.total_iterations = iteration
            break

        # Step 9: score after
        score_after = score_render_quality(
            current_bundle.render_metadata, ir,
        )
        score_after.ai_score = review_report.overall_score

        # Step 11: record trace entry
        entry = VisualReviewTraceEntry(
            iteration=iteration,
            images=images,
            review_report=review_report,
            patch_plan=patch_plan,
            score_before=score_before,
            score_after=score_after,
            patches_applied=patch_plan.action_count,
            patches_rejected=len(patch_plan.rejected_actions),
        )
        trace.entries.append(entry)

        # Step 10: check improvement (VC10)
        if prev_score > 0:
            improvement = score_after.combined_score - prev_score
        else:
            improvement = score_after.combined_score - score_before.combined_score

        if improvement < cfg.min_improvement:
            no_improvement_count += 1
        else:
            no_improvement_count = 0

        if no_improvement_count >= cfg.no_improvement_limit:
            trace.stop_reason = StopReason.NO_IMPROVEMENT
            trace.final_score = score_after.combined_score
            trace.total_iterations = iteration
            break

        prev_score = score_after.combined_score
        trace.final_score = score_after.combined_score
        trace.total_iterations = iteration
    else:
        trace.stop_reason = StopReason.MAX_ITERATIONS
        trace.total_iterations = cfg.max_iterations

    return current_bundle, trace
