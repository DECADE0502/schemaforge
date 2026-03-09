"""系统级模块综合：ModuleInstance 参数计算与依赖传播。

将已解析的 ModuleInstance（status=RESOLVED）综合为带有完整外围元件
参数的实例（status=SYNTHESIZED）。所有计算纯确定性，不调用 AI。

约束遵循:
- C46 Buck/LDO/Boost 参数必须由本地公式计算
- C47 公式必须有 evidence 来源
- C48 工作点变化必须触发依赖模块重算
- C49 不允许复用旧工况缓存污染新工况
- C50 参数默认值必须优先用 typ/nom，不得用 abs max
- C51 电压域必须一致
- C54 LED 电阻必须由驱动电压和目标电流算
- C56 每个模块的外围件必须有角色标记
- C57 每个外围件必须有实例来源
- C58 参数更新必须只重算受影响子图
"""

from __future__ import annotations

import logging

from schemaforge.render.base import find_nearest_e24
from schemaforge.system.models import (
    ModuleInstance,
    ModuleStatus,
    SystemDesignIR,
)

logger = logging.getLogger(__name__)

# ============================================================
# 工程常量
# ============================================================

_CAP_SERIES = [1.0, 2.2, 4.7, 10.0, 22.0, 47.0, 100.0, 220.0]
_INDUCTOR_SERIES = [1.0, 1.5, 2.2, 3.3, 4.7, 6.8, 10.0, 15.0, 22.0, 33.0, 47.0]

# LED 正向压降默认值（按颜色）
_LED_VF_DEFAULTS: dict[str, float] = {
    "red": 2.0,
    "green": 2.2,
    "yellow": 2.0,
    "orange": 2.1,
    "blue": 3.0,
    "white": 3.0,
}

_DEFAULT_LED_CURRENT_A = 0.010  # 10mA


# ============================================================
# 内部工具函数
# ============================================================


def _safe_float(value: str | float | None, default: float = 0.0) -> float:
    """安全解析数值，不使用 abs max（C50）。"""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _trim_float(value: float) -> str:
    """格式化浮点数为紧凑字符串。"""
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _nearest_series_value(
    value: float, series: list[float], scale: float,
) -> float:
    """将数值圆整到标准系列。"""
    if value <= 0:
        return scale
    normalized = value / scale
    magnitude = scale
    while normalized >= 1000:
        normalized /= 1000.0
        magnitude *= 1000.0
    while normalized < 1.0:
        normalized *= 10.0
        magnitude /= 10.0
    best = min(series, key=lambda item: abs(item - normalized))
    return best * magnitude


def _format_cap(value_f: float) -> str:
    """电容值格式化为 ASCII 字符串。"""
    if value_f >= 1e-6:
        return f"{_trim_float(value_f * 1e6)}uF"
    if value_f >= 1e-9:
        return f"{_trim_float(value_f * 1e9)}nF"
    return f"{_trim_float(value_f * 1e12)}pF"


def _format_inductor(value_h: float) -> str:
    """电感值格式化为 ASCII 字符串。"""
    if value_h >= 1e-3:
        return f"{_trim_float(value_h * 1e3)}mH"
    return f"{_trim_float(value_h * 1e6)}uH"


def _format_resistor(value_ohm: float) -> str:
    """电阻值格式化为 ASCII 字符串。"""
    if value_ohm >= 1000:
        return f"{_trim_float(value_ohm / 1000.0)}k\u03a9"
    return f"{_trim_float(value_ohm)}\u03a9"


def _get_device_spec(
    instance: ModuleInstance, key: str, default: float | None = None,
) -> float | None:
    """从器件 specs 中提取数值，优先 typ/nom（C50）。"""
    device = instance.device
    if device is None:
        return default
    specs = getattr(device, "specs", {}) or {}
    constraints = getattr(device, "operating_constraints", {}) or {}

    # 优先 typ/nom，避免 abs max
    for suffix in ("_typ", "_nom", ""):
        candidate = specs.get(f"{key}{suffix}")
        if candidate is not None:
            val = _safe_float(candidate)
            if val > 0:
                return val

    candidate = constraints.get(key)
    if candidate is not None:
        val = _safe_float(candidate)
        if val > 0:
            return val

    return default


# ============================================================
# T062: Buck 模块综合
# ============================================================


def synthesize_buck_module(instance: ModuleInstance) -> ModuleInstance:
    """计算 Buck 转换器外围元件。

    使用 v_in, v_out, i_out 从 instance.parameters 中提取。
    计算: L, C_in, C_out, R_fb_upper, R_fb_lower, C_boot, diode。

    Args:
        instance: 状态为 RESOLVED 的 Buck 模块实例

    Returns:
        更新后的实例，status=SYNTHESIZED，含外围元件列表
    """
    p = instance.parameters

    # 提取参数：优先 parameters，回退 device specs，再回退安全默认值（C50）
    v_in = _safe_float(p.get("v_in"), 0.0) or _get_device_spec(instance, "v_in", 12.0) or 12.0
    v_out = _safe_float(p.get("v_out"), 0.0) or _get_device_spec(instance, "v_out", 5.0) or 5.0
    i_out = _safe_float(p.get("i_out"), 0.0) or _get_device_spec(instance, "i_out", 1.0) or 1.0
    fsw_hz = _safe_float(p.get("fsw"), 0.0) or _get_device_spec(instance, "fsw", 500000.0) or 500000.0
    if fsw_hz < 10000:
        fsw_hz *= 1000.0

    # 反馈参考电压
    v_ref = _get_device_spec(instance, "v_fb") or _get_device_spec(instance, "v_ref") or 0.8

    # 占空比
    duty = min(max(v_out / max(v_in, 0.1), 0.05), 0.95)

    # 电感: L = Vout * (1 - D) / (fsw * delta_IL)，delta_IL = 30% Iout
    ripple_current = max(i_out * 0.3, 0.2)
    l_h = (v_out * (1.0 - duty)) / (fsw_hz * ripple_current)
    l_h = _nearest_series_value(max(l_h, 1e-6), _INDUCTOR_SERIES, 1e-6)

    # 输出电容: Cout = delta_IL / (8 * fsw * delta_Vout)
    ripple_voltage = max(v_out * 0.01, 0.05)
    c_out_f = ripple_current / (8.0 * fsw_hz * ripple_voltage)
    c_out_f = max(c_out_f, 22e-6)
    c_out_f = _nearest_series_value(c_out_f, _CAP_SERIES, 1e-6)

    # 输入电容: Cin = Iout * D * (1 - D) / (fsw * delta_Vin)
    input_ripple = max(v_in * 0.05, 0.5)
    c_in_f = (i_out * duty * (1.0 - duty)) / (fsw_hz * input_ripple)
    c_in_f = max(c_in_f, 10e-6)
    c_in_f = _nearest_series_value(c_in_f, _CAP_SERIES, 1e-6)

    # 反馈电阻网络
    r_lower = find_nearest_e24(10000.0)
    r_upper = find_nearest_e24(
        max(r_lower * (v_out / max(v_ref, 0.1) - 1.0), 1000.0),
    )

    # 更新参数
    instance.parameters.update({
        "v_in": _trim_float(v_in),
        "v_out": _trim_float(v_out),
        "i_out": _trim_float(i_out),
        "fsw": _trim_float(fsw_hz / 1000.0),
        "duty": _trim_float(duty),
        "l_value": _format_inductor(l_h),
        "c_in": _format_cap(c_in_f),
        "c_out": _format_cap(c_out_f),
        "c_boot": "100nF",
        "r_fb_upper": _format_resistor(r_upper),
        "r_fb_lower": _format_resistor(r_lower),
        "v_ref": _trim_float(v_ref),
    })

    # 外围元件清单（C56: 角色标记，C57: 来源）
    instance.external_components = [
        {
            "role": "inductor",
            "ref_prefix": "L",
            "value": _format_inductor(l_h),
            "formula": "L = Vout * (1 - D) / (fsw * 0.3 * Iout)",
            "evidence": "Buck 典型拓扑电感计算",
        },
        {
            "role": "input_cap",
            "ref_prefix": "C",
            "value": _format_cap(c_in_f),
            "formula": "Cin = Iout * D * (1-D) / (fsw * dVin)",
            "evidence": "输入纹波电压估算，10uF 工程下限",
        },
        {
            "role": "output_cap",
            "ref_prefix": "C",
            "value": _format_cap(c_out_f),
            "formula": "Cout = dIL / (8 * fsw * dVout)",
            "evidence": "1% 输出纹波估算，22uF 工程下限",
        },
        {
            "role": "boot_cap",
            "ref_prefix": "C",
            "value": "100nF",
            "formula": "Cboot = 100nF (typical)",
            "evidence": "自举电容典型值",
        },
        {
            "role": "fb_upper",
            "ref_prefix": "R",
            "value": _format_resistor(r_upper),
            "formula": "Rupper = Rlower * (Vout/Vref - 1)",
            "evidence": "反馈分压网络上拉电阻",
        },
        {
            "role": "fb_lower",
            "ref_prefix": "R",
            "value": _format_resistor(r_lower),
            "formula": "Rlower = 10k (fixed)",
            "evidence": "反馈分压网络下拉电阻",
        },
        {
            "role": "diode",
            "ref_prefix": "D",
            "value": "SS34",
            "formula": "Schottky diode rated for Vin",
            "evidence": "续流二极管，额定电压 > Vin",
        },
    ]

    instance.status = ModuleStatus.SYNTHESIZED
    instance.evidence.append(
        f"Buck 综合完成: {v_in}V->{v_out}V@{i_out}A, L={_format_inductor(l_h)}",
    )
    return instance


# ============================================================
# T063: LDO 模块综合
# ============================================================


def synthesize_ldo_module(instance: ModuleInstance) -> ModuleInstance:
    """计算 LDO 外围元件。

    LDO 比 Buck 简单：无电感、无反馈分压器（固定输出型）。
    主要是输入/输出去耦电容。

    Args:
        instance: 状态为 RESOLVED 的 LDO 模块实例

    Returns:
        更新后的实例，status=SYNTHESIZED
    """
    p = instance.parameters

    v_in = _safe_float(p.get("v_in"), 0.0) or _get_device_spec(instance, "v_in", 5.0) or 5.0
    v_out = _safe_float(p.get("v_out"), 0.0) or _get_device_spec(instance, "v_out", 3.3) or 3.3

    # LDO 典型 C_in = 10uF, C_out = 22uF（数据手册推荐值）
    c_in = "10uF"
    c_out = "22uF"

    instance.parameters.update({
        "v_in": _trim_float(v_in),
        "v_out": _trim_float(v_out),
        "c_in": c_in,
        "c_out": c_out,
    })

    instance.external_components = [
        {
            "role": "input_cap",
            "ref_prefix": "C",
            "value": c_in,
            "formula": "Cin >= 10uF",
            "evidence": "LDO 数据手册典型输入去耦电容",
        },
        {
            "role": "output_cap",
            "ref_prefix": "C",
            "value": c_out,
            "formula": "Cout >= 22uF",
            "evidence": "LDO 数据手册典型输出电容，维持稳定性与瞬态响应",
        },
    ]

    instance.status = ModuleStatus.SYNTHESIZED
    instance.evidence.append(
        f"LDO 综合完成: {v_in}V->{v_out}V, Cin={c_in}, Cout={c_out}",
    )
    return instance


# ============================================================
# T064: MCU 最小系统综合
# ============================================================


def synthesize_mcu_minimum_system(instance: ModuleInstance) -> ModuleInstance:
    """MCU 最小系统外围：去耦电容。

    每个 VDD 引脚一个 100nF 去耦电容，加一个 10uF 整体储能电容。

    Args:
        instance: 状态为 RESOLVED 的 MCU 模块实例

    Returns:
        更新后的实例，status=SYNTHESIZED
    """
    # 统计 VDD 引脚数量
    vdd_count = 0
    for port in instance.resolved_ports.values():
        if port.port_role == "power_in":
            vdd_count += 1
    vdd_count = max(vdd_count, 1)  # 至少 1 个

    components: list[dict[str, object]] = []

    # 每个 VDD 引脚一个 100nF 去耦电容
    for i in range(vdd_count):
        components.append({
            "role": f"decoupling_cap_{i + 1}",
            "ref_prefix": "C",
            "value": "100nF",
            "formula": "每个 VDD 引脚 100nF 去耦",
            "evidence": "MCU 数据手册标准去耦要求",
        })

    # 一个 10uF 储能电容
    components.append({
        "role": "bulk_cap",
        "ref_prefix": "C",
        "value": "10uF",
        "formula": "整体储能电容 10uF",
        "evidence": "MCU 电源系统典型储能需求",
    })

    instance.external_components = components  # type: ignore[assignment]
    instance.parameters["decoupling_count"] = str(vdd_count)

    instance.status = ModuleStatus.SYNTHESIZED
    instance.evidence.append(
        f"MCU 最小系统综合完成: {vdd_count}x100nF + 1x10uF",
    )
    return instance


# ============================================================
# T065: LED 指示灯综合
# ============================================================


def synthesize_led_indicator(
    instance: ModuleInstance,
    drive_voltage: float = 3.3,
) -> ModuleInstance:
    """LED 指示灯限流电阻计算（C54）。

    R = (V_drive - V_led) / I_led

    Args:
        instance: 状态为 RESOLVED 的 LED 模块实例
        drive_voltage: GPIO 驱动电压，默认 3.3V

    Returns:
        更新后的实例，status=SYNTHESIZED
    """
    p = instance.parameters

    # 驱动电压：优先 parameters，否则用传入的 drive_voltage
    v_drive = _safe_float(p.get("v_supply"), 0.0) or drive_voltage

    # LED 颜色与正向压降
    color = p.get("led_color", "green").lower()
    v_forward = _safe_float(
        p.get("v_forward"), _LED_VF_DEFAULTS.get(color, 2.2),
    )

    # LED 电流
    i_led = _safe_float(p.get("led_current"), 0.0)
    if i_led <= 0:
        i_led = _DEFAULT_LED_CURRENT_A

    # 限流电阻: R = (Vdrive - Vf) / Iled
    v_drop = max(v_drive - v_forward, 0.1)
    r_value = v_drop / i_led
    r_value = find_nearest_e24(max(r_value, 10.0))

    instance.parameters.update({
        "v_supply": _trim_float(v_drive),
        "v_forward": _trim_float(v_forward),
        "led_current": _trim_float(i_led),
        "led_color": color,
        "r_value": _format_resistor(r_value),
    })

    instance.external_components = [
        {
            "role": "led_limit",
            "ref_prefix": "R",
            "value": _format_resistor(r_value),
            "formula": f"R = (Vdrive - Vf) / Iled = ({v_drive} - {v_forward}) / {i_led}",
            "evidence": "C54: LED 限流电阻由驱动电压和目标电流计算",
        },
    ]

    instance.status = ModuleStatus.SYNTHESIZED
    instance.evidence.append(
        f"LED 综合完成: {color}, R={_format_resistor(r_value)}, "
        f"Vdrive={v_drive}V, Vf={v_forward}V, I={i_led * 1000:.0f}mA",
    )
    return instance


# ============================================================
# T066: 通用占位模块综合
# ============================================================


def synthesize_generic_module(instance: ModuleInstance) -> ModuleInstance:
    """未知类别模块的占位综合。

    不生成外围元件，仅标记为 SYNTHESIZED。

    Args:
        instance: 任意类别的模块实例

    Returns:
        status=SYNTHESIZED 的实例（无外围元件）
    """
    instance.external_components = []
    instance.status = ModuleStatus.SYNTHESIZED
    instance.evidence.append(
        f"通用模块综合: 类别 '{instance.resolved_category}' 无专用计算",
    )
    return instance


# ============================================================
# T067: 供电约束传播
# ============================================================


def propagate_supply_constraints(ir: SystemDesignIR) -> SystemDesignIR:
    """传播电源链电压域（C51）。

    当 buck.v_out 变化 -> 下游 ldo.v_in 同步更新。
    遍历 SUPPLY_CHAIN 连接，将源模块 v_out 写入目标模块 v_in。

    Args:
        ir: 系统设计 IR

    Returns:
        更新后的 IR
    """
    for conn in ir.connections:
        # 只处理电源链连接
        if conn.src_port.port_role != "power_out":
            continue
        if conn.dst_port.port_role != "power_in":
            continue

        src_module = ir.get_module(conn.src_port.module_id)
        dst_module = ir.get_module(conn.dst_port.module_id)

        if src_module is None or dst_module is None:
            continue

        src_v_out = src_module.parameters.get("v_out", "")
        if not src_v_out:
            continue

        old_v_in = dst_module.parameters.get("v_in", "")
        if old_v_in != src_v_out:
            dst_module.parameters["v_in"] = src_v_out
            logger.info(
                "电压域传播: %s.v_out=%s -> %s.v_in (was %s)",
                src_module.module_id,
                src_v_out,
                dst_module.module_id,
                old_v_in,
            )

    return ir


# ============================================================
# T068: 依赖模块重算
# ============================================================


def _get_downstream_ids(ir: SystemDesignIR, source_ids: set[str]) -> set[str]:
    """获取依赖于 source_ids 的下游模块 ID。"""
    downstream: set[str] = set()
    for conn in ir.connections:
        src_id = conn.src_port.module_id
        dst_id = conn.dst_port.module_id
        if src_id in source_ids and dst_id not in source_ids:
            downstream.add(dst_id)
    return downstream


def recompute_dependent_modules(
    ir: SystemDesignIR,
    changed_ids: set[str],
) -> SystemDesignIR:
    """重新综合依赖于已变更模块的下游模块（C48, C58）。

    只重算受影响子图，不触碰无关模块（C49）。

    Args:
        ir: 系统设计 IR
        changed_ids: 已变更的模块 ID 集合

    Returns:
        更新后的 IR
    """
    # 先传播电压域
    ir = propagate_supply_constraints(ir)

    # 找到直接依赖的下游模块
    to_resynth = _get_downstream_ids(ir, changed_ids)

    for mid in to_resynth:
        module = ir.get_module(mid)
        if module is None:
            continue
        # 只重算已综合或已解析的模块
        if module.status not in (ModuleStatus.RESOLVED, ModuleStatus.SYNTHESIZED):
            continue

        logger.info("重算依赖模块: %s (因 %s 变更)", mid, changed_ids)
        _synthesize_single(module)

    return ir


# ============================================================
# 分发器
# ============================================================

# 类别 -> 综合函数映射
_CATEGORY_SYNTHESIZERS: dict[str, object] = {
    "buck": synthesize_buck_module,
    "ldo": synthesize_ldo_module,
    "mcu": synthesize_mcu_minimum_system,
    "led": synthesize_led_indicator,
}


def _synthesize_single(instance: ModuleInstance) -> ModuleInstance:
    """根据类别分发到对应的综合函数。"""
    cat = instance.resolved_category.lower()
    fn = _CATEGORY_SYNTHESIZERS.get(cat)
    if fn is not None:
        return fn(instance)  # type: ignore[operator]
    return synthesize_generic_module(instance)


def synthesize_all_modules(ir: SystemDesignIR) -> SystemDesignIR:
    """综合所有已解析模块。

    1. 传播供电约束（C51）
    2. 按优先级综合每个 RESOLVED 模块
    3. 跳过已综合或错误状态的模块

    Args:
        ir: 包含已解析模块的系统设计 IR

    Returns:
        所有可综合模块已标记 SYNTHESIZED 的 IR
    """
    # 先传播电压域
    ir = propagate_supply_constraints(ir)

    for module_id, instance in ir.module_instances.items():
        if instance.status != ModuleStatus.RESOLVED:
            continue
        _synthesize_single(instance)

    return ir
