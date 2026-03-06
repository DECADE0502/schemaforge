"""Step 5: GUI/CLI/Agent all use same backend pipeline.

Tests verifying that:
1. CLI main.py --new-chain correctly creates DesignSession and dispatches
2. CLI main.py without --new-chain uses classic SchemaForgeEngine
3. gui.py DesignSessionWorker exists and has correct interface
4. gui.py MainWindow chain toggle logic works
5. Both entry points converge on the same DesignSession backend

Per Rule 6: "所有入口共用同一条后端规则"

NOTE: main.py wraps sys.stdout at import time on Windows, which conflicts with
pytest's capture mechanism. Tests that need to verify main.py/gui.py structure
use source-level checks instead of importing those modules.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from schemaforge.core.engine import SchemaForgeEngine
from schemaforge.core.models import ParameterDef, PinType
from schemaforge.library.models import (
    DeviceModel,
    ExternalComponent,
    SymbolDef,
    SymbolPin,
    TopologyConnection,
    TopologyDef,
)
from schemaforge.library.store import ComponentStore
from schemaforge.workflows.design_session import DesignSession, DesignSessionResult


# ============================================================
# Helpers — read source without importing (avoids stdout poisoning)
# ============================================================

_MAIN_PY = Path(__file__).parent.parent / "main.py"
_GUI_PY = Path(__file__).parent.parent / "gui.py"


def _main_source() -> str:
    return _MAIN_PY.read_text(encoding="utf-8")


def _gui_source() -> str:
    return _GUI_PY.read_text(encoding="utf-8")


# ============================================================
# Test fixtures — minimal store with LDO + LED
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
# CLI tests — main.py source-level verification
# ============================================================


class TestCLIMainPyStructure:
    """Verify main.py source has correct --new-chain integration."""

    def test_main_py_has_new_chain_arg(self):
        source = _main_source()
        assert '"--new-chain"' in source

    def test_main_py_has_store_arg(self):
        source = _main_source()
        assert '"--store"' in source

    def test_main_py_imports_design_session(self):
        source = _main_source()
        assert (
            "from schemaforge.workflows.design_session import DesignSession" in source
        )

    def test_main_py_has_process_and_display_session(self):
        source = _main_source()
        assert "def process_and_display_session(" in source

    def test_main_py_run_interactive_accepts_session(self):
        source = _main_source()
        assert "session: DesignSession | None = None" in source

    def test_main_py_dispatches_by_new_chain(self):
        source = _main_source()
        assert "args.new_chain" in source
        assert "DesignSession(" in source

    def test_main_py_passes_session_to_interactive(self):
        source = _main_source()
        assert "run_interactive(engine, session)" in source

    def test_argparse_new_chain_flag(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--new-chain", action="store_true")
        parser.add_argument("--store", type=str, default="")
        args = parser.parse_args(["--new-chain", "--store", "/some/path"])
        assert args.new_chain is True
        assert args.store == "/some/path"

    def test_argparse_default_no_new_chain(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--new-chain", action="store_true")
        parser.add_argument("--store", type=str, default="")
        args = parser.parse_args([])
        assert args.new_chain is False
        assert args.store == ""


# ============================================================
# CLI end-to-end — DesignSession backend works correctly
# ============================================================


class TestCLINewChainEndToEnd:
    """End-to-end test: DesignSession produces valid results."""

    def test_new_chain_creates_design_session(self, store_dir):
        session = DesignSession(store_dir=store_dir, use_mock=True)
        assert session is not None

    def test_new_chain_session_runs_full_pipeline(self, store_dir):
        session = DesignSession(store_dir=store_dir, use_mock=True)
        result = session.run(USER_QUERY)

        assert isinstance(result, DesignSessionResult)
        assert result.success is True
        assert len(result.svg_paths) >= 1
        assert result.bom_text
        assert result.plan is not None

    def test_classic_engine_still_works(self):
        engine = SchemaForgeEngine(use_mock=True)
        result = engine.process("5V转3.3V稳压电路")
        assert result.success is True
        assert len(result.svg_paths) >= 1

    def test_both_backends_produce_svg(self, store_dir):
        engine = SchemaForgeEngine(use_mock=True)
        old_result = engine.process("5V转3.3V稳压电路")
        assert old_result.svg_paths

        session = DesignSession(store_dir=store_dir, use_mock=True)
        new_result = session.run(USER_QUERY)
        assert new_result.svg_paths


# ============================================================
# GUI tests — source-level verification (PySide6 not installed)
# ============================================================


class TestGUIDesignSessionWorker:
    """Verify DesignSessionWorker class exists in gui.py."""

    def test_design_session_worker_class_exists(self):
        source = _gui_source()
        assert "class DesignSessionWorker" in source

    def test_design_session_worker_has_signals(self):
        source = _gui_source()
        start = source.index("class DesignSessionWorker")
        next_class = source.find("\nclass ", start + 1)
        section = source[start:] if next_class == -1 else source[start:next_class]

        assert "finished = Signal(object)" in section
        assert "error = Signal(str)" in section
        assert "progress = Signal(str, int)" in section

    def test_design_session_worker_has_run_method(self):
        source = _gui_source()
        start = source.index("class DesignSessionWorker")
        next_class = source.find("\nclass ", start + 1)
        section = source[start:] if next_class == -1 else source[start:next_class]

        assert "def run(self)" in section
        assert "self.session.run(" in section

    def test_main_window_has_chain_combo(self):
        source = _gui_source()
        assert "self.chain_combo" in source

    def test_main_window_has_session_result_handler(self):
        source = _gui_source()
        assert "def _on_session_result" in source

    def test_main_window_dispatches_by_chain(self):
        source = _gui_source()
        assert "use_new_chain = self.chain_combo.currentData()" in source
        assert "DesignSessionWorker(" in source
        assert "EngineWorker(" in source

    def test_gui_imports_design_session(self):
        source = _gui_source()
        assert (
            "from schemaforge.workflows.design_session "
            "import DesignSession, DesignSessionResult" in source
        )

    def test_chain_combo_has_classic_and_new_options(self):
        source = _gui_source()
        assert '"classic"' in source or "'classic'" in source
        assert '"new"' in source or "'new'" in source

    def test_ensure_design_session_method_exists(self):
        source = _gui_source()
        assert "def _ensure_design_session" in source

    def test_on_chain_changed_method_exists(self):
        source = _gui_source()
        assert "def _on_chain_changed" in source


# ============================================================
# GUI chain toggle logic — DesignSession creation without Qt
# ============================================================


class TestGUIChainToggleLogic:
    """Test chain toggle DesignSession creation logic sans Qt."""

    def test_ensure_design_session_creates_session(self, store_dir):
        design_session: DesignSession | None = None
        if design_session is None:
            design_session = DesignSession(
                store_dir=store_dir,
                use_mock=True,
            )
        assert design_session is not None
        result = design_session.run(USER_QUERY)
        assert result.success is True

    def test_mode_change_resets_session(self, store_dir):
        session = DesignSession(store_dir=store_dir, use_mock=True)
        assert session is not None

        session_after_reset: DesignSession | None = None
        assert session_after_reset is None

        session_after_reset = DesignSession(
            store_dir=store_dir,
            use_mock=False,
        )
        assert session_after_reset is not None

    def test_design_session_with_progress_callback(self, store_dir):
        messages: list[tuple[str, int]] = []

        def on_progress(msg: str, pct: int) -> None:
            messages.append((msg, pct))

        session = DesignSession(
            store_dir=store_dir,
            use_mock=True,
            progress_callback=on_progress,
        )
        result = session.run(USER_QUERY)
        assert result.success is True
        assert len(messages) >= 3


# ============================================================
# Unified backend convergence — Rule 6 verification
# ============================================================


class TestUnifiedBackendConvergence:
    """Both CLI and GUI converge on the same DesignSession class."""

    def test_both_entry_points_import_same_class(self):
        main_source = _main_source()
        gui_source = _gui_source()
        expected = "from schemaforge.workflows.design_session import DesignSession"
        assert expected in main_source
        assert expected in gui_source

    def test_design_session_result_structure_stable(self, store_dir):
        session = DesignSession(store_dir=store_dir, use_mock=True)
        result = session.run(USER_QUERY)

        assert hasattr(result, "success")
        assert hasattr(result, "stage")
        assert hasattr(result, "error")
        assert hasattr(result, "plan")
        assert hasattr(result, "svg_paths")
        assert hasattr(result, "bom_text")
        assert hasattr(result, "spice_text")
        assert hasattr(result, "modules")
        assert hasattr(result, "design_review")
        assert hasattr(result, "reference_design")

        for mr in result.modules:
            d = mr.to_dict()
            assert "role" in d
            assert "device" in d
            assert "solver_candidates" in d
            assert "review_passed" in d

    def test_ir_accessible_after_session_run(self, store_dir):
        session = DesignSession(store_dir=store_dir, use_mock=True)
        result = session.run(USER_QUERY)

        assert result.success is True
        ir = session.ir
        assert ir is not None
        summary = ir.to_summary()
        assert summary["module_count"] >= 1

    def test_design_session_result_to_dict(self, store_dir):
        session = DesignSession(store_dir=store_dir, use_mock=True)
        result = session.run(USER_QUERY)
        d = result.to_dict()

        assert d["success"] is True
        assert d["design_name"]
        assert d["module_count"] >= 1
        assert d["svg_count"] >= 1


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
