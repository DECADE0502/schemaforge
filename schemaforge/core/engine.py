"""SchemaForge 核心引擎

串联全流程：AI输出 → 验证 → 实例化 → ERC → 渲染 → 导出
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from schemaforge.ai.client import call_llm_json
from schemaforge.ai.prompts import build_user_message, load_system_prompt
from schemaforge.ai.validator import ValidationResult, validate_design_spec
from schemaforge.core.calculator import (
    calculate_divider,
    calculate_led_resistor,
    calculate_rc_filter,
)
from schemaforge.core.erc import ERCChecker
from schemaforge.core.exporter import generate_bom, generate_spice
from schemaforge.core.models import (
    CircuitInstance,
    ComponentInstance,
    ERCError,
)
from schemaforge.core.templates import get_template
from schemaforge.render.base import format_value
from schemaforge.render.divider import render_divider_from_params
from schemaforge.render.ldo import render_ldo_from_params
from schemaforge.render.led import render_led_from_params
from schemaforge.render.rc_filter import render_rc_filter_from_params


# 渲染函数注册表
RENDER_FUNCTIONS: dict[str, Any] = {
    "voltage_divider": render_divider_from_params,
    "ldo_regulator": render_ldo_from_params,
    "led_indicator": render_led_from_params,
    "rc_lowpass": render_rc_filter_from_params,
}


@dataclass
class EngineResult:
    """引擎处理结果"""
    success: bool = False
    design_name: str = ""
    description: str = ""
    notes: str = ""

    # 各阶段结果
    raw_design: dict[str, Any] = field(default_factory=dict)
    validation: ValidationResult | None = None
    circuits: list[CircuitInstance] = field(default_factory=list)
    erc_errors: list[ERCError] = field(default_factory=list)
    svg_paths: list[str] = field(default_factory=list)
    bom_text: str = ""
    spice_text: str = ""

    # 错误信息
    error: str = ""
    stage: str = ""  # 失败的阶段


class SchemaForgeEngine:
    """SchemaForge 核心引擎"""

    def __init__(self) -> None:
        """初始化引擎"""
        self.erc_checker = ERCChecker()

    def process(
        self,
        user_input: str,
        progress_callback: Callable[[str, int], None] | None = None,
    ) -> EngineResult:
        """处理用户输入的完整流程

        Args:
            user_input: 用户的自然语言电路需求
            progress_callback: 可选的进度回调，签名 (message, percentage)

        Returns:
            EngineResult 包含全部处理结果
        """
        result = EngineResult()

        def _emit(msg: str, pct: int) -> None:
            if progress_callback:
                progress_callback(msg, pct)

        # === 阶段1：调用LLM获取设计规格 ===
        _emit("正在调用AI模型...", 5)
        result.stage = "llm_call"
        try:
            system_prompt = load_system_prompt()
            user_message = build_user_message(user_input)
            design_data_or_none = call_llm_json(system_prompt, user_message)
            if design_data_or_none is None:
                result.error = "LLM返回的内容无法解析为JSON"
                return result
            design_data = design_data_or_none
        except Exception as e:
            result.error = f"LLM调用失败: {e}"
            return result

        result.raw_design = design_data
        result.design_name = design_data.get("design_name", "未命名设计")
        result.description = design_data.get("description", "")
        result.notes = design_data.get("notes", "")
        _emit("AI响应已收到", 20)

        # === 阶段2：验证设计规格 ===
        _emit("正在验证设计规格...", 25)
        result.stage = "validation"
        validation = validate_design_spec(design_data)
        result.validation = validation

        if not validation.is_valid:
            result.error = validation.summary()
            return result

        # === 阶段3：实例化电路 ===
        _emit("正在实例化电路...", 40)
        result.stage = "instantiation"
        try:
            circuits = self._instantiate_circuits(design_data)
            result.circuits = circuits
        except Exception as e:
            result.error = f"电路实例化失败: {e}"
            return result

        # === 阶段4：ERC检查 ===
        _emit("正在执行电气规则检查...", 55)
        result.stage = "erc"
        all_erc_errors: list[ERCError] = []
        for circuit in circuits:
            errors = self.erc_checker.check_all(circuit)
            all_erc_errors.extend(errors)
        result.erc_errors = all_erc_errors

        # ERC错误只记录warning，不阻断（模板保证的连接是正确的）

        # === 阶段5：渲染SVG ===
        _emit("正在渲染原理图SVG...", 70)
        result.stage = "render"
        try:
            svg_paths = self._render_circuits(design_data)
            result.svg_paths = svg_paths
        except Exception as e:
            result.error = f"渲染失败: {e}"
            return result

        # === 阶段6：导出BOM + SPICE ===
        _emit("正在生成BOM和SPICE...", 85)
        result.stage = "export"
        try:
            for circuit in circuits:
                bom = generate_bom(circuit)
                spice = generate_spice(circuit)
                result.bom_text += bom + "\n\n"
                result.spice_text += spice + "\n\n"
        except Exception as e:
            result.error = f"导出失败: {e}"
            return result

        # === 完成 ===
        _emit("处理完成!", 100)
        result.success = True
        result.stage = "done"
        return result

    def _instantiate_circuits(self, design_data: dict[str, Any]) -> list[CircuitInstance]:
        """从设计规格实例化电路"""
        circuits: list[CircuitInstance] = []

        for mod in design_data.get("modules", []):
            template_name = mod["template"]
            instance_name = mod["instance_name"]
            params = mod.get("parameters", {})

            template = get_template(template_name)
            if template is None:
                continue

            # 计算派生参数
            calc_values = self._calculate_params(template_name, params)

            # 构建器件实例
            components: list[ComponentInstance] = []
            ref_counters: dict[str, int] = {}

            for comp_def in template.components:
                prefix = comp_def.ref_prefix
                count = ref_counters.get(prefix, 0) + 1
                ref_counters[prefix] = count
                ref = f"{prefix}{count}"

                # 替换参数占位符
                comp_params: dict[str, str] = {}
                for k, v in comp_def.parameters.items():
                    resolved = self._render_template_string(v, params, calc_values)
                    comp_params[k] = resolved

                # comp_def.name 也可能含模板占位符（如 "{ic_model}"）
                resolved_name = self._render_template_string(
                    comp_def.name, params, calc_values,
                )

                components.append(ComponentInstance(
                    ref=ref,
                    component_type=resolved_name,
                    parameters=comp_params,
                ))

            # 构建网络
            nets = [net.model_copy() for net in template.net_template]

            circuit = CircuitInstance(
                name=f"{instance_name} ({template.display_name})",
                description=template.description,
                components=components,
                nets=nets,
                template_name=template_name,
                input_parameters=params,
                calculated_values=calc_values,
            )
            circuits.append(circuit)

        return circuits

    def _calculate_params(
        self,
        template_name: str,
        params: dict[str, str],
    ) -> dict[str, Any]:
        """根据模板类型计算派生参数"""
        if template_name == "voltage_divider":
            return calculate_divider(
                float(params.get("v_in", "5")),
                float(params.get("v_out", "2.5")),
                float(params.get("r_total", "20")),
            )
        elif template_name == "led_indicator":
            return calculate_led_resistor(
                float(params.get("v_supply", "3.3")),
                params.get("led_color", "green"),
                float(params.get("led_current", "10")),
            )
        elif template_name == "rc_lowpass":
            return calculate_rc_filter(
                float(params.get("f_cutoff", "1000")),
                float(params.get("r_value", "10")),
            )
        return {}

    # 别名映射：模板占位符 -> calculator结果key
    _ALIAS_MAP: dict[str, str] = {
        "r_limit_value": "r_str",
        "r1_value": "r1_str",
        "r2_value": "r2_str",
        "r_value_ohm": "r_str",
        "c_value": "c_str",
    }

    # 匹配 {key} 占位符的正则
    _PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")

    def _resolve_single_key(
        self,
        key: str,
        params: dict[str, str],
        calc_values: dict[str, Any],
    ) -> str | None:
        """解析单个占位符 key，返回替换值或 None（未找到）"""
        # 先查别名
        alias_key = self._ALIAS_MAP.get(key)
        if alias_key and alias_key in calc_values:
            return str(calc_values[alias_key])

        # 再查计算值
        if key in calc_values:
            val = calc_values[key]
            if isinstance(val, float):
                if key.endswith("_value") or key.startswith("r"):
                    return format_value(val, "Ω")
                if key.endswith("_raw") and "c" in key:
                    return format_value(val, "F")
                return str(val)
            return str(val)

        # 再查用户参数
        if key in params:
            return str(params[key])

        return None

    def _render_template_string(
        self,
        template_text: str,
        params: dict[str, str],
        calc_values: dict[str, Any],
    ) -> str:
        """通用模板插值：支持任意数量的 {key} 占位符

        示例:
            "{ic_model}"        -> "AMS1117"
            "{ic_model}-{v_out}" -> "AMS1117-3.3"
            "固定文本"           -> "固定文本"（无占位符，直接返回）

        若解析后仍有未替换的 {key}，抛出 ValueError。
        """
        if "{" not in template_text:
            return template_text

        def _replacer(match: re.Match[str]) -> str:
            key = match.group(1)
            resolved = self._resolve_single_key(key, params, calc_values)
            if resolved is not None:
                return resolved
            # 保持原样，后面统一检查
            return match.group(0)

        result = self._PLACEHOLDER_RE.sub(_replacer, template_text)

        # 检查是否仍有未解析的占位符
        remaining = self._PLACEHOLDER_RE.search(result)
        if remaining:
            raise ValueError(
                f"模板字符串存在未解析的占位符: '{result}' "
                f"(未解析: {{{remaining.group(1)}}})"
            )

        return result

    def _render_circuits(self, design_data: dict[str, Any]) -> list[str]:
        """渲染所有模块的SVG

        双路径策略：
        1. 优先尝试通过器件库 + TopologyRenderer 渲染（新路径）
        2. 若新路径失败，回退到 RENDER_FUNCTIONS 硬编码渲染（旧路径）
        """
        svg_paths: list[str] = []

        for mod in design_data.get("modules", []):
            template_name = mod["template"]
            params = mod.get("parameters", {})

            # 新路径：尝试 TopologyRenderer
            rendered = self._try_topology_render(template_name, params)
            if rendered:
                svg_paths.append(rendered)
                continue

            # 旧路径：硬编码渲染函数
            render_fn = RENDER_FUNCTIONS.get(template_name)
            if render_fn:
                path = render_fn(params)
                svg_paths.append(path)

        return svg_paths

    def _try_topology_render(
        self,
        template_name: str,
        params: dict[str, Any],
    ) -> str | None:
        """尝试使用 TopologyRenderer 渲染

        Returns:
            SVG路径，失败时返回 None（静默回退到旧路径）
        """
        try:
            from pathlib import Path as _Path

            from schemaforge.library.store import ComponentStore
            from schemaforge.schematic.renderer import TopologyRenderer

            store_dir = _Path(__file__).parent.parent / "store"
            store = ComponentStore(store_dir)
            renderer = TopologyRenderer()

            device = self._find_device_for_template(store, template_name, params)
            if device is None or device.topology is None:
                return None

            circuit_type = device.topology.circuit_type
            if circuit_type not in renderer.LAYOUT_STRATEGIES:
                return None

            return renderer.render(device, params)
        except Exception:
            return None

    @staticmethod
    def _find_device_for_template(
        store: Any,
        template_name: str,
        params: dict[str, Any],
    ) -> Any:
        """将模板名映射到器件库中的 DeviceModel

        映射规则：
        - ldo_regulator -> 根据 ic_model 参数查找（默认 AMS1117-3.3）
        - voltage_divider -> VOLTAGE_DIVIDER
        - led_indicator -> LED_INDICATOR
        - rc_lowpass -> RC_LOWPASS

        Returns:
            DeviceModel 或 None
        """
        # 模板名 -> 料号的直接映射
        direct_map: dict[str, str] = {
            "voltage_divider": "VOLTAGE_DIVIDER",
            "led_indicator": "LED_INDICATOR",
            "rc_lowpass": "RC_LOWPASS",
        }

        if template_name in direct_map:
            return store.get_device(direct_map[template_name])

        if template_name == "ldo_regulator":
            # 从参数推断器件料号
            ic_model = str(params.get("ic_model", "AMS1117"))
            v_out = str(params.get("v_out", "3.3"))
            part_number = f"{ic_model}-{v_out}"
            device = store.get_device(part_number)
            if device:
                return device
            # 回退：直接用 ic_model
            return store.get_device(ic_model)

        return None
