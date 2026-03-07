"""SchemaForge 统一设计工作台。"""

from __future__ import annotations

from dataclasses import dataclass, field
import base64
import json
from pathlib import Path
from typing import Any, Callable

from schemaforge.design.review import DesignReviewEngine, ModuleReviewInput
from schemaforge.design.synthesis import (
    DesignBundle,
    DesignRecipeSynthesizer,
    ExactPartResolver,
    UserDesignRequest,
    apply_request_updates,
    parse_design_request,
    parse_revision_request_v2,
)
from schemaforge.ingest.datasheet_extractor import (
    apply_user_answers,
    extract_from_image,
    extract_from_pdf,
)
from schemaforge.library.models import DeviceModel
from schemaforge.library.service import LibraryService
from schemaforge.library.store import ComponentStore
from schemaforge.library.symbol_builder import build_symbol
from schemaforge.library.validator import DeviceDraft, PinDraft
from schemaforge.schematic.renderer import TopologyRenderer


@dataclass(slots=True)
class ImportPreview:
    """待确认导入预览。"""

    draft: DeviceDraft
    symbol_preview_base64: str
    questions: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "part_number": self.draft.part_number,
            "category": self.draft.category,
            "package": self.draft.package,
            "questions": list(self.questions),
            "symbol_preview_base64": self.symbol_preview_base64,
        }


@dataclass(slots=True)
class SchemaForgeTurnResult:
    """会话层返回结果。"""

    status: str
    message: str
    request: UserDesignRequest | None = None
    bundle: DesignBundle | None = None
    import_preview: ImportPreview | None = None
    missing_part_number: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "message": self.message,
            "request": (
                {
                    "raw_text": self.request.raw_text,
                    "part_number": self.request.part_number,
                    "category": self.request.category,
                    "v_in": self.request.v_in,
                    "v_out": self.request.v_out,
                    "i_out": self.request.i_out,
                    "wants_led": self.request.wants_led,
                    "led_color": self.request.led_color,
                    "led_current_ma": self.request.led_current_ma,
                }
                if self.request
                else None
            ),
            "bundle": self.bundle.to_dict() if self.bundle else None,
            "import_preview": (
                self.import_preview.to_dict() if self.import_preview else None
            ),
            "missing_part_number": self.missing_part_number,
            "warnings": list(self.warnings),
        }


class SchemaForgeSession:
    """统一设计会话。"""

    def __init__(
        self,
        store_dir: Path | str,
        on_event: Callable[..., Any] | None = None,
        skip_ai_parse: bool | None = None,
    ) -> None:
        self.store_dir = Path(store_dir)
        self._on_event = on_event
        self._skip_ai_parse = skip_ai_parse
        self._store = ComponentStore(self.store_dir)
        self._service = LibraryService(self.store_dir)
        self._resolver = ExactPartResolver(self._store)
        self._synthesizer = DesignRecipeSynthesizer()
        self._request: UserDesignRequest | None = None
        self._device: DeviceModel | None = None
        self._bundle: DesignBundle | None = None
        self._parameter_overrides: dict[str, str] = {}
        self._pending_draft: DeviceDraft | None = None
        self._pending_app_circuit: dict[str, object] = {}  # P3: 待附加的应用电路
        self._orchestrator: Any | None = None

    @property
    def bundle(self) -> DesignBundle | None:
        return self._bundle

    # ----------------------------------------------------------
    # Orchestrator（AI 多轮编排）
    # ----------------------------------------------------------

    def _build_orchestrator(self) -> Any:
        """懒构建 Orchestrator: 合并全局工具 + 会话工具，注入 system prompt。"""
        from schemaforge.agent.design_tools import build_design_tool_registry
        from schemaforge.agent.orchestrator import Orchestrator
        from schemaforge.agent.tools import default_registry
        from schemaforge.ai.prompts import build_design_workbench_prompt

        session_registry = build_design_tool_registry(self)
        merged = default_registry.merge(session_registry)

        tool_desc_list = merged.get_tool_descriptions()
        tool_desc_text = json.dumps(tool_desc_list, ensure_ascii=False, indent=2)
        system_prompt = build_design_workbench_prompt(tool_desc_text)

        return Orchestrator(
            tool_registry=merged,
            system_prompt=system_prompt,
            on_event=self._on_event,
        )

    def get_orchestrator(self) -> Any:
        """获取或创建 Orchestrator 实例。"""
        if self._orchestrator is None:
            self._orchestrator = self._build_orchestrator()
        return self._orchestrator

    def run_orchestrated(self, user_input: str) -> Any:
        """通过 Orchestrator 的 AI 多轮循环处理用户输入。

        Orchestrator 自动执行工具调用循环，直到返回需要
        GUI 响应的动作（ask_user / present_draft / finalize / fail）。

        Args:
            user_input: 用户自然语言消息

        Returns:
            AgentStep — 需要 GUI 处理的动作
        """
        orch = self.get_orchestrator()
        return orch.run_turn(user_input)

    def start(self, user_input: str) -> SchemaForgeTurnResult:
        request = parse_design_request(user_input, skip_ai_parse=self._skip_ai_parse)
        self._request = request
        self._parameter_overrides = {}

        if request.part_number:
            device = self._resolver.resolve(request.part_number)
            if device is None:
                return SchemaForgeTurnResult(
                    status="needs_asset",
                    message=(
                        f"本地器件库里没有精确型号 {request.part_number}，"
                        "请上传 datasheet PDF 或引脚图片继续导入。"
                    ),
                    request=request,
                    missing_part_number=request.part_number,
                )
            return self._build_from_device(device, message=f"已精确命中 {device.part_number}。")

        # --- 多模块分解：用 Planner 解析需求，逐模块匹配器件 ---
        return self._start_multi_module(user_input, request)

    def _start_multi_module(
        self, user_input: str, request: UserDesignRequest,
    ) -> SchemaForgeTurnResult:
        """无精确型号时，用 Planner 分解为多模块，逐一检索匹配器件。"""
        from schemaforge.design.planner import DesignPlanner
        from schemaforge.design.retrieval import DeviceRetriever

        planner = DesignPlanner()
        plan = planner.plan(user_input)

        if not plan.modules:
            return SchemaForgeTurnResult(
                status="error",
                message="无法从需求中识别出任何电路模块，请描述更具体的电路需求。",
                request=request,
            )

        retriever = DeviceRetriever(self._store)
        primary_device = None
        aux_params: dict[str, str] = {}
        module_info: list[str] = []

        for mod in plan.modules:
            # 如果模块指定了料号，优先精确匹配
            if mod.part_number:
                device = self._resolver.resolve(mod.part_number)
                if device is None:
                    return SchemaForgeTurnResult(
                        status="needs_asset",
                        message=(
                            f"本地器件库里没有精确型号 {mod.part_number}，"
                            "请上传 datasheet PDF 或引脚图片继续导入。"
                        ),
                        request=request,
                        missing_part_number=mod.part_number,
                    )
                if primary_device is None:
                    primary_device = device
                    # 合并模块参数到 request
                    if mod.parameters.get("v_in") and not request.v_in:
                        request = apply_request_updates(request, v_in=mod.parameters["v_in"])
                    if mod.parameters.get("v_out") and not request.v_out:
                        request = apply_request_updates(request, v_out=mod.parameters["v_out"])
                    self._request = request
                module_info.append(f"{mod.role}: {device.part_number}")
                continue

            # 无料号：按分类检索
            dev_req = mod.to_device_requirement()
            matches = retriever.search_by_requirement(dev_req)
            if matches:
                device = matches[0].device
                if primary_device is None and mod.category not in ("led", "voltage_divider", "rc_filter"):
                    primary_device = device
                    if mod.parameters.get("v_in") and not request.v_in:
                        request = apply_request_updates(request, v_in=mod.parameters["v_in"])
                    if mod.parameters.get("v_out") and not request.v_out:
                        request = apply_request_updates(request, v_out=mod.parameters["v_out"])
                    self._request = request
                module_info.append(f"{mod.role}: {device.part_number}")
            else:
                module_info.append(f"{mod.role}: 未找到匹配器件")

            # 辅助模块参数（LED 等）合并到 request/overrides
            if mod.category == "led":
                request = apply_request_updates(
                    request,
                    wants_led=True,
                    led_color=mod.parameters.get("led_color", "green"),
                )
                self._request = request
            elif mod.category in ("voltage_divider", "rc_filter"):
                aux_params.update(mod.parameters)

        if primary_device is None:
            return SchemaForgeTurnResult(
                status="error",
                message="器件库中没有匹配的器件，请指定具体型号或上传 datasheet。",
                request=request,
            )

        self._parameter_overrides.update(aux_params)
        modules_desc = "、".join(module_info)
        return self._build_from_device(
            primary_device,
            message=f"已按规划匹配器件（{modules_desc}）。",
        )

    def ingest_asset(self, filepath: str) -> SchemaForgeTurnResult:
        if self._request is None:
            return SchemaForgeTurnResult(
                status="error",
                message="请先发起设计请求，再上传资料。",
            )

        path = Path(filepath)
        hint = self._request.part_number
        if path.suffix.lower() == ".pdf":
            result = extract_from_pdf(str(path), hint=hint)
        else:
            result = extract_from_image(str(path), hint=hint)

        if not result.success or result.draft is None:
            return SchemaForgeTurnResult(
                status="error",
                message=result.error_message or "资料解析失败。",
                request=self._request,
            )

        draft = result.draft
        if hint and not draft.part_number:
            draft = apply_user_answers(draft, {"part_number": hint})

        preview_base64 = ""
        if draft.pins:
            symbol = build_symbol(
                part_number=draft.part_number or hint or "UNKNOWN",
                pins_data=[
                    {
                        "name": pin.name,
                        "number": pin.number,
                        "type": pin.pin_type,
                        "description": pin.description,
                    }
                    for pin in draft.pins
                ],
                category=draft.category,
                package=draft.package,
            )
            preview_base64 = base64.b64encode(
                TopologyRenderer.render_symbol_preview(symbol, label=draft.part_number)
            ).decode("ascii")

        self._pending_draft = draft
        self._pending_app_circuit = result.application_circuit
        preview = ImportPreview(
            draft=draft,
            symbol_preview_base64=preview_base64,
            questions=result.questions,
        )
        return SchemaForgeTurnResult(
            status="needs_confirmation",
            message="资料已解析，请确认导入信息后继续设计。",
            request=self._request,
            import_preview=preview,
        )

    def confirm_import(
        self,
        answers: dict[str, object] | None = None,
    ) -> SchemaForgeTurnResult:
        if self._pending_draft is None or self._request is None:
            return SchemaForgeTurnResult(
                status="error",
                message="当前没有待确认的导入任务。",
                request=self._request,
            )

        draft = self._apply_draft_answers(self._pending_draft, answers or {})

        # --- 安全落库流程: 校验 → 生成 symbol → 试转换(不写盘) → 写盘 ---

        # 1. 校验草稿（不跳过）
        validation = self._service.validate_only(draft)
        if not validation.is_valid:
            return SchemaForgeTurnResult(
                status="needs_confirmation",
                message="草稿校验未通过，请补充信息: "
                + "; ".join(e.message for e in validation.errors),
                request=self._request,
                import_preview=ImportPreview(
                    draft=draft,
                    symbol_preview_base64="",
                    questions=[
                        {"field": e.field_path, "message": e.message, "suggestion": e.suggestion}
                        for e in validation.errors
                    ],
                ),
            )

        # 2. 生成 symbol（在落库前）
        symbol = None
        if draft.pins:
            symbol = build_symbol(
                part_number=draft.part_number,
                pins_data=[
                    {
                        "name": pin.name,
                        "number": pin.number,
                        "type": pin.pin_type,
                        "description": pin.description,
                    }
                    for pin in draft.pins
                ],
                category=draft.category,
                package=draft.package,
            )

        # 3. 试转换（不写盘），确保 DeviceModel 构造成功
        dry_run = self._service.add_device_from_draft(
            draft,
            force=True,
            skip_dedupe=True,
            persist=False,
        )
        if not dry_run.success or dry_run.device is None:
            return SchemaForgeTurnResult(
                status="error",
                message=dry_run.error_message or "器件模型转换失败，无法入库。",
                request=self._request,
            )

        # 4. 一切通过，写盘
        add_result = self._service.add_device_from_draft(
            draft,
            force=True,
            skip_dedupe=True,
        )
        if not add_result.success or add_result.device is None:
            return SchemaForgeTurnResult(
                status="error",
                message=add_result.error_message or "器件入库失败。",
                request=self._request,
            )

        # 5. 附加 symbol
        if symbol is not None:
            self._service.update_device_symbol(draft.part_number, symbol)

        # 6. 附加 datasheet 提取的应用电路 recipe
        if self._pending_app_circuit:
            from schemaforge.ingest.datasheet_extractor import (
                build_recipe_from_application_circuit,
            )

            recipe = build_recipe_from_application_circuit(
                self._pending_app_circuit,
                part_number=draft.part_number,
            )
            if recipe is not None:
                self._service.update_device_recipe(draft.part_number, recipe)

        device = self._service.get(draft.part_number)
        if device is None:
            return SchemaForgeTurnResult(
                status="error",
                message="导入成功但重新加载器件失败。",
                request=self._request,
            )

        self._pending_draft = None
        self._pending_app_circuit = {}
        return self._build_from_device(device, message=f"已导入并应用 {device.part_number}。")

    def revise(self, user_input: str) -> SchemaForgeTurnResult:
        if self._request is None or self._device is None:
            return SchemaForgeTurnResult(
                status="error",
                message="当前还没有可修改的设计。",
            )

        revision = parse_revision_request_v2(user_input, skip_ai_parse=self._skip_ai_parse)

        # 检查是否有任何可执行的修改
        has_changes = bool(
            revision.param_updates
            or revision.request_updates
            or revision.replace_device
            or revision.structural_ops
        )
        if not has_changes:
            return SchemaForgeTurnResult(
                status="error",
                message="暂时无法理解这条修改请求，请再具体一点。",
                request=self._request,
            )

        # 1. 器件替换: 尝试从库中解析新器件
        if revision.replace_device:
            new_device = self._resolver.resolve(revision.replace_device)
            if new_device is None:
                return SchemaForgeTurnResult(
                    status="needs_asset",
                    message=(
                        f"本地器件库里没有型号 {revision.replace_device}，"
                        "请上传 datasheet PDF 或引脚图片继续导入。"
                    ),
                    request=self._request,
                    missing_part_number=revision.replace_device,
                )
            # 替换器件并重建设计
            self._parameter_overrides.clear()  # 器件变了，旧参数覆盖不再适用
            if revision.request_updates:
                self._request = apply_request_updates(
                    self._request, **revision.request_updates,
                )
            return self._build_from_device(
                new_device,
                message=f"已将器件替换为 {new_device.part_number}，重新生成设计。",
            )

        # 2. 应用请求级更新
        if revision.request_updates:
            self._request = apply_request_updates(
                self._request, **revision.request_updates,
            )

        # 3. 应用参数级覆盖
        self._parameter_overrides.update(revision.param_updates)

        # 4. 执行结构化操作（add_module / remove_module）
        structural_warnings: list[str] = []
        for op in revision.structural_ops:
            op_type = str(op.get("op_type", ""))
            if op_type == "add_module":
                category = str(op.get("category", ""))
                desc = str(op.get("description", category))
                warning = self._execute_add_module(category, desc)
                if warning:
                    structural_warnings.append(warning)
            elif op_type == "remove_module":
                target = str(op.get("target", ""))
                warning = self._execute_remove_module(target)
                if warning:
                    structural_warnings.append(warning)

        # 5. 收集修改说明
        change_parts: list[str] = []
        if revision.param_updates:
            change_parts.append(
                "参数: " + ", ".join(
                    f"{k}={v}" for k, v in revision.param_updates.items()
                )
            )
        if revision.request_updates:
            change_parts.append(
                "约束: " + ", ".join(
                    f"{k}={v}" for k, v in revision.request_updates.items()
                )
            )
        if revision.structural_ops:
            executed_ops = [
                str(op.get("op_type", "")) + ":" + str(
                    op.get("category", op.get("target", ""))
                )
                for op in revision.structural_ops
            ]
            change_parts.append("结构: " + ", ".join(executed_ops))

        summary = "；".join(change_parts) if change_parts else "局部修改"
        result = self._build_from_device(
            self._device,
            message=f"已在现有设计上完成修改（{summary}）。",
        )
        result.warnings.extend(structural_warnings)
        return result

    # ----------------------------------------------------------
    # 结构化操作执行
    # ----------------------------------------------------------

    _MODULE_CATEGORY_TO_REQUEST: dict[str, dict[str, object]] = {
        "led_indicator": {"wants_led": True, "led_color": "green"},
    }

    _MODULE_CATEGORY_TO_PARAMS: dict[str, dict[str, str]] = {
        "decoupling": {"c_decoupling": "100nF"},
        "rc_filter": {"rc_filter_r": "10kΩ", "rc_filter_c": "100nF"},
        "voltage_divider": {"divider_r1": "10kΩ", "divider_r2": "10kΩ"},
    }

    _REMOVE_TARGET_MAP: dict[str, str] = {
        "led": "led_indicator",
        "指示灯": "led_indicator",
        "led模块": "led_indicator",
        "指示灯模块": "led_indicator",
        "滤波器": "rc_filter",
        "分压器": "voltage_divider",
        "去耦电容": "decoupling",
        "稳压器": "ldo",
        "ldo": "ldo",
    }

    def _execute_add_module(self, category: str, description: str) -> str:
        """执行 add_module 结构化操作。

        将模块类型映射到 request 更新或 parameter 覆盖。
        返回空字符串表示成功，非空字符串为警告信息。
        """
        # LED 直接走 request 级别
        if category in self._MODULE_CATEGORY_TO_REQUEST:
            updates = self._MODULE_CATEGORY_TO_REQUEST[category]
            if self._request is not None:
                self._request = apply_request_updates(self._request, **updates)
            return ""

        # 其他辅助模块走参数覆盖
        if category in self._MODULE_CATEGORY_TO_PARAMS:
            params = self._MODULE_CATEGORY_TO_PARAMS[category]
            self._parameter_overrides.update(params)
            return ""

        return f"不支持自动添加 '{description}' 类型的模块，需手动配置。"

    def _execute_remove_module(self, target: str) -> str:
        """执行 remove_module 结构化操作。

        返回空字符串表示成功，非空字符串为警告信息。
        """
        category = self._REMOVE_TARGET_MAP.get(target.lower(), target.lower())

        # LED 移除
        if category == "led_indicator":
            if self._request is not None:
                self._request = apply_request_updates(
                    self._request, wants_led=False,
                )
            # 清理相关参数
            for key in ["power_led", "led_color", "led_current_ma"]:
                self._parameter_overrides.pop(key, None)
            return ""

        # 辅助模块参数移除
        if category in self._MODULE_CATEGORY_TO_PARAMS:
            for key in self._MODULE_CATEGORY_TO_PARAMS[category]:
                self._parameter_overrides.pop(key, None)
            return ""

        return f"未找到可移除的 '{target}' 模块。"

    def _build_from_device(self, device: DeviceModel, *, message: str) -> SchemaForgeTurnResult:
        assert self._request is not None
        bundle = self._synthesizer.build_bundle(
            device,
            self._request,
            parameter_overrides=self._parameter_overrides,
        )
        self._device = bundle.device
        self._bundle = bundle
        # 不将计算后的 enriched device 写回 store，避免 recipe 缓存污染。
        # 器件 JSON 只在 confirm_import() 新增器件时写入。

        # --- 工程审查 (42 条规则) ---
        review_warnings: list[str] = []
        try:
            review_engine = DesignReviewEngine()
            review_input = ModuleReviewInput(
                role="main",
                category=device.category or "",
                device=device,
                parameters=bundle.parameters,
            )
            review_result = review_engine.review_module(review_input)
            for issue in review_result.issues:
                severity = issue.severity.value if hasattr(issue.severity, "value") else str(issue.severity)
                if severity == "blocking":
                    review_warnings.append(f"[阻断] {issue.message}")
                elif severity == "warning":
                    review_warnings.append(f"[警告] {issue.message}")
        except Exception:
            pass  # 审查失败不影响设计输出

        # --- 多器件提示：告知用户检测到但未处理的额外器件 ---
        if self._request.additional_devices:
            extra_parts = []
            for dev_info in self._request.additional_devices:
                pn = dev_info.get("part_number", "")
                role = dev_info.get("role", "")
                if pn:
                    extra_parts.append(f"{pn}({role})" if role else pn)
            if extra_parts:
                review_warnings.append(
                    f"[提示] 检测到额外器件尚未设计: {', '.join(extra_parts)}。"
                    "请分步设计每个模块，或使用多轮对话逐一添加。"
                )
        if self._request.design_notes:
            review_warnings.append(
                f"[提示] 设计备注: {self._request.design_notes}"
            )

        return SchemaForgeTurnResult(
            status="generated",
            message=message,
            request=self._request,
            bundle=bundle,
            warnings=review_warnings,
        )

    def _apply_draft_answers(
        self,
        draft: DeviceDraft,
        answers: dict[str, object],
    ) -> DeviceDraft:
        simple_answers: dict[str, str] = {}
        for key in [
            "part_number",
            "manufacturer",
            "description",
            "category",
            "package",
            "datasheet_url",
        ]:
            val = answers.get(key)
            if isinstance(val, str):
                simple_answers[key] = val

        updated = apply_user_answers(draft, simple_answers)
        updates: dict[str, object] = {}

        if "pins" in answers and isinstance(answers["pins"], list):
            updates["pins"] = [
                PinDraft(
                    name=str(item.get("name", "")),
                    number=str(item.get("number", "")),
                    pin_type=str(item.get("type", item.get("pin_type", "passive"))),
                    description=str(item.get("description", "")),
                )
                for item in answers["pins"]
                if isinstance(item, dict)
            ]
            updates["pin_count"] = len(updates["pins"])

        if "specs" in answers and isinstance(answers["specs"], dict):
            updates["specs"] = {
                str(key): str(value) for key, value in answers["specs"].items()
            }

        if "aliases" in answers and isinstance(answers["aliases"], list):
            updates["aliases"] = [str(item) for item in answers["aliases"]]

        if updates:
            updated = updated.model_copy(update=updates)
        return updated
