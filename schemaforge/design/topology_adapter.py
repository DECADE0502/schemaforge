"""拓扑适配器

将器件库中的 DeviceModel（含 TopologyDef）+ 规划参数
转换为引擎可用的 DesignSpec 格式，或直接驱动 TopologyRenderer 渲染。

这是连接"器件库"和"渲染引擎"的桥梁：
- DeviceModel.topology → 外部元件列表 + 连接定义
- ModuleRequirement.parameters → 用户参数
- 组合后生成可渲染的输出

用法::

    adapter = TopologyAdapter()
    result = adapter.adapt(device, parameters={"v_in": "5", "v_out": "3.3"})
    # result.design_spec — 可传给引擎的 DesignSpec dict
    # result.render_params — 可传给 TopologyRenderer.render() 的参数

    # 直接渲染
    svg_path = adapter.render(device, parameters={"v_in": "5", "v_out": "3.3"})
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from schemaforge.core.exporter import generate_bom, generate_spice
from schemaforge.core.models import (
    CircuitInstance,
    ComponentInstance,
    Net,
    NetConnection,
)
from schemaforge.library.models import DeviceModel, TopologyDef
from schemaforge.schematic.renderer import TopologyRenderer


# ============================================================
# 适配结果
# ============================================================


@dataclass
class AdaptedModule:
    """单个模块的适配结果"""

    device: DeviceModel
    role: str = ""
    parameters: dict[str, str] = field(default_factory=dict)
    render_params: dict[str, Any] = field(default_factory=dict)
    # render_params 是传给 TopologyRenderer 的参数

    def to_design_spec_module(self) -> dict[str, Any]:
        """转为 DesignSpec 的 modules[] 格式

        兼容现有 SchemaForgeEngine 的 design_data 格式。
        """
        # 将拓扑 circuit_type 映射回旧 template_name
        template_map: dict[str, str] = {
            "ldo": "ldo_regulator",
            "buck": "buck_converter",
            "voltage_divider": "voltage_divider",
            "led_driver": "led_indicator",
            "rc_filter": "rc_lowpass",
        }

        topology = self.device.topology
        circuit_type = topology.circuit_type if topology else ""
        template_name = template_map.get(circuit_type, circuit_type)

        return {
            "template": template_name,
            "instance_name": self.role or self.device.part_number,
            "parameters": self.render_params,
        }


@dataclass
class AdaptationResult:
    """完整适配结果"""

    modules: list[AdaptedModule] = field(default_factory=list)
    connections: list[dict[str, str]] = field(default_factory=list)
    design_name: str = ""
    description: str = ""
    notes: str = ""

    def to_design_spec(self) -> dict[str, Any]:
        """转为完整的 DesignSpec dict（兼容 engine.process）"""
        return {
            "design_name": self.design_name,
            "description": self.description,
            "modules": [m.to_design_spec_module() for m in self.modules],
            "connections": self.connections,
            "notes": self.notes,
        }


# ============================================================
# 拓扑适配器
# ============================================================


class TopologyAdapter:
    """拓扑适配器

    将器件库数据 + 用户参数 → 可渲染/可导出的格式。

    Args:
        use_mock_draft: 当器件无拓扑时是否使用 mock 草稿生成器（默认 True）
    """

    def __init__(self, use_mock_draft: bool = True) -> None:
        self._renderer = TopologyRenderer()
        from schemaforge.design.topology_draft import TopologyDraftGenerator

        self._draft_generator = TopologyDraftGenerator(use_mock=use_mock_draft)

    def adapt_single(
        self,
        device: DeviceModel,
        parameters: dict[str, str] | None = None,
        role: str = "",
    ) -> AdaptedModule:
        """适配单个器件

        将 DeviceModel 的 TopologyDef 和用户参数合并，
        生成可用于渲染的完整参数集。

        Args:
            device: 器件模型
            parameters: 用户指定的参数
            role: 模块角色标识

        Returns:
            AdaptedModule

        Raises:
            ValueError: 器件无拓扑定义
        """
        if device.topology is None:
            try:
                draft = self._draft_generator.generate(device, context=parameters)
                generated_topology = self._draft_generator.draft_to_topology(
                    draft, device
                )
                device = device.model_copy(update={"topology": generated_topology})
            except (ValueError, NotImplementedError) as exc:
                raise ValueError(
                    f"器件 {device.part_number} 没有拓扑定义，且自动草稿生成失败：{exc}"
                ) from exc

        topology = cast(TopologyDef, device.topology)
        user_params = parameters or {}

        render_params = self._merge_parameters(topology, user_params, device)

        return AdaptedModule(
            device=device,
            role=role,
            parameters=user_params,
            render_params=render_params,
        )

    def adapt_multi(
        self,
        modules: list[tuple[DeviceModel, dict[str, str], str]],
        design_name: str = "",
        description: str = "",
        connections: list[dict[str, str]] | None = None,
    ) -> AdaptationResult:
        """适配多个模块

        Args:
            modules: [(device, parameters, role), ...]
            design_name: 设计名称
            description: 设计描述
            connections: 模块间连接

        Returns:
            AdaptationResult
        """
        adapted: list[AdaptedModule] = []
        notes_parts: list[str] = []

        for device, params, role in modules:
            try:
                mod = self.adapt_single(device, params, role)
                adapted.append(mod)
            except ValueError as e:
                notes_parts.append(f"跳过 {device.part_number}: {e}")

        return AdaptationResult(
            modules=adapted,
            connections=connections or [],
            design_name=design_name,
            description=description,
            notes="; ".join(notes_parts) if notes_parts else "",
        )

    def render(
        self,
        device: DeviceModel,
        parameters: dict[str, str] | None = None,
        filename: str | None = None,
    ) -> str:
        """直接渲染单个器件的原理图 SVG

        Args:
            device: 器件模型
            parameters: 用户参数
            filename: 输出文件名

        Returns:
            SVG 文件路径
        """
        adapted = self.adapt_single(device, parameters)
        return self._renderer.render(device, adapted.render_params, filename)

    def render_multi(
        self,
        modules: list[tuple[DeviceModel, dict[str, str], str]],
    ) -> list[str]:
        """渲染多个模块

        Args:
            modules: [(device, parameters, role), ...]

        Returns:
            SVG 文件路径列表
        """
        paths: list[str] = []
        for device, params, role in modules:
            try:
                path = self.render(device, params)
                paths.append(path)
            except (ValueError, KeyError):
                # 无法渲染的跳过
                continue
        return paths

    def build_circuit_instance(
        self,
        device: DeviceModel,
        parameters: dict[str, str] | None = None,
        role: str = "",
    ) -> CircuitInstance:
        """从器件库构建 CircuitInstance（用于 BOM/SPICE 导出）

        Args:
            device: 器件模型
            parameters: 用户参数
            role: 角色标识

        Returns:
            CircuitInstance
        """
        adapted = self.adapt_single(device, parameters, role)
        topology = device.topology
        assert topology is not None

        # 构建器件实例列表
        components: list[ComponentInstance] = []

        # 主器件
        components.append(
            ComponentInstance(
                ref="U1",
                component_type=device.part_number,
                parameters=adapted.render_params,
            )
        )

        # 外部元件
        ref_counters: dict[str, int] = {}
        for ext in topology.external_components:
            prefix = ext.ref_prefix
            count = ref_counters.get(prefix, 0) + 1
            ref_counters[prefix] = count
            ref = f"{prefix}{count}"

            # 解析值
            value = ext.default_value
            if ext.value_expression:
                # 尝试从参数中解析 {key}
                import re

                def _replace(m: Any) -> str:
                    key = m.group(1)
                    return adapted.render_params.get(key, m.group(0))

                value = re.sub(r"\{([^{}]+)\}", _replace, ext.value_expression)

            comp_params: dict[str, str] = {"value": value}
            if ext.schemdraw_element:
                comp_params["element"] = ext.schemdraw_element

            components.append(
                ComponentInstance(
                    ref=ref,
                    component_type=ext.role,
                    parameters=comp_params,
                )
            )

        # 构建网络
        nets: list[Net] = []
        for conn in topology.connections:
            connections_list: list[NetConnection] = []
            if conn.device_pin:
                connections_list.append(
                    NetConnection(
                        component_ref="U1",
                        pin_name=conn.device_pin,
                    )
                )
            for ext_ref in conn.external_refs:
                # ext_ref 格式: "input_cap.1"
                parts = ext_ref.split(".")
                if len(parts) == 2:
                    # 查找匹配的外部元件
                    role_name = parts[0]
                    pin = parts[1]
                    for comp in components[1:]:  # 跳过 U1
                        if comp.component_type == role_name:
                            connections_list.append(
                                NetConnection(
                                    component_ref=comp.ref,
                                    pin_name=pin,
                                )
                            )
                            break

            nets.append(
                Net(
                    name=conn.net_name,
                    connections=connections_list,
                    is_power=conn.is_power,
                    is_ground=conn.is_ground,
                )
            )

        instance_name = role or device.part_number
        return CircuitInstance(
            name=instance_name,
            description=device.description,
            components=components,
            nets=nets,
            template_name=topology.circuit_type,
            input_parameters=adapted.render_params,
        )

    def generate_exports(
        self,
        device: DeviceModel,
        parameters: dict[str, str] | None = None,
        role: str = "",
    ) -> tuple[str, str]:
        """生成 BOM + SPICE 导出

        Args:
            device: 器件模型
            parameters: 用户参数
            role: 角色标识

        Returns:
            (bom_text, spice_text)
        """
        circuit = self.build_circuit_instance(device, parameters, role)
        bom = generate_bom(circuit)
        spice = generate_spice(circuit)
        return bom, spice

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _merge_parameters(
        self,
        topology: TopologyDef,
        user_params: dict[str, str],
        device: DeviceModel,
    ) -> dict[str, Any]:
        """合并拓扑默认参数和用户参数

        优先级：用户参数 > 器件 specs > 拓扑默认值
        """
        result: dict[str, Any] = {}

        # 1. 从拓扑参数定义中取默认值
        for param_name, param_def in topology.parameters.items():
            if param_def.default:
                result[param_name] = param_def.default

        # 2. 从器件 specs 中补充（如 v_out: "3.3V" → "3.3"）
        spec_to_param: dict[str, str] = {
            "v_out": "v_out",
            "v_in_max": "v_in_max",
            "i_out_max": "i_out_max",
        }
        for spec_key, param_key in spec_to_param.items():
            if spec_key in device.specs and param_key not in result:
                val = device.specs[spec_key]
                # 去单位
                numeric = _strip_unit(val)
                if numeric:
                    result[param_key] = numeric

        # 3. 从外部元件默认值中填充
        for ext in topology.external_components:
            role_key_map: dict[str, str] = {
                "input_cap": "c_in",
                "output_cap": "c_out",
                "inductor": "l_value",
                "boot_cap": "c_boot",
            }
            param_key = role_key_map.get(ext.role, "")
            if param_key and ext.default_value and param_key not in result:
                result[param_key] = ext.default_value

        # 4. 用户参数覆盖一切
        result.update(user_params)

        # 5. 添加器件标识
        result.setdefault("ic_model", device.part_number)

        return result


def _strip_unit(value: str) -> str:
    """去除值中的单位，只保留数值

    "3.3V" → "3.3"
    "1A" → "1"
    "10uF" → "10uF" (保留电容单位)
    """
    import re

    # 只去除 V, A, Ω 等简单单位
    m = re.match(r"^([-+]?\d*\.?\d+)\s*[VAΩ]$", value.strip())
    if m:
        return m.group(1)
    return value.strip()
