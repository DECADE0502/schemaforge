"""AI 原子工具集 v3 — 细粒度工具，AI 真正控制每一步。

v2 的 start_system_design 是黑盒（内含完整管线 + 冗余 AI 调用），
v3 把管线拆成原子工具，AI 自己编排调用顺序：

1. resolve_modules    — AI 提交模块意图列表，本地查器件库
2. resolve_connections — AI 提交连接意图列表，本地规则引擎解析
3. synthesize_parameters — 本地公式引擎计算所有外围元件参数
4. render_schematic   — 本地渲染引擎生成 SVG
5. export_outputs     — 本地导出 BOM + SPICE

辅助工具（保留）：
- search_device_library — 查器件库
- get_design_status    — 查看当前 IR 状态
- review_design        — 工程审查规则检查
- revise_module_param  — 修改单个模块参数后重新综合
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from schemaforge.agent.tool_registry import ToolRegistry, ToolResult
from schemaforge.common.errors import ErrorCode, ToolError
from schemaforge.system.session import SystemDesignSession

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# AI Agent System Prompt — AI 真正控制每一步
# ------------------------------------------------------------------

AGENT_SYSTEM_PROMPT = """\
你是 SchemaForge 电路设计 AI 助手。你通过调用工具来完成电路设计。

## 你的工作流程

1. **理解需求**：从用户自然语言中提取出模块意图（器件型号、类别、电气参数、连接关系）。
2. **调用 resolve_modules**：提交结构化的模块意图列表，本地器件库会查找匹配的器件。
3. **调用 resolve_connections**：提交连接意图列表（电源链、GPIO、SPI等），本地规则引擎解析为引脚级连接。
4. **调用 synthesize_parameters**：本地公式引擎计算所有外围元件参数（电感、电容、分压电阻等）。
5. **调用 render_schematic**：渲染原理图 SVG。
6. **调用 export_outputs**：导出 BOM 清单和 SPICE 网表。
7. **调用 review_design**：运行工程审查规则，检查设计是否合理。
8. **总结结果**给用户。

## 关键规则

- 用户指定了器件型号时，`part_number_hint` 必须精确保留（如 "TPS54202"、"AMS1117-3.3"）。
- 器件库查不到时（status=needs_asset），告知用户缺失哪些器件，让用户上传 datasheet。
- intent_id 命名规则：类别+序号，如 buck1, ldo1, mcu1, led1, boost1。
- category_hint 可选值：buck, ldo, boost, mcu, led, resistor, capacitor, diode, connector。
- signal_type 可选值：power_supply, gpio, spi, i2c, uart, analog, enable, feedback, other。
- connection_semantics 可选值：supply_chain, gpio_drive, bus_connect, enable_control, ground_tie。
- 电源链上下游关系：buck/boost 的 VOUT 接下一级的 VIN，用 signal_type=power_supply + connection_semantics=supply_chain。
- MCU 控制 LED：用 signal_type=gpio + connection_semantics=gpio_drive。
- 不要输出电阻/电容/电感的具体数值（由本地公式引擎在 synthesize_parameters 中计算）。
- 不要输出原理图坐标或 SVG 结构（由本地渲染引擎在 render_schematic 中生成）。

## 辅助工具

- search_device_library：不确定器件库里有什么时可先搜索。
- get_design_status：查看当前设计状态。
- revise_module_param：修改某个模块的参数（如改输出电压），修改后需重新 render + export。

用中文回复用户。
"""


# ------------------------------------------------------------------
# 辅助：序列化
# ------------------------------------------------------------------


def _serialize_module_instance(mid: str, inst: object) -> dict[str, Any]:
    """ModuleInstance → 简单 dict 供 AI 阅读。"""
    device = getattr(inst, "device", None)
    part_number = getattr(device, "part_number", "") if device else ""
    status = getattr(inst, "status", "")
    status_val = status.value if hasattr(status, "value") else str(status)
    return {
        "module_id": mid,
        "role": getattr(inst, "role", ""),
        "category": getattr(inst, "resolved_category", ""),
        "part_number": part_number,
        "status": status_val,
        "parameters": dict(getattr(inst, "parameters", {})),
        "missing_part_number": getattr(inst, "missing_part_number", ""),
        "warnings": list(getattr(inst, "warnings", [])),
    }


def _serialize_connection(conn: object) -> dict[str, str]:
    """ResolvedConnection → 简单 dict。"""
    src = getattr(conn, "src_port", None)
    dst = getattr(conn, "dst_port", None)
    return {
        "id": getattr(conn, "resolved_connection_id", ""),
        "src": f"{getattr(src, 'module_id', '')}.{getattr(src, 'pin_name', '')}"
        if src else "",
        "dst": f"{getattr(dst, 'module_id', '')}.{getattr(dst, 'pin_name', '')}"
        if dst else "",
        "net": getattr(conn, "net_name", ""),
        "rule": getattr(conn, "rule_id", ""),
    }


# ------------------------------------------------------------------
# 主入口
# ------------------------------------------------------------------


def build_atomic_design_tools(session: SystemDesignSession) -> ToolRegistry:
    """为指定 SystemDesignSession 构建原子级设计工具集。"""
    registry = ToolRegistry()

    # ================================================================
    # 工具 1: resolve_modules — 器件查找
    # ================================================================
    def _handle_resolve_modules(modules: list[dict[str, Any]]) -> ToolResult:
        """接收 AI 解析的模块意图列表，逐个在器件库中查找。

        AI 负责从用户自然语言中提取出结构化的模块意图，
        本地负责在器件库中查找匹配的器件并实例化。
        """
        try:
            from schemaforge.system.models import (
                ModuleIntent,
                ModuleStatus,
                SystemDesignIR,
                SystemDesignRequest,
            )
            from schemaforge.system.resolver import (
                instantiate_module_from_device,
                resolve_part_candidates,
            )

            module_intents: list[ModuleIntent] = []
            for m in modules:
                intent = ModuleIntent(
                    intent_id=m.get("intent_id", ""),
                    role=m.get("role", ""),
                    part_number_hint=m.get("part_number_hint", ""),
                    category_hint=m.get("category_hint", ""),
                    electrical_targets=m.get("electrical_targets", {}),
                    control_targets=m.get("control_targets", {}),
                    placement_hint=m.get("placement_hint", ""),
                    priority=m.get("priority", 0),
                )
                module_intents.append(intent)

            # 逐个解析
            results: list[dict[str, Any]] = []
            module_instances: dict[str, Any] = {}
            missing: list[str] = []

            for intent in module_intents:
                candidates = resolve_part_candidates(session._store, intent)
                if candidates:
                    instance = instantiate_module_from_device(intent, candidates[0])
                else:
                    from schemaforge.system.models import ModuleInstance as MI
                    instance = MI(
                        module_id=intent.intent_id,
                        role=intent.role,
                        resolved_category=intent.category_hint,
                        parameters=dict(intent.electrical_targets),
                        status=ModuleStatus.NEEDS_ASSET,
                        missing_part_number=(
                            intent.part_number_hint or intent.category_hint
                        ),
                        warnings=[
                            f"器件未命中: part='{intent.part_number_hint}', "
                            f"category='{intent.category_hint}'"
                        ],
                    )

                module_instances[intent.intent_id] = instance
                results.append(
                    _serialize_module_instance(intent.intent_id, instance)
                )
                if instance.status == ModuleStatus.NEEDS_ASSET:
                    display = instance.missing_part_number or intent.intent_id
                    missing.append(display)

            # 构建初始 IR（无连接，无综合）并存入 session
            request = SystemDesignRequest(
                raw_text="",  # AI 已做解析，无需存原文
                modules=module_intents,
            )
            ir = SystemDesignIR(
                request=request,
                module_instances=module_instances,
            )
            session._ir = ir

            return ToolResult(
                success=True,
                data={
                    "modules": results,
                    "resolved_count": sum(
                        1 for r in results if r["status"] == "resolved"
                    ),
                    "missing_count": len(missing),
                    "missing_modules": missing,
                },
            )
        except Exception as exc:
            logger.exception("resolve_modules failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"模块解析失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="resolve_modules",
        description=(
            "接收模块意图列表，在器件库中查找匹配器件并实例化。"
            "必须在 resolve_connections 之前调用。"
        ),
        handler=_handle_resolve_modules,
        parameters_schema={
            "modules": {
                "type": "array",
                "description": (
                    "模块意图列表，每个元素: "
                    '{"intent_id": "buck1", "role": "降压", '
                    '"part_number_hint": "TPS54202", "category_hint": "buck", '
                    '"electrical_targets": {"v_in": "20", "v_out": "5"}}'
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "intent_id": {
                            "type": "string",
                            "description": "模块唯一ID: buck1, ldo1, mcu1, led1",
                        },
                        "role": {
                            "type": "string",
                            "description": "模块功能描述: 第一级降压, 主控",
                        },
                        "part_number_hint": {
                            "type": "string",
                            "description": "用户指定的器件型号（精确保留）",
                        },
                        "category_hint": {
                            "type": "string",
                            "description": "器件类别: buck/ldo/mcu/led/boost",
                        },
                        "electrical_targets": {
                            "type": "object",
                            "description": "电气参数目标: {v_in, v_out, i_out}",
                        },
                        "control_targets": {
                            "type": "object",
                            "description": "控制参数: {gpio_pin, drive_mode}",
                        },
                        "placement_hint": {
                            "type": "string",
                            "description": "放置提示: power_chain / control_side",
                        },
                    },
                    "required": ["intent_id", "role", "category_hint"],
                },
            },
        },
        required_params=["modules"],
        category="design",
    )

    # ================================================================
    # 工具 2: resolve_connections — 连接解析
    # ================================================================
    def _handle_resolve_connections(
        connections: list[dict[str, Any]],
    ) -> ToolResult:
        """接收 AI 解析的连接意图列表，用规则引擎解析为引脚级连接。"""
        try:
            if session._ir is None:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        code=ErrorCode.DESIGN_INVALID,
                        message="请先调用 resolve_modules 创建模块。",
                    ),
                )

            from schemaforge.system.connection_rules import resolve_all_connections
            from schemaforge.system.models import (
                ConnectionIntent,
                ConnectionSemantic,
                ModuleStatus,
                SignalType,
            )

            ir = session._ir

            # 构建 ConnectionIntent 列表
            conn_intents: list[ConnectionIntent] = []
            for c in connections:
                signal_type_str = c.get("signal_type", "other")
                semantics_str = c.get("connection_semantics", "unknown")
                try:
                    signal_type = SignalType(signal_type_str)
                except ValueError:
                    signal_type = SignalType.OTHER
                try:
                    semantics = ConnectionSemantic(semantics_str)
                except ValueError:
                    semantics = ConnectionSemantic.UNKNOWN

                intent = ConnectionIntent(
                    connection_id=c.get("connection_id", ""),
                    src_module_intent=c.get("src_module", ""),
                    src_port_hint=c.get("src_port_hint", ""),
                    dst_module_intent=c.get("dst_module", ""),
                    dst_port_hint=c.get("dst_port_hint", ""),
                    signal_type=signal_type,
                    connection_semantics=semantics,
                    confidence=c.get("confidence", 1.0),
                )
                conn_intents.append(intent)

            # 保存到 request 里供后续 revise 使用
            ir.request.connections = conn_intents

            # 过滤：仅保留两端都已解析的连接
            resolved_ids = {
                mid for mid, m in ir.module_instances.items()
                if m.status in (ModuleStatus.RESOLVED, ModuleStatus.SYNTHESIZED)
            }
            valid = [
                c for c in conn_intents
                if c.src_module_intent in resolved_ids
                and (c.dst_module_intent or "") in resolved_ids
            ]
            skipped = [c for c in conn_intents if c not in valid]

            resolved_conns, nets, unresolved = resolve_all_connections(
                ir.module_instances, valid,
            )
            ir.connections = resolved_conns
            ir.nets = nets
            ir.unresolved_items.extend(unresolved)

            # 序列化结果
            conn_results = [_serialize_connection(c) for c in resolved_conns]
            net_results = [
                {
                    "net_id": n.net_id,
                    "net_name": n.net_name,
                    "net_type": n.net_type.value
                    if hasattr(n.net_type, "value") else str(n.net_type),
                    "member_count": len(n.members),
                }
                for n in nets.values()
            ]

            return ToolResult(
                success=True,
                data={
                    "connections": conn_results,
                    "nets": net_results,
                    "resolved_count": len(resolved_conns),
                    "skipped_count": len(skipped),
                    "unresolved_count": len(unresolved),
                    "skipped_reasons": [
                        f"连接 '{c.connection_id}' 跳过: 端模块未解析"
                        for c in skipped
                    ],
                },
            )
        except Exception as exc:
            logger.exception("resolve_connections failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"连接解析失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="resolve_connections",
        description=(
            "接收连接意图列表，用规则引擎解析为引脚级连接。"
            "必须在 resolve_modules 之后、synthesize_parameters 之前调用。"
        ),
        handler=_handle_resolve_connections,
        parameters_schema={
            "connections": {
                "type": "array",
                "description": (
                    "连接意图列表，每个元素: "
                    '{"connection_id": "conn1", "src_module": "buck1", '
                    '"dst_module": "ldo1", "signal_type": "power_supply", '
                    '"connection_semantics": "supply_chain"}'
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "connection_id": {
                            "type": "string",
                            "description": "连接唯一ID",
                        },
                        "src_module": {
                            "type": "string",
                            "description": "源模块 intent_id",
                        },
                        "dst_module": {
                            "type": "string",
                            "description": "目标模块 intent_id",
                        },
                        "signal_type": {
                            "type": "string",
                            "description": (
                                "信号类型: power_supply / gpio / spi / i2c / "
                                "uart / analog / enable / feedback / other"
                            ),
                        },
                        "connection_semantics": {
                            "type": "string",
                            "description": (
                                "连接语义: supply_chain / gpio_drive / "
                                "bus_connect / enable_control / ground_tie"
                            ),
                        },
                        "src_port_hint": {
                            "type": "string",
                            "description": "源端口提示: VOUT / PA1（可空）",
                        },
                        "dst_port_hint": {
                            "type": "string",
                            "description": "目标端口提示: VIN / ANODE（可空）",
                        },
                    },
                    "required": ["connection_id", "src_module", "dst_module",
                                 "signal_type", "connection_semantics"],
                },
            },
        },
        required_params=["connections"],
        category="design",
    )

    # ================================================================
    # 工具 3: synthesize_parameters — 参数计算
    # ================================================================
    def _handle_synthesize_parameters() -> ToolResult:
        """用本地公式引擎为所有已解析模块计算外围元件参数。"""
        try:
            if session._ir is None:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        code=ErrorCode.DESIGN_INVALID,
                        message="请先调用 resolve_modules 和 resolve_connections。",
                    ),
                )

            from schemaforge.system.synthesis import synthesize_all_modules

            session._ir = synthesize_all_modules(session._ir)

            # 返回每个模块的综合后参数
            module_params: list[dict[str, Any]] = []
            for mid, inst in session._ir.module_instances.items():
                ext_comps = []
                for ec in getattr(inst, "external_components", []):
                    ext_comps.append({
                        "role": ec.get("role", ""),
                        "value": ec.get("value", ""),
                        "type": ec.get("type", ""),
                    })
                module_params.append({
                    "module_id": mid,
                    "status": inst.status.value
                    if hasattr(inst.status, "value") else str(inst.status),
                    "parameters": dict(inst.parameters),
                    "external_component_count": len(ext_comps),
                    "external_components": ext_comps,
                })

            return ToolResult(
                success=True,
                data={
                    "synthesized_count": sum(
                        1 for m in module_params
                        if m["status"] == "synthesized"
                    ),
                    "modules": module_params,
                },
            )
        except Exception as exc:
            logger.exception("synthesize_parameters failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"参数综合失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="synthesize_parameters",
        description=(
            "用公式引擎为所有模块计算外围元件参数（电感、电容、电阻分压器等）。"
            "必须在 resolve_connections 之后调用。"
        ),
        handler=_handle_synthesize_parameters,
        parameters_schema={},
        category="design",
    )

    # ================================================================
    # 工具 4: render_schematic — 渲染 SVG
    # ================================================================
    def _handle_render_schematic() -> ToolResult:
        """渲染当前设计为 SVG 原理图。"""
        try:
            if session._ir is None:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        code=ErrorCode.DESIGN_INVALID,
                        message="请先完成模块解析和参数综合。",
                    ),
                )

            from schemaforge.system.instances import (
                allocate_global_references,
                create_component_instances,
                stabilize_references_after_revision,
            )
            from schemaforge.system.layout import create_default_layout
            from schemaforge.system.rendering import render_system_svg_with_metadata

            ir = session._ir

            # 实例收集 + 编号分配
            comp_instances = create_component_instances(ir)
            if session._prev_component_instances:
                comp_instances = stabilize_references_after_revision(
                    session._prev_component_instances,
                    comp_instances,  # type: ignore[arg-type]
                )
            else:
                comp_instances = allocate_global_references(comp_instances)
            session._prev_component_instances = comp_instances  # type: ignore[assignment]

            # 布局 + 渲染
            session._layout_spec = create_default_layout(ir)
            svg_path, render_metadata = render_system_svg_with_metadata(
                ir, layout_spec=session._layout_spec,
            )

            # 暂存 comp_instances, render_metadata, svg_path 供后续 export 使用
            session._last_comp_instances = comp_instances  # type: ignore[attr-defined]
            session._last_render_metadata = render_metadata  # type: ignore[attr-defined]
            session._last_svg_path = svg_path  # type: ignore[attr-defined]

            meta_summary: dict[str, Any] = {
                "canvas_size": list(render_metadata.canvas_size),
                "module_count": len(render_metadata.module_bboxes),
                "wire_count": len(render_metadata.wire_paths),
            }

            return ToolResult(
                success=True,
                data={
                    "svg_path": svg_path,
                    "render_metadata": meta_summary,
                    "component_count": len(comp_instances),
                },
            )
        except Exception as exc:
            logger.exception("render_schematic failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.RENDER_FAILED,
                    message=f"原理图渲染失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="render_schematic",
        description=(
            "渲染当前设计为 SVG 原理图，自动完成元件编号和布局。"
            "必须在 synthesize_parameters 之后调用。"
        ),
        handler=_handle_render_schematic,
        parameters_schema={},
        category="design",
    )

    # ================================================================
    # 工具 5: export_outputs — 导出 BOM + SPICE
    # ================================================================
    def _handle_export_outputs() -> ToolResult:
        """导出 BOM (Markdown + CSV) 和 SPICE 网表。"""
        try:
            if session._ir is None:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        code=ErrorCode.DESIGN_INVALID,
                        message="请先完成设计并渲染原理图。",
                    ),
                )

            from schemaforge.system.export_bom import (
                export_system_bom_csv,
                export_system_bom_markdown,
            )
            from schemaforge.system.export_spice import export_system_spice

            ir = session._ir
            comp_instances = getattr(
                session, "_last_comp_instances", None
            )
            if comp_instances is None:
                # 如果没渲染过，先做实例收集
                from schemaforge.system.instances import (
                    allocate_global_references,
                    create_component_instances,
                )
                comp_instances = create_component_instances(ir)
                comp_instances = allocate_global_references(comp_instances)

            bom_text = export_system_bom_markdown(comp_instances, ir)
            bom_csv = export_system_bom_csv(comp_instances)
            spice_text = export_system_spice(ir, comp_instances)

            # 组装 bundle 并存入 session
            from schemaforge.system.models import RenderMetadata, SystemBundle

            svg_path = getattr(session, "_last_svg_path", "") or ""
            render_metadata = getattr(
                session, "_last_render_metadata", RenderMetadata(),
            ) or RenderMetadata()

            bundle = SystemBundle(
                design_ir=ir,
                svg_path=svg_path,
                bom_text=bom_text,
                bom_csv=bom_csv,
                spice_text=spice_text,
                render_metadata=render_metadata,
            )
            session._bundle = bundle

            return ToolResult(
                success=True,
                data={
                    "bom_text": bom_text,
                    "spice_text": spice_text,
                    "bom_csv_lines": len(bom_csv.splitlines()),
                    "bundle_assembled": True,
                },
            )
        except Exception as exc:
            logger.exception("export_outputs failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"导出失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="export_outputs",
        description=(
            "导出 BOM 清单 (Markdown) 和 SPICE 网表，并组装最终 bundle。"
            "必须在 render_schematic 之后调用。"
        ),
        handler=_handle_export_outputs,
        parameters_schema={},
        category="design",
    )

    # ================================================================
    # 工具 6: search_device_library — 查器件库
    # ================================================================
    def _handle_search_device_library(query: str) -> ToolResult:
        """在器件库中搜索器件。"""
        try:
            devices = session._store.search_devices(query=query)
            results: list[dict[str, str]] = []
            for dev in devices:
                results.append({
                    "part_number": getattr(dev, "part_number", ""),
                    "category": getattr(dev, "category", ""),
                    "manufacturer": getattr(dev, "manufacturer", ""),
                    "description": getattr(dev, "description", ""),
                    "package": getattr(dev, "package", ""),
                })
            return ToolResult(
                success=True,
                data={
                    "query": query,
                    "count": len(results),
                    "devices": results,
                },
            )
        except Exception as exc:
            logger.exception("search_device_library failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"器件搜索失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="search_device_library",
        description="在器件库中搜索器件（按型号、类别、厂商）",
        handler=_handle_search_device_library,
        parameters_schema={
            "query": {
                "type": "string",
                "description": "搜索关键词：型号、类别、厂商",
            },
        },
        required_params=["query"],
        category="library",
    )

    # ================================================================
    # 工具 7: get_design_status — 查设计状态
    # ================================================================
    def _handle_get_design_status() -> ToolResult:
        """获取当前设计状态。"""
        ir = session._ir
        if ir is None:
            return ToolResult(
                success=True,
                data={"has_design": False, "message": "当前没有活跃设计。"},
            )

        modules = []
        for mid, inst in ir.module_instances.items():
            modules.append(_serialize_module_instance(mid, inst))

        connections = [_serialize_connection(c) for c in ir.connections]

        bundle = session._bundle
        svg_path = getattr(bundle, "svg_path", "") if bundle else ""
        has_bom = bool(getattr(bundle, "bom_text", "")) if bundle else False
        has_spice = bool(getattr(bundle, "spice_text", "")) if bundle else False

        return ToolResult(
            success=True,
            data={
                "has_design": True,
                "modules": modules,
                "connections": connections,
                "svg_path": svg_path,
                "has_bom": has_bom,
                "has_spice": has_spice,
                "warnings": list(ir.warnings),
            },
        )

    registry.register_fn(
        name="get_design_status",
        description="获取当前设计状态：模块列表、连接关系、SVG路径、BOM",
        handler=_handle_get_design_status,
        parameters_schema={},
        category="design",
    )

    # ================================================================
    # 工具 8: review_design — 工程审查
    # ================================================================
    def _handle_review_design() -> ToolResult:
        """运行工程审查规则检查。"""
        ir = session._ir
        if ir is None:
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.DESIGN_INVALID,
                    message="当前没有活跃的设计，请先完成设计流程。",
                ),
            )

        resolved = ir.get_resolved_modules()
        if not resolved:
            return ToolResult(
                success=True,
                data={
                    "passed": True,
                    "issue_count": 0,
                    "issues": [],
                    "message": "没有已解析的模块可审查。",
                },
            )

        try:
            from schemaforge.design.review import (
                DesignReviewEngine,
                ModuleReviewInput,
            )

            review_inputs: list[ModuleReviewInput] = []
            for inst in resolved:
                if inst.device is None:
                    continue
                category = (inst.resolved_category or "").lower()
                role = (
                    f"{inst.module_id}_{category}" if category else inst.module_id
                )
                review_inputs.append(
                    ModuleReviewInput(
                        role=role,
                        category=category,
                        device=inst.device,
                        parameters=inst.parameters,
                    )
                )

            engine = DesignReviewEngine()
            review = engine.review_design(review_inputs)

            issues_data: list[dict[str, str | None]] = []
            for issue in review.issues:
                issues_data.append({
                    "severity": issue.severity.value
                    if hasattr(issue.severity, "value")
                    else str(issue.severity),
                    "category": issue.category.value
                    if hasattr(issue.category, "value")
                    else str(issue.category),
                    "message": issue.message,
                    "suggestion": issue.suggestion,
                    "module_role": issue.module_role,
                })

            return ToolResult(
                success=True,
                data={
                    "overall_passed": review.overall_passed,
                    "issue_count": len(review.issues),
                    "blocking_count": sum(
                        1
                        for i in review.issues
                        if hasattr(i.severity, "value")
                        and i.severity.value == "blocking"
                    ),
                    "issues": issues_data,
                    "reviewed_modules": len(review_inputs),
                },
            )
        except Exception as exc:
            logger.exception("review_design failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"工程审查失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="review_design",
        description="运行工程审查规则检查当前设计",
        handler=_handle_review_design,
        parameters_schema={},
        category="design",
    )

    # ================================================================
    # 工具 9: revise_module_param — 修改单个模块参数
    # ================================================================
    def _handle_revise_module_param(
        module_id: str, parameters: dict[str, str],
    ) -> ToolResult:
        """修改指定模块的参数，然后重新综合。"""
        try:
            if session._ir is None:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        code=ErrorCode.DESIGN_INVALID,
                        message="当前没有活跃设计。",
                    ),
                )

            ir = session._ir
            inst = ir.module_instances.get(module_id)
            if inst is None:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        code=ErrorCode.DESIGN_INVALID,
                        message=f"模块 '{module_id}' 不存在。",
                    ),
                )

            # 更新参数
            inst.parameters.update(parameters)
            # 也更新 request 中对应 intent 的 electrical_targets
            for m_intent in ir.request.modules:
                if m_intent.intent_id == module_id:
                    m_intent.electrical_targets.update(parameters)
                    break

            # 重新综合
            from schemaforge.system.synthesis import synthesize_all_modules
            session._ir = synthesize_all_modules(ir)

            updated = session._ir.module_instances.get(module_id)
            return ToolResult(
                success=True,
                data=_serialize_module_instance(
                    module_id, updated,
                ) if updated else {"message": "模块参数已更新"},
            )
        except Exception as exc:
            logger.exception("revise_module_param failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"参数修改失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="revise_module_param",
        description=(
            "修改指定模块的参数（如修改输出电压），然后自动重新综合。"
            "修改后需重新调用 render_schematic 和 export_outputs 更新输出。"
        ),
        handler=_handle_revise_module_param,
        parameters_schema={
            "module_id": {
                "type": "string",
                "description": "要修改的模块 ID（如 buck1, ldo1）",
            },
            "parameters": {
                "type": "object",
                "description": '要修改的参数键值对: {"v_out": "3.3"}',
            },
        },
        required_params=["module_id", "parameters"],
        category="design",
    )

    # ================================================================
    # 工具 10: get_svg_template — 生成 SVG 坐标骨架
    # ================================================================
    def _handle_get_svg_template() -> ToolResult:
        """根据当前 IR 数据生成严格的 SVG 坐标模板。

        返回一个结构化 JSON，包含每个元件的精确坐标、
        每条连线的起止点，AI 据此绘制 SVG 即可保证不重叠不断开。
        """
        try:
            if session._ir is None:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        code=ErrorCode.DESIGN_INVALID,
                        message="请先完成 resolve_modules + synthesize_parameters。",
                    ),
                )
            ir = session._ir
            template = _build_svg_template(ir)
            return ToolResult(success=True, data=template)
        except Exception as exc:
            logger.exception("get_svg_template failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"SVG 模板生成失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="get_svg_template",
        description=(
            "根据当前设计数据生成 SVG 坐标模板。"
            "返回每个元件的精确位置、每条连线的起止点坐标。"
            "AI 必须在调用 render_schematic_ai 之前先调用此工具获取布局。"
        ),
        handler=_handle_get_svg_template,
        parameters_schema={},
        category="design",
    )

    # ================================================================
    # 工具 11: render_schematic_ai — 本地确定性 SVG 渲染
    # ================================================================
    def _handle_render_schematic_ai() -> ToolResult:
        """根据当前 IR 数据，本地生成 SVG 原理图 + PNG 截图。

        不需要 AI 写 SVG 代码，由本地渲染器确定性生成。
        前置条件: resolve_modules + resolve_connections + synthesize_parameters 已完成。
        """
        try:
            if session._ir is None:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        code=ErrorCode.DESIGN_INVALID,
                        message="请先完成 resolve_modules / resolve_connections / synthesize_parameters。",
                    ),
                )

            import base64
            import os
            import time

            from schemaforge.render.base import output_path

            # 1. 生成坐标模板
            ir = session._ir
            template = _build_svg_template(ir)

            # 2. 本地确定性渲染 SVG
            svg_code = _render_svg_from_template(template)

            # 3. 保存 SVG 文件
            ts = int(time.time() * 1000) % 100000
            svg_filename = f"ai_schematic_{ts}.svg"
            svg_path = output_path(svg_filename)
            with open(svg_path, "w", encoding="utf-8") as f:
                f.write(svg_code)

            # 4. SVG → PNG 用于视觉审查
            png_path = svg_path.replace(".svg", ".png")
            _svg_to_png(svg_path, png_path)

            # 5. 读取 PNG 的 base64
            png_b64 = ""
            if os.path.exists(png_path):
                with open(png_path, "rb") as f:
                    png_b64 = base64.b64encode(f.read()).decode("ascii")

            # 6. 暂存到 session
            session._last_svg_path = svg_path  # type: ignore[attr-defined]
            session._last_svg_content = svg_code  # type: ignore[attr-defined]
            session._last_png_path = png_path  # type: ignore[attr-defined]
            session._last_png_b64 = png_b64  # type: ignore[attr-defined]

            return ToolResult(
                success=True,
                data={
                    "svg_path": svg_path,
                    "png_path": png_path,
                    "svg_length": len(svg_code),
                    "has_png": bool(png_b64),
                    "component_count": len(template.get("components", [])),
                    "wire_count": len(template.get("wires", [])),
                    "message": (
                        "原理图已由本地渲染器生成。"
                        "SVG 和 PNG 已保存。可调用 review_schematic_visual 审查渲染效果。"
                    ),
                },
            )
        except Exception as exc:
            logger.exception("render_schematic_ai failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.RENDER_FAILED,
                    message=f"本地 SVG 渲染失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="render_schematic_ai",
        description=(
            "根据当前设计数据，由本地渲染器自动生成 SVG 原理图和 PNG 截图。"
            "无需传入 SVG 代码，渲染器会根据 IR 数据自动生成。"
            "前置条件: resolve_modules + resolve_connections + synthesize_parameters 已完成。"
        ),
        handler=_handle_render_schematic_ai,
        parameters_schema={},
        category="design",
    )

    # ================================================================
    # 工具 11: review_schematic_visual — 视觉审查（PNG + SVG 源码）
    # ================================================================
    def _handle_review_schematic_visual() -> ToolResult:
        """将当前 PNG 截图 + SVG 源码发给 vision API 做审查。

        返回 AI 的审查意见（JSON），AI 可据此决定是否调用
        revise_schematic_svg 修改。
        """
        try:
            png_b64 = getattr(session, "_last_png_b64", "") or ""
            svg_content = getattr(session, "_last_svg_content", "") or ""

            if not png_b64:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        code=ErrorCode.DESIGN_INVALID,
                        message="没有可审查的 PNG，请先调用 render_schematic_ai。",
                    ),
                )

            from schemaforge.ai.client import call_llm_vision

            # 构建审查 prompt — 把 SVG 源码也传过去
            review_prompt = (
                "你是电路原理图视觉审查专家。审查这张原理图 PNG 截图，指出渲染问题。\n"
                "只关注视觉/布局问题（重叠、遮挡、对齐、标签可读性、连线清晰度），"
                "不要修改电路设计本身。\n\n"
                "用 JSON 回复：\n"
                '{"score": 1-10, "issues": [{"type": "...", "description": "...", "fix": "..."}], "summary": "..."}'
            )
            user_text = (
                "请审查这张原理图截图。以下是对应的 SVG 源码（你稍后可能需要修改它）：\n\n"
                f"```svg\n{svg_content}\n```"
            )

            review_text = call_llm_vision(
                system_prompt=review_prompt,
                user_text=user_text,
                image_base64=png_b64,
                max_tokens=4096,
            )

            # 尝试解析 JSON
            from schemaforge.ai.client import _extract_json
            review_json = _extract_json(review_text)

            return ToolResult(
                success=True,
                data={
                    "review": review_json or {"raw_text": review_text},
                    "svg_length": len(svg_content),
                    "message": (
                        "审查完成。如果需要修改，调用 revise_schematic_svg 提交修改后的完整 SVG。"
                        "你已经看到了 SVG 源码和渲染截图，请基于审查意见直接修改 SVG。"
                    ),
                },
            )
        except Exception as exc:
            logger.exception("review_schematic_visual failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"视觉审查失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="review_schematic_visual",
        description=(
            "将当前渲染的 PNG 截图和 SVG 源码发给 vision AI 做视觉审查。"
            "返回审查评分和问题列表。审查后可调用 revise_schematic_svg 修改。"
        ),
        handler=_handle_review_schematic_visual,
        parameters_schema={},
        category="design",
    )

    # revise_schematic_svg 已移除 —
    # 本地渲染是确定性的，修改设计应通过 synthesize_parameters 等工具修改 IR，
    # 然后重新调用 render_schematic_ai 即可。

    return registry


# ------------------------------------------------------------------
# LDO 布局函数
# ------------------------------------------------------------------


def _layout_ldo(
    idx: int, mid: str, pn: str,
    ext_comps: list[dict[str, str]], inst: Any,
    components: list[dict[str, Any]],
    wires: list[dict[str, Any]],
    ic_data: list[dict[str, Any]],
    gnd_symbols: list[dict[str, int]],
    power_flags: list[dict[str, Any]],
    _vert_component: Any, _find_ext_value_fn: Any,
    cap_body_h: int, cap_lead: int, cap_half: int,
    gnd_stub: int,
) -> None:
    """LDO 布局: IC (VIN/VOUT/GND/EN) + C_in + C_out。

    不使用全局 rail 横线。接地用 GND 符号，电源用 power flag 标注。
    """
    IC_PIN_STUB = 40

    # --- IC 矩形 ---
    ic_cx = 350 + idx * 500
    ic_cy = 340
    ic_w, ic_h = 120, 140
    ic_left = ic_cx - ic_w // 2
    ic_top = ic_cy - ic_h // 2
    ic_right = ic_cx + ic_w // 2
    ic_bottom = ic_cy + ic_h // 2

    # --- 引脚位置 (pin endpoint 含 stub) ---
    pin_positions: dict[str, dict[str, Any]] = {
        "VIN":  {"x": ic_left - IC_PIN_STUB,  "y": ic_top + 35,   "side": "left"},
        "EN":   {"x": ic_left - IC_PIN_STUB,  "y": ic_top + 95,   "side": "left"},
        "VOUT": {"x": ic_right + IC_PIN_STUB, "y": ic_top + 35,   "side": "right"},
        "GND":  {"x": ic_cx,                  "y": ic_bottom + IC_PIN_STUB, "side": "bottom"},
    }

    ic_data.append({
        "module_id": mid,
        "ic_rect": {"x": ic_left, "y": ic_top, "w": ic_w, "h": ic_h},
        "label": pn,
        "ref": f"U{idx + 1}",
        "pins": pin_positions,
    })

    vin_pin = pin_positions["VIN"]
    vout_pin = pin_positions["VOUT"]
    gnd_pin = pin_positions["GND"]
    en_pin = pin_positions["EN"]

    # ---- X 坐标 ----
    cin_x = ic_left - 100
    cout_x = ic_right + 100

    # 获取电压参数
    params = dict(getattr(inst, "parameters", {}))
    v_in = params.get("v_in", "")
    v_out = params.get("v_out", "")

    # ============================================================
    # 1. 输入电容 C_in: VIN → GND
    # ============================================================
    # C_in 中心与 VIN pin 同高
    cin_center_y = int(vin_pin["y"])
    comp_cin = _vert_component(
        "capacitor", "input_cap", "C1",
        _find_ext_value_fn(ext_comps, "input_cap", "10uF"),
        cin_x, cin_center_y, cap_body_h, cap_lead,
    )
    components.append(comp_cin)
    cin_pin1 = comp_cin["pins"]["1"]
    cin_pin2 = comp_cin["pins"]["2"]

    # C_in pin1 → VIN pin（水平直连）
    wires.append({
        "from": cin_pin1,
        "to": vin_pin,
        "label": "C_in→VIN",
    })
    # C_in pin2 → GND 符号
    gnd_symbols.append({"x": cin_x, "y": int(cin_pin2["y"]) + gnd_stub})
    wires.append({
        "from": cin_pin2,
        "to": {"x": cin_x, "y": int(cin_pin2["y"]) + gnd_stub},
        "label": "C_in→GND",
    })
    # VIN power flag 在 C_in pin1 上方
    power_flags.append({
        "x": cin_x, "y": int(cin_pin1["y"]),
        "text": f"VIN {v_in}V" if v_in else "VIN",
        "color": "#c62828", "direction": "up",
    })

    # ============================================================
    # 2. 输出电容 C_out: VOUT → GND
    # ============================================================
    vout_y = int(vout_pin["y"])
    cout_center_y = vout_y
    comp_cout = _vert_component(
        "capacitor", "output_cap", "C2",
        _find_ext_value_fn(ext_comps, "output_cap", "22uF"),
        cout_x, cout_center_y, cap_body_h, cap_lead,
    )
    components.append(comp_cout)
    cout_pin1 = comp_cout["pins"]["1"]
    cout_pin2 = comp_cout["pins"]["2"]

    # VOUT pin → C_out pin1（水平直连）
    wires.append({
        "from": vout_pin,
        "to": cout_pin1,
        "label": "VOUT→Cout", "weight": 3,
    })
    # C_out pin2 → GND 符号
    gnd_symbols.append({"x": cout_x, "y": int(cout_pin2["y"]) + gnd_stub})
    wires.append({
        "from": cout_pin2,
        "to": {"x": cout_x, "y": int(cout_pin2["y"]) + gnd_stub},
        "label": "Cout→GND",
    })
    # VOUT power flag
    power_flags.append({
        "x": cout_x + 40, "y": vout_y,
        "text": f"VOUT {v_out}V" if v_out else "VOUT",
        "color": "#1565c0", "direction": "up",
    })

    # ============================================================
    # 3. GND 引脚 → GND 符号
    # ============================================================
    gnd_symbols.append({"x": int(gnd_pin["x"]), "y": int(gnd_pin["y"]) + gnd_stub})
    wires.append({
        "from": gnd_pin,
        "to": {"x": gnd_pin["x"], "y": int(gnd_pin["y"]) + gnd_stub},
        "label": "IC GND",
    })

    # ============================================================
    # 4. EN 上拉: 短竖线 + VIN power flag
    # ============================================================
    en_flag_y = int(en_pin["y"]) - 40
    wires.append({
        "from": en_pin,
        "to": {"x": en_pin["x"], "y": en_flag_y},
        "label": "EN pullup",
    })
    power_flags.append({
        "x": int(en_pin["x"]), "y": en_flag_y,
        "text": "VIN", "color": "#c62828", "direction": "up",
    })


# ------------------------------------------------------------------
# SVG 坐标模板生成器
# ------------------------------------------------------------------


def _build_svg_template(ir: Any) -> dict[str, Any]:
    """根据 IR 生成严格的 SVG 坐标模板（Pin + Body 分离架构）。

    每个元件输出:
    - pins: {"1": {"x": ..., "y": ...}, "2": {"x": ..., "y": ...}}
      走线只能连接到 pin 末端坐标
    - body_bbox: {"x1": ..., "y1": ..., "x2": ..., "y2": ...}
      元件 body 占用的矩形，走线不应穿过

    布局策略（信号从左到右流动）：
    - IC 左侧引脚: VIN（上）、EN（下）— 输入侧
    - IC 右侧引脚: BOOT（上）、SW（下）— 开关/输出侧
    - IC 底部引脚: GND（左）、FB（右）

    外围元件布局：
    - C_in: IC 左边，VIN rail → GND
    - L: SW 引脚右边 → VOUT 节点（水平）
    - D: L 左端（SW 节点）→ GND（竖直）
    - C_out: VOUT 节点 → GND
    - C_bst: BOOT → SW 节点（竖直，在 IC 右上方）
    - R1/R2: VOUT → FB 分压（竖直，在 IC 右下方）
    """
    canvas_w, canvas_h = 1400, 900
    sw_rail_y = 320       # SW / inductor / VOUT 水平轨道 Y
    GND_STUB = 25         # 接地 pin 下方短引线长度（到 GND 符号）

    # ---- 标准元件尺寸（body 高/宽 + pin 引线长度）----
    # lead 要足够长，让走线连接点远离器件本体，拐弯空间充裕
    # 竖直电容: body 10px gap, pin引线各 35px
    CAP_BODY_H = 10       # 两极板间距
    CAP_LEAD = 35         # 引线长度 (body 到 pin 末端)
    CAP_HALF = CAP_BODY_H // 2 + CAP_LEAD  # center 到 pin 末端 = 40
    # 竖直电阻: body 高 40px, pin引线各 30px
    RES_BODY_H = 40
    RES_LEAD = 30
    RES_HALF = RES_BODY_H // 2 + RES_LEAD  # 50
    # 竖直二极管: body 高 35px, pin引线各 30px
    DIO_BODY_H = 35
    DIO_LEAD = 30
    DIO_HALF = DIO_BODY_H // 2 + DIO_LEAD  # ~48
    # 水平电感: body 宽 130px, pin引线各 20px
    IND_BODY_W = 130
    IND_LEAD = 20

    components: list[dict[str, Any]] = []
    wires: list[dict[str, Any]] = []
    ic_data: list[dict[str, Any]] = []
    gnd_symbols: list[dict[str, int]] = []       # 每个接地点 {x, y}
    power_flags: list[dict[str, Any]] = []        # 电源标号 {x, y, text, color}
    cout_x = canvas_w - 200  # 默认值，power_modules 循环中会覆盖

    modules = list(ir.module_instances.items())
    power_modules = [
        (mid, m) for mid, m in modules
        if getattr(m, "resolved_category", "") in ("buck", "ldo", "boost")
    ]

    # ---- 辅助函数: 生成竖直2端元件的 pins + body_bbox ----
    def _vert_component(
        ctype: str, role: str, ref: str, value: str,
        x: int, center_y: int,
        body_h: int, lead: int,
        half_w: int = 12,
    ) -> dict[str, Any]:
        """生成竖直2端元件 (电容/电阻/二极管) 的标准数据。

        pin1 (top) = center_y - body_h/2 - lead
        pin2 (bottom) = center_y + body_h/2 + lead
        body_bbox 仅包含 body 区域 (不含引线)
        """
        body_top = center_y - body_h // 2
        body_bottom = center_y + body_h // 2
        pin1_y = body_top - lead    # 上端 pin (走线连接点)
        pin2_y = body_bottom + lead  # 下端 pin (走线连接点)
        return {
            "role": role, "ref": ref, "type": ctype,
            "orientation": "vertical",
            "x": x,
            "pins": {
                "1": {"x": x, "y": pin1_y},   # top pin endpoint
                "2": {"x": x, "y": pin2_y},   # bottom pin endpoint
            },
            "body_bbox": {
                "x1": x - half_w, "y1": body_top,
                "x2": x + half_w, "y2": body_bottom,
            },
            "value": value,
        }

    # ---- 辅助函数: 生成水平2端元件 (电感) 的 pins + body_bbox ----
    def _horiz_inductor(
        ref: str, value: str,
        y: int, center_x: int,
        body_w: int = IND_BODY_W, lead: int = IND_LEAD,
    ) -> dict[str, Any]:
        body_left = center_x - body_w // 2
        body_right = center_x + body_w // 2
        pin1_x = body_left - lead    # 左端 pin
        pin2_x = body_right + lead   # 右端 pin
        return {
            "role": "inductor", "ref": ref, "type": "inductor",
            "orientation": "horizontal",
            "pins": {
                "1": {"x": pin1_x, "y": y},   # left pin endpoint
                "2": {"x": pin2_x, "y": y},   # right pin endpoint
            },
            "body_bbox": {
                "x1": body_left, "y1": y - 20,
                "x2": body_right, "y2": y + 5,
            },
            "value": value,
        }

    for idx, (mid, inst) in enumerate(power_modules):
        category = getattr(inst, "resolved_category", "buck")
        device = getattr(inst, "device", None)
        pn = getattr(device, "part_number", mid) if device else mid
        ext_comps = getattr(inst, "external_components", [])

        # ============================================================
        # 根据拓扑类型分派布局
        # ============================================================
        if category == "ldo":
            # ---- LDO 布局 ----
            # LDO 比 Buck 简单: 只有 VIN/VOUT/GND/EN 引脚 + C_in + C_out
            _layout_ldo(
                idx, mid, pn, ext_comps, inst,
                components, wires, ic_data,
                gnd_symbols, power_flags,
                _vert_component, _find_ext_value,
                CAP_BODY_H, CAP_LEAD, CAP_HALF,
                GND_STUB,
            )
            # 更新 cout_x 用于标签定位
            cout_x = 350 + idx * 500 + 70 + 100 + 80
            continue

        # ---- Buck / Boost 布局 (默认) ----
        # --- IC 矩形 ---
        # 每个电源模块占 500px 水平空间
        ic_cx = 350 + idx * 500
        ic_cy = 340                    # IC 中心 Y（在 rail_y 和 gnd_y 之间）
        ic_w, ic_h = 140, 180
        ic_left = ic_cx - ic_w // 2    # 280
        ic_top = ic_cy - ic_h // 2     # 250
        ic_right = ic_cx + ic_w // 2   # 420
        ic_bottom = ic_cy + ic_h // 2  # 430

        # --- IC 引脚末端位置 (pin stub 外端 = 走线连接点) ---
        IC_PIN_STUB = 40  # IC 引脚短线长度（足够长，走线连接点远离 IC 本体）
        pin_positions: dict[str, dict[str, Any]] = {
            # 左侧（输入侧）: VIN 在上, EN 在下
            # pin endpoint 在 IC 矩形左边 - stub 长度
            "VIN":  {"x": ic_left - IC_PIN_STUB,  "y": ic_top + 45,   "side": "left"},
            "EN":   {"x": ic_left - IC_PIN_STUB,  "y": ic_top + 135,  "side": "left"},
            # 右侧（输出侧）: BOOT 在上, SW 在下
            "BOOT": {"x": ic_right + IC_PIN_STUB, "y": ic_top + 45,   "side": "right"},
            "SW":   {"x": ic_right + IC_PIN_STUB, "y": ic_top + 135,  "side": "right"},
            # 底部: GND 左, FB 右
            "GND":  {"x": ic_cx - 30,             "y": ic_bottom + IC_PIN_STUB, "side": "bottom"},
            "FB":   {"x": ic_cx + 30,             "y": ic_bottom + IC_PIN_STUB, "side": "bottom"},
        }

        ic_data.append({
            "module_id": mid,
            "ic_rect": {"x": ic_left, "y": ic_top, "w": ic_w, "h": ic_h},
            "label": pn,
            "ref": f"U{idx + 1}",
            "pins": pin_positions,
        })

        vin_pin = pin_positions["VIN"]
        sw_pin = pin_positions["SW"]
        gnd_pin = pin_positions["GND"]
        boot_pin = pin_positions["BOOT"]
        en_pin = pin_positions["EN"]
        fb_pin = pin_positions["FB"]

        # ============================================================
        # 关键 X 坐标（间距充足，避免元件重叠）
        # ============================================================
        cin_x = ic_left - 120          # 输入电容 (往左移避开 IC pin stub)
        sw_node_x = ic_right + 80      # SW 节点（电感/二极管交汇点，在 SW pin 右边）
        l_right_x = sw_node_x + 160    # 电感右端（加宽避免 C3 重叠）
        out_x = l_right_x + 80         # VOUT 节点
        cout_x = out_x + 80            # 输出电容
        fb_x = out_x                   # FB 分压电阻 X（与 VOUT 同列）

        # 获取电压参数
        params = dict(getattr(inst, "parameters", {}))
        v_in = params.get("v_in", "")
        v_out = params.get("v_out", "")

        # ============================================================
        # 1. 输入电容 C_in: VIN → GND
        # ============================================================
        # C_in 中心与 VIN pin 同高
        cin_center_y = int(vin_pin["y"])
        comp_cin = _vert_component(
            "capacitor", "input_cap", "C1",
            _find_ext_value(ext_comps, "input_cap", "10uF"),
            cin_x, cin_center_y, CAP_BODY_H, CAP_LEAD,
        )
        components.append(comp_cin)
        cin_pin1 = comp_cin["pins"]["1"]  # top pin
        cin_pin2 = comp_cin["pins"]["2"]  # bottom pin

        # C_in pin1 → VIN pin（水平直连）
        wires.append({
            "from": cin_pin1,
            "to": vin_pin,
            "label": "C_in→VIN",
        })
        # C_in pin2 → GND 符号
        gnd_symbols.append({"x": cin_x, "y": int(cin_pin2["y"]) + GND_STUB})
        wires.append({
            "from": cin_pin2,
            "to": {"x": cin_x, "y": int(cin_pin2["y"]) + GND_STUB},
            "label": "C_in→GND",
        })
        # VIN power flag 在 C_in pin1 上方
        power_flags.append({
            "x": cin_x, "y": int(cin_pin1["y"]),
            "text": f"VIN {v_in}V" if v_in else "VIN",
            "color": "#c62828", "direction": "up",
        })

        # ============================================================
        # 2. SW 引脚 → SW 节点 (sw_node_x, sw_rail_y)
        # ============================================================
        # 路由策略: SW pin → 水平到 D1 body 左侧 → 竖直上到 sw_rail_y
        # D1 body 左边界 = sw_node_x - 12, 路由在其左侧
        sw_route_x = sw_node_x - 20  # D1 body_bbox x1 约 sw_node_x-12
        wires.append({
            "from": sw_pin,
            "to": {"x": sw_route_x, "y": sw_pin["y"]},
            "label": "SW→left of D1",
        })
        wires.append({
            "from": {"x": sw_route_x, "y": sw_pin["y"]},
            "to": {"x": sw_route_x, "y": sw_rail_y},
            "label": "SW vertical up",
        })
        wires.append({
            "from": {"x": sw_route_x, "y": sw_rail_y},
            "to": {"x": sw_node_x, "y": sw_rail_y},
            "label": "SW→node",
        })

        # ============================================================
        # 3. 电感 L: SW 节点 → VOUT（水平，在 sw_rail_y 高度）
        # ============================================================
        # 电感 center_x 使得 pin1 在 sw_node_x, pin2 在 l_right_x
        l_center_x = (sw_node_x + IND_LEAD + l_right_x - IND_LEAD) // 2
        # 精确计算: pin1_x = center_x - body_w/2 - lead = sw_node_x
        #           pin2_x = center_x + body_w/2 + lead = l_right_x
        #           => center_x = (sw_node_x + l_right_x) / 2, body_w = l_right_x - sw_node_x - 2*lead
        l_span = l_right_x - sw_node_x
        l_body_w = l_span - 2 * IND_LEAD
        if l_body_w < 40:
            l_body_w = 40  # 最小 body 宽度
        l_center_x = (sw_node_x + l_right_x) // 2
        comp_l = _horiz_inductor(
            "L1", _find_ext_value(ext_comps, "inductor", "10uH"),
            sw_rail_y, l_center_x, l_body_w, IND_LEAD,
        )
        components.append(comp_l)
        l_pin1 = comp_l["pins"]["1"]  # left pin (x≈sw_node_x)
        l_pin2 = comp_l["pins"]["2"]  # right pin (x≈l_right_x)

        # SW 节点 → 电感 pin1 (应该完全吻合，但为安全画一条短线)
        wires.append({
            "from": {"x": sw_node_x, "y": sw_rail_y},
            "to": l_pin1,
            "label": "SW→L1 pin1",
        })
        # 电感 pin2 → VOUT 节点
        wires.append({
            "from": l_pin2,
            "to": {"x": out_x, "y": sw_rail_y},
            "label": "L→VOUT", "weight": 3,
        })

        # ============================================================
        # 4. 续流二极管 D: SW 节点 → GND（竖直）
        # ============================================================
        # 二极管 center 在 SW 节点下方，pin1 对齐 sw_rail_y 下方留间距
        d_center_y = sw_rail_y + DIO_HALF + 30  # 留 30px 间距避免与 L1 弧线重叠
        comp_d = _vert_component(
            "diode", "diode", "D1",
            _find_ext_value(ext_comps, "diode", "SS34"),
            sw_node_x, d_center_y, DIO_BODY_H, DIO_LEAD,
        )
        components.append(comp_d)
        d_pin1 = comp_d["pins"]["1"]  # top pin (anode)
        d_pin2 = comp_d["pins"]["2"]  # bottom pin (cathode)

        # SW 节点 → D1 pin1
        wires.append({
            "from": {"x": sw_node_x, "y": sw_rail_y},
            "to": d_pin1,
            "label": "SW→D1 anode",
        })
        # D1 pin2 → GND 符号
        gnd_symbols.append({"x": sw_node_x, "y": int(d_pin2["y"]) + GND_STUB})
        wires.append({
            "from": d_pin2,
            "to": {"x": sw_node_x, "y": int(d_pin2["y"]) + GND_STUB},
            "label": "D1→GND",
        })

        # ============================================================
        # 5. 输出电容 C_out: VOUT → GND
        # ============================================================
        cout_center_y = sw_rail_y + CAP_HALF  # pin1_y = sw_rail_y
        comp_cout = _vert_component(
            "capacitor", "output_cap", "C2",
            _find_ext_value(ext_comps, "output_cap", "22uF"),
            cout_x, cout_center_y, CAP_BODY_H, CAP_LEAD,
        )
        components.append(comp_cout)
        cout_pin1 = comp_cout["pins"]["1"]  # top pin
        cout_pin2 = comp_cout["pins"]["2"]  # bottom pin

        # VOUT 节点 → C_out pin1
        wires.append({
            "from": {"x": out_x, "y": sw_rail_y},
            "to": cout_pin1,
            "label": "VOUT→Cout",
        })
        # C_out pin2 → GND 符号
        gnd_symbols.append({"x": cout_x, "y": int(cout_pin2["y"]) + GND_STUB})
        wires.append({
            "from": cout_pin2,
            "to": {"x": cout_x, "y": int(cout_pin2["y"]) + GND_STUB},
            "label": "Cout→GND",
        })

        # ============================================================
        # 6. 自举电容 C_bst: BOOT 引脚 → SW 节点
        # ============================================================
        # C_bst pin2 (bottom) 对齐 sw_rail_y, pin1 (top) 连到 BOOT
        bst_x = l_right_x  # 和电感右端对齐，避开电感弧线区域
        bst_center_y = sw_rail_y - CAP_HALF  # pin2_y = sw_rail_y
        comp_bst = _vert_component(
            "capacitor", "boot_cap", "C3",
            _find_ext_value(ext_comps, "boot_cap", "100nF"),
            bst_x, bst_center_y, CAP_BODY_H, CAP_LEAD,
        )
        components.append(comp_bst)
        bst_pin1 = comp_bst["pins"]["1"]  # top pin
        bst_pin2 = comp_bst["pins"]["2"]  # bottom pin (y=sw_rail_y)

        # BOOT 引脚 → C_bst pin1
        # 路由策略: 先竖直到 pin1 高度，再水平到 bst_x (避免穿过 C3 body)
        bst_pin1_y = int(bst_pin1["y"])
        wires.append({
            "from": boot_pin,
            "to": {"x": boot_pin["x"], "y": bst_pin1_y},
            "label": "BOOT→up",
        })
        wires.append({
            "from": {"x": boot_pin["x"], "y": bst_pin1_y},
            "to": bst_pin1,
            "label": "BOOT→C3 pin1",
        })
        # C_bst pin2 在 sw_rail_y 高度，与电感轨道交汇（不需要额外走线）

        # ============================================================
        # 7. FB 分压电阻: VOUT → R1 → FB_mid → R2 → GND
        # ============================================================
        # R1 pin1 连 VOUT 下降线，R1 pin2 = R2 pin1 = FB 中点
        fb_mid_y = ic_bottom + 100     # R1/R2 分界点（FB 节点）
        r1_center_y = fb_mid_y - RES_HALF  # R1 pin2 = fb_mid_y
        r2_center_y = fb_mid_y + RES_HALF  # R2 pin1 = fb_mid_y

        comp_r1 = _vert_component(
            "resistor", "fb_upper", "R1",
            _find_ext_value(ext_comps, "fb_upper", "30k"),
            fb_x, r1_center_y, RES_BODY_H, RES_LEAD, half_w=7,
        )
        components.append(comp_r1)
        r1_pin1 = comp_r1["pins"]["1"]  # top pin
        r1_pin2 = comp_r1["pins"]["2"]  # bottom pin (y=fb_mid_y)

        comp_r2 = _vert_component(
            "resistor", "fb_lower", "R2",
            _find_ext_value(ext_comps, "fb_lower", "10k"),
            fb_x, r2_center_y, RES_BODY_H, RES_LEAD, half_w=7,
        )
        components.append(comp_r2)
        r2_pin1 = comp_r2["pins"]["1"]  # top pin (y=fb_mid_y)
        r2_pin2 = comp_r2["pins"]["2"]  # bottom pin

        # VOUT rail → R1 pin1
        wires.append({
            "from": {"x": out_x, "y": sw_rail_y},
            "to": r1_pin1,
            "label": "VOUT→R1 pin1",
        })
        # R1 pin2 → R2 pin1 (应完全吻合，画短线确保连接)
        wires.append({
            "from": r1_pin2,
            "to": r2_pin1,
            "label": "R1→R2 (FB mid)",
        })
        # R2 pin2 → GND 符号
        gnd_symbols.append({"x": fb_x, "y": int(r2_pin2["y"]) + GND_STUB})
        wires.append({
            "from": r2_pin2,
            "to": {"x": fb_x, "y": int(r2_pin2["y"]) + GND_STUB},
            "label": "R2→GND",
        })
        # FB 中点 → FB 引脚（水平线 + 竖直线）
        wires.append({
            "from": {"x": fb_x, "y": fb_mid_y},
            "to": {"x": fb_pin["x"], "y": fb_mid_y},
            "label": "FB mid→horiz",
        })
        wires.append({
            "from": {"x": fb_pin["x"], "y": fb_mid_y},
            "to": fb_pin,
            "label": "FB→IC",
        })

        # ============================================================
        # 8. GND 引脚 → GND 符号
        # ============================================================
        gnd_symbols.append({"x": int(gnd_pin["x"]), "y": int(gnd_pin["y"]) + GND_STUB})
        wires.append({
            "from": gnd_pin,
            "to": {"x": gnd_pin["x"], "y": int(gnd_pin["y"]) + GND_STUB},
            "label": "IC GND",
        })

        # ============================================================
        # 9. EN 上拉: 短竖线 + VIN power flag
        # ============================================================
        en_flag_y = int(en_pin["y"]) - 40
        wires.append({
            "from": en_pin,
            "to": {"x": en_pin["x"], "y": en_flag_y},
            "label": "EN pullup",
        })
        power_flags.append({
            "x": int(en_pin["x"]), "y": en_flag_y,
            "text": "VIN", "color": "#c62828", "direction": "up",
        })

        # VOUT power flag
        power_flags.append({
            "x": cout_x + 40, "y": sw_rail_y,
            "text": f"VOUT {v_out}V" if v_out else "VOUT",
            "color": "#1565c0", "direction": "up",
        })

    return {
        "canvas": {"width": canvas_w, "height": canvas_h},
        "ic_modules": ic_data,
        "components": components,
        "wires": wires,
        "gnd_symbols": gnd_symbols,
        "power_flags": power_flags,
    }


def _find_ext_value(
    ext_comps: list[dict[str, str]], role: str, default: str,
) -> str:
    """从外围元件列表中查找指定角色的值。"""
    for ec in ext_comps:
        if ec.get("role", "") == role:
            return ec.get("value", default)
    return default


# ------------------------------------------------------------------
# 本地确定性 SVG 渲染器
# ------------------------------------------------------------------


def _render_svg_from_template(template: dict[str, Any]) -> str:
    """根据坐标模板生成完整 SVG 源码（确定性，无 AI）。

    输入: _build_svg_template() 的返回值
    输出: 完整 SVG 字符串，可直接保存为 .svg 文件

    不画全局 VIN/GND 横轨。接地用 GND 符号，电源用 power flag 标注。
    """
    cw = template["canvas"]["width"]
    ch = template["canvas"]["height"]

    lines: list[str] = []

    def _a(s: str) -> None:
        lines.append(s)

    # ---- SVG 头 ----
    _a('<?xml version="1.0" encoding="UTF-8"?>')
    _a(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {cw} {ch}" width="{cw}" height="{ch}" '
        f'style="background:#ffffff">'
    )
    _a("<defs>")
    _a(
        '<marker id="dot" viewBox="0 0 6 6" refX="3" refY="3" '
        'markerWidth="4" markerHeight="4">'
    )
    _a('  <circle cx="3" cy="3" r="2.5" fill="#333"/>')
    _a("</marker>")
    _a("</defs>")
    _a("<style>")
    _a("  text { font-family: 'Consolas','Courier New',monospace; font-size: 11px; fill: #222; }")
    _a("  .wire { stroke: #333; stroke-width: 1.8; fill: none; stroke-linecap: round; }")
    _a("  .pwr  { stroke: #333; stroke-width: 2.5; fill: none; stroke-linecap: round; }")
    _a("  .ic   { stroke: #000; stroke-width: 2; fill: #fffde7; }")
    _a("  .comp { stroke: #000; stroke-width: 1.8; fill: none; }")
    _a("  .pin  { stroke: #555; stroke-width: 1.4; }")
    _a("  .junction { fill: #333; }")
    _a("  .gnd-sym  { stroke: #2e7d32; fill: none; }")
    _a("  .label-ref  { font-size: 10px; fill: #000; font-weight: bold; }")
    _a("  .label-val  { font-size: 9px; fill: #555; }")
    _a("  .label-pin  { font-size: 9px; fill: #444; }")
    _a("  .label-pwr  { font-size: 14px; font-weight: bold; }")
    _a("</style>")

    # ---- IC 模块 ----
    for ic in template.get("ic_modules", []):
        _svg_ic_module(lines, ic)

    # ---- 元件 ----
    for comp in template.get("components", []):
        _svg_component(lines, comp)

    # ---- 走线 ----
    for wire in template.get("wires", []):
        _svg_wire(lines, wire)

    # ---- GND 符号 (每个接地点单独画) ----
    for gs in template.get("gnd_symbols", []):
        _svg_gnd_symbol(lines, int(gs["x"]), int(gs["y"]))

    # ---- Power flags (VIN/VOUT 电源标号) ----
    for pf in template.get("power_flags", []):
        px, py = int(pf["x"]), int(pf["y"])
        color = pf.get("color", "#c62828")
        text = pf.get("text", "")
        # 画一条短横线 + 三角形 power flag 符号
        _a(f'<line x1="{px - 8}" y1="{py}" x2="{px + 8}" y2="{py}" stroke="{color}" stroke-width="2"/>')
        _a(
            f'<text x="{px}" y="{py - 8}" text-anchor="middle" '
            f'fill="{color}" class="label-pwr">{text}</text>'
        )

    # ---- Junction dots (T 型交叉点) ----
    # 统计每个坐标点被多少条走线端点引用
    endpoint_count: Counter[tuple[int, int]] = Counter()
    for wire in template.get("wires", []):
        path = wire.get("path")
        if path and len(path) >= 2:
            for p in path:
                endpoint_count[(int(p["x"]), int(p["y"]))] += 1
        else:
            f = wire["from"]
            t = wire["to"]
            endpoint_count[(int(f["x"]), int(f["y"]))] += 1
            endpoint_count[(int(t["x"]), int(t["y"]))] += 1
    # 3+ 条走线在同一点 → 需要 junction dot
    for (jx, jy), cnt in endpoint_count.items():
        if cnt >= 3:
            _a(f'<circle cx="{jx}" cy="{jy}" r="3" class="junction"/>')

    _a("</svg>")
    return "\n".join(lines)


def _svg_gnd_symbol(lines: list[str], x: int, y: int) -> None:
    """在 (x, y) 画一个 GND 符号（三条递减横线）。"""
    lines.append(f'<line x1="{x - 8}" y1="{y}" x2="{x + 8}" y2="{y}" stroke="#2e7d32" stroke-width="2"/>')
    lines.append(f'<line x1="{x - 5}" y1="{y + 4}" x2="{x + 5}" y2="{y + 4}" stroke="#2e7d32" stroke-width="1.5"/>')
    lines.append(f'<line x1="{x - 2}" y1="{y + 8}" x2="{x + 2}" y2="{y + 8}" stroke="#2e7d32" stroke-width="1"/>')


def _svg_ic_module(lines: list[str], ic: dict[str, Any]) -> None:
    """绘制 IC 矩形 + 引脚短线（从矩形边到 pin endpoint）+ 标注。

    Pin endpoint 已经包含 stub 长度，所以引脚短线从 IC 矩形边缘画到 pin endpoint。
    """
    r = ic["ic_rect"]
    rx, ry, rw, rh = r["x"], r["y"], r["w"], r["h"]

    # IC 矩形
    lines.append(f'<rect x="{rx}" y="{ry}" width="{rw}" height="{rh}" class="ic" rx="3"/>')

    # IC 标注（ref + label）
    cx = rx + rw // 2
    cy = ry + rh // 2
    lines.append(f'<text x="{cx}" y="{cy - 6}" text-anchor="middle" class="label-ref" font-size="12">{ic["ref"]}</text>')
    label = ic.get("label", "")
    if label and label != ic["ref"]:
        lines.append(f'<text x="{cx}" y="{cy + 10}" text-anchor="middle" class="label-val" font-size="10">{label}</text>')

    # 引脚短线: 从 IC 矩形边缘 → pin endpoint
    for pname, pp in ic.get("pins", {}).items():
        px, py, side = int(pp["x"]), int(pp["y"]), pp["side"]
        if side == "left":
            # pin endpoint 在矩形左边外侧, 短线从 rx 画到 px
            lines.append(f'<line x1="{rx}" y1="{py}" x2="{px}" y2="{py}" class="pin"/>')
            lines.append(f'<text x="{rx + 6}" y="{py + 4}" class="label-pin">{pname}</text>')
        elif side == "right":
            # pin endpoint 在矩形右边外侧
            lines.append(f'<line x1="{rx + rw}" y1="{py}" x2="{px}" y2="{py}" class="pin"/>')
            lines.append(f'<text x="{rx + rw - 6}" y="{py + 4}" text-anchor="end" class="label-pin">{pname}</text>')
        elif side == "bottom":
            # pin endpoint 在矩形底部外侧
            lines.append(f'<line x1="{px}" y1="{ry + rh}" x2="{px}" y2="{py}" class="pin"/>')
            lines.append(f'<text x="{px}" y="{ry + rh - 6}" text-anchor="middle" class="label-pin">{pname}</text>')
        elif side == "top":
            # pin endpoint 在矩形顶部外侧
            lines.append(f'<line x1="{px}" y1="{ry}" x2="{px}" y2="{py}" class="pin"/>')
            lines.append(f'<text x="{px}" y="{ry + 14}" text-anchor="middle" class="label-pin">{pname}</text>')


def _svg_component(lines: list[str], comp: dict[str, Any]) -> None:
    """根据元件类型绘制标准原理图符号（Pin + Body 分离架构）。

    每个元件从 comp["pins"] 和 comp["body_bbox"] 读取坐标:
    - 画 body (内部符号) 在 body_bbox 区域内
    - 画 lead (引线) 从 body 边缘到 pin endpoint
    - 走线只连接到 pin endpoint，不进入 body_bbox
    """
    ctype = comp["type"]
    ref = comp.get("ref", "")
    val = comp.get("value", "")

    if ctype == "capacitor":
        _svg_capacitor(lines, comp, ref, val)
    elif ctype == "inductor":
        _svg_inductor(lines, comp, ref, val)
    elif ctype == "resistor":
        _svg_resistor(lines, comp, ref, val)
    elif ctype == "diode":
        _svg_diode(lines, comp, ref, val)


def _svg_capacitor(lines: list[str], c: dict[str, Any], ref: str, val: str) -> None:
    """竖直电容符号：两条平行横线 + 上下引线（Pin+Body 架构）。

    body_bbox: 两极板区域
    pin1 (top): 上端走线连接点
    pin2 (bottom): 下端走线连接点
    引线: pin1 → body top, body bottom → pin2
    """
    pins = c["pins"]
    bbox = c["body_bbox"]
    x = int(c["x"])
    pin1_y = int(pins["1"]["y"])   # top pin endpoint
    pin2_y = int(pins["2"]["y"])   # bottom pin endpoint
    body_top = int(bbox["y1"])
    body_bottom = int(bbox["y2"])
    mid = (body_top + body_bottom) // 2
    gap = (body_bottom - body_top) // 2  # 两极板间距的一半
    pw = 12  # 极板半宽

    # 上引线: pin1 → body top (上极板)
    lines.append(f'<line x1="{x}" y1="{pin1_y}" x2="{x}" y2="{mid - gap}" class="comp"/>')
    # 上极板
    lines.append(f'<line x1="{x - pw}" y1="{mid - gap}" x2="{x + pw}" y2="{mid - gap}" stroke="#000" stroke-width="2"/>')
    # 下极板
    lines.append(f'<line x1="{x - pw}" y1="{mid + gap}" x2="{x + pw}" y2="{mid + gap}" stroke="#000" stroke-width="2"/>')
    # 下引线: body bottom (下极板) → pin2
    lines.append(f'<line x1="{x}" y1="{mid + gap}" x2="{x}" y2="{pin2_y}" class="comp"/>')
    # 标注
    lines.append(f'<text x="{x + pw + 4}" y="{mid - 2}" class="label-ref">{ref}</text>')
    lines.append(f'<text x="{x + pw + 4}" y="{mid + 11}" class="label-val">{val}</text>')


def _svg_inductor(lines: list[str], c: dict[str, Any], ref: str, val: str) -> None:
    """水平电感符号：4 个半圆弧 + 左右引线（Pin+Body 架构）。

    body_bbox: 弧线区域
    pin1 (left): 左端走线连接点
    pin2 (right): 右端走线连接点
    引线: pin1 → body left, body right → pin2
    """
    pins = c["pins"]
    bbox = c["body_bbox"]
    pin1_x = int(pins["1"]["x"])
    pin2_x = int(pins["2"]["x"])
    y = int(pins["1"]["y"])
    body_left = int(bbox["x1"])
    body_right = int(bbox["x2"])

    # 左引线: pin1 → body left
    if pin1_x < body_left:
        lines.append(f'<line x1="{pin1_x}" y1="{y}" x2="{body_left}" y2="{y}" class="comp"/>')
    # 4 个半圆弧 (body)
    body_w = body_right - body_left
    seg = body_w / 4
    d_parts = [f"M{body_left},{y}"]
    for i in range(4):
        ex = body_left + (i + 1) * seg
        d_parts.append(f"A{seg / 2},{seg / 2} 0 0 1 {ex},{y}")
    d_str = " ".join(d_parts)
    lines.append(f'<path d="{d_str}" class="comp"/>')
    # 右引线: body right → pin2
    if pin2_x > body_right:
        lines.append(f'<line x1="{body_right}" y1="{y}" x2="{pin2_x}" y2="{y}" class="comp"/>')
    # 标注
    mx = (body_left + body_right) // 2
    lines.append(f'<text x="{mx}" y="{y - 14}" text-anchor="middle" class="label-ref">{ref}</text>')
    lines.append(f'<text x="{mx}" y="{y - 4}" text-anchor="middle" class="label-val">{val}</text>')


def _svg_resistor(lines: list[str], c: dict[str, Any], ref: str, val: str) -> None:
    """竖直电阻符号：矩形框 + 上下引线（Pin+Body 架构）。

    body_bbox: 矩形体区域
    pin1 (top): 上端走线连接点
    pin2 (bottom): 下端走线连接点
    """
    pins = c["pins"]
    bbox = c["body_bbox"]
    x = int(c["x"])
    pin1_y = int(pins["1"]["y"])
    pin2_y = int(pins["2"]["y"])
    body_top = int(bbox["y1"])
    body_bottom = int(bbox["y2"])
    hw = (int(bbox["x2"]) - int(bbox["x1"])) // 2

    # 上引线: pin1 → body top
    lines.append(f'<line x1="{x}" y1="{pin1_y}" x2="{x}" y2="{body_top}" class="comp"/>')
    # 矩形体
    lines.append(f'<rect x="{x - hw}" y="{body_top}" width="{hw * 2}" height="{body_bottom - body_top}" class="comp"/>')
    # 下引线: body bottom → pin2
    lines.append(f'<line x1="{x}" y1="{body_bottom}" x2="{x}" y2="{pin2_y}" class="comp"/>')
    # 标注
    mid_y = (body_top + body_bottom) // 2
    lines.append(f'<text x="{x + hw + 4}" y="{mid_y - 2}" class="label-ref">{ref}</text>')
    lines.append(f'<text x="{x + hw + 4}" y="{mid_y + 11}" class="label-val">{val}</text>')


def _svg_diode(lines: list[str], c: dict[str, Any], ref: str, val: str) -> None:
    """竖直二极管符号：三角形 + 横线 + 上下引线（Pin+Body 架构）。

    阳极在上 (pin1), 阴极在下 (pin2)。
    body_bbox: 三角形 + 阴极横线区域
    """
    pins = c["pins"]
    bbox = c["body_bbox"]
    x = int(c["x"])
    pin1_y = int(pins["1"]["y"])
    pin2_y = int(pins["2"]["y"])
    body_top = int(bbox["y1"])
    body_bottom = int(bbox["y2"])
    hw = (int(bbox["x2"]) - int(bbox["x1"])) // 2

    # 上引线（阳极）: pin1 → body top
    lines.append(f'<line x1="{x}" y1="{pin1_y}" x2="{x}" y2="{body_top}" class="comp"/>')
    # 三角形（尖朝下）
    lines.append(
        f'<polygon points="{x - hw},{body_top} {x + hw},{body_top} {x},{body_bottom}" '
        f'fill="none" stroke="#000" stroke-width="1.8"/>'
    )
    # 阴极横线
    lines.append(f'<line x1="{x - hw}" y1="{body_bottom}" x2="{x + hw}" y2="{body_bottom}" stroke="#000" stroke-width="2"/>')
    # 下引线（阴极）: body bottom → pin2
    lines.append(f'<line x1="{x}" y1="{body_bottom}" x2="{x}" y2="{pin2_y}" class="comp"/>')
    # 标注
    mid_y = (body_top + body_bottom) // 2
    lines.append(f'<text x="{x + hw + 4}" y="{mid_y - 2}" class="label-ref">{ref}</text>')
    lines.append(f'<text x="{x + hw + 4}" y="{mid_y + 11}" class="label-val">{val}</text>')


def _svg_wire(lines: list[str], wire: dict[str, Any]) -> None:
    """绘制一条走线（支持直线和折线路径）。"""
    path = wire.get("path")
    weight = wire.get("weight", 0)
    css = "pwr" if weight >= 3 else "wire"

    if path and len(path) >= 2:
        # 折线路径
        points = " ".join(f'{p["x"]},{p["y"]}' for p in path)
        lines.append(f'<polyline points="{points}" class="{css}"/>')
    else:
        fx, fy = int(wire["from"]["x"]), int(wire["from"]["y"])
        tx, ty = int(wire["to"]["x"]), int(wire["to"]["y"])
        # 跳过零长度线
        if fx == tx and fy == ty:
            return
        lines.append(f'<line x1="{fx}" y1="{fy}" x2="{tx}" y2="{ty}" class="{css}"/>')


# ------------------------------------------------------------------
# SVG → PNG 转换辅助
# ------------------------------------------------------------------


def _svg_to_png(svg_path: str, png_path: str, width: int = 1200) -> None:
    """SVG 文件转 PNG 文件。

    Fallback 链:
    1. cairosvg（高保真）
    2. Qt QSvgRenderer（Windows 上最可靠）
    3. svglib + reportlab renderPM
    4. PIL 占位图
    """
    # --- 1. cairosvg ---
    try:
        import cairosvg
        cairosvg.svg2png(url=svg_path, write_to=png_path, output_width=width)
        return
    except ImportError:
        pass
    except Exception:
        pass

    # --- 2. Qt SVG 渲染（Windows 首选） ---
    try:
        from PySide6.QtCore import QSize, Qt
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer
        from PySide6.QtWidgets import QApplication

        # 确保 QApplication 存在
        app = QApplication.instance()
        if app is None:
            import sys
            app = QApplication(sys.argv)

        renderer = QSvgRenderer(svg_path)
        if renderer.isValid():
            default_size = renderer.defaultSize()
            scale = width / max(default_size.width(), 1)
            render_size = QSize(
                int(default_size.width() * scale),
                int(default_size.height() * scale),
            )
            img = QImage(render_size, QImage.Format.Format_ARGB32)
            img.fill(Qt.GlobalColor.white)
            painter = QPainter(img)
            renderer.render(painter)
            painter.end()
            img.save(png_path)
            return
    except ImportError:
        pass
    except Exception:
        pass

    # --- 3. svglib + reportlab ---
    try:
        from io import BytesIO

        from PIL import Image
        from reportlab.graphics import renderPM
        from svglib.svglib import svg2rlg

        drawing = svg2rlg(svg_path)
        if drawing:
            png_data = BytesIO()
            renderPM.drawToFile(drawing, png_data, fmt="PNG")
            png_data.seek(0)
            img = Image.open(png_data)
            img.save(png_path)
            return
    except ImportError:
        pass
    except Exception:
        pass

    # --- 4. PIL 占位图 ---
    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (width, 400), "white")
        draw = ImageDraw.Draw(img)
        draw.text(
            (20, 180),
            "SVG to PNG: cairosvg / Qt / svglib all unavailable",
            fill="red",
        )
        img.save(png_path)
    except Exception:
        import struct
        import zlib

        def _minimal_png() -> bytes:
            sig = b"\x89PNG\r\n\x1a\n"
            ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
            ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
            ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
            raw = zlib.compress(b"\x00\xff\xff\xff")
            idat_crc = zlib.crc32(b"IDAT" + raw) & 0xFFFFFFFF
            idat = struct.pack(">I", len(raw)) + b"IDAT" + raw + struct.pack(">I", idat_crc)
            iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
            iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
            return sig + ihdr + idat + iend

        with open(png_path, "wb") as f:
            f.write(_minimal_png())


# ------------------------------------------------------------------
# AI SVG 模式 System Prompt
# ------------------------------------------------------------------

AGENT_SYSTEM_PROMPT_AI_SVG = """\
你是 SchemaForge 电路设计 AI 助手（本地渲染模式）。

在此模式下，你负责电路设计决策（选型、参数、连接），原理图由**本地渲染器自动生成**。
你不需要编写任何 SVG 代码。

## 工作流程（严格按顺序）

1. **调用 resolve_modules** — 提交模块意图，查器件库
2. **调用 resolve_connections** — 提交连接意图
3. **调用 synthesize_parameters** — 计算外围元件参数
4. **调用 render_schematic_ai** — 本地渲染器自动生成 SVG + PNG（无需传参数）
5. **调用 review_schematic_visual** — 视觉审查（可选）
6. **调用 export_outputs** — 导出 BOM + SPICE
7. **总结结果** — 输出设计概要

## 核心规则

- **不需要调用 get_svg_template** — 渲染器内部自动使用
- **不需要编写 SVG 代码** — render_schematic_ai 会自动生成
- 如果审查发现问题，可修改参数后重新调用 render_schematic_ai
- 专注于电路设计本身：器件选型、参数计算、连接关系

## 设计规则

- intent_id 命名：buck1, ldo1, mcu1, led1
- category_hint：buck, ldo, boost, mcu, led
- signal_type：power_supply, gpio, spi, i2c, uart, analog, enable, feedback, other
- connection_semantics：supply_chain, gpio_drive, bus_connect, enable_control, ground_tie
- 不要在 resolve_modules 时指定电阻电容电感数值，让 synthesize_parameters 自动计算

用中文回复用户。
"""
