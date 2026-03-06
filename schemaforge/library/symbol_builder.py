"""KLC v3.0.64 兼容符号构建引擎

确定性的本地符号构建器，AI Agent 通过 ToolRegistry 调用。
AI 只传入结构化引脚数据，本引擎负责生成符合 KLC 标准的 SymbolDef。

核心流程:
  build_symbol(part_number, pins_data, category, package)
    → assign_pin_sides()   # 引脚方位分配
    → auto_body_size()     # 符号尺寸计算
    → assign_slots()       # 槽位分配
    → SymbolDef            # 最终输出
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict

from schemaforge.core.models import PinType
from schemaforge.library.models import PinSide, SymbolDef, SymbolPin

logger = logging.getLogger(__name__)


# ============================================================
# KLC v3.0.64 标准常量
# ============================================================


class KLCDefaults:
    """KLC v3.0.64 (IEC-60617) 标准常量

    包含 KiCad 物理单位 (mm) 和 schemdraw 坐标单位两套体系。
    """

    # --- KiCad 物理单位 (mm) ---
    GRID_MM: float = 2.54            # 100mil 栅格
    PIN_SPACING_MM: float = 2.54     # 100mil 引脚间距
    PIN_LENGTH_SHORT: float = 2.54   # 100mil (1-2 字符引脚编号)
    PIN_LENGTH_MEDIUM: float = 3.81  # 150mil (3 字符引脚编号)
    PIN_LENGTH_LONG: float = 5.08    # 200mil (4+ 字符引脚编号)
    FONT_SIZE_MM: float = 1.27       # 50mil 文字
    PIN_NAME_OFFSET_MM: float = 0.508  # 20mil
    BODY_LINE_WIDTH_MM: float = 0.254  # 10mil
    BODY_MIN_W_MM: float = 10.16     # 400mil 最小宽度
    BODY_MIN_H_MM: float = 15.24     # 600mil 最小高度

    # --- schemdraw 坐标单位 ---
    # schemdraw 使用自有浮点坐标系，1 unit ≈ 1 "grid step"
    # 参考已有器件 JSON: pin_spacing=1.0, lead_len=0.5, edge_pad=0.5
    PIN_SPACING: float = 1.0         # schemdraw 引脚间距
    LEAD_LEN: float = 0.5           # schemdraw 引线长度
    EDGE_PAD_W: float = 0.5         # schemdraw 水平边距
    EDGE_PAD_H: float = 0.5         # schemdraw 垂直边距

    # --- 符号尺寸下限 (schemdraw 单位) ---
    BODY_MIN_W: float = 3.0         # 最小宽度
    BODY_MIN_H: float = 3.0         # 最小高度

    # --- 栅格对齐步长 (schemdraw 单位) ---
    SNAP_GRID: float = 0.5          # 对齐到 0.5 步长


# ============================================================
# 引脚名称模式 → 方位映射
# ============================================================

# 电源正极模式 (默认 → TOP，电源转换器例外)
_POWER_POSITIVE_PATTERNS: set[str] = {
    "VCC", "VDD", "VIN", "V+", "VBAT", "VREF",
    "AVDD", "DVDD", "IOVDD", "PVDD", "VDDIO",
}

# 电源输出模式 (用于电源转换器 → RIGHT)
_POWER_OUTPUT_PATTERNS: set[str] = {
    "VOUT", "VO", "VOUT1", "VOUT2",
}

# 接地模式 → BOTTOM
_GROUND_PATTERNS: set[str] = {
    "GND", "VSS", "V-", "AGND", "DGND", "PGND",
    "GNDA", "GNDD", "EP", "PAD", "EPAD",
}

# 输入信号模式 → LEFT
_INPUT_PATTERNS: set[str] = {
    "EN", "ENABLE", "~RESET", "RESET", "CLK", "SCL",
    "MOSI", "CS", "~CS", "SS", "~SS", "SYNC",
    "SHDN", "~SHDN", "SHUTDOWN", "CE", "OE", "~OE",
    "WE", "~WE", "RD", "~RD", "WR", "~WR",
    "DIN", "RX", "SCLK", "SCK",
}

# 输出信号模式 → RIGHT
_OUTPUT_PATTERNS: set[str] = {
    "OUT", "ALARM", "INT", "~INT", "IRQ", "MISO",
    "SDA", "TX", "DOUT", "PG", "PGOOD", "~FAULT",
    "FAULT", "~PG", "READY", "~READY", "BUSY", "~BUSY",
    "SDO", "DO",
}

# 反馈/补偿 → RIGHT
_FEEDBACK_PATTERNS: set[str] = {
    "FB", "COMP", "SS",  # SS 在此为 soft-start
}

# 自举 → TOP
_BOOTSTRAP_PATTERNS: set[str] = {
    "BST", "BOOT", "BOOTSTRAP",
}

# 开关节点 → RIGHT
_SWITCH_NODE_PATTERNS: set[str] = {
    "SW", "PH", "PHASE", "LX",
}

# 电源转换器类别
_POWER_CONVERTER_CATEGORIES: set[str] = {
    "ldo", "buck", "boost", "buck_boost", "linear_regulator",
}


# ============================================================
# AI 引脚类型 → PinType 映射
# ============================================================


def _map_ai_pin_type(ai_type: str, pin_name: str) -> PinType:
    """将 AI 提取的引脚类型映射到 PinType 枚举

    AI 输出的 "power" 类型需要根据引脚名称进一步区分:
    - 接地名称 → GROUND
    - 输出名称 → POWER_OUT
    - 其他 → POWER_IN
    """
    ai_type_lower = ai_type.strip().lower()
    name_upper = pin_name.strip().upper()

    if ai_type_lower == "power":
        if name_upper in _GROUND_PATTERNS:
            return PinType.GROUND
        if name_upper in _POWER_OUTPUT_PATTERNS:
            return PinType.POWER_OUT
        return PinType.POWER_IN

    type_map: dict[str, PinType] = {
        "input": PinType.INPUT,
        "output": PinType.OUTPUT,
        "passive": PinType.PASSIVE,
        "nc": PinType.NO_CONNECT,
        "no_connect": PinType.NO_CONNECT,
        "bidirectional": PinType.BIDIRECTIONAL,
        "power_in": PinType.POWER_IN,
        "power_out": PinType.POWER_OUT,
        "ground": PinType.GROUND,
        "open_collector": PinType.OUTPUT,
        "open_drain": PinType.OUTPUT,
    }
    return type_map.get(ai_type_lower, PinType.PASSIVE)


# ============================================================
# 引脚方位分配
# ============================================================


def _is_inverted_pin(name: str) -> bool:
    """检测引脚名称是否为反相（低电平有效）"""
    return name.startswith("~") or name.startswith("/")


def _normalize_pin_name(name: str) -> str:
    """标准化引脚名称用于模式匹配（去除反相前缀）"""
    stripped = name.strip()
    if stripped.startswith("~") or stripped.startswith("/"):
        stripped = stripped[1:]
    return stripped.upper()


def assign_pin_sides(
    pins: list[dict[str, str]],
    category: str = "",
    is_power_converter: bool = False,
) -> list[tuple[dict[str, str], PinSide]]:
    """确定性引脚方位分配

    根据引脚名称模式和类型，按优先级规则分配方位。
    电源转换器 (LDO/Buck/Boost) 有特殊规则：电源输入→LEFT，电源输出→RIGHT。

    Args:
        pins: 引脚字典列表，每项含 name, pin_type, 可选 number/description
        category: 器件类别 (ldo, buck, boost 等)
        is_power_converter: 是否为电源转换器（也可从 category 自动推断）

    Returns:
        (引脚字典, 分配方位) 的元组列表
    """
    cat_lower = category.strip().lower()
    is_converter = is_power_converter or cat_lower in _POWER_CONVERTER_CATEGORIES

    results: list[tuple[dict[str, str], PinSide]] = []

    for pin in pins:
        name_raw = pin.get("name", "").strip()
        name_upper = _normalize_pin_name(name_raw)
        pin_type_str = pin.get("type", pin.get("pin_type", "passive")).strip().lower()

        side = _assign_single_pin_side(name_upper, pin_type_str, is_converter)
        results.append((pin, side))

    return results


def _assign_single_pin_side(
    name_upper: str,
    pin_type_str: str,
    is_converter: bool,
) -> PinSide:
    """单个引脚的方位分配（纯规则，无副作用）

    优先级顺序:
    1. 接地模式 → BOTTOM
    2. 自举模式 → TOP
    3. 电源正极（含转换器例外）
    4. 电源输出（含转换器例外）
    5. 开关节点 → RIGHT
    6. 反馈/补偿 → RIGHT
    7. 输入信号模式 → LEFT
    8. 输出信号模式 → RIGHT
    9. 按 pin_type 兜底
    """
    # 1. 接地 → BOTTOM（最高优先级）
    if name_upper in _GROUND_PATTERNS:
        return PinSide.BOTTOM

    # 2. 自举 → TOP
    if name_upper in _BOOTSTRAP_PATTERNS:
        return PinSide.TOP

    # 3. 电源正极
    if name_upper in _POWER_POSITIVE_PATTERNS:
        if is_converter:
            return PinSide.LEFT  # 电源转换器: 电源输入 → LEFT
        return PinSide.TOP

    # 4. 电源输出
    if name_upper in _POWER_OUTPUT_PATTERNS:
        if is_converter:
            return PinSide.RIGHT  # 电源转换器: 电源输出 → RIGHT
        return PinSide.RIGHT

    # 5. 开关节点 → RIGHT
    if name_upper in _SWITCH_NODE_PATTERNS:
        return PinSide.RIGHT

    # 6. 反馈/补偿 → RIGHT
    if name_upper in _FEEDBACK_PATTERNS:
        return PinSide.RIGHT

    # 7. 输入信号 → LEFT
    if name_upper in _INPUT_PATTERNS:
        return PinSide.LEFT

    # 8. 输出信号 → RIGHT
    if name_upper in _OUTPUT_PATTERNS:
        return PinSide.RIGHT

    # 9. 按 pin_type 兜底
    return _side_from_pin_type(pin_type_str)


def _side_from_pin_type(pin_type_str: str) -> PinSide:
    """根据引脚电气类型分配默认方位"""
    type_side_map: dict[str, PinSide] = {
        "input": PinSide.LEFT,
        "output": PinSide.RIGHT,
        "power": PinSide.TOP,
        "power_in": PinSide.TOP,
        "power_out": PinSide.RIGHT,
        "ground": PinSide.BOTTOM,
        "passive": PinSide.LEFT,
        "bidirectional": PinSide.RIGHT,
        "nc": PinSide.LEFT,
        "no_connect": PinSide.LEFT,
    }
    return type_side_map.get(pin_type_str, PinSide.LEFT)


# ============================================================
# 符号尺寸计算
# ============================================================


def _snap_up(value: float, grid: float) -> float:
    """向上对齐到指定栅格步长"""
    return math.ceil(value / grid) * grid


def auto_body_size(
    pins_per_side: dict[PinSide, int],
) -> tuple[float, float]:
    """计算 KLC 兼容的符号尺寸 (schemdraw 单位)

    宽度由 top/bottom 引脚数决定，同时需要足够宽以容纳 left/right 引脚名称。
    高度由 left/right 引脚数决定，同时需要足够高以容纳 top/bottom 引脚名称。
    结果向上对齐到 0.5 栅格。

    尺寸公式参考已有器件:
    - AMS1117 (1L, 1R, 0T, 1B) → (4.0, 3.0)
    - TPS5430 (2L, 2R, 1T, 1B) → (5.0, 4.0)

    Args:
        pins_per_side: 每侧引脚数量字典

    Returns:
        (width, height) 元组，schemdraw 单位
    """
    n_top = pins_per_side.get(PinSide.TOP, 0)
    n_bottom = pins_per_side.get(PinSide.BOTTOM, 0)
    n_left = pins_per_side.get(PinSide.LEFT, 0)
    n_right = pins_per_side.get(PinSide.RIGHT, 0)

    sp = KLCDefaults.PIN_SPACING
    pad_w = KLCDefaults.EDGE_PAD_W
    pad_h = KLCDefaults.EDGE_PAD_H
    grid = KLCDefaults.SNAP_GRID

    # 宽度: 容纳 top/bottom 引脚 + left/right 引脚名称空间
    # 经验公式 (匹配已有器件 JSON):
    #   AMS1117 (1L,1R,0T,1B) → 4.0
    #   TPS5430 (2L,2R,1T,1B) → 5.0
    # 规则: 基础 = max(各侧引脚数) * sp + 2 * pad
    #       + 每个有引脚的 left/right 侧额外 1.0 (引脚名称空间)
    max_tb = max(n_top, n_bottom)
    max_lr = max(n_left, n_right)

    # 水平方向: top/bottom 引脚占位 + left/right 引脚名称空间
    w_pin_driven = max_tb * sp + 2 * pad_w
    w_label_bonus = (1.0 if n_left > 0 else 0.0) + (1.0 if n_right > 0 else 0.0)
    raw_w = max(w_pin_driven, max_lr * sp + 2 * pad_w) + w_label_bonus
    width = max(_snap_up(raw_w, grid), KLCDefaults.BODY_MIN_W)

    # 垂直方向: left/right 引脚占位 + top/bottom 引脚名称空间
    h_pin_driven = max_lr * sp + 2 * pad_h
    h_label_bonus = (0.5 if n_top > 0 else 0.0) + (0.5 if n_bottom > 0 else 0.0)
    raw_h = max(h_pin_driven, max_tb * sp + 2 * pad_h) + h_label_bonus
    height = max(_snap_up(raw_h, grid), KLCDefaults.BODY_MIN_H)

    return (width, height)


# ============================================================
# 槽位分配
# ============================================================


def _pin_sort_key(pin: SymbolPin) -> tuple[int, str]:
    """引脚排序键: 电源引脚优先，然后按名称字母序

    排序优先级:
    0 — 电源类 (POWER_IN, POWER_OUT, GROUND)
    1 — 信号类 (其他所有类型)
    """
    power_types = {PinType.POWER_IN, PinType.POWER_OUT, PinType.GROUND}
    priority = 0 if pin.pin_type in power_types else 1
    return (priority, pin.name.upper())


def assign_slots(pins: list[SymbolPin]) -> list[SymbolPin]:
    """为每个引脚分配槽位字符串 (slot = "i/n")

    每侧独立编号。LEFT/RIGHT 侧电源引脚优先排列，
    TOP/BOTTOM 侧按名称字母序排列。

    Args:
        pins: 已分配方位的 SymbolPin 列表

    Returns:
        更新了 slot 字段的 SymbolPin 列表（新列表，不修改原始对象）
    """
    # 按方位分组
    side_groups: dict[PinSide, list[int]] = defaultdict(list)
    for idx, pin in enumerate(pins):
        side_groups[pin.side].append(idx)

    # 构建更新后的引脚列表
    updated: list[SymbolPin] = []
    slot_order: dict[int, tuple[int, int]] = {}  # idx → (position, total)

    for side, indices in side_groups.items():
        # 取出该侧引脚并排序
        side_pins = [(i, pins[i]) for i in indices]

        if side in (PinSide.LEFT, PinSide.RIGHT):
            # 电源优先，然后字母序
            side_pins.sort(key=lambda x: _pin_sort_key(x[1]))
        else:
            # TOP/BOTTOM: 纯字母序
            side_pins.sort(key=lambda x: x[1].name.upper())

        total = len(side_pins)
        for pos_0based, (orig_idx, _pin) in enumerate(side_pins):
            slot_order[orig_idx] = (pos_0based + 1, total)

    # 按原始顺序重建，更新 slot
    for idx, pin in enumerate(pins):
        pos, total = slot_order[idx]
        updated.append(pin.model_copy(update={"slot": f"{pos}/{total}"}))

    return updated


# ============================================================
# 主入口: build_symbol
# ============================================================


def build_symbol(
    part_number: str,
    pins_data: list[dict[str, str]],
    category: str = "",
    package: str = "",
) -> SymbolDef:
    """统一入口: 结构化引脚数据 → KLC 兼容 SymbolDef

    AI Agent 通过 ToolRegistry 调用此函数。AI 只需提供从 datasheet
    提取的引脚列表，本函数负责所有 KLC 合规性处理。

    Args:
        part_number: 器件型号 (如 "AMS1117-3.3")
        pins_data: AI 提取的引脚列表，每项格式:
            {"name": "VIN", "number": "1", "type": "power", "description": "输入电压"}
        category: 器件类别 (如 "ldo", "buck")
        package: 封装类型 (如 "SOT-223")

    Returns:
        完整的 SymbolDef，可直接用于 schemdraw 渲染

    Raises:
        ValueError: 无有效引脚时抛出
    """
    # --- 1. 预处理: 过滤和修复引脚数据 ---
    cleaned = _clean_pins_data(pins_data)

    if not cleaned:
        raise ValueError(
            f"器件 {part_number} 无有效引脚数据，"
            "请检查 pins_data 是否为空或全部为 NC 引脚"
        )

    # --- 2. 映射 AI 引脚类型 → PinType ---
    for pin in cleaned:
        ai_type = pin.get("type", pin.get("pin_type", "passive"))
        pin["_mapped_type"] = _map_ai_pin_type(ai_type, pin.get("name", ""))

    # --- 3. 分配方位 ---
    sided = assign_pin_sides(cleaned, category=category)

    # --- 4. 构建 SymbolPin 列表 ---
    symbol_pins: list[SymbolPin] = []
    for pin_dict, side in sided:
        name = pin_dict.get("name", "").strip()
        mapped_type: PinType = pin_dict.get("_mapped_type", PinType.PASSIVE)  # type: ignore[assignment]

        symbol_pins.append(SymbolPin(
            name=name,
            pin_number=pin_dict.get("number", ""),
            side=side,
            pin_type=mapped_type,
            inverted=_is_inverted_pin(name),
            description=pin_dict.get("description", ""),
        ))

    # --- 5. 检查方位分布 ---
    _warn_if_single_side(symbol_pins, part_number)

    # --- 6. 分配槽位 ---
    symbol_pins = assign_slots(symbol_pins)

    # --- 7. 计算尺寸 ---
    pins_per_side = _count_pins_per_side(symbol_pins)
    size = auto_body_size(pins_per_side)

    # --- 8. 组装 SymbolDef ---
    return SymbolDef(
        pins=symbol_pins,
        size=size,
        edge_pad_w=KLCDefaults.EDGE_PAD_W,
        edge_pad_h=KLCDefaults.EDGE_PAD_H,
        pin_spacing=KLCDefaults.PIN_SPACING,
        lead_len=KLCDefaults.LEAD_LEN,
        label_position="top",
    )


# ============================================================
# 内部辅助函数
# ============================================================


def _clean_pins_data(pins_data: list[dict[str, str]]) -> list[dict[str, str]]:
    """清洗引脚数据: 修复空名称、过滤多余 NC 引脚

    规则:
    - 空名称 → 使用 "PIN{number}" 替代
    - 缺少 type → 默认 "passive"
    - NC 引脚占比 ≥ 50% 时全部移除，否则保留
    """
    cleaned: list[dict[str, str]] = []
    nc_pins: list[dict[str, str]] = []
    valid_pins: list[dict[str, str]] = []

    for pin in pins_data:
        # 浅拷贝避免修改原始数据
        p = dict(pin)

        # 修复空名称
        name = p.get("name", "").strip()
        if not name:
            number = p.get("number", "")
            p["name"] = f"PIN{number}" if number else "PIN"
            logger.warning("引脚名称为空，已替换为 %s", p["name"])

        # 修复缺失类型
        ai_type = p.get("type", p.get("pin_type", "")).strip().lower()
        if not ai_type:
            p["type"] = "passive"

        # 分类 NC 和有效引脚
        if ai_type in ("nc", "no_connect"):
            nc_pins.append(p)
        else:
            valid_pins.append(p)

    # NC 引脚过滤策略: 占比 < 50% 时保留
    total = len(valid_pins) + len(nc_pins)
    if total > 0 and len(nc_pins) < total * 0.5:
        cleaned = valid_pins + nc_pins
    else:
        cleaned = valid_pins
        if nc_pins:
            logger.info(
                "NC 引脚占比 >= 50%%，已移除 %d 个 NC 引脚",
                len(nc_pins),
            )

    return cleaned


def _count_pins_per_side(pins: list[SymbolPin]) -> dict[PinSide, int]:
    """统计每侧引脚数量"""
    counts: dict[PinSide, int] = defaultdict(int)
    for pin in pins:
        counts[pin.side] += 1
    return dict(counts)


def _warn_if_single_side(pins: list[SymbolPin], part_number: str) -> None:
    """如果所有引脚集中在同一侧，记录警告"""
    sides_used = {pin.side for pin in pins}
    if len(sides_used) == 1 and len(pins) > 1:
        logger.warning(
            "器件 %s 的所有 %d 个引脚都在 %s 侧，"
            "可能需要人工调整方位",
            part_number,
            len(pins),
            next(iter(sides_used)).value,
        )
