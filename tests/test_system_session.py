"""Tests for schemaforge.system.session (T091-T100).

验证系统级设计会话的完整管线：
- 全新设计（start / start_from_request）
- 部分设计（模块缺失）
- 模块替换（replace_module）
- 修订（revise）
- 错误处理
- 会话状态（ir / bundle 属性）

使用真实 store 数据（schemaforge/store/）进行集成测试。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from schemaforge.system.models import (
    ConnectionIntent,
    ConnectionSemantic,
    ModuleIntent,
    ModuleStatus,
    SignalType,
    SystemDesignRequest,
)
from schemaforge.system.session import SystemDesignSession


# ============================================================
# Fixtures
# ============================================================

_REAL_STORE = Path(__file__).resolve().parent.parent / "schemaforge" / "store"


@pytest.fixture()
def real_store_dir() -> Path:
    """Return path to the real store directory (read-only usage)."""
    assert _REAL_STORE.exists(), f"Store not found: {_REAL_STORE}"
    return _REAL_STORE


@pytest.fixture()
def tmp_store(real_store_dir: Path, tmp_path: Path) -> Path:
    """Copy real store to a temp directory for isolation."""
    dest = tmp_path / "store"
    shutil.copytree(real_store_dir, dest)
    return dest


def _buck_ldo_request() -> SystemDesignRequest:
    """TPS5430 12V->5V + AMS1117 5V->3.3V two-stage power."""
    return SystemDesignRequest(
        raw_text="TPS5430 12V->5V + AMS1117 5V->3.3V",
        modules=[
            ModuleIntent(
                intent_id="buck1",
                role="降压",
                part_number_hint="TPS5430",
                category_hint="buck",
                electrical_targets={"v_in": "12", "v_out": "5"},
            ),
            ModuleIntent(
                intent_id="ldo1",
                role="稳压",
                part_number_hint="AMS1117-3.3",
                category_hint="ldo",
                electrical_targets={"v_in": "5", "v_out": "3.3"},
            ),
        ],
        connections=[
            ConnectionIntent(
                connection_id="c1",
                src_module_intent="buck1",
                dst_module_intent="ldo1",
                signal_type=SignalType.POWER_SUPPLY,
                connection_semantics=ConnectionSemantic.SUPPLY_CHAIN,
            ),
        ],
        global_v_in="12",
    )


def _buck_only_request() -> SystemDesignRequest:
    """Single TPS5430 Buck module."""
    return SystemDesignRequest(
        raw_text="TPS5430 12V->5V",
        modules=[
            ModuleIntent(
                intent_id="buck1",
                role="降压",
                part_number_hint="TPS5430",
                category_hint="buck",
                electrical_targets={"v_in": "12", "v_out": "5"},
            ),
        ],
        connections=[],
        global_v_in="12",
    )


# ============================================================
# T099: ULTIMATE TEST — Full Buck+LDO scenario
# ============================================================


class TestFullBuckLdoScenario:
    """T099: Two-stage power: TPS5430 12V->5V + AMS1117 5V->3.3V."""

    def test_full_scenario_buck_ldo(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        request = _buck_ldo_request()
        result = session.start_from_request(request)

        assert result.status == "generated"
        assert result.bundle is not None
        assert result.bundle.svg_path
        assert "TPS5430" in result.bundle.bom_text
        assert "AMS1117" in result.bundle.bom_text
        assert result.bundle.spice_text
        assert os.path.exists(result.bundle.svg_path)

    def test_full_scenario_ir_modules(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        result = session.start_from_request(_buck_ldo_request())

        ir = result.bundle.design_ir
        assert "buck1" in ir.module_instances
        assert "ldo1" in ir.module_instances
        buck = ir.module_instances["buck1"]
        ldo = ir.module_instances["ldo1"]
        assert buck.status == ModuleStatus.SYNTHESIZED
        assert ldo.status == ModuleStatus.SYNTHESIZED

    def test_full_scenario_connections(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        result = session.start_from_request(_buck_ldo_request())

        ir = result.bundle.design_ir
        # Should have at least one inter-module connection plus special-pin connections
        assert len(ir.connections) >= 1
        # Should have nets
        assert len(ir.nets) >= 1
        # GND net should exist
        assert "GND" in ir.nets

    def test_full_scenario_external_components(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        result = session.start_from_request(_buck_ldo_request())

        ir = result.bundle.design_ir
        buck = ir.module_instances["buck1"]
        ldo = ir.module_instances["ldo1"]
        # Buck should have external components (inductor, caps, resistors, diode)
        assert len(buck.external_components) >= 5
        # LDO should have at least input and output cap
        assert len(ldo.external_components) >= 2


# ============================================================
# Single module tests
# ============================================================


class TestSingleModule:
    """Single Buck module design."""

    def test_single_buck_generated(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        result = session.start_from_request(_buck_only_request())

        assert result.status == "generated"
        assert result.bundle is not None
        assert result.bundle.bom_text
        assert "TPS5430" in result.bundle.bom_text

    def test_single_buck_spice(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        result = session.start_from_request(_buck_only_request())

        assert result.bundle is not None
        assert result.bundle.spice_text
        assert ".end" in result.bundle.spice_text

    def test_single_buck_svg_exists(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        result = session.start_from_request(_buck_only_request())

        assert result.bundle is not None
        assert result.bundle.svg_path
        assert os.path.exists(result.bundle.svg_path)


# ============================================================
# Partial design (module missing)
# ============================================================


class TestPartialDesign:
    """Some modules resolved, some not."""

    def test_partial_one_missing(self, tmp_store: Path) -> None:
        """One module exists (TPS5430), one does not (FAKE_DEVICE)."""
        request = SystemDesignRequest(
            raw_text="TPS5430 + FAKE_DEVICE",
            modules=[
                ModuleIntent(
                    intent_id="buck1",
                    role="降压",
                    part_number_hint="TPS5430",
                    category_hint="buck",
                    electrical_targets={"v_in": "12", "v_out": "5"},
                ),
                ModuleIntent(
                    intent_id="unknown1",
                    role="未知",
                    part_number_hint="FAKE_DEVICE_XYZ",
                    category_hint="",
                    electrical_targets={},
                ),
            ],
            connections=[],
        )
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        result = session.start_from_request(request)

        assert result.status == "partial"
        assert "unknown1" in result.missing_modules
        assert "buck1" not in result.missing_modules
        # Bundle should still be produced with whatever succeeded
        assert result.bundle is not None

    def test_all_missing_returns_error(self, tmp_store: Path) -> None:
        """All modules missing -> error status."""
        request = SystemDesignRequest(
            raw_text="nothing found",
            modules=[
                ModuleIntent(
                    intent_id="x1",
                    role="未知",
                    part_number_hint="DOESNT_EXIST_1",
                    category_hint="",
                ),
                ModuleIntent(
                    intent_id="x2",
                    role="未知",
                    part_number_hint="DOESNT_EXIST_2",
                    category_hint="",
                ),
            ],
            connections=[],
        )
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        result = session.start_from_request(request)

        assert result.status == "error"
        assert len(result.missing_modules) == 2


# ============================================================
# LED indicator module
# ============================================================


class TestLedModule:
    """Module with LED indicator (LED_INDICATOR device has symbol=null)."""

    def test_led_resolves_by_category(self, tmp_store: Path) -> None:
        """LED_INDICATOR is in store with category='led'."""
        request = SystemDesignRequest(
            raw_text="LED indicator",
            modules=[
                ModuleIntent(
                    intent_id="led1",
                    role="指示灯",
                    part_number_hint="LED_INDICATOR",
                    category_hint="led",
                    electrical_targets={"v_supply": "3.3", "led_color": "green"},
                ),
            ],
            connections=[],
        )
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        result = session.start_from_request(request)

        # LED_INDICATOR has symbol=null, so it will have no ports,
        # but it should still resolve (device is found in store)
        assert result.bundle is not None
        ir = result.bundle.design_ir
        led = ir.module_instances.get("led1")
        assert led is not None
        # Device found => RESOLVED or SYNTHESIZED
        assert led.status in (ModuleStatus.RESOLVED, ModuleStatus.SYNTHESIZED)

    def test_buck_plus_led(self, tmp_store: Path) -> None:
        """Buck + LED indicator combination."""
        request = SystemDesignRequest(
            raw_text="TPS5430 + LED",
            modules=[
                ModuleIntent(
                    intent_id="buck1",
                    role="降压",
                    part_number_hint="TPS5430",
                    category_hint="buck",
                    electrical_targets={"v_in": "12", "v_out": "5"},
                ),
                ModuleIntent(
                    intent_id="led1",
                    role="指示灯",
                    part_number_hint="LED_INDICATOR",
                    category_hint="led",
                    electrical_targets={"v_supply": "5", "led_color": "green"},
                ),
            ],
            connections=[],
        )
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        result = session.start_from_request(request)

        assert result.bundle is not None
        assert result.bundle.bom_text


# ============================================================
# Replace module
# ============================================================


class TestReplaceModule:
    """replace_module() swaps a device and re-synthesizes."""

    def test_replace_buck_device(self, tmp_store: Path) -> None:
        """Replace TPS5430 with TPS54202 (also a buck in store)."""
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        result = session.start_from_request(_buck_only_request())
        assert result.status == "generated"

        # Now replace with TPS54202
        result2 = session.replace_module("buck1", "TPS54202")
        assert result2.status == "generated"
        assert result2.bundle is not None
        assert "TPS54202" in result2.bundle.bom_text

    def test_replace_nonexistent_module(self, tmp_store: Path) -> None:
        """Replacing a module that doesn't exist returns error."""
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        session.start_from_request(_buck_only_request())

        result = session.replace_module("no_such_module", "TPS5430")
        assert result.status == "error"

    def test_replace_with_missing_device(self, tmp_store: Path) -> None:
        """Replacing with a device not in store returns needs_asset."""
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        session.start_from_request(_buck_only_request())

        result = session.replace_module("buck1", "NONEXISTENT_PART")
        assert result.status == "needs_asset"
        assert "buck1" in result.missing_modules


# ============================================================
# Error handling
# ============================================================


class TestErrorHandling:
    """Edge cases and error handling."""

    def test_empty_request(self, tmp_store: Path) -> None:
        """Empty request (no modules) produces valid result without crash."""
        request = SystemDesignRequest(
            raw_text="",
            modules=[],
            connections=[],
        )
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        result = session.start_from_request(request)

        # No modules => "generated" (0 modules, 0 connections) is valid
        assert result.status == "generated"
        assert result.bundle is not None

    def test_revise_without_start(self, tmp_store: Path) -> None:
        """Calling revise() before start() returns error."""
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        result = session.revise("change v_out to 3.3V")
        assert result.status == "error"

    def test_replace_without_start(self, tmp_store: Path) -> None:
        """Calling replace_module() before start() returns error."""
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        result = session.replace_module("buck1", "TPS54202")
        assert result.status == "error"

    def test_connection_with_missing_module_skipped(self, tmp_store: Path) -> None:
        """Connection referencing a missing module is skipped gracefully."""
        request = SystemDesignRequest(
            raw_text="test",
            modules=[
                ModuleIntent(
                    intent_id="buck1",
                    role="降压",
                    part_number_hint="TPS5430",
                    category_hint="buck",
                    electrical_targets={"v_in": "12", "v_out": "5"},
                ),
            ],
            connections=[
                ConnectionIntent(
                    connection_id="c_bad",
                    src_module_intent="buck1",
                    dst_module_intent="nonexistent_module",
                    signal_type=SignalType.POWER_SUPPLY,
                    connection_semantics=ConnectionSemantic.SUPPLY_CHAIN,
                ),
            ],
        )
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        result = session.start_from_request(request)

        # Should not crash; connection is skipped
        assert result.status == "generated"
        assert result.bundle is not None


# ============================================================
# Session state (ir and bundle properties)
# ============================================================


class TestSessionState:
    """Session exposes ir and bundle after design."""

    def test_ir_property_none_before_start(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        assert session.ir is None
        assert session.bundle is None

    def test_ir_property_after_start(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        session.start_from_request(_buck_only_request())

        assert session.ir is not None
        assert session.bundle is not None
        assert "buck1" in session.ir.module_instances

    def test_ir_summary(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        session.start_from_request(_buck_ldo_request())

        summary = session.ir.to_summary()
        assert summary["total_modules"] == 2
        assert summary["resolved_modules"] == 2
        assert summary["unresolved_modules"] == 0

    def test_bundle_to_dict(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        session.start_from_request(_buck_ldo_request())

        d = session.bundle.to_dict()
        assert "summary" in d
        assert "svg_path" in d
        assert "bom_text" in d
        assert "spice_text" in d


# ============================================================
# start() with text input (skip_ai_parse=True uses regex fallback)
# ============================================================


class TestStartWithText:
    """start() with text input exercises the regex fallback path."""

    def test_start_text_tps5430(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        result = session.start("TPS5430 12V转5V 降压电源")

        assert result.status == "generated"
        assert result.bundle is not None
        assert "TPS5430" in result.bundle.bom_text
