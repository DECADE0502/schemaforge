"""SchemaForge 通用拓扑渲染器

从 DeviceModel 的 SymbolDef + TopologyDef 动态生成原理图SVG。
替代原来的5个硬编码渲染文件。
"""

from __future__ import annotations

from typing import Any, Callable

import schemdraw
import schemdraw.elements as elm
from schemdraw.types import ImageFormat

from schemaforge.library.models import DeviceModel, SymbolDef


class TopologyRenderer:
    """通用拓扑渲染器 -- 从器件库数据动态生成原理图"""

    # 布局策略注册表（circuit_type -> layout function）
    LAYOUT_STRATEGIES: dict[str, Callable[..., str]] = {}

    @classmethod
    def register_layout(cls, circuit_type: str) -> Callable[..., Any]:
        """装饰器：注册布局策略"""

        def decorator(fn: Callable[..., str]) -> Callable[..., str]:
            cls.LAYOUT_STRATEGIES[circuit_type] = fn
            return fn

        return decorator

    def render(
        self,
        device: DeviceModel,
        params: dict[str, Any],
        filename: str | None = None,
    ) -> str:
        """从DeviceModel渲染原理图SVG

        Args:
            device: 器件模型（包含symbol和topology定义）
            params: 渲染参数（v_in, v_out等）
            filename: 输出文件名，None则自动生成

        Returns:
            SVG文件路径

        Raises:
            ValueError: 器件没有拓扑定义或不支持的电路类型
        """
        topology = device.topology
        if topology is None:
            raise ValueError(f"器件 {device.part_number} 没有拓扑定义")

        layout_fn = self.LAYOUT_STRATEGIES.get(topology.circuit_type)
        if layout_fn is None:
            raise ValueError(f"不支持的电路类型: {topology.circuit_type}")

        return layout_fn(device, params, filename)

    def render_from_params(self, device: DeviceModel, params: dict[str, Any]) -> str:
        """兼容旧接口的渲染入口"""
        return self.render(device, params)

    @staticmethod
    def build_ic_element(symbol: SymbolDef, label: str) -> elm.Ic:
        """从SymbolDef构建schemdraw Ic元件

        Args:
            symbol: 符号定义（从DeviceModel.symbol获取）
            label: IC标签文本（如 "AMS1117-3.3"）

        Returns:
            配置好的schemdraw Ic元件
        """
        pins: list[elm.IcPin] = []
        for sp in symbol.pins:
            pin_kwargs: dict[str, Any] = {
                "name": sp.name,
                "side": sp.side.value,
            }
            if sp.pin_number:
                pin_kwargs["pin"] = sp.pin_number
            if sp.slot:
                pin_kwargs["slot"] = sp.slot
            if sp.inverted:
                pin_kwargs["invert"] = True
            if sp.anchor_name:
                pin_kwargs["anchorname"] = sp.anchor_name
            pins.append(elm.IcPin(**pin_kwargs))

        kwargs: dict[str, Any] = {
            "pins": pins,
            "edgepadW": symbol.edge_pad_w,
            "edgepadH": symbol.edge_pad_h,
            "pinspacing": symbol.pin_spacing,
            "leadlen": symbol.lead_len,
        }
        if symbol.size:
            kwargs["size"] = symbol.size

        ic = elm.Ic(**kwargs)
        ic.label(label, symbol.label_position)
        return ic

    @staticmethod
    def render_symbol_preview(
        symbol: SymbolDef,
        label: str = "",
        dpi: int = 120,
    ) -> bytes:
        """将 SymbolDef 渲染为 PNG 字节流（用于 GUI 预览）"""
        with schemdraw.Drawing(show=False, dpi=dpi) as d:
            d.config(fontsize=11)
            ic = TopologyRenderer.build_ic_element(symbol, label)
            d.add(ic)
        return d.get_imagedata(ImageFormat.PNG)


# 导入布局策略模块（触发装饰器注册）
import schemaforge.schematic.topology  # noqa: E402, F401
