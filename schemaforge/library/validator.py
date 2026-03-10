"""器件草稿校验

DeviceDraft 是器件入库前的草稿模型，字段可为空/不完整。
validate_draft() 对其进行结构、电气、封装一致性校验，
返回 ValidationReport 供 GUI / AI 决策。

校验分三级:
  - error:   阻塞入库（必须修复）
  - warning: 建议修复但不阻塞
  - info:    补全建议
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ============================================================
# 校验级别
# ============================================================

class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


# ============================================================
# 单条校验项
# ============================================================

@dataclass
class ValidationIssue:
    """单条校验问题"""

    severity: Severity
    field_path: str  # 出问题的字段路径, e.g. "pins[2].name"
    message: str  # 中文描述
    suggestion: str = ""  # 修复建议


# ============================================================
# 校验报告
# ============================================================

@dataclass
class ValidationReport:
    """校验结果报告"""

    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """无 error 级问题即为合法"""
        return not any(i.severity == Severity.ERROR for i in self.issues)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def suggestions(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.INFO]

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典 (给 AI / GUI)"""
        return {
            "is_valid": self.is_valid,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [
                {
                    "severity": i.severity.value,
                    "field": i.field_path,
                    "message": i.message,
                    "suggestion": i.suggestion,
                }
                for i in self.issues
            ],
        }


# ============================================================
# DeviceDraft — 器件草稿
# ============================================================

class PinDraft(BaseModel):
    """引脚草稿"""

    name: str = ""
    number: str = ""
    pin_type: str = ""  # input, output, power, passive, nc
    side: str = ""  # left, right, top, bottom
    description: str = ""


class DeviceDraft(BaseModel):
    """器件入库草稿 — 字段可为空（待补全）

    与 DeviceModel 的区别:
    - 所有字段可选（草稿可不完整）
    - 多了 missing_fields / confidence_map / evidence_refs 用于 AI 补全流程
    - 入库时需通过 validate_draft() 才能转为 DeviceModel
    """

    # --- 基础信息 ---
    part_number: str = ""
    manufacturer: str = ""
    description: str = ""
    category: str = ""  # ldo, buck, mcu, passive, led, resistor, capacitor, ...
    aliases: list[str] = Field(default_factory=list)

    # --- 封装 ---
    package: str = ""
    pin_count: int = 0

    # --- 引脚定义 ---
    pins: list[PinDraft] = Field(default_factory=list)

    # --- 电气参数 ---
    specs: dict[str, str] = Field(default_factory=dict)

    # --- 符号外形 ---
    symbol_shape: str = ""  # ic, resistor, capacitor, inductor, led, diode, transistor

    # --- SPICE ---
    spice_model: str = ""

    # --- 采购 ---
    lcsc_part: str = ""
    datasheet_url: str = ""
    easyeda_id: str = ""

    # --- Datasheet 文件 ---
    datasheet_path: str = ""
    """入库时保存的 PDF datasheet 相对路径"""

    # --- 来源 ---
    source: str = "manual"  # manual, easyeda, pdf_parsed, ...
    confidence: float = 1.0
    notes: str = ""

    # --- AI 补全辅助 ---
    missing_fields: list[str] = Field(default_factory=list)
    confidence_map: dict[str, float] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)


# ============================================================
# 已知封装 → 引脚数映射（常见封装）
# ============================================================

_PACKAGE_PIN_RANGES: dict[str, tuple[int, int]] = {
    "SOT-23": (3, 6),
    "SOT-23-3": (3, 3),
    "SOT-23-5": (5, 5),
    "SOT-23-6": (6, 6),
    "SOT-223": (3, 4),
    "SOT-89": (3, 3),
    "SOP-8": (8, 8),
    "SOIC-8": (8, 8),
    "SOIC-14": (14, 14),
    "SOIC-16": (16, 16),
    "TSSOP-8": (8, 8),
    "TSSOP-14": (14, 14),
    "TSSOP-16": (16, 16),
    "TSSOP-20": (20, 20),
    "QFN-16": (16, 17),
    "QFN-20": (20, 21),
    "QFN-24": (24, 25),
    "QFN-32": (32, 33),
    "QFP-32": (32, 32),
    "QFP-44": (44, 44),
    "QFP-48": (48, 48),
    "QFP-64": (64, 64),
    "QFP-100": (100, 100),
    "DIP-8": (8, 8),
    "DIP-14": (14, 14),
    "DIP-16": (16, 16),
    "TO-220": (3, 3),
    "TO-252": (3, 3),
    "TO-263": (3, 3),
    "0201": (2, 2),
    "0402": (2, 2),
    "0603": (2, 2),
    "0805": (2, 2),
    "1206": (2, 2),
    "1210": (2, 2),
    "2512": (2, 2),
}

# 已知类别
VALID_CATEGORIES = {
    "ldo", "buck", "boost", "buck_boost", "linear_regulator",
    "mcu", "fpga", "dsp", "soc",
    "opamp", "comparator", "adc", "dac",
    "mosfet", "bjt", "igbt", "diode", "zener", "schottky",
    "resistor", "capacitor", "inductor", "ferrite_bead",
    "led", "crystal", "oscillator", "relay", "fuse",
    "connector", "switch", "transformer",
    "sensor", "driver", "transceiver", "esd_protection",
    "passive", "active", "ic", "other",
}

# 引脚类型
VALID_PIN_TYPES = {"input", "output", "power", "passive", "nc", "bidirectional", "open_collector", "open_drain", ""}


# ============================================================
# 校验函数
# ============================================================

def validate_draft(draft: DeviceDraft) -> ValidationReport:
    """校验器件草稿

    分三层:
    1. 结构校验 — 必填字段、格式
    2. 电气校验 — 引脚合理性、参数范围
    3. 封装校验 — 引脚数与封装一致性

    Returns:
        ValidationReport — is_valid=True 表示可入库
    """
    report = ValidationReport()

    _check_required_fields(draft, report)
    _check_category(draft, report)
    _check_pins(draft, report)
    _check_package_consistency(draft, report)
    _check_specs(draft, report)
    _check_source_info(draft, report)

    return report


def _check_required_fields(draft: DeviceDraft, report: ValidationReport) -> None:
    """检查必填字段"""
    if not draft.part_number.strip():
        report.issues.append(ValidationIssue(
            severity=Severity.ERROR,
            field_path="part_number",
            message="料号不能为空",
            suggestion="请输入器件型号，如 TPS54202、AMS1117-3.3",
        ))

    if not draft.category.strip():
        report.issues.append(ValidationIssue(
            severity=Severity.WARNING,
            field_path="category",
            message="未指定器件类别",
            suggestion="建议设置类别便于分类检索，如 ldo、buck、resistor",
        ))

    if not draft.description.strip():
        report.issues.append(ValidationIssue(
            severity=Severity.INFO,
            field_path="description",
            message="缺少器件描述",
            suggestion="建议添加简短描述，如 '3.3V 1A 低压差线性稳压器'",
        ))

    if not draft.manufacturer.strip():
        report.issues.append(ValidationIssue(
            severity=Severity.INFO,
            field_path="manufacturer",
            message="未指定制造商",
            suggestion="建议填写制造商名称，如 Texas Instruments、Microchip",
        ))


def _check_category(draft: DeviceDraft, report: ValidationReport) -> None:
    """检查类别有效性"""
    if draft.category and draft.category.strip().lower() not in VALID_CATEGORIES:
        report.issues.append(ValidationIssue(
            severity=Severity.WARNING,
            field_path="category",
            message=f"未知器件类别: {draft.category}",
            suggestion=f"已知类别: {', '.join(sorted(VALID_CATEGORIES)[:10])}...",
        ))


def _check_pins(draft: DeviceDraft, report: ValidationReport) -> None:
    """检查引脚定义"""
    if not draft.pins:
        # 无源器件可以没有引脚定义
        if draft.category not in ("resistor", "capacitor", "inductor", "ferrite_bead", "passive"):
            report.issues.append(ValidationIssue(
                severity=Severity.WARNING,
                field_path="pins",
                message="未定义引脚",
                suggestion="有源器件建议定义引脚信息",
            ))
        return

    # 检查引脚名重复
    pin_names = [p.name for p in draft.pins if p.name]
    seen_names: set[str] = set()
    for name in pin_names:
        if name in seen_names:
            report.issues.append(ValidationIssue(
                severity=Severity.ERROR,
                field_path="pins",
                message=f"引脚名重复: {name}",
                suggestion="每个引脚名称必须唯一",
            ))
        seen_names.add(name)

    # 检查引脚编号重复
    pin_numbers = [p.number for p in draft.pins if p.number]
    seen_numbers: set[str] = set()
    for num in pin_numbers:
        if num in seen_numbers:
            report.issues.append(ValidationIssue(
                severity=Severity.ERROR,
                field_path="pins",
                message=f"引脚编号重复: {num}",
                suggestion="每个引脚编号必须唯一",
            ))
        seen_numbers.add(num)

    # 检查引脚类型
    for i, pin in enumerate(draft.pins):
        if pin.pin_type and pin.pin_type not in VALID_PIN_TYPES:
            report.issues.append(ValidationIssue(
                severity=Severity.WARNING,
                field_path=f"pins[{i}].pin_type",
                message=f"未知引脚类型: {pin.pin_type}",
                suggestion=f"有效类型: {', '.join(sorted(t for t in VALID_PIN_TYPES if t))}",
            ))

    # 检查是否有空名引脚
    empty_name_count = sum(1 for p in draft.pins if not p.name.strip())
    if empty_name_count > 0:
        report.issues.append(ValidationIssue(
            severity=Severity.WARNING,
            field_path="pins",
            message=f"{empty_name_count} 个引脚缺少名称",
            suggestion="建议为所有引脚命名",
        ))

    # pin_count 与实际引脚数一致性
    if draft.pin_count > 0 and len(draft.pins) != draft.pin_count:
        report.issues.append(ValidationIssue(
            severity=Severity.WARNING,
            field_path="pin_count",
            message=f"声明引脚数 ({draft.pin_count}) 与实际引脚定义数 ({len(draft.pins)}) 不一致",
            suggestion="请核实引脚数量",
        ))


def _check_package_consistency(draft: DeviceDraft, report: ValidationReport) -> None:
    """检查封装与引脚数一致性"""
    if not draft.package:
        return

    pkg_upper = draft.package.strip().upper()

    # 查找匹配的封装
    matched_range: tuple[int, int] | None = None
    for pkg_key, pin_range in _PACKAGE_PIN_RANGES.items():
        if pkg_key.upper() == pkg_upper:
            matched_range = pin_range
            break

    if matched_range is None:
        return  # 未知封装，不校验

    actual_pin_count = len(draft.pins) if draft.pins else draft.pin_count

    if actual_pin_count <= 0:
        return  # 没有引脚信息，跳过

    min_pins, max_pins = matched_range
    if actual_pin_count < min_pins or actual_pin_count > max_pins:
        expected = f"{min_pins}" if min_pins == max_pins else f"{min_pins}-{max_pins}"
        report.issues.append(ValidationIssue(
            severity=Severity.WARNING,
            field_path="package",
            message=f"封装 {draft.package} 通常有 {expected} 个引脚，"
                    f"但当前定义了 {actual_pin_count} 个",
            suggestion="请检查封装型号或引脚定义是否正确",
        ))


def _check_specs(draft: DeviceDraft, report: ValidationReport) -> None:
    """检查电气参数合理性"""
    # 对于稳压器类（ldo, buck 等），检查输入/输出电压范围
    regulator_categories = {"ldo", "buck", "boost", "buck_boost", "linear_regulator"}
    if draft.category in regulator_categories:
        if not draft.specs.get("v_in_max") and not draft.specs.get("v_in"):
            report.issues.append(ValidationIssue(
                severity=Severity.INFO,
                field_path="specs",
                message="稳压器缺少输入电压参数 (v_in / v_in_max)",
                suggestion="建议补充输入电压范围",
            ))
        if not draft.specs.get("v_out") and not draft.specs.get("v_out_typ"):
            report.issues.append(ValidationIssue(
                severity=Severity.INFO,
                field_path="specs",
                message="稳压器缺少输出电压参数 (v_out / v_out_typ)",
                suggestion="建议补充输出电压",
            ))


def _check_source_info(draft: DeviceDraft, report: ValidationReport) -> None:
    """检查来源信息"""
    if draft.source == "easyeda" and not draft.easyeda_id:
        report.issues.append(ValidationIssue(
            severity=Severity.WARNING,
            field_path="easyeda_id",
            message="来源标记为 EasyEDA 但缺少 EasyEDA ID",
            suggestion="EasyEDA 导入的器件应保留原始 UUID",
        ))


def _infer_design_roles(
    category: str,
    description: str,
    specs: dict[str, str],
) -> list[str]:
    """根据分类、描述和规格自动推断器件设计角色。

    设计角色用于检索器评分匹配。推断规则基于器件分类和
    描述中的关键字。
    """
    roles: list[str] = []
    desc_lower = description.lower()
    cat = category.lower()

    # 关键字 → 角色映射
    _KW_ROLES: dict[str, list[str]] = {
        # 存储
        "flash": ["spi_flash", "data_storage"],
        "闪存": ["spi_flash", "data_storage"],
        "eeprom": ["eeprom", "data_storage"],
        "sram": ["sram", "data_storage"],
        "nor": ["spi_flash", "data_storage"],
        "nand": ["nand_flash", "data_storage"],
        # 电源
        "ldo": ["voltage_regulator"],
        "稳压": ["voltage_regulator"],
        "buck": ["dc_dc_converter"],
        "boost": ["dc_dc_converter"],
        "降压": ["dc_dc_converter"],
        "升压": ["dc_dc_converter"],
        # MCU / 数字
        "mcu": ["microcontroller"],
        "单片机": ["microcontroller"],
        "stm32": ["microcontroller"],
        "esp32": ["microcontroller", "wifi"],
        # 传感器
        "sensor": ["sensor"],
        "传感器": ["sensor"],
        "温度": ["temperature_sensor"],
        "accelerometer": ["accelerometer"],
        # 通信
        "uart": ["communication"],
        "i2c": ["communication"],
        "spi": ["communication"],
        "can": ["communication"],
        "usb": ["communication"],
        # 接口
        "connector": ["connector"],
        "接插件": ["connector"],
    }

    # 基于分类
    cat_roles: dict[str, list[str]] = {
        "ldo": ["voltage_regulator"],
        "buck": ["dc_dc_converter"],
        "led": ["indicator"],
        "voltage_divider": ["voltage_sampling"],
        "rc_filter": ["signal_filter"],
        "passive": ["passive_component"],
        "memory": ["data_storage"],
        "mcu": ["microcontroller"],
        "sensor": ["sensor"],
        "connector": ["connector"],
    }
    if cat in cat_roles:
        for r in cat_roles[cat]:
            if r not in roles:
                roles.append(r)

    # 基于描述关键字
    for kw, kw_roles in _KW_ROLES.items():
        if kw in desc_lower:
            for r in kw_roles:
                if r not in roles:
                    roles.append(r)

    # 基于接口规格
    interface = specs.get("interface", "").lower()
    if "spi" in interface:
        if "spi_device" not in roles:
            roles.append("spi_device")
    if "i2c" in interface:
        if "i2c_device" not in roles:
            roles.append("i2c_device")

    return roles


def draft_to_device_model_dict(draft: DeviceDraft) -> dict[str, Any]:
    """将 DeviceDraft 转换为 DeviceModel 构造字典

    不做校验 — 调用方应先 validate_draft() 通过。
    仅转换字段格式。
    """
    from schemaforge.core.models import PinType
    from schemaforge.library.models import PinSide, SymbolDef, SymbolPin

    # 转换引脚
    symbol_pins: list[SymbolPin] = []
    for pin in draft.pins:
        # pin_type 映射
        type_map = {
            "input": PinType.INPUT,
            "output": PinType.OUTPUT,
            "power": PinType.POWER_IN,
            "passive": PinType.PASSIVE,
            "nc": PinType.NO_CONNECT,
            "bidirectional": PinType.BIDIRECTIONAL,
            "open_collector": PinType.OUTPUT,
            "open_drain": PinType.OUTPUT,
        }
        pt = type_map.get(pin.pin_type, PinType.PASSIVE)

        # side 映射
        side_map = {
            "left": PinSide.LEFT,
            "right": PinSide.RIGHT,
            "top": PinSide.TOP,
            "bottom": PinSide.BOTTOM,
        }
        side = side_map.get(pin.side, PinSide.LEFT)

        symbol_pins.append(SymbolPin(
            name=pin.name or f"PIN{pin.number}",
            pin_number=pin.number,
            side=side,
            pin_type=pt,
            description=pin.description,
        ))

    # 构造 SymbolDef (如果有引脚)
    symbol: SymbolDef | None = None
    if symbol_pins:
        symbol = SymbolDef(pins=symbol_pins)

    # 自动推断 design_roles
    design_roles = _infer_design_roles(
        category=draft.category.strip().lower(),
        description=draft.description.strip(),
        specs=dict(draft.specs),
    )

    return {
        "part_number": draft.part_number.strip(),
        "manufacturer": draft.manufacturer.strip(),
        "description": draft.description.strip(),
        "category": draft.category.strip().lower(),
        "specs": dict(draft.specs),
        "symbol": symbol,
        "spice_model": draft.spice_model,
        "lcsc_part": draft.lcsc_part,
        "datasheet_url": draft.datasheet_url,
        "easyeda_id": draft.easyeda_id,
        "package": draft.package.strip(),
        "datasheet_path": draft.datasheet_path,
        "source": draft.source,
        "confidence": draft.confidence,
        "notes": draft.notes,
        "design_roles": design_roles,
    }
