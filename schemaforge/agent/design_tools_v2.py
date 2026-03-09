"""AI 工具集 v2 — 绑定 SystemDesignSession。

为 Orchestrator 提供系统级设计工具：
- 解析需求并生成多模块设计
- 修改已有设计
- 导入缺失器件
- 重新渲染原理图
- 查询当前设计状态
"""

from __future__ import annotations

import logging
from typing import Any

from schemaforge.agent.tool_registry import ToolRegistry, ToolResult
from schemaforge.common.errors import ErrorCode, ToolError
from schemaforge.system.session import SystemDesignSession

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 辅助
# ------------------------------------------------------------------


def _no_design_error() -> ToolResult:
    """当会话中没有活跃设计时返回的统一错误。"""
    return ToolResult(
        success=False,
        error=ToolError(
            code=ErrorCode.DESIGN_INVALID,
            message="当前没有活跃的设计，请先通过 start_system_design 创建设计。",
        ),
    )


def _serialize_result(result: object) -> dict[str, Any]:
    """将 SystemDesignResult 序列化为简单 dict。"""
    status = getattr(result, "status", "unknown")
    message = getattr(result, "message", "")
    warnings = list(getattr(result, "warnings", []))
    missing_modules = list(getattr(result, "missing_modules", []))

    bundle = getattr(result, "bundle", None)
    svg_path = ""
    bom_text = ""
    spice_text = ""
    module_list: list[dict[str, Any]] = []

    if bundle is not None:
        svg_path = getattr(bundle, "svg_path", "")
        bom_text = getattr(bundle, "bom_text", "")
        spice_text = getattr(bundle, "spice_text", "")

        ir = getattr(bundle, "design_ir", None)
        if ir is not None:
            instances = getattr(ir, "module_instances", {})
            for mid, inst in instances.items():
                device = getattr(inst, "device", None)
                part_number = getattr(device, "part_number", "") if device else ""
                module_list.append({
                    "module_id": mid,
                    "role": getattr(inst, "role", ""),
                    "category": getattr(inst, "resolved_category", ""),
                    "part_number": part_number,
                    "status": getattr(inst, "status", "").value
                    if hasattr(getattr(inst, "status", None), "value")
                    else str(getattr(inst, "status", "")),
                    "parameters": dict(getattr(inst, "parameters", {})),
                })

    return {
        "status": status,
        "message": message,
        "svg_path": svg_path,
        "bom_text": bom_text,
        "spice_text": spice_text,
        "module_list": module_list,
        "warnings": warnings,
        "missing_modules": missing_modules,
    }


# ------------------------------------------------------------------
# 主入口
# ------------------------------------------------------------------


def build_system_design_tools(session: SystemDesignSession) -> ToolRegistry:
    """为指定 SystemDesignSession 构建系统级设计工具集。"""
    registry = ToolRegistry()

    # ----------------------------------------------------------------
    # 工具 1: start_system_design — 从自然语言启动系统级设计
    # ----------------------------------------------------------------
    def _handle_start_system_design(user_input: str) -> ToolResult:
        try:
            result = session.start(user_input)
            return ToolResult(success=True, data=_serialize_result(result))
        except Exception as exc:
            logger.exception("start_system_design failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"系统设计生成失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="start_system_design",
        description="根据自然语言需求生成系统级原理图设计（多模块：电源链+MCU+外设）",
        handler=_handle_start_system_design,
        parameters_schema={
            "user_input": {
                "type": "string",
                "description": "用户自然语言电路需求",
            },
        },
        required_params=["user_input"],
        category="design",
    )

    # ----------------------------------------------------------------
    # 工具 2: revise_design — 自然语言修改已有设计
    # ----------------------------------------------------------------
    def _handle_revise_design(user_input: str) -> ToolResult:
        if session.ir is None:
            return _no_design_error()
        try:
            result = session.revise(user_input)
            return ToolResult(success=True, data=_serialize_result(result))
        except Exception as exc:
            logger.exception("revise_design failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"设计修订失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="revise_design",
        description="根据自然语言修改指令修改当前设计",
        handler=_handle_revise_design,
        parameters_schema={
            "user_input": {
                "type": "string",
                "description": "修改指令，如'把输出电压改成5V'",
            },
        },
        required_params=["user_input"],
        category="design",
    )

    # ----------------------------------------------------------------
    # 工具 3: get_design_status — 查询当前设计状态
    # ----------------------------------------------------------------
    def _handle_get_design_status() -> ToolResult:
        ir = session.ir
        bundle = session.bundle

        if ir is None:
            return ToolResult(
                success=True,
                data={
                    "has_design": False,
                    "message": "当前没有活跃设计。",
                },
            )

        # 构建模块列表
        modules: list[dict[str, Any]] = []
        for mid, inst in ir.module_instances.items():
            device = getattr(inst, "device", None)
            part_number = getattr(device, "part_number", "") if device else ""
            modules.append({
                "module_id": mid,
                "role": inst.role,
                "category": inst.resolved_category,
                "part_number": part_number,
                "status": inst.status.value
                if hasattr(inst.status, "value")
                else str(inst.status),
                "parameters": dict(inst.parameters),
            })

        # 构建连接列表
        connections: list[dict[str, str]] = []
        for conn in ir.connections:
            connections.append({
                "id": conn.resolved_connection_id,
                "src": f"{conn.src_port.module_id}.{conn.src_port.pin_name}",
                "dst": f"{conn.dst_port.module_id}.{conn.dst_port.pin_name}",
                "net": conn.net_name,
                "rule": conn.rule_id,
            })

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
                "unresolved_items": ir.unresolved_items,
            },
        )

    registry.register_fn(
        name="get_design_status",
        description="获取当前设计状态：模块列表、连接关系、SVG路径、BOM",
        handler=_handle_get_design_status,
        parameters_schema={},
        category="design",
    )

    # ----------------------------------------------------------------
    # 工具 4: search_device_library — 在器件库中搜索器件
    # ----------------------------------------------------------------
    def _handle_search_device_library(query: str) -> ToolResult:
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
        description="在器件库中搜索器件",
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

    # ----------------------------------------------------------------
    # 工具 5: ingest_device — 上传器件资料解析
    # ----------------------------------------------------------------
    def _handle_ingest_device(file_path: str) -> ToolResult:
        try:
            result = session.ingest_asset(file_path)
            return ToolResult(success=True, data=_serialize_result(result))
        except Exception as exc:
            logger.exception("ingest_device failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"器件资料解析失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="ingest_device",
        description="上传器件 datasheet (PDF/图片) 解析器件参数",
        handler=_handle_ingest_device,
        parameters_schema={
            "file_path": {
                "type": "string",
                "description": "PDF或图片文件路径",
            },
        },
        required_params=["file_path"],
        category="ingest",
    )

    # ----------------------------------------------------------------
    # 工具 6: confirm_device_import — 确认导入已解析的器件
    # ----------------------------------------------------------------
    def _handle_confirm_device_import(
        answers: dict[str, Any] | None = None,
    ) -> ToolResult:
        try:
            result = session.confirm_import(answers)
            return ToolResult(success=True, data=_serialize_result(result))
        except Exception as exc:
            logger.exception("confirm_device_import failed")
            return ToolResult(
                success=False,
                error=ToolError(
                    code=ErrorCode.ENGINE_FAILED,
                    message=f"器件导入确认失败: {exc}",
                    retriable=True,
                ),
            )

    registry.register_fn(
        name="confirm_device_import",
        description="确认导入已解析的器件到库中",
        handler=_handle_confirm_device_import,
        parameters_schema={
            "answers": {
                "type": "object",
                "description": "用户确认的参数修正",
            },
        },
        category="ingest",
    )

    # ----------------------------------------------------------------
    # 工具 7: render_schematic — 重新渲染当前设计的原理图 SVG
    # ----------------------------------------------------------------
    def _handle_render_schematic() -> ToolResult:
        ir = session.ir
        if ir is None:
            return _no_design_error()

        try:
            from schemaforge.system.rendering import render_system_svg_with_metadata

            layout_spec = getattr(session, "_layout_spec", None)
            svg_path, render_metadata = render_system_svg_with_metadata(
                ir, layout_spec=layout_spec,
            )

            # 序列化 render_metadata 摘要
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
        description="重新渲染当前设计的原理图SVG",
        handler=_handle_render_schematic,
        parameters_schema={},
        category="design",
    )

    # ----------------------------------------------------------------
    # 工具 8: review_design — 运行工程审查规则检查
    # ----------------------------------------------------------------
    def _handle_review_design() -> ToolResult:
        ir = session.ir
        if ir is None:
            return _no_design_error()

        resolved = ir.get_resolved_modules()
        if not resolved:
            return ToolResult(
                success=True,
                data={
                    "passed": True,
                    "issue_count": 0,
                    "blocking_count": 0,
                    "issues": [],
                    "message": "没有已解析的模块可审查。",
                },
            )

        try:
            from schemaforge.design.review import DesignReviewEngine, ModuleReviewInput

            review_inputs: list[ModuleReviewInput] = []
            for inst in resolved:
                if inst.device is None:
                    continue
                category = (inst.resolved_category or "").lower()
                role = f"{inst.module_id}_{category}" if category else inst.module_id
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

    return registry
