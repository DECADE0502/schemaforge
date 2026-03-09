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
from types import SimpleNamespace

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
        # missing_modules now contains part numbers, not module_ids
        assert "FAKE_DEVICE_XYZ" in result.missing_modules
        assert "TPS5430" not in result.missing_modules
        # Bundle should still be produced with whatever succeeded
        assert result.bundle is not None

    def test_all_missing_returns_needs_asset(self, tmp_store: Path) -> None:
        """All modules missing -> needs_asset status."""
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

        assert result.status == "needs_asset"
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


class TestOptionalVisualReview:
    def test_visual_review_runs_only_when_enabled(
        self,
        tmp_store: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        reviewed = {"called": False}

        def _mock_review_loop(ir, bundle, config=None):
            reviewed["called"] = True
            return (
                bundle.__class__(
                    design_ir=bundle.design_ir,
                    svg_path="reviewed.svg",
                    bom_text=bundle.bom_text,
                    bom_csv=bundle.bom_csv,
                    spice_text=bundle.spice_text,
                    review_report=["reviewed"],
                    render_metadata=bundle.render_metadata,
                ),
                SimpleNamespace(),
            )

        monkeypatch.setattr(
            "schemaforge.visual_review.loop.run_visual_review_loop",
            _mock_review_loop,
        )

        session = SystemDesignSession(
            store_dir=tmp_store,
            skip_ai_parse=True,
            enable_visual_review=True,
        )
        result = session.start_from_request(_buck_only_request())

        assert reviewed["called"] is True
        assert result.bundle is not None
        assert result.bundle.svg_path == "reviewed.svg"
        assert result.bundle.review_report == ["reviewed"]

    def test_visual_review_session_path_preserves_bom_and_spice(
        self,
        tmp_store: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _mock_review_loop(ir, bundle, config=None):
            return (
                bundle.__class__(
                    design_ir=bundle.design_ir,
                    svg_path="reviewed.svg",
                    bom_text=bundle.bom_text,
                    bom_csv=bundle.bom_csv,
                    spice_text=bundle.spice_text,
                    review_report=["reviewed"],
                    render_metadata=bundle.render_metadata,
                ),
                SimpleNamespace(),
            )

        monkeypatch.setattr(
            "schemaforge.visual_review.loop.run_visual_review_loop",
            _mock_review_loop,
        )

        session = SystemDesignSession(
            store_dir=tmp_store,
            skip_ai_parse=True,
            enable_visual_review=True,
        )
        result = session.start_from_request(_buck_ldo_request())

        assert result.bundle is not None
        assert result.bundle.svg_path == "reviewed.svg"
        assert "TPS5430" in result.bundle.bom_text
        assert result.bundle.spice_text


class TestImageRevision:
    def test_revise_from_image_uses_same_session_revise_pipeline(
        self,
        tmp_store: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        session.start_from_request(_buck_only_request())

        called: dict[str, str] = {}

        def _mock_infer(_base64_png: str, _context: str) -> tuple[str, list[str]]:
            return "把输出电压改成3.3V", ["from image"]

        def _mock_revise(text: str):
            called["text"] = text
            return session.start_from_request(_buck_only_request())

        monkeypatch.setattr(
            "schemaforge.system.session._infer_revision_text_from_image",
            _mock_infer,
        )
        monkeypatch.setattr(session, "revise", _mock_revise)

        result = session.revise_from_image("aGVsbG8=")

        assert called["text"] == "把输出电压改成3.3V"
        assert result.bundle is not None
        assert "图片修订完成" in result.message

    def test_revise_from_image_requires_existing_design(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        result = session.revise_from_image("aGVsbG8=")
        assert result.status == "error"

    def test_revise_from_image_rejects_invalid_base64(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        session.start_from_request(_buck_only_request())
        result = session.revise_from_image("%%%not-base64%%%")
        assert result.status == "error"


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

    def test_start_text_system_gold_path(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        result = session.start(
            "20V 输入，用 TPS54202 降压到 5V，再用 AMS1117 降到 3.3V，"
            "给 STM32F103C8T6 供电，并且用 MCU 的 PA1 控制一颗 LED"
        )

        assert result.status == "generated"
        assert result.bundle is not None

        ir = result.bundle.design_ir
        assert set(ir.module_instances) == {"buck1", "ldo1", "mcu1", "led1"}

        resolved_links = {
            (c.rule_id, c.src_port.module_id, c.dst_port.module_id)
            for c in ir.connections
        }
        assert ("RULE_POWER_SUPPLY", "buck1", "ldo1") in resolved_links
        assert ("RULE_POWER_SUPPLY", "ldo1", "mcu1") in resolved_links
        assert ("RULE_GPIO_LED", "mcu1", "led1") in resolved_links

        assert "NET_5V" in ir.nets
        assert "NET_3.3V" in ir.nets
        assert "NET_PA1_led1" in ir.nets
        assert ir.unresolved_items == []

        assert all(
            name in result.bundle.bom_text
            for name in ["TPS54202", "AMS1117", "STM32F103C8T6", "LED"]
        )
        assert "NET_5V" in result.bundle.spice_text
        assert "NET_3.3V" in result.bundle.spice_text

    def test_start_explicit_missing_part_returns_needs_asset_not_substitute(
        self, tmp_store: Path,
    ) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        result = session.start("TPS5450，20V降3.3VDCDC电路")

        assert result.status == "needs_asset"
        assert result.bundle is not None
        assert result.missing_modules == ["TPS5450"]
        ir = result.bundle.design_ir
        assert "buck1" in ir.module_instances
        buck = ir.module_instances["buck1"]
        assert buck.status == ModuleStatus.NEEDS_ASSET
        assert buck.missing_part_number == "TPS5450"
        assert buck.device is None
        assert "TPS54202" not in result.bundle.bom_text

    def test_revise_gold_path_replaces_existing_part(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        session.start(
            "20V 输入，用 TPS54202 降压到 5V，再用 AMS1117 降到 3.3V，"
            "给 STM32F103C8T6 供电，并且用 MCU 的 PA1 控制一颗 LED"
        )

        result = session.revise("把 TPS54202 换成 TPS5430")

        assert result.status == "generated"
        assert result.bundle is not None
        assert set(session.ir.module_instances) == {"buck1", "ldo1", "mcu1", "led1"}
        assert session.ir.module_instances["buck1"].device.part_number == "TPS5430"
        assert "TPS5430" in result.bundle.bom_text

    def test_revise_gold_path_updates_led_color(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        session.start(
            "20V 输入，用 TPS54202 降压到 5V，再用 AMS1117 降到 3.3V，"
            "给 STM32F103C8T6 供电，并且用 MCU 的 PA1 控制一颗 LED"
        )

        result = session.revise("把 LED 改成蓝色")

        assert result.status == "generated"
        assert session.ir.module_instances["led1"].parameters["led_color"] == "blue"

    def test_revise_gold_path_updates_unique_vout(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        session.start(
            "20V 输入，用 TPS54202 降压到 5V，再用 AMS1117 降到 3.3V，"
            "给 STM32F103C8T6 供电，并且用 MCU 的 PA1 控制一颗 LED"
        )

        result = session.revise("把 5V 改成 4.2V")

        assert result.status == "generated"
        assert session.ir.module_instances["buck1"].parameters["v_out"] == "4.2"

    def test_revise_gold_path_removes_unique_led_module(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        session.start(
            "20V 输入，用 TPS54202 降压到 5V，再用 AMS1117 降到 3.3V，"
            "给 STM32F103C8T6 供电，并且用 MCU 的 PA1 控制一颗 LED"
        )

        result = session.revise("删除 LED")

        assert result.status == "generated"
        assert result.bundle is not None
        assert set(session.ir.module_instances) == {"buck1", "ldo1", "mcu1"}
        assert all(
            conn.dst_port.module_id != "led1" and conn.src_port.module_id != "led1"
            for conn in session.ir.connections
        )
        assert "NET_PA1_led1" not in session.ir.nets
        assert "LED_INDICATOR" not in result.bundle.bom_text

    def test_revise_gold_path_adds_second_power_led_and_merges_net(
        self, tmp_store: Path,
    ) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        session.start(
            "20V 输入，用 TPS54202 降压到 5V，再用 AMS1117 降到 3.3V，"
            "给 STM32F103C8T6 供电，并且用 MCU 的 PA1 控制一颗 LED"
        )

        result = session.revise("再加一个蓝色 LED")

        assert result.status == "generated"
        assert result.bundle is not None
        assert set(session.ir.module_instances) == {"buck1", "ldo1", "mcu1", "led1", "led2"}
        assert session.ir.module_instances["led2"].parameters["led_color"] == "blue"

        resolved_links = {
            (c.rule_id, c.src_port.module_id, c.dst_port.module_id)
            for c in session.ir.connections
        }
        assert ("RULE_POWER_SUPPLY", "ldo1", "led2") in resolved_links

        net_3v3 = session.ir.nets["NET_3.3V"]
        members = {(member.module_id, member.pin_name) for member in net_3v3.members}
        assert any(module_id == "mcu1" for module_id, _pin_name in members)
        assert any(module_id == "led2" for module_id, _pin_name in members)

    def test_revise_gold_path_can_remove_explicit_led2(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        session.start(
            "20V 输入，用 TPS54202 降压到 5V，再用 AMS1117 降到 3.3V，"
            "给 STM32F103C8T6 供电，并且用 MCU 的 PA1 控制一颗 LED"
        )
        session.revise("再加一个蓝色 LED")

        result = session.revise("删除 led2")

        assert result.status == "generated"
        assert result.bundle is not None
        assert set(session.ir.module_instances) == {"buck1", "ldo1", "mcu1", "led1"}
        assert "led2" not in result.bundle.bom_text

    def test_revise_gold_path_adds_second_gpio_led(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        session.start(
            "20V 输入，用 TPS54202 降压到 5V，再用 AMS1117 降到 3.3V，"
            "给 STM32F103C8T6 供电，并且用 MCU 的 PA1 控制一颗 LED"
        )

        result = session.revise("再加一个由 PA2 控制的蓝色 LED")

        assert result.status == "generated"
        assert result.bundle is not None
        assert set(session.ir.module_instances) == {"buck1", "ldo1", "mcu1", "led1", "led2"}
        assert session.ir.module_instances["led2"].parameters["led_color"] == "blue"
        assert session.ir.module_instances["led2"].parameters["gpio_pin"] == "PA2"

        resolved_links = {
            (c.rule_id, c.src_port.module_id, c.src_port.pin_name, c.dst_port.module_id)
            for c in session.ir.connections
        }
        assert ("RULE_GPIO_LED", "mcu1", "PA2", "led2") in resolved_links
        assert "NET_PA2_led2" in session.ir.nets

    def test_revise_gold_path_retargets_existing_led_gpio(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        session.start(
            "20V 输入，用 TPS54202 降压到 5V，再用 AMS1117 降到 3.3V，"
            "给 STM32F103C8T6 供电，并且用 MCU 的 PA1 控制一颗 LED"
        )

        result = session.revise("把 LED 改到 PA2")

        assert result.status == "generated"
        assert result.bundle is not None
        assert session.ir.module_instances["led1"].parameters["gpio_pin"] == "PA2"
        assert "NET_PA1_led1" not in session.ir.nets
        assert "NET_PA2_led1" in session.ir.nets
        resolved_links = {
            (c.rule_id, c.src_port.module_id, c.src_port.pin_name, c.dst_port.module_id)
            for c in session.ir.connections
        }
        assert ("RULE_GPIO_LED", "mcu1", "PA2", "led1") in resolved_links

    def test_revise_gold_path_adds_downstream_ldo_module(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        session.start(
            "20V 输入，用 TPS54202 降压到 5V，再用 AMS1117 降到 3.3V，"
            "给 STM32F103C8T6 供电，并且用 MCU 的 PA1 控制一颗 LED"
        )

        result = session.revise("再加一个 AMS1117-3.3 把 5V 降到 3.3V")

        assert result.status == "generated"
        assert result.bundle is not None
        assert "ldo2" in session.ir.module_instances
        assert session.ir.module_instances["ldo2"].device.part_number == "AMS1117-3.3"
        assert session.ir.module_instances["ldo2"].parameters["v_in"] == "5"
        assert session.ir.module_instances["ldo2"].parameters["v_out"] == "3.3"

        resolved_links = {
            (c.rule_id, c.src_port.module_id, c.dst_port.module_id)
            for c in session.ir.connections
        }
        assert ("RULE_POWER_SUPPLY", "buck1", "ldo2") in resolved_links

    def test_revise_targets_explicit_led2_color(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        session.start(
            "20V 输入，用 TPS54202 降压到 5V，再用 AMS1117 降到 3.3V，"
            "给 STM32F103C8T6 供电，并且用 MCU 的 PA1 控制一颗 LED"
        )
        session.revise("再加一个蓝色 LED")

        result = session.revise("把 led2 改成白色")

        assert result.status == "generated"
        assert session.ir.module_instances["led1"].parameters["led_color"] == "green"
        assert session.ir.module_instances["led2"].parameters["led_color"] == "white"

    def test_revise_targets_explicit_ldo2_vout(self, tmp_store: Path) -> None:
        session = SystemDesignSession(store_dir=tmp_store, skip_ai_parse=True)
        session.start(
            "20V 输入，用 TPS54202 降压到 5V，再用 AMS1117 降到 3.3V，"
            "给 STM32F103C8T6 供电，并且用 MCU 的 PA1 控制一颗 LED"
        )
        session.revise("再加一个 AMS1117-3.3 把 5V 降到 3.3V")

        result = session.revise("把 ldo2 改成 1.8V")

        assert result.status == "generated"
        assert session.ir.module_instances["ldo2"].parameters["v_out"] == "1.8"
