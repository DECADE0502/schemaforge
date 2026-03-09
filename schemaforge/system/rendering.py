"""系统级渲染：将 SystemDesignIR 渲染为单一 SVG 原理图。

所有模块绘制在同一个 schemdraw.Drawing 中，然后输出为一个 SVG 文件。

约束遵循:
- C61 渲染必须基于系统 IR，而不是基于字符串模板
- C62 同一系统只能输出一张主 SVG
- C63 模块布局必须稳定，局部修改不能整图乱跳
- C64 全局参考编号必须一致
- C65 net label 必须与系统 net 保持一致
- C66 GND 视觉表示必须统一
- C67 缺失模块必须可视化为占位块
- C68 unresolved 连接必须有可视提示
- C69 控制支路应区别于主电源链布局
- C70 原理图中每条连接都必须能在 IR 中找到来源
"""

from __future__ import annotations

import logging

import schemdraw
import schemdraw.elements as elm

from schemaforge.render.base import output_path
from schemaforge.system.layout import SystemLayoutSpec
from schemaforge.system.models import (
    ModuleInstance,
    ModuleStatus,
    NetType,
    RenderMetadata,
    ResolvedConnection,
    SystemDesignIR,
    SystemNet,
)

logger = logging.getLogger(__name__)

# ============================================================
# 布局常量
# ============================================================

_MODULE_X_SPACING = 20.0   # 模块间水平间距（增大避免重叠）
_MODULE_Y_OFFSET = 0.0     # 电源链 Y 基线
_CONTROL_Y_OFFSET = -16.0  # 控制支路 Y 偏移（在电源链下方，加大间距）
_CONTROL_X_SPACING = 10.0  # 控制支路模块间距

# 电源链类别（参与主链条横向排列）
_POWER_CATEGORIES: set[str] = {"buck", "ldo", "boost", "flyback", "sepic"}


def _layout_positions(layout_spec: object | None) -> dict[str, tuple[float, float]]:
    positions = getattr(layout_spec, "module_positions", None)
    if isinstance(positions, dict):
        return positions
    return {}


def _layout_number(layout_spec: object | None, name: str, default: float) -> float:
    value = getattr(layout_spec, name, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _estimate_module_bbox(
    instance: ModuleInstance,
    origin: tuple[float, float],
) -> tuple[float, float, float, float]:
    category = instance.resolved_category.lower()
    width = 4.0
    height = 3.0

    if instance.status in (
        ModuleStatus.PENDING,
        ModuleStatus.NEEDS_ASSET,
        ModuleStatus.ERROR,
    ):
        width = 3.0
        height = 2.5
    elif category in _POWER_CATEGORIES:
        width = 10.0
        height = 4.5
    elif category == "mcu":
        width = 6.0
        height = 4.0
    elif category == "led":
        width = 2.5
        height = 4.5

    x, y = origin
    return (x, y - height / 2, width, height)


def _record_module_metadata(
    metadata: RenderMetadata | None,
    instance: ModuleInstance,
    origin: tuple[float, float],
    anchors: dict[str, tuple[float, float]],
) -> None:
    if metadata is None:
        return
    bbox = _estimate_module_bbox(instance, origin)
    metadata.module_bboxes[instance.module_id] = bbox
    metadata.anchor_points[instance.module_id] = dict(anchors)
    label_width = max(3.5, len(instance.module_id) * 0.35)
    metadata.label_bboxes[f"L_{instance.module_id}"] = (
        bbox[0],
        bbox[1] + bbox[3] + 0.4,
        label_width,
        0.8,
    )


def _normalize_render_metadata(
    metadata: RenderMetadata,
    layout_spec: SystemLayoutSpec | None = None,
) -> RenderMetadata:
    xs: list[float] = []
    ys: list[float] = []

    for x, y, width, height in metadata.module_bboxes.values():
        xs.extend([x, x + width])
        ys.extend([y, y + height])
    for x, y, width, height in metadata.label_bboxes.values():
        xs.extend([x, x + width])
        ys.extend([y, y + height])
    for _src, _dst, points in metadata.wire_paths:
        for px, py in points:
            xs.append(px)
            ys.append(py)
    for anchors in metadata.anchor_points.values():
        for px, py in anchors.values():
            xs.append(px)
            ys.append(py)

    shift_x = 0.0
    shift_y = 0.0
    if xs and min(xs) < 1.0:
        shift_x = 1.0 - min(xs)
    if ys and min(ys) < 1.0:
        shift_y = 1.0 - min(ys)

    if shift_x or shift_y:
        metadata.module_bboxes = {
            module_id: (x + shift_x, y + shift_y, width, height)
            for module_id, (x, y, width, height) in metadata.module_bboxes.items()
        }
        metadata.label_bboxes = {
            label_id: (x + shift_x, y + shift_y, width, height)
            for label_id, (x, y, width, height) in metadata.label_bboxes.items()
        }
        metadata.anchor_points = {
            module_id: {
                pin_name: (px + shift_x, py + shift_y)
                for pin_name, (px, py) in anchors.items()
            }
            for module_id, anchors in metadata.anchor_points.items()
        }
        metadata.wire_paths = [
            (
                src,
                dst,
                [(px + shift_x, py + shift_y) for px, py in points],
            )
            for src, dst, points in metadata.wire_paths
        ]

    layout_width = _layout_number(layout_spec, "canvas_width", 0.0)
    layout_height = _layout_number(layout_spec, "canvas_height", 0.0)

    max_x = max((x + width for x, y, width, height in metadata.module_bboxes.values()), default=0.0)
    max_y = max((y + height for x, y, width, height in metadata.module_bboxes.values()), default=0.0)
    metadata.canvas_size = (
        max(layout_width, max_x + 1.0),
        max(layout_height, max_y + 1.0),
    )
    return metadata


# ============================================================
# T085: 全局参考编号分配器 (C64)
# ============================================================


def _build_global_ref_map(
    ir: SystemDesignIR,
) -> dict[str, dict[str, str]]:
    """为所有模块的外围元件分配全局唯一参考编号。

    Returns:
        module_id -> {role: global_ref}  例如 {"buck1": {"input_cap": "C1"}}
    """
    counters: dict[str, int] = {}  # prefix -> next number
    ref_map: dict[str, dict[str, str]] = {}

    for module_id, instance in ir.module_instances.items():
        module_refs: dict[str, str] = {}
        for comp in instance.external_components:
            role = comp.get("role", "")
            comp_type = comp.get("type", "")
            ref_prefix = comp.get("ref_prefix", "")

            if not ref_prefix:
                if comp_type in ("capacitor",) or "cap" in role:
                    ref_prefix = "C"
                elif comp_type in ("resistor",) or "resistor" in role or "pullup" in role or "limit" in role:
                    ref_prefix = "R"
                elif comp_type in ("inductor",):
                    ref_prefix = "L"
                elif comp_type in ("diode",) or role == "diode":
                    ref_prefix = "D"
                elif comp_type in ("resistor_divider",) or "divider" in role:
                    ref_prefix = "R"
                else:
                    ref_prefix = "X"

            idx = counters.get(ref_prefix, 1)
            counters[ref_prefix] = idx + 1
            ref_name = f"{ref_prefix}{idx}"
            module_refs[role] = ref_name

        ref_map[module_id] = module_refs

    return ref_map


# ============================================================
# T082: Module layout functions
# ============================================================


def draw_power_module(
    d: schemdraw.Drawing,
    instance: ModuleInstance,
    origin: tuple[float, float],
    ref_map: dict[str, str],
) -> dict[str, tuple[float, float]]:
    """Draw a power module (Buck/LDO/Boost) into the Drawing.

    Returns anchor points: {"VIN": (x,y), "VOUT": (x,y), "GND": (x,y)}
    """
    category = instance.resolved_category.lower()
    module_id = instance.module_id
    part = ""
    if instance.device is not None:
        part = getattr(instance.device, "part_number", module_id)
    label = part or module_id

    anchors: dict[str, tuple[float, float]] = {}

    if category == "buck":
        anchors = _draw_buck_module(d, instance, origin, ref_map, label)
    elif category == "ldo":
        anchors = _draw_ldo_module(d, instance, origin, ref_map, label)
    elif category in ("boost", "flyback", "sepic"):
        # 与 buck 共享基本布局，只是标签不同
        anchors = _draw_buck_module(d, instance, origin, ref_map, label)
    else:
        anchors = _draw_generic_power_module(d, instance, origin, ref_map, label)

    return anchors


def _get_comp_value(instance: ModuleInstance, role: str, fallback: str = "?") -> str:
    """从模块的 external_components 中读取指定角色的真实计算值。"""
    for comp in instance.external_components:
        if comp.get("role") == role:
            return comp.get("value", fallback)
    # 回退到 parameters
    return instance.parameters.get(role, fallback)


def _draw_buck_module(
    d: schemdraw.Drawing,
    instance: ModuleInstance,
    origin: tuple[float, float],
    ref_map: dict[str, str],
    label: str,
) -> dict[str, tuple[float, float]]:
    """绘制 Buck 转换器模块（含完整外围元件）。

    绘制元件：IC、输入电容、电感、输出电容、FB 分压电阻、
    续流二极管、自举电容、EN 上拉电阻。
    """
    ox, oy = origin

    # --- 输入电容 ---
    c_in_ref = ref_map.get("input_cap", "C?")
    c_in_val = _get_comp_value(instance, "input_cap", "10\u03bcF")
    vin_pos = (ox, oy)

    elm.Dot(open=True).at(vin_pos).label("VIN", "left")
    elm.Line().at(vin_pos).right(1)
    cap_top = (ox + 1, oy)
    elm.Dot().at(cap_top)

    d.push()
    elm.Capacitor(polar=True).at(cap_top).down().label(
        f"{c_in_ref}\n{c_in_val}", "right",
    )
    elm.Ground()
    d.pop()

    # --- IC ---
    ic = elm.Ic(
        pins=[
            elm.IcPin(name="VIN", side="left", slot="1/3"),
            elm.IcPin(name="EN", side="left", slot="2/3"),
            elm.IcPin(name="BST", side="left", slot="3/3"),
            elm.IcPin(name="GND", side="bottom", slot="1/1"),
            elm.IcPin(name="SW", side="right", slot="1/3"),
            elm.IcPin(name="FB", side="right", slot="2/3"),
        ],
        size=(4, 4),
    ).at((ox + 2, oy)).anchor("inL1").label(label, "top")

    # GND
    d.push()
    elm.Line().at(ic.inB1).down(1)  # type: ignore[call-arg]
    gnd_ic = d.here
    elm.Ground()
    d.pop()

    # --- EN 上拉电阻 ---
    r_en_ref = ref_map.get("en_pullup", "")
    if r_en_ref:
        r_en_val = _get_comp_value(instance, "en_pullup", "100k\u03a9")
        d.push()
        elm.Line().at(ic.inL2).left(0.5)  # type: ignore[call-arg]
        elm.Resistor().up().label(f"{r_en_ref}\n{r_en_val}", "left")
        # 短线连接到 VIN 轨附近
        elm.Line().right(1.5)
        elm.Dot()
        d.pop()

    # --- 自举电容 (BST→SW) ---
    c_bst_ref = ref_map.get("boot_cap", "")
    if c_bst_ref:
        c_bst_val = _get_comp_value(instance, "boot_cap", "100nF")
        d.push()
        elm.Line().at(ic.inL3).left(1)  # type: ignore[call-arg]
        bst_wire_end = d.here
        # 向右上弯到 SW 引脚的右侧上方
        elm.Capacitor().at(bst_wire_end).right(2).label(
            f"{c_bst_ref}\n{c_bst_val}", "top",
        )
        d.pop()

    # --- SW → 电感 → VOUT ---
    elm.Line().at(ic.inR1).right(0.5)  # type: ignore[call-arg]
    ind_start = d.here

    l_ref = ref_map.get("inductor", "L?")
    l_val = _get_comp_value(instance, "inductor", "4.7\u03bcH")
    elm.Inductor2().at(ind_start).right().label(f"{l_ref}\n{l_val}", "top")

    # --- 续流二极管 (SW 节点 → GND) ---
    diode_ref = ref_map.get("diode", "")
    if diode_ref:
        diode_val = _get_comp_value(instance, "diode", "SS34")
        d.push()
        elm.Diode().at(ind_start).down().reverse().label(
            f"{diode_ref}\n{diode_val}", "right",
        )
        elm.Ground()
        d.pop()

    # 电感输出节点 (VOUT rail)
    vout_node = d.here
    elm.Dot()

    # --- 输出电容 ---
    c_out_ref = ref_map.get("output_cap", "C?")
    c_out_val = _get_comp_value(instance, "output_cap", "22\u03bcF")
    d.push()
    elm.Capacitor(polar=True).down().label(f"{c_out_ref}\n{c_out_val}", "right")
    elm.Ground()
    d.pop()

    # --- FB 反馈电阻分压器 ---
    fb_upper_ref = ref_map.get("fb_upper", "")
    fb_lower_ref = ref_map.get("fb_lower", "")
    if fb_upper_ref or fb_lower_ref:
        fb_upper_val = _get_comp_value(instance, "fb_upper", "?")
        fb_lower_val = _get_comp_value(instance, "fb_lower", "?")

        # 从 FB 引脚向右引出
        d.push()
        elm.Line().at(ic.inR2).right(1)  # type: ignore[call-arg]
        fb_junction = d.here
        elm.Dot()

        # fb_upper: junction → VOUT rail (向上)
        if fb_upper_ref:
            d.push()
            elm.Resistor().at(fb_junction).up().label(
                f"{fb_upper_ref}\n{fb_upper_val}", "left",
            )
            # 短线示意连接到 VOUT
            elm.Line().right(1)
            elm.Dot(open=True).label("VOUT", "right")
            d.pop()

        # fb_lower: junction → GND (向下)
        if fb_lower_ref:
            elm.Resistor().at(fb_junction).down().label(
                f"{fb_lower_ref}\n{fb_lower_val}", "left",
            )
            elm.Ground()

        d.pop()

    # --- VOUT 标记 ---
    v_out = instance.parameters.get("v_out", "?")
    elm.Line().at(vout_node).right(1)
    vout_end = d.here
    elm.Dot(open=True).label(f"VOUT\n{v_out}V", "right")

    anchors = {
        "VIN": (ox, oy),
        "VOUT": (vout_end.x, vout_end.y),
        "GND": (gnd_ic.x, gnd_ic.y),
    }
    return anchors


def _draw_ldo_module(
    d: schemdraw.Drawing,
    instance: ModuleInstance,
    origin: tuple[float, float],
    ref_map: dict[str, str],
    label: str,
) -> dict[str, tuple[float, float]]:
    """绘制 LDO 模块。"""
    ox, oy = origin

    # 输入
    vin_pos = (ox, oy)
    elm.Dot(open=True).at(vin_pos).label("VIN", "left")
    elm.Line().at(vin_pos).right(1)
    cap_top = (ox + 1, oy)
    elm.Dot().at(cap_top)

    c_in_ref = ref_map.get("input_cap", "C?")
    d.push()
    elm.Capacitor(polar=True).at(cap_top).down().label(
        f"{c_in_ref}\n10\u03bcF", "right",
    )
    elm.Ground()
    d.pop()

    # IC (3-pin LDO)
    ic = elm.Ic(
        pins=[
            elm.IcPin(name="VIN", side="left", slot="1/1"),
            elm.IcPin(name="GND", side="bottom", slot="1/1"),
            elm.IcPin(name="VOUT", side="right", slot="1/1"),
        ],
        size=(3, 2),
    ).at((ox + 2, oy)).anchor("inL1").label(label, "top")

    # GND
    d.push()
    elm.Line().at(ic.inB1).down(1)  # type: ignore[call-arg]
    gnd_ic = d.here
    elm.Ground()
    d.pop()

    # 输出电容
    elm.Line().at(ic.inR1).right(1)  # type: ignore[call-arg]
    c_out_ref = ref_map.get("output_cap", "C?")
    elm.Dot()
    d.push()
    elm.Capacitor(polar=True).down().label(f"{c_out_ref}\n22\u03bcF", "right")
    elm.Ground()
    d.pop()

    # VOUT
    v_out = instance.parameters.get("v_out", "?")
    elm.Line().right(1)
    vout_end = d.here
    elm.Dot(open=True).label(f"VOUT\n{v_out}V", "right")

    anchors = {
        "VIN": (ox, oy),
        "VOUT": (vout_end.x, vout_end.y),
        "GND": (gnd_ic.x, gnd_ic.y),
    }
    return anchors


def _draw_generic_power_module(
    d: schemdraw.Drawing,
    instance: ModuleInstance,
    origin: tuple[float, float],
    ref_map: dict[str, str],
    label: str,
) -> dict[str, tuple[float, float]]:
    """绘制通用电源模块（不是 buck/ldo/boost 的情况）。"""
    ox, oy = origin

    vin_pos = (ox, oy)
    elm.Dot(open=True).at(vin_pos).label("VIN", "left")
    elm.Line().at(vin_pos).right(1)

    ic = elm.Ic(
        pins=[
            elm.IcPin(name="IN", side="left", slot="1/1"),
            elm.IcPin(name="GND", side="bottom", slot="1/1"),
            elm.IcPin(name="OUT", side="right", slot="1/1"),
        ],
        size=(3, 2),
    ).at((ox + 1, oy)).anchor("inL1").label(label, "top")

    d.push()
    elm.Line().at(ic.inB1).down(1)  # type: ignore[call-arg]
    gnd_ic = d.here
    elm.Ground()
    d.pop()

    elm.Line().at(ic.inR1).right(1)  # type: ignore[call-arg]
    vout_end = d.here
    v_out = instance.parameters.get("v_out", "?")
    elm.Dot(open=True).label(f"OUT\n{v_out}V", "right")

    return {
        "VIN": (ox, oy),
        "VOUT": (vout_end.x, vout_end.y),
        "GND": (gnd_ic.x, gnd_ic.y),
    }


# ============================================================
# T082: 控制模块绘制
# ============================================================


def draw_control_module(
    d: schemdraw.Drawing,
    instance: ModuleInstance,
    origin: tuple[float, float],
    ref_map: dict[str, str],
) -> dict[str, tuple[float, float]]:
    """Draw a control module (MCU/LED/other) into the Drawing.

    Returns anchor points: {"VDD": (x,y), "GND": (x,y), "PA0": (x,y), ...}
    """
    category = instance.resolved_category.lower()
    module_id = instance.module_id
    part = ""
    if instance.device is not None:
        part = getattr(instance.device, "part_number", module_id)
    label = part or module_id

    if category == "mcu":
        return _draw_mcu_module(d, instance, origin, ref_map, label)
    elif category == "led":
        return _draw_led_module(d, instance, origin, ref_map, label)
    else:
        return _draw_generic_control_module(d, instance, origin, ref_map, label)


def _draw_mcu_module(
    d: schemdraw.Drawing,
    instance: ModuleInstance,
    origin: tuple[float, float],
    ref_map: dict[str, str],
    label: str,
) -> dict[str, tuple[float, float]]:
    """绘制 MCU 模块。"""
    ox, oy = origin

    # 收集 GPIO 引脚
    gpio_pins = [
        name for name, port in instance.resolved_ports.items()
        if port.port_role == "gpio"
    ]

    pins = [
        elm.IcPin(name="VDD", side="left", slot="1/2"),
        elm.IcPin(name="GND", side="bottom", slot="1/1"),
    ]

    # 添加 GPIO 到右侧
    for i, gpio_name in enumerate(gpio_pins[:4]):  # 最多显示 4 个 GPIO
        pins.append(
            elm.IcPin(
                name=gpio_name,
                side="right",
                slot=f"{i + 1}/{min(len(gpio_pins), 4)}",
            ),
        )

    # EN 引脚（如果有）
    if any(p.port_role == "enable" for p in instance.resolved_ports.values()):
        pins.append(elm.IcPin(name="EN", side="left", slot="2/2"))

    ic = elm.Ic(
        pins=pins,
        size=(4, 3),
    ).at(origin).anchor("inL1").label(label, "top")

    # GND
    d.push()
    elm.Line().at(ic.inB1).down(1)  # type: ignore[call-arg]
    gnd_pos = d.here
    elm.Ground()
    d.pop()

    # --- 去耦电容 (VDD 旁) ---
    # 画一个代表性 100nF 去耦电容 + bulk cap
    decoup_ref = ref_map.get("decoupling_cap_1", "")
    bulk_ref = ref_map.get("bulk_cap", "")
    if decoup_ref or bulk_ref:
        d.push()
        elm.Line().at(ic.inL1).left(1)  # type: ignore[call-arg]
        vdd_wire = d.here
        elm.Dot()

        if decoup_ref:
            decoup_val = _get_comp_value(instance, "decoupling_cap_1", "100nF")
            d.push()
            elm.Capacitor().at(vdd_wire).down().label(
                f"{decoup_ref}\n{decoup_val}", "right",
            )
            elm.Ground()
            d.pop()

        if bulk_ref:
            bulk_val = _get_comp_value(instance, "bulk_cap", "10\u03bcF")
            d.push()
            # 画在去耦电容左边
            elm.Line().at(vdd_wire).left(1.5)
            elm.Dot()
            elm.Capacitor(polar=True).down().label(
                f"{bulk_ref}\n{bulk_val}", "right",
            )
            elm.Ground()
            d.pop()

        d.pop()

    anchors: dict[str, tuple[float, float]] = {
        "VDD": (ic.inL1.x, ic.inL1.y),  # type: ignore[union-attr]
        "GND": (gnd_pos.x, gnd_pos.y),
    }

    # GPIO 锚点
    for i, gpio_name in enumerate(gpio_pins[:4]):
        anchor_name = f"inR{i + 1}"
        try:
            pos = getattr(ic, anchor_name)
            anchors[gpio_name] = (pos.x, pos.y)
        except AttributeError:
            pass

    return anchors


def _draw_led_module(
    d: schemdraw.Drawing,
    instance: ModuleInstance,
    origin: tuple[float, float],
    ref_map: dict[str, str],
    label: str,
) -> dict[str, tuple[float, float]]:
    """绘制 LED 指示灯模块。"""
    ox, oy = origin

    # LED: 限流电阻 → LED → GND (垂直向下)
    r_ref = ref_map.get("led_limit", "R?")
    r_val = _get_comp_value(instance, "led_limit", "")
    led_color = instance.parameters.get("led_color", "green")

    anode_pos = (ox, oy)
    elm.Dot().at(anode_pos)
    r_label = f"{r_ref}\n{r_val}" if r_val else r_ref
    elm.Resistor().at(anode_pos).down().label(r_label, "right")
    elm.LED().down().label(f"LED\n{led_color}", "right")
    gnd_pos = d.here
    elm.Ground()

    return {
        "ANODE": (ox, oy),
        "GND": (gnd_pos.x, gnd_pos.y),
    }


def _draw_generic_control_module(
    d: schemdraw.Drawing,
    instance: ModuleInstance,
    origin: tuple[float, float],
    ref_map: dict[str, str],
    label: str,
) -> dict[str, tuple[float, float]]:
    """绘制通用控制模块。"""
    ox, oy = origin

    ic = elm.Ic(
        pins=[
            elm.IcPin(name="IN", side="left", slot="1/1"),
            elm.IcPin(name="GND", side="bottom", slot="1/1"),
            elm.IcPin(name="OUT", side="right", slot="1/1"),
        ],
        size=(3, 2),
    ).at(origin).anchor("inL1").label(label, "top")

    d.push()
    elm.Line().at(ic.inB1).down(1)  # type: ignore[call-arg]
    gnd_pos = d.here
    elm.Ground()
    d.pop()

    return {
        "IN": (ic.inL1.x, ic.inL1.y),  # type: ignore[union-attr]
        "OUT": (ic.inR1.x, ic.inR1.y),  # type: ignore[union-attr]
        "GND": (gnd_pos.x, gnd_pos.y),
    }


# ============================================================
# T082: 占位块（C67 缺失模块可视化）
# ============================================================


def _draw_placeholder_module(
    d: schemdraw.Drawing,
    instance: ModuleInstance,
    origin: tuple[float, float],
) -> dict[str, tuple[float, float]]:
    """绘制占位方块（用于未解析/缺失的模块）。"""
    ox, oy = origin
    label = f"[{instance.module_id}]\n{instance.missing_part_number or '?'}"

    ic = elm.Ic(
        pins=[
            elm.IcPin(name="IN", side="left", slot="1/1"),
            elm.IcPin(name="GND", side="bottom", slot="1/1"),
            elm.IcPin(name="OUT", side="right", slot="1/1"),
        ],
        size=(3, 2),
    ).at(origin).anchor("inL1").label(label, "top")

    d.push()
    elm.Line().at(ic.inB1).down(1)  # type: ignore[call-arg]
    gnd_pos = d.here
    elm.Ground()
    d.pop()

    return {
        "VIN": (ic.inL1.x, ic.inL1.y),  # type: ignore[union-attr]
        "VOUT": (ic.inR1.x, ic.inR1.y),  # type: ignore[union-attr]
        "GND": (gnd_pos.x, gnd_pos.y),
    }


# ============================================================
# T083: Power chain layout
# ============================================================


def layout_power_chain(
    d: schemdraw.Drawing,
    ir: SystemDesignIR,
    ref_map: dict[str, dict[str, str]],
    layout_spec: SystemLayoutSpec | None = None,
    metadata: RenderMetadata | None = None,
) -> dict[str, dict[str, tuple[float, float]]]:
    """Layout all power chain modules left-to-right.

    Returns: module_id -> {port_role: (x,y)}
    """
    all_anchors: dict[str, dict[str, tuple[float, float]]] = {}

    # 确定电源链中的模块（按 placement_hint 或类别）
    # 如果 layout_spec 提供了 module_order，使用它代替拓扑排序
    spec_order = getattr(layout_spec, "module_order", None)
    if spec_order:
        # 按 layout_spec.module_order 排列，仅取存在于 IR 中且属于电源类别的模块
        power_modules = []
        for mid in spec_order:
            inst = ir.module_instances.get(mid)
            if inst is not None and inst.resolved_category.lower() in _POWER_CATEGORIES:
                power_modules.append(inst)
        # 补充 module_order 中未列出但属于电源类别的模块（确保不漏）
        ordered_ids = {m.module_id for m in power_modules}
        for inst in ir.module_instances.values():
            if inst.resolved_category.lower() in _POWER_CATEGORIES and inst.module_id not in ordered_ids:
                power_modules.append(inst)
    else:
        power_modules = _order_power_chain(ir)

    module_positions = _layout_positions(layout_spec)

    # 如果 layout_spec 提供了 module_spacing，直接使用；否则用默认值
    spec_spacing = getattr(layout_spec, "module_spacing", None)
    if spec_spacing is not None and spec_spacing != _MODULE_X_SPACING:
        spacing = float(spec_spacing)
    else:
        spacing = _MODULE_X_SPACING * _layout_number(
            layout_spec, "module_spacing_scale", 1.0,
        )

    x_cursor = 0.0
    for instance in power_modules:
        module_id = instance.module_id
        module_refs = ref_map.get(module_id, {})
        origin = module_positions.get(module_id, (x_cursor, _MODULE_Y_OFFSET))

        if instance.status in (ModuleStatus.PENDING, ModuleStatus.NEEDS_ASSET, ModuleStatus.ERROR):
            # C67: 缺失模块用占位块
            anchors = _draw_placeholder_module(d, instance, origin)
        else:
            anchors = draw_power_module(
                d, instance, origin, module_refs,
            )

        all_anchors[module_id] = anchors
        _record_module_metadata(metadata, instance, origin, anchors)
        x_cursor = max(x_cursor, origin[0]) + spacing

    return all_anchors


def _order_power_chain(ir: SystemDesignIR) -> list[ModuleInstance]:
    """按电源链拓扑排序模块。

    通过连接关系推导顺序：没有上游电源的模块排在前面。
    C63: 布局稳定 — 使用确定性排序。
    """
    power_modules = [
        inst for inst in ir.module_instances.values()
        if inst.resolved_category.lower() in _POWER_CATEGORIES
    ]

    if not power_modules:
        return []

    # 构建供电依赖图: dst_module -> src_module (电源链)
    fed_by: dict[str, str] = {}
    for conn in ir.connections:
        if conn.rule_id == "RULE_POWER_SUPPLY":
            src_mod = conn.src_port.module_id
            dst_mod = conn.dst_port.module_id
            fed_by[dst_mod] = src_mod

    # 拓扑排序：找到没有上游的模块作为起点
    power_ids = {m.module_id for m in power_modules}
    ordered: list[ModuleInstance] = []
    visited: set[str] = set()

    def _visit(mid: str) -> None:
        if mid in visited or mid not in power_ids:
            return
        # 先访问上游
        upstream = fed_by.get(mid)
        if upstream and upstream not in visited:
            _visit(upstream)
        visited.add(mid)
        inst = ir.module_instances.get(mid)
        if inst is not None:
            ordered.append(inst)

    # 确定性遍历：按 module_id 字典序
    for mid in sorted(power_ids):
        _visit(mid)

    return ordered


# ============================================================
# T084: Control side layout
# ============================================================


def layout_control_side(
    d: schemdraw.Drawing,
    ir: SystemDesignIR,
    power_anchors: dict[str, dict[str, tuple[float, float]]],
    ref_map: dict[str, dict[str, str]],
    layout_spec: SystemLayoutSpec | None = None,
    metadata: RenderMetadata | None = None,
) -> None:
    """Layout MCU, LED, and other control modules below/beside the power chain.

    C69: 控制支路区别于主电源链布局。
    """
    control_modules = [
        inst for inst in ir.module_instances.values()
        if inst.resolved_category.lower() not in _POWER_CATEGORIES
    ]

    if not control_modules:
        return

    # 控制模块放在电源链下方
    # 如果 layout_spec 提供了 control_y_gap，使用它；否则用默认值
    spec_y_gap = getattr(layout_spec, "control_y_gap", None)
    control_y = -abs(spec_y_gap) if spec_y_gap is not None else _CONTROL_Y_OFFSET

    # 确定 X 起点：与最后一个电源模块对齐或从 0 开始
    x_start = 0.0
    if power_anchors:
        # 所有电源模块的 VOUT x 坐标的最大值
        max_vout_x = max(
            anchors.get("VOUT", (0, 0))[0]
            for anchors in power_anchors.values()
        )
        # 控制模块起点在电源链中间
        x_start = max_vout_x / 2 if max_vout_x > 0 else 0.0

    # 按 module_id 排序确保稳定布局 (C63)
    control_modules.sort(key=lambda m: m.module_id)
    module_positions = _layout_positions(layout_spec)
    spacing = _CONTROL_X_SPACING * _layout_number(
        layout_spec, "module_spacing_scale", 1.0,
    )

    x_cursor = x_start
    for instance in control_modules:
        module_id = instance.module_id
        module_refs = ref_map.get(module_id, {})
        origin = module_positions.get(module_id, (x_cursor, control_y))

        if instance.status in (ModuleStatus.PENDING, ModuleStatus.NEEDS_ASSET, ModuleStatus.ERROR):
            anchors = _draw_placeholder_module(d, instance, origin)
        else:
            anchors = draw_control_module(d, instance, origin, module_refs)

        power_anchors[module_id] = anchors
        _record_module_metadata(metadata, instance, origin, anchors)
        x_cursor = max(x_cursor, origin[0]) + spacing


# ============================================================
# T086: Inter-module wires
# ============================================================


def draw_intermodule_wires(
    d: schemdraw.Drawing,
    connections: list[ResolvedConnection],
    all_anchors: dict[str, dict[str, tuple[float, float]]],
) -> list[tuple[str, str, list[tuple[float, float]]]]:
    """Draw wires between modules using resolved connections and anchor points.

    C70: 每条连接都在 IR 中有来源。
    C68: unresolved 连接的可视提示由调用方处理。
    """
    wire_paths: list[tuple[str, str, list[tuple[float, float]]]] = []
    for conn in connections:
        src_module = conn.src_port.module_id
        dst_module = conn.dst_port.module_id

        src_anchors = all_anchors.get(src_module, {})
        dst_anchors = all_anchors.get(dst_module, {})

        if not src_anchors or not dst_anchors:
            continue

        # 找到最佳匹配的锚点
        src_pos = _find_best_anchor(src_anchors, conn.src_port.pin_name, "VOUT")
        dst_pos = _find_best_anchor(dst_anchors, conn.dst_port.pin_name, "VIN")

        if src_pos is None or dst_pos is None:
            continue

        # 使用 L 形走线连接两点
        wire_paths.append((src_module, dst_module, _draw_wire(d, src_pos, dst_pos)))

    return wire_paths


def _find_best_anchor(
    anchors: dict[str, tuple[float, float]],
    pin_name: str,
    fallback: str,
) -> tuple[float, float] | None:
    """从锚点字典中找到最佳匹配。"""
    # 精确匹配
    if pin_name in anchors:
        return anchors[pin_name]

    # 回退
    if fallback in anchors:
        return anchors[fallback]

    # 返回任意一个
    if anchors:
        return next(iter(anchors.values()))

    return None


def _draw_wire(
    d: schemdraw.Drawing,
    p1: tuple[float, float],
    p2: tuple[float, float],
) -> list[tuple[float, float]]:
    """在两个点之间绘制连线。

    如果 Y 坐标相同则直线连接，否则使用 L 形走线。
    """
    x1, y1 = p1
    x2, y2 = p2

    if abs(y1 - y2) < 0.1:
        # 水平直线
        elm.Line().at(p1).to(p2)
        return [p1, p2]
    else:
        # L 形走线：先水平到中点 x，再垂直
        mid_x = (x1 + x2) / 2
        elm.Line().at(p1).to((mid_x, y1))
        elm.Line().at((mid_x, y1)).to((mid_x, y2))
        elm.Line().at((mid_x, y2)).to(p2)
        return [p1, (mid_x, y1), (mid_x, y2), p2]


# ============================================================
# T087: Net labels
# ============================================================


def draw_net_labels(
    d: schemdraw.Drawing,
    nets: dict[str, SystemNet],
    all_anchors: dict[str, dict[str, tuple[float, float]]],
) -> None:
    """为非 GND 网络绘制网络标签。

    C65: net label 与系统 net 保持一致。
    """
    for net_id, net in nets.items():
        if net.net_type == NetType.GROUND:
            continue  # GND 由 GND 符号表示

        if not net.members:
            continue

        # 在第一个成员端口的位置放置标签
        first_member = net.members[0]
        mod_anchors = all_anchors.get(first_member.module_id, {})
        pos = _find_best_anchor(mod_anchors, first_member.pin_name, "VOUT")
        if pos is None:
            continue

        # 在连接点旁边标注网络名
        label_pos = (pos[0], pos[1] + 0.8)
        elm.Label().at(label_pos).label(net.net_name, "top")


# ============================================================
# T088: GND symbols
# ============================================================


def draw_gnd_symbols(
    d: schemdraw.Drawing,
    all_anchors: dict[str, dict[str, tuple[float, float]]],
) -> None:
    """在每个模块的 GND 锚点绘制补充 GND 符号。

    C66: GND 视觉表示统一。
    注：模块绘制函数已经在 IC 底部画了主要 GND 符号，
    此函数为含有额外 GND 锚点（名称包含 "GND"/"gnd" 但非主 "GND"）
    的模块补充地符号。例如 AGND、DGND、GND2 等。
    """
    for _module_id, anchors in all_anchors.items():
        for anchor_name, pos in anchors.items():
            # 跳过已由模块绘制函数处理的主 GND 锚点
            if anchor_name == "GND":
                continue
            # 为包含 "GND"/"gnd" 的额外锚点绘制补充地符号
            if "GND" in anchor_name.upper():
                d.add(elm.Ground().at(pos).down())


# ============================================================
# T068: Unresolved connection visual hints
# ============================================================


def _draw_unresolved_hints(
    d: schemdraw.Drawing,
    ir: SystemDesignIR,
    all_anchors: dict[str, dict[str, tuple[float, float]]],
) -> None:
    """为未解析连接绘制可视提示 (C68)。"""
    for item in ir.unresolved_items:
        if item.get("type") != "unresolved_connection":
            continue
        module_id = item.get("src_module", "")
        mod_anchors = all_anchors.get(module_id, {})
        if mod_anchors:
            # 在模块旁边添加警告标签
            any_pos = next(iter(mod_anchors.values()))
            label_pos = (any_pos[0], any_pos[1] + 1.5)
            elm.Label().at(label_pos).label(
                f"[!] {item.get('reason', 'unresolved')[:30]}", "top",
            )


# ============================================================
# T088 主入口: render_system_svg
# ============================================================


def render_system_svg_with_metadata(
    ir: SystemDesignIR,
    instances: list[ModuleInstance] | None = None,
    filename: str | None = None,
    layout_spec: SystemLayoutSpec | None = None,
) -> tuple[str, RenderMetadata]:
    """Render the complete system into a single SVG file.

    C61: 基于系统 IR 渲染
    C62: 输出单一 SVG

    Args:
        ir: 系统设计中间表示
        instances: 可选的实例列表覆盖（若为 None，使用 ir.module_instances）
        filename: 输出文件名（若为 None，自动生成）

    Returns:
        (SVG 文件路径, RenderMetadata)
    """
    if filename is None:
        import time
        filename = f"system_design_{int(time.time() * 1000) % 100000}.svg"
    filepath = output_path(filename)

    # 如果提供了 instances 列表，同步到 IR
    if instances is not None:
        for inst in instances:
            if inst.module_id not in ir.module_instances:
                ir.module_instances[inst.module_id] = inst

    # 全局参考编号 (C64)
    ref_map = _build_global_ref_map(ir)
    metadata = RenderMetadata()

    with schemdraw.Drawing(file=filepath, show=False) as d:
        d.config(fontsize=10, unit=3)

        # T083: 电源链布局
        all_anchors = layout_power_chain(
            d, ir, ref_map, layout_spec=layout_spec, metadata=metadata,
        )

        # 空系统时添加占位元素防止 schemdraw 因空白 bbox 崩溃
        if not ir.module_instances:
            elm.Label().at((0, 0)).label("(empty system)", "top")

        # T084: 控制支路布局
        layout_control_side(
            d, ir, all_anchors, ref_map,
            layout_spec=layout_spec,
            metadata=metadata,
        )

        # T086: 模块间连线
        metadata.wire_paths = draw_intermodule_wires(d, ir.connections, all_anchors)

        # T087: 网络标签（暂时禁用，避免标签遮挡电路）
        # draw_net_labels(d, ir.nets, all_anchors)

        # T088: GND 符号（补充）
        draw_gnd_symbols(d, all_anchors)

        # C68: 未解析连接可视提示
        # _draw_unresolved_hints(d, ir, all_anchors)  # 暂时禁用避免乱码

    logger.info("System SVG rendered: %s", filepath)
    return filepath, _normalize_render_metadata(metadata, layout_spec)


def render_system_svg(
    ir: SystemDesignIR,
    instances: list[ModuleInstance] | None = None,
    filename: str | None = None,
    layout_spec: SystemLayoutSpec | None = None,
) -> str:
    filepath, _metadata = render_system_svg_with_metadata(
        ir,
        instances=instances,
        filename=filename,
        layout_spec=layout_spec,
    )
    return filepath
