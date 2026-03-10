"""Backend regression tests — source-level CLI/GUI wiring verification.

Current system-session mainline regressions live primarily in:

- `tests/test_system_session.py`
- `tests/test_gui_wiring.py`
- `tests/test_legacy_freeze.py`
- `tests/test_session_pipeline_integration.py`

NOTE: `main.py` wraps sys.stdout at import time on Windows, which conflicts
with pytest's capture mechanism. Tests that only need to inspect CLI/GUI wiring
prefer source-level checks instead of importing those modules.
"""

from __future__ import annotations

import argparse
from pathlib import Path


# ============================================================
# Helpers — read source without importing (avoids stdout poisoning)
# ============================================================

_MAIN_PY = Path(__file__).parent.parent / "main.py"
_GUI_MAIN_WINDOW_PY = (
    Path(__file__).parent.parent / "schemaforge" / "gui" / "main_window.py"
)
_GUI_DESIGN_PY = (
    Path(__file__).parent.parent / "schemaforge" / "gui" / "pages" / "design_page.py"
)


def _main_source() -> str:
    return _MAIN_PY.read_text(encoding="utf-8")


def _gui_main_window_source() -> str:
    return _GUI_MAIN_WINDOW_PY.read_text(encoding="utf-8")


def _gui_design_source() -> str:
    if _GUI_DESIGN_PY.exists():
        return _GUI_DESIGN_PY.read_text(encoding="utf-8")
    return ""


# ============================================================
# CLI tests — main.py source-level verification
# ============================================================


class TestCLIMainPyStructure:
    """Verify main.py keeps a system default path plus legacy compatibility."""

    def test_main_py_has_store_arg(self):
        source = _main_source()
        assert '"--store"' in source

    def test_main_py_imports_system_session_only(self):
        source = _main_source()
        assert "SystemDesignSession" in source
        # SchemaForgeSession removed from main.py — unified to SystemDesignSession
        assert "SchemaForgeSession" not in source

    def test_main_py_has_agent_display(self):
        source = _main_source()
        assert "def process_via_agent(" in source

    def test_main_py_has_interactive(self):
        source = _main_source()
        assert "def run_interactive(" in source

    def test_main_py_has_orchestrator(self):
        source = _main_source()
        assert "def _build_orchestrator(" in source
        assert "Orchestrator" in source

    def test_argparse_store_flag(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--store", type=str, default="")
        args = parser.parse_args(["--store", "/some/path"])
        assert args.store == "/some/path"

    def test_argparse_defaults(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--store", type=str, default="")
        parser.add_argument("--visual-review", action="store_true")
        args = parser.parse_args([])
        assert args.store == ""
        assert args.visual_review is False


# ============================================================
# Web GUI tests — source-level verification
# ============================================================


class TestGUIStructure:
    """Verify PySide6 GUI structure exists and is properly wired."""

    def test_gui_main_window_exists(self):
        assert _GUI_MAIN_WINDOW_PY.exists(), "schemaforge/gui/main_window.py must exist"

    def test_gui_main_window_has_class(self):
        source = _gui_main_window_source()
        assert "class MainWindow" in source

    def test_gui_has_design_tab(self):
        source = _gui_main_window_source()
        assert "DesignPage" in source or "原理图设计" in source

    def test_gui_has_library_tab(self):
        source = _gui_main_window_source()
        assert "LibraryPage" in source or "器件库管理" in source

    def test_gui_design_page_exists(self):
        assert _GUI_DESIGN_PY.exists(), "design_page.py must exist"

    def test_gui_design_imports_engines(self):
        source = _gui_design_source()
        if not source:
            return  # page not yet built
        has_engine = "ClassicEngineWorker" in source
        has_worker = "SchemaForgeWorker" in source
        assert has_engine or has_worker, (
            "Design page must import at least one engine worker"
        )

    def test_gui_entry_point_exists(self):
        entry = Path(__file__).parent.parent / "gui.py"
        source = entry.read_text(encoding="utf-8")
        assert "schemaforge.gui.main_window" in source


# ============================================================
# Unified backend convergence — Rule 6 verification
# ============================================================


class TestUnifiedBackendConvergence:
    """CLI/GUI default paths stay on the system session; legacy remains opt-in."""

    def test_both_entry_points_import_same_class(self):
        main_source = _main_source()
        assert "SystemDesignSession" in main_source
        # main.py fully unified — no more SchemaForgeSession import
        assert "SchemaForgeSession" not in main_source
        # GUI imports SchemaForgeWorker, whose default worker path uses SystemDesignSession.
        design_source = _gui_design_source()
        if design_source:
            assert "SchemaForgeWorker" in design_source


# ============================================================
# Orchestrator entry point — no direct pipeline change needed
# ============================================================


class TestOrchestratorUnchanged:
    """Orchestrator uses ToolRegistry — no changes for Step 5."""

    def test_orchestrator_imports_cleanly(self):
        from schemaforge.agent import orchestrator

        assert hasattr(orchestrator, "Orchestrator")

    def test_tool_registry_imports_cleanly(self):
        from schemaforge.agent import tool_registry

        assert hasattr(tool_registry, "ToolRegistry")
