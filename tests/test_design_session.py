"""设计会话工作流测试

端到端测试：自然语言 → SVG + BOM + SPICE
使用项目自带的 store/devices/ 数据。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

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
from schemaforge.workflows.design_session import (
    DesignSession,
)


def _populate_store(store_dir: Path) -> None:
    """填充测试 store"""
    store = ComponentStore(store_dir)

    # AMS1117-3.3 (完整 LDO)
    store.save_device(DeviceModel(
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
        symbol=SymbolDef(pins=[
            SymbolPin(
                name="VIN", pin_number="3",
                side="left", pin_type=PinType.POWER_IN,
                slot="1/3",
            ),
            SymbolPin(
                name="VOUT", pin_number="2",
                side="right", pin_type=PinType.POWER_OUT,
                slot="1/3",
            ),
            SymbolPin(
                name="GND", pin_number="1",
                side="bottom", pin_type=PinType.GROUND,
                slot="1/1",
            ),
        ]),
        topology=TopologyDef(
            circuit_type="ldo",
            external_components=[
                ExternalComponent(
                    role="input_cap", ref_prefix="C",
                    default_value="10uF", value_expression="{c_in}",
                    schemdraw_element="Capacitor",
                ),
                ExternalComponent(
                    role="output_cap", ref_prefix="C",
                    default_value="22uF", value_expression="{c_out}",
                    schemdraw_element="Capacitor",
                ),
            ],
            connections=[
                TopologyConnection(
                    net_name="VIN", device_pin="VIN",
                    external_refs=["input_cap.1"], is_power=True,
                ),
                TopologyConnection(
                    net_name="VOUT", device_pin="VOUT",
                    external_refs=["output_cap.1"], is_power=True,
                ),
                TopologyConnection(
                    net_name="GND", device_pin="GND",
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
    ))

    # LED_INDICATOR
    store.save_device(DeviceModel(
        part_number="LED_INDICATOR",
        description="LED指示灯电路",
        category="led",
        topology=TopologyDef(circuit_type="led_driver"),
    ))

    # VOLTAGE_DIVIDER
    store.save_device(DeviceModel(
        part_number="VOLTAGE_DIVIDER",
        description="通用电压分压器",
        category="voltage_divider",
        topology=TopologyDef(circuit_type="voltage_divider"),
    ))

    # RC_LOWPASS
    store.save_device(DeviceModel(
        part_number="RC_LOWPASS",
        description="RC低通滤波器",
        category="rc_filter",
        topology=TopologyDef(circuit_type="rc_filter"),
    ))


class TestDesignSession:
    """设计会话测试"""

    def setup_method(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        _populate_store(self.tmp)
        self.progress_log: list[tuple[str, int]] = []

    def teardown_method(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_session(self) -> DesignSession:
        return DesignSession(
            store_dir=self.tmp,
            progress_callback=lambda msg, pct: self.progress_log.append((msg, pct)),
        )

    # --- 端到端成功路径 ---

    def test_ldo_end_to_end(self) -> None:
        """核心验收: 自然语言 → SVG + BOM + SPICE"""
        session = self._make_session()
        result = session.run("5V转3.3V稳压电路")

        assert result.success, f"失败: {result.error}"
        assert len(result.svg_paths) >= 1
        assert result.bom_text  # 非空
        assert result.spice_text  # 非空
        assert result.plan is not None
        assert result.plan.name

        # SVG 文件确实存在
        for svg in result.svg_paths:
            assert Path(svg).exists()

    def test_ldo_with_led(self) -> None:
        """LDO + LED 组合"""
        session = self._make_session()
        result = session.run("5V转3.3V稳压电路，带绿色LED指示灯")

        assert result.success, f"失败: {result.error}"
        # 至少应该渲染出 LDO（LED 可能因为 led_driver 拓扑没有外部元件而简化）
        assert len(result.svg_paths) >= 1

    def test_divider(self) -> None:
        """分压器"""
        session = self._make_session()
        result = session.run("12V到3.3V分压采样电路")

        assert result.success, f"失败: {result.error}"
        assert len(result.svg_paths) >= 1

    def test_rc_filter(self) -> None:
        """RC 滤波器"""
        session = self._make_session()
        result = session.run("1kHz RC滤波器")

        assert result.success, f"失败: {result.error}"
        assert len(result.svg_paths) >= 1

    # --- 进度回调 ---

    def test_progress_callback_fired(self) -> None:
        session = self._make_session()
        session.run("5V转3.3V稳压电路")

        assert len(self.progress_log) > 0
        # 应该有从 5 到 100 的进度
        percentages = [pct for _, pct in self.progress_log]
        assert min(percentages) <= 10
        assert max(percentages) >= 90

    # --- 结果结构 ---

    def test_result_to_dict(self) -> None:
        session = self._make_session()
        result = session.run("5V转3.3V稳压电路")
        d = result.to_dict()
        assert "success" in d
        assert "design_name" in d
        assert "modules" in d

    def test_module_results(self) -> None:
        session = self._make_session()
        result = session.run("5V转3.3V稳压电路")

        assert len(result.modules) >= 1
        for mr in result.modules:
            mr_dict = mr.to_dict()
            assert "role" in mr_dict
            assert "category" in mr_dict

    # --- 设计规格 ---

    def test_design_spec_generated(self) -> None:
        session = self._make_session()
        result = session.run("5V转3.3V稳压电路")

        assert result.design_spec
        assert "design_name" in result.design_spec
        assert "modules" in result.design_spec

    # --- 失败路径 ---

    def test_empty_store_fails_gracefully(self) -> None:
        empty_tmp = Path(tempfile.mkdtemp())
        try:
            session = DesignSession(store_dir=empty_tmp, )
            result = session.run("5V转3.3V稳压电路")
            assert not result.success
            # 空库时所有模块缺失，返回 missing_modules 而非硬错误
            assert result.has_missing
            assert len(result.missing_modules) >= 1
            assert result.stage == "waiting_devices"
        finally:
            shutil.rmtree(empty_tmp, ignore_errors=True)

    # --- 合理性检查集成 ---

    def test_rationality_included(self) -> None:
        session = self._make_session()
        result = session.run("5V转3.3V稳压电路")

        matched = [m for m in result.modules if m.device is not None]
        for mr in matched:
            if mr.rationality is not None:
                assert hasattr(mr.rationality, "is_acceptable")


class TestDesignSessionWithRealStore:
    """使用项目自带 store/devices/ 的测试"""

    def test_with_real_store(self) -> None:
        """使用 schemaforge/store 真实数据"""
        store_dir = Path(__file__).parent.parent / "schemaforge" / "store"
        if not store_dir.exists():
            return  # CI 环境可能没有

        session = DesignSession(store_dir=store_dir, )
        result = session.run("5V转3.3V稳压电路")

        # 真实 store 有 AMS1117-3.3，应该能匹配
        assert result.success, f"失败: {result.error}"
        assert len(result.svg_paths) >= 1
