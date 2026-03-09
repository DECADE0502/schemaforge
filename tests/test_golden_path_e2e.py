"""Golden path E2E integration test: Buck -> LDO -> MCU -> LED.

Uses skip_ai_parse=True (regex fallback) so no AI call needed.
Verifies the FULL pipeline: parse -> resolve -> connect -> synthesize -> render -> export.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from itertools import combinations
from pathlib import Path

import pytest

from schemaforge.system.session import SystemDesignSession, SystemDesignResult

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

INPUT_TEXT = (
    "用TPS54202做20V转5V降压，再用AMS1117-3.3降到3.3V，"
    "STM32F103C8T6做主控，PA1控制一颗LED指示灯"
)

_REAL_STORE = Path(__file__).resolve().parent.parent / "schemaforge" / "store"


# ------------------------------------------------------------------
# Module-scoped fixture: run the pipeline once, reuse across tests
# ------------------------------------------------------------------

@pytest.fixture(scope="module")
def golden_result() -> SystemDesignResult:
    """Run the full system design pipeline once for all tests in this module."""
    assert _REAL_STORE.exists(), f"Store not found: {_REAL_STORE}"
    session = SystemDesignSession(
        store_dir=_REAL_STORE,
        skip_ai_parse=True,
    )
    result = session.start(INPUT_TEXT)
    return result


# ------------------------------------------------------------------
# Test: pipeline produces a result
# ------------------------------------------------------------------

class TestGoldenPathPipeline:
    """Verify the pipeline completes and produces a usable result."""

    def test_golden_path_pipeline_produces_result(
        self, golden_result: SystemDesignResult,
    ) -> None:
        assert golden_result is not None
        assert golden_result.status in ("generated", "partial"), (
            f"Unexpected status: {golden_result.status!r} — {golden_result.message}"
        )
        assert golden_result.bundle is not None


# ------------------------------------------------------------------
# Test: IR has modules and connections
# ------------------------------------------------------------------

class TestGoldenPathIR:
    """Verify the design IR contains expected modules and connections."""

    def test_golden_path_ir_has_modules(
        self, golden_result: SystemDesignResult,
    ) -> None:
        ir = golden_result.bundle.design_ir
        assert len(ir.module_instances) >= 2, (
            f"Expected >= 2 modules, got {len(ir.module_instances)}: "
            f"{list(ir.module_instances.keys())}"
        )

    def test_golden_path_ir_has_power_module(
        self, golden_result: SystemDesignResult,
    ) -> None:
        ir = golden_result.bundle.design_ir
        categories = [
            inst.resolved_category
            for inst in ir.module_instances.values()
        ]
        has_power = any(
            cat in ("buck", "ldo") for cat in categories
        )
        assert has_power, (
            f"Expected at least one buck/ldo module, got categories: {categories}"
        )

    def test_golden_path_ir_has_connections(
        self, golden_result: SystemDesignResult,
    ) -> None:
        ir = golden_result.bundle.design_ir
        assert len(ir.connections) >= 1, "Expected at least 1 connection"


# ------------------------------------------------------------------
# Test: SVG output
# ------------------------------------------------------------------

class TestGoldenPathSVG:
    """Verify SVG rendering output."""

    def test_golden_path_svg_path_exists(
        self, golden_result: SystemDesignResult,
    ) -> None:
        svg_path = golden_result.bundle.svg_path
        assert svg_path, "svg_path is empty"
        assert Path(svg_path).exists(), f"SVG file not found: {svg_path}"

    def test_golden_path_svg_size(
        self, golden_result: SystemDesignResult,
    ) -> None:
        svg_path = Path(golden_result.bundle.svg_path)
        size = svg_path.stat().st_size
        assert size > 500, f"SVG too small ({size} bytes), likely incomplete"

    def test_golden_path_svg_valid_xml(
        self, golden_result: SystemDesignResult,
    ) -> None:
        svg_path = golden_result.bundle.svg_path
        # Should not raise
        tree = ET.parse(svg_path)
        root = tree.getroot()
        # SVG root element should be <svg> (possibly with namespace)
        assert "svg" in root.tag.lower(), f"Unexpected root tag: {root.tag}"


# ------------------------------------------------------------------
# Test: BOM output
# ------------------------------------------------------------------

class TestGoldenPathBOM:
    """Verify BOM text output."""

    def test_golden_path_bom_not_empty(
        self, golden_result: SystemDesignResult,
    ) -> None:
        bom = golden_result.bundle.bom_text
        assert bom and len(bom.strip()) > 0, "BOM text is empty"

    def test_golden_path_bom_contains_part(
        self, golden_result: SystemDesignResult,
    ) -> None:
        bom = golden_result.bundle.bom_text.lower()
        has_known_part = (
            "tps54202" in bom
            or "ams1117" in bom
            or "stm32" in bom
        )
        assert has_known_part, (
            f"BOM should contain at least one known part number. BOM:\n{golden_result.bundle.bom_text[:500]}"
        )


# ------------------------------------------------------------------
# Test: SPICE output
# ------------------------------------------------------------------

class TestGoldenPathSPICE:
    """Verify SPICE netlist output."""

    def test_golden_path_spice_not_empty(
        self, golden_result: SystemDesignResult,
    ) -> None:
        spice = golden_result.bundle.spice_text
        assert spice and len(spice.strip()) > 0, "SPICE text is empty"

    def test_golden_path_spice_has_ground(
        self, golden_result: SystemDesignResult,
    ) -> None:
        spice_lower = golden_result.bundle.spice_text.lower()
        assert "gnd" in spice_lower or "0" in spice_lower, (
            "SPICE should reference GND or node 0"
        )


# ------------------------------------------------------------------
# Test: render metadata
# ------------------------------------------------------------------

class TestGoldenPathRenderMetadata:
    """Verify render metadata structure and content."""

    def test_golden_path_render_metadata_exists(
        self, golden_result: SystemDesignResult,
    ) -> None:
        meta = golden_result.bundle.render_metadata
        assert meta is not None

    def test_golden_path_render_metadata_module_bboxes(
        self, golden_result: SystemDesignResult,
    ) -> None:
        meta = golden_result.bundle.render_metadata
        assert len(meta.module_bboxes) >= 2, (
            f"Expected >= 2 module bboxes, got {len(meta.module_bboxes)}: "
            f"{list(meta.module_bboxes.keys())}"
        )

    def test_golden_path_render_metadata_anchor_points(
        self, golden_result: SystemDesignResult,
    ) -> None:
        meta = golden_result.bundle.render_metadata
        assert len(meta.anchor_points) >= 2, (
            f"Expected >= 2 anchor point entries, got {len(meta.anchor_points)}"
        )

    def test_golden_path_render_metadata_canvas_size(
        self, golden_result: SystemDesignResult,
    ) -> None:
        meta = golden_result.bundle.render_metadata
        assert meta.canvas_size[0] > 0, "Canvas width should be > 0"
        assert meta.canvas_size[1] > 0, "Canvas height should be > 0"


# ------------------------------------------------------------------
# Test: no bounding box overlaps
# ------------------------------------------------------------------

class TestGoldenPathNoBBoxOverlaps:
    """Verify module bounding boxes do not overlap."""

    @staticmethod
    def _boxes_overlap(
        a: tuple[float, float, float, float],
        b: tuple[float, float, float, float],
    ) -> bool:
        """Check if two (x, y, width, height) bounding boxes overlap.

        Two boxes do NOT overlap when one is entirely to the left, right,
        above, or below the other.
        """
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        # No overlap if separated on x-axis or y-axis
        if ax + aw <= bx or bx + bw <= ax:
            return False
        if ay + ah <= by or by + bh <= ay:
            return False
        return True

    def test_golden_path_no_bbox_overlaps(
        self, golden_result: SystemDesignResult,
    ) -> None:
        bboxes = golden_result.bundle.render_metadata.module_bboxes
        if len(bboxes) < 2:
            pytest.skip("Not enough modules to check overlaps")

        items = list(bboxes.items())
        for (id_a, box_a), (id_b, box_b) in combinations(items, 2):
            assert not self._boxes_overlap(box_a, box_b), (
                f"Bounding boxes overlap: {id_a}{box_a} vs {id_b}{box_b}"
            )
