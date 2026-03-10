"""系统级设计会话 (T091-T096)。

编排多器件设计的完整生命周期：
  意图解析 → 器件解析 → 连接规则 → 参数综合 → 实例收集 → 渲染 → 导出

支持：
- 全新设计（start）
- 修订（revise）
- 替换模块（replace_module）
- 增删模块（add_module / remove_module）
- AI / 非 AI 两种模式（skip_ai_parse 控制）
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from schemaforge.ingest.datasheet_extractor import (
    apply_user_answers,
    extract_from_image,
    extract_from_pdf,
)
from schemaforge.library.service import LibraryService
from schemaforge.library.store import ComponentStore
from schemaforge.library.symbol_builder import build_symbol
from schemaforge.library.validator import DeviceDraft, PinDraft
from schemaforge.system.ai_protocol import parse_system_intent, regex_fallback_parse
from schemaforge.system.connection_rules import resolve_all_connections
from schemaforge.system.export_bom import (
    export_system_bom_csv,
    export_system_bom_markdown,
)
from schemaforge.system.export_spice import export_system_spice
from schemaforge.system.instances import (
    allocate_global_references,
    create_component_instances,
    stabilize_references_after_revision,
)
from schemaforge.system.models import (
    ConnectionIntent,
    ConnectionSemantic,
    ModuleInstance,
    ModuleIntent,
    ModuleStatus,
    RenderMetadata,
    SignalType,
    SystemBundle,
    SystemDesignIR,
    SystemDesignRequest,
)
from schemaforge.system.layout import SystemLayoutSpec, create_default_layout
from schemaforge.system.rendering import render_system_svg_with_metadata
from schemaforge.system.resolver import (
    instantiate_module_from_device,
    resolve_part_candidates,
)
from schemaforge.system.synthesis import (
    recompute_dependent_modules,
    synthesize_all_modules,
)

logger = logging.getLogger(__name__)

_REPLACE_PART_RE = re.compile(
    r"(?:把|将)?\s*([A-Za-z][A-Za-z0-9_.+-]*)\s*(?:换成|替换成|替换为|改成)\s*([A-Za-z][A-Za-z0-9_.+-]*)",
    re.IGNORECASE,
)
_REVISE_VOLTAGE_RE = re.compile(
    r"(?:把|将)?\s*([0-9]+(?:\.[0-9]+)?)\s*V\s*(?:改成|改为|换成)\s*([0-9]+(?:\.[0-9]+)?)\s*V",
    re.IGNORECASE,
)
_REVISE_LED_COLOR_RE = re.compile(
    r"(?:把|将)?\s*(?:一颗\s*)?(?:led|指示灯|led灯)\s*(?:改成|改为|换成)\s*(红色|绿色|蓝色|白色|黄色|red|green|blue|white|yellow)",
    re.IGNORECASE,
)
_TARGETED_LED_COLOR_RE = re.compile(
    r"(?:把|将)?\s*([A-Za-z][A-Za-z0-9_.+-]*|LED|led|指示灯)\s*(?:改成|改为|换成)\s*(红色|绿色|蓝色|白色|黄色|red|green|blue|white|yellow)",
    re.IGNORECASE,
)
_REMOVE_MODULE_RE = re.compile(
    r"(?:去掉|删除|移除)\s*(?:一个|一颗|这个|这颗)?\s*([A-Za-z][A-Za-z0-9_.+-]*|LED|led|指示灯|稳压器|LDO|ldo|降压|Buck|buck|MCU|mcu|主控)",
    re.IGNORECASE,
)
_ADD_LED_RE = re.compile(
    r"(?:再加|添加|增加)\s*(?:一个|一颗)?\s*(红色|绿色|蓝色|白色|黄色|red|green|blue|white|yellow)?\s*(?:LED|led|指示灯|led灯)",
    re.IGNORECASE,
)
_GPIO_PIN_RE = re.compile(r"(?<![A-Za-z0-9])(P[A-Z]\d+)(?![A-Za-z0-9])", re.IGNORECASE)
_ADD_DOWNSTREAM_MODULE_RE = re.compile(
    r"(?:再加|添加|增加)\s*(?:一个|一颗)?\s*([A-Za-z][A-Za-z0-9_.+-]*)\s*(?:把|将)?\s*([0-9]+(?:\.[0-9]+)?)V\s*(?:降到|稳压到|转换到|到)\s*([0-9]+(?:\.[0-9]+)?)V",
    re.IGNORECASE,
)
_TARGETED_VOUT_RE = re.compile(
    r"(?:把|将)?\s*([A-Za-z][A-Za-z0-9_.+-]*|LDO|ldo|Buck|buck|稳压器|降压)\s*(?:输出)?\s*(?:改成|改为|换成)\s*([0-9]+(?:\.[0-9]+)?)V",
    re.IGNORECASE,
)
_LED_COLOR_ALIASES = {
    "红色": "red",
    "绿色": "green",
    "蓝色": "blue",
    "白色": "white",
    "黄色": "yellow",
    "red": "red",
    "green": "green",
    "blue": "blue",
    "white": "white",
    "yellow": "yellow",
}
_MODULE_DESCRIPTOR_ALIASES = {
    "led": "led",
    "指示灯": "led",
    "ldo": "ldo",
    "稳压器": "ldo",
    "buck": "buck",
    "降压": "buck",
    "mcu": "mcu",
    "主控": "mcu",
}


def _normalize_electrical_value(value: str) -> str:
    return value.strip().lower().removesuffix("v")


def _infer_led_color_from_text(text: str) -> str:
    lower_text = text.lower()
    for token, color in _LED_COLOR_ALIASES.items():
        if token in lower_text:
            return color
    return "green"


def _design_revision_context(ir: SystemDesignIR) -> str:
    module_lines: list[str] = []
    for module_id, instance in sorted(ir.module_instances.items()):
        device = getattr(instance, "device", None)
        part_number = getattr(device, "part_number", module_id)
        params = []
        for key in ("v_in", "v_out", "gpio_pin", "led_color"):
            value = instance.parameters.get(key, "")
            if value:
                params.append(f"{key}={value}")
        param_text = f" ({', '.join(params)})" if params else ""
        module_lines.append(
            f"- {module_id}: {part_number} [{instance.resolved_category}]{param_text}"
        )

    connection_lines = [
        (
            f"- {conn.src_port.module_id}.{conn.src_port.pin_name} -> "
            f"{conn.dst_port.module_id}.{conn.dst_port.pin_name} [{conn.rule_id}]"
        )
        for conn in ir.connections
    ]
    if not connection_lines:
        connection_lines = ["- (none)"]

    return (
        "当前设计摘要:\n"
        + "模块:\n"
        + "\n".join(module_lines or ["- (none)"])
        + "\n连接:\n"
        + "\n".join(connection_lines)
    )


def _infer_revision_text_from_image(
    base64_png: str,
    design_context: str,
) -> tuple[str, list[str]]:
    from schemaforge.ai.client import DEFAULT_MODEL, _extract_json, get_client

    prompt = (
        "你是电子原理图修改意图提取器。"
        "用户已经有一版设计，现在又粘贴了一张图片作为修改反馈。"
        "请结合当前设计摘要，从图片中提取最可能的'可执行修改指令'。\n\n"
        f"{design_context}\n\n"
        "严格输出 JSON:\n"
        "{\n"
        '  "revision_text": "一句可直接交给电路设计会话的中文修改指令",\n'
        '  "confidence": 0.0,\n'
        '  "warnings": ["如图片信息不足、存在歧义，在这里说明"]\n'
        "}\n\n"
        "规则:\n"
        "1. 只提取图片中明确表达的修改，不要编造新需求。\n"
        "2. 如果图片没有明确修改意图，revision_text 设为空字符串。\n"
        "3. 不要输出解释性段落，只输出 JSON。"
    )

    client = get_client()
    response = client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{base64_png}"},
                    },
                ],
            },
        ],
        temperature=0.1,
        max_tokens=1024,
    )
    raw_text = response.choices[0].message.content or ""
    parsed = _extract_json(raw_text) or {}
    revision_text = str(parsed.get("revision_text", "")).strip()
    warnings_raw = parsed.get("warnings", [])
    warnings = [str(item) for item in warnings_raw] if isinstance(warnings_raw, list) else []
    if not revision_text and raw_text.strip().startswith("{") is False:
        warnings.append("图片修订解析未返回结构化 revision_text")
    return revision_text, warnings


# ============================================================
# T091: SystemDesignResult
# ============================================================


@dataclass
class SystemDesignResult:
    """系统级设计结果。"""

    status: str  # "generated" / "needs_asset" / "partial" / "error"
    message: str
    bundle: SystemBundle | None = None
    missing_modules: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ============================================================
# T092-T096: SystemDesignSession
# ============================================================


class SystemDesignSession:
    """系统级设计会话。

    编排多器件设计从意图到 SVG 的完整管线。
    """

    def __init__(
        self,
        store_dir: Path | str,
        skip_ai_parse: bool | None = None,
        enable_visual_review: bool = False,
        ai_svg_mode: bool = False,
    ) -> None:
        self._store = ComponentStore(Path(store_dir))
        self._skip_ai_parse = skip_ai_parse
        self._enable_visual_review = enable_visual_review
        self._ai_svg_mode = ai_svg_mode
        self._ir: SystemDesignIR | None = None
        self._bundle: SystemBundle | None = None
        self._layout_spec: SystemLayoutSpec | None = None
        self._prev_component_instances: list[object] = []
        self._pending_draft: DeviceDraft | None = None
        self._pending_app_circuit: dict[str, object] = {}

    @property
    def ir(self) -> SystemDesignIR | None:
        return self._ir

    @property
    def bundle(self) -> SystemBundle | None:
        return self._bundle

    @property
    def visual_review_enabled(self) -> bool:
        return self._enable_visual_review

    @property
    def ai_svg_mode(self) -> bool:
        return self._ai_svg_mode

    @ai_svg_mode.setter
    def ai_svg_mode(self, value: bool) -> None:
        self._ai_svg_mode = value

    # ----------------------------------------------------------
    # T093: start — 全新设计
    # ----------------------------------------------------------

    def start(self, user_input: str) -> SystemDesignResult:
        """完整管线：解析 → 解析器件 → 连接 → 综合 → 渲染 → 导出。"""
        if self._skip_ai_parse:
            request = regex_fallback_parse(user_input)
        else:
            request = parse_system_intent(user_input)
        return self._run_pipeline(request)

    def start_from_request(
        self, request: SystemDesignRequest,
    ) -> SystemDesignResult:
        """跳过 AI 解析，直接从 SystemDesignRequest 运行管线。

        用于测试和程序化调用。

        Args:
            request: 已构建的系统设计请求

        Returns:
            SystemDesignResult
        """
        return self._run_pipeline(request)

    # ----------------------------------------------------------
    # T093 核心: _run_pipeline
    # ----------------------------------------------------------

    def _run_pipeline(self, request: SystemDesignRequest) -> SystemDesignResult:
        """执行完整设计管线。

        Steps:
        1. 为每个 ModuleIntent 解析器件 → 实例化模块
        2. resolve_all_connections → 连接 + 网络
        3. synthesize_all_modules → 参数 + 外围元件
        4. create_component_instances + allocate_global_references
        5. render_system_svg → SVG
        6. export_system_bom_markdown → BOM
        7. export_system_spice → SPICE
        8. 组装 SystemBundle
        """
        warnings: list[str] = []
        missing_modules: list[str] = []

        # --- Step 1: 模块解析 ---
        module_instances: dict[str, ModuleInstance] = {}
        for intent in request.modules:
            instance = self._resolve_module(intent)
            module_instances[intent.intent_id] = instance
            if instance.status == ModuleStatus.NEEDS_ASSET:
                # 用器件型号（用户可识别）而非内部 module_id
                display_name = instance.missing_part_number or intent.intent_id
                missing_modules.append(display_name)
                warnings.append(
                    f"模块 '{intent.intent_id}' 缺少器件 "
                    f"'{instance.missing_part_number}', 标记为 NEEDS_ASSET"
                )

        # 构建 IR
        ir = SystemDesignIR(
            request=request,
            module_instances=module_instances,
        )

        # --- Step 2: 连接解析 ---
        resolved_ids = {
            mid for mid, m in module_instances.items()
            if m.status in (ModuleStatus.RESOLVED, ModuleStatus.SYNTHESIZED)
        }
        # 过滤连接意图：仅保留两端模块都已解析的
        valid_intents = [
            c for c in request.connections
            if c.src_module_intent in resolved_ids
            and (c.dst_module_intent or "") in resolved_ids
        ]
        skipped_intents = [
            c for c in request.connections if c not in valid_intents
        ]
        for c in skipped_intents:
            warnings.append(
                f"连接 '{c.connection_id}' 跳过: 端模块未解析"
            )

        connections, nets, unresolved = resolve_all_connections(
            module_instances, valid_intents,
        )
        ir.connections = connections
        ir.nets = nets
        ir.unresolved_items.extend(unresolved)

        # --- Step 3: 综合 ---
        ir = synthesize_all_modules(ir)

        # --- Step 4: 实例收集 + 编号分配 ---
        comp_instances = create_component_instances(ir)
        if self._prev_component_instances:
            comp_instances = stabilize_references_after_revision(
                self._prev_component_instances, comp_instances,  # type: ignore[arg-type]
            )
        else:
            comp_instances = allocate_global_references(comp_instances)
        self._prev_component_instances = comp_instances  # type: ignore[assignment]

        # --- Step 5: 布局 + 渲染 ---
        self._layout_spec = create_default_layout(ir)
        svg_path = ""
        render_metadata = None
        try:
            svg_path, render_metadata = render_system_svg_with_metadata(
                ir, layout_spec=self._layout_spec,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"SVG 渲染失败: {exc}")
            logger.warning("SVG 渲染异常: %s", exc)

        # --- Step 6: BOM ---
        bom_text = ""
        bom_csv = ""
        try:
            bom_text = export_system_bom_markdown(comp_instances, ir)
            bom_csv = export_system_bom_csv(comp_instances)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"BOM 导出失败: {exc}")

        # --- Step 7: SPICE ---
        spice_text = ""
        try:
            spice_text = export_system_spice(ir, comp_instances)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"SPICE 导出失败: {exc}")

        # --- Step 8: 组装 Bundle ---
        bundle = SystemBundle(
            design_ir=ir,
            svg_path=svg_path,
            bom_text=bom_text,
            bom_csv=bom_csv,
            spice_text=spice_text,
            render_metadata=render_metadata or RenderMetadata(),
        )

        # --- Step 8.5: 可选视觉审稿闭环 ---
        if self._enable_visual_review and svg_path and ir.get_resolved_modules():
            try:
                from schemaforge.visual_review.loop import run_visual_review_loop

                bundle, _trace = run_visual_review_loop(ir, bundle)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"视觉审稿失败: {exc}")

        self._ir = ir
        self._bundle = bundle

        # 判断结果状态
        if missing_modules and not ir.get_resolved_modules():
            status = "needs_asset"
            message = (
                "所有模块均缺少器件，请先补录后继续设计: "
                + ", ".join(missing_modules)
            )
        elif missing_modules:
            status = "partial"
            message = (
                f"部分设计已完成，{len(missing_modules)} 个模块缺少器件: "
                + ", ".join(missing_modules)
            )
        else:
            status = "generated"
            message = (
                f"设计已生成: {len(ir.get_resolved_modules())} 个模块, "
                f"{len(ir.connections)} 条连接"
            )

        ir.warnings.extend(warnings)

        return SystemDesignResult(
            status=status,
            message=message,
            bundle=bundle,
            missing_modules=missing_modules,
            warnings=warnings,
        )

    # ----------------------------------------------------------
    # T094: revise
    # ----------------------------------------------------------

    def revise(self, user_input: str) -> SystemDesignResult:
        """修订已有设计。

        解析修订意图，识别目标模块，应用变更，重新综合。
        """
        if self._ir is None:
            return SystemDesignResult(
                status="error",
                message="当前没有可修改的设计，请先调用 start()。",
            )

        direct_result = self._apply_direct_revision(user_input)
        if direct_result is not None:
            return direct_result

        # 解析修订文本
        if self._skip_ai_parse:
            revision_request = regex_fallback_parse(user_input)
        else:
            revision_request = parse_system_intent(user_input)

        # 将修订意图中的模块参数合并到现有 IR
        changed_ids: set[str] = set()

        for intent in revision_request.modules:
            existing = self._ir.get_module(intent.intent_id)
            if existing is not None:
                # 更新参数
                if intent.electrical_targets:
                    existing.parameters.update(intent.electrical_targets)
                    changed_ids.add(intent.intent_id)
                    existing.status = ModuleStatus.RESOLVED
                    request_module = next(
                        (
                            module_intent
                            for module_intent in self._ir.request.modules
                            if module_intent.intent_id == intent.intent_id
                        ),
                        None,
                    )
                    if request_module is not None:
                        request_module.electrical_targets.update(intent.electrical_targets)
            else:
                # 新模块 → 走 add 路径
                instance = self._resolve_module(intent)
                self._ir.module_instances[intent.intent_id] = instance
                self._ir.request.modules.append(intent)
                changed_ids.add(intent.intent_id)

        # 合并新连接
        if revision_request.connections:
            for connection in revision_request.connections:
                self._ir.request.connections.append(connection)

        # 重新综合受影响子图
        if changed_ids or revision_request.connections:
            self._rebuild_connections_from_request()
        if changed_ids:
            self._ir = recompute_dependent_modules(self._ir, changed_ids)
        if changed_ids or revision_request.connections:
            self._ir = synthesize_all_modules(self._ir)

        # 重新生成输出
        return self._regenerate_outputs("修订完成")

    def _apply_direct_revision(self, user_input: str) -> SystemDesignResult | None:
        if self._ir is None:
            return None

        lower_text = user_input.lower()
        gpio_match = _GPIO_PIN_RE.search(user_input)
        has_led_term = any(token in lower_text for token in ["led", "指示灯", "led灯"])
        if gpio_match is not None and has_led_term:
            gpio_pin = gpio_match.group(1).upper()
            if any(token in user_input for token in ["再加", "添加", "增加"]):
                led_color = _infer_led_color_from_text(user_input)
                return self._add_gpio_led_module(led_color, gpio_pin)
            if any(token in user_input for token in ["改到", "改成", "改为", "换到", "换成", "换为"]):
                descriptor_match = re.search(r"(led\d+|LED|led|指示灯)", user_input, re.IGNORECASE)
                descriptor = descriptor_match.group(1) if descriptor_match is not None else "LED"
                return self._retarget_led_gpio(descriptor, gpio_pin)

        downstream_match = _ADD_DOWNSTREAM_MODULE_RE.search(user_input)
        if downstream_match is not None:
            return self._add_downstream_power_module(
                downstream_match.group(1),
                downstream_match.group(2),
                downstream_match.group(3),
            )

        targeted_led_match = _TARGETED_LED_COLOR_RE.search(user_input)
        if targeted_led_match is not None:
            matched_ids = self._find_modules_by_descriptor(targeted_led_match.group(1))
            if len(matched_ids) == 1:
                return self._apply_parameter_updates(
                    matched_ids[0],
                    {"led_color": _LED_COLOR_ALIASES[targeted_led_match.group(2).lower()]},
                    message=f"修订完成：{matched_ids[0]} 颜色已更新",
                )

        targeted_vout_match = _TARGETED_VOUT_RE.search(user_input)
        if targeted_vout_match is not None:
            matched_ids = self._find_modules_by_descriptor(targeted_vout_match.group(1))
            if len(matched_ids) == 1:
                return self._apply_parameter_updates(
                    matched_ids[0],
                    {"v_out": targeted_vout_match.group(2)},
                    message=f"修订完成：{matched_ids[0]} 输出已更新为 {targeted_vout_match.group(2)}V",
                )

        remove_match = _REMOVE_MODULE_RE.search(user_input)
        if remove_match is not None:
            matched_ids = self._find_modules_by_descriptor(remove_match.group(1))
            if len(matched_ids) == 1:
                return self.remove_module(matched_ids[0])

        add_led_match = _ADD_LED_RE.search(user_input)
        if add_led_match is not None:
            color = _infer_led_color_from_text(user_input)
            return self._add_power_led_module(color)

        replace_match = _REPLACE_PART_RE.search(user_input)
        if replace_match is not None:
            old_part = replace_match.group(1).upper()
            new_part = replace_match.group(2).upper()
            matched_ids = [
                module_id
                for module_id, instance in self._ir.module_instances.items()
                if getattr(instance.device, "part_number", "").upper() == old_part
            ]
            if len(matched_ids) == 1:
                return self.replace_module(matched_ids[0], new_part)

        led_match = _REVISE_LED_COLOR_RE.search(user_input)
        if led_match is not None:
            color = _LED_COLOR_ALIASES[led_match.group(1).lower()]
            led_ids = [
                module_id
                for module_id, instance in self._ir.module_instances.items()
                if instance.resolved_category == "led"
            ]
            if len(led_ids) == 1:
                return self._apply_parameter_updates(
                    led_ids[0],
                    {"led_color": color},
                    message=f"修订完成：LED 已改为 {color}",
                )

        voltage_match = _REVISE_VOLTAGE_RE.search(user_input)
        if voltage_match is not None:
            old_v = _normalize_electrical_value(voltage_match.group(1))
            new_v = voltage_match.group(2)
            matched_ids = [
                module_id
                for module_id, instance in self._ir.module_instances.items()
                if _normalize_electrical_value(instance.parameters.get("v_out", "")) == old_v
            ]
            if len(matched_ids) == 1:
                return self._apply_parameter_updates(
                    matched_ids[0],
                    {"v_out": new_v},
                    message=f"修订完成：{old_v}V 已改为 {new_v}V",
                )

        return None

    def _apply_parameter_updates(
        self,
        module_id: str,
        updates: dict[str, str],
        *,
        message: str,
    ) -> SystemDesignResult:
        if self._ir is None:
            return SystemDesignResult(
                status="error",
                message="当前没有可修改的设计，请先调用 start()。",
            )

        instance = self._ir.get_module(module_id)
        if instance is None:
            return SystemDesignResult(
                status="error",
                message=f"未找到可修改模块: {module_id}",
            )

        instance.parameters.update(updates)
        instance.status = ModuleStatus.RESOLVED
        request_module = next(
            (
                module_intent
                for module_intent in self._ir.request.modules
                if module_intent.intent_id == module_id
            ),
            None,
        )
        if request_module is not None:
            request_module.electrical_targets.update(updates)
        self._rebuild_connections_from_request()
        self._ir = recompute_dependent_modules(self._ir, {module_id})
        self._ir = synthesize_all_modules(self._ir)
        return self._regenerate_outputs(message)

    def _find_modules_by_descriptor(self, descriptor: str) -> list[str]:
        assert self._ir is not None
        normalized = descriptor.strip().lower()
        category = _MODULE_DESCRIPTOR_ALIASES.get(normalized, "")
        matched: list[str] = []
        for module_id, instance in self._ir.module_instances.items():
            if module_id.lower() == normalized:
                matched.append(module_id)
                continue
            part_number = getattr(instance.device, "part_number", "")
            if part_number.lower() == normalized:
                matched.append(module_id)
                continue
            if category and instance.resolved_category == category:
                matched.append(module_id)
        return matched

    def _next_module_id(self, category: str) -> str:
        assert self._ir is not None
        index = 1
        while f"{category}{index}" in self._ir.module_instances:
            index += 1
        return f"{category}{index}"

    def _merge_resolved_connection_batch(
        self,
        new_connections: list[object],
        new_nets: dict[str, object],
        new_unresolved: list[object],
    ) -> None:
        assert self._ir is not None
        self._ir.connections.extend(new_connections)
        for net_name, new_net in new_nets.items():
            existing_net = self._ir.nets.get(net_name)
            if existing_net is None:
                self._ir.nets[net_name] = new_net
                continue
            seen = {
                (member.module_id, member.pin_name)
                for member in existing_net.members
            }
            for member in new_net.members:
                key = (member.module_id, member.pin_name)
                if key not in seen:
                    existing_net.members.append(member)
                    seen.add(key)
            if not existing_net.voltage_domain:
                existing_net.voltage_domain = new_net.voltage_domain
        self._ir.unresolved_items.extend(new_unresolved)

    def _rebuild_connections_from_request(self) -> None:
        assert self._ir is not None
        for instance in self._ir.module_instances.values():
            instance.external_components = []

        resolved_ids = {
            module_id
            for module_id, instance in self._ir.module_instances.items()
            if instance.status in (ModuleStatus.RESOLVED, ModuleStatus.SYNTHESIZED)
        }
        valid_intents = [
            connection
            for connection in self._ir.request.connections
            if connection.src_module_intent in resolved_ids
            and (connection.dst_module_intent or "") in resolved_ids
        ]
        connections, nets, unresolved = resolve_all_connections(
            self._ir.module_instances,
            valid_intents,
        )
        self._ir.connections = connections
        self._ir.nets = nets
        self._ir.unresolved_items = list(unresolved)

    def _prune_orphan_nets(self) -> None:
        assert self._ir is not None
        self._ir.nets = {
            net_name: net
            for net_name, net in self._ir.nets.items()
            if net.is_global or len(net.members) >= 2
        }

    def _infer_module_supply_voltage(self, module_id: str, default: str = "3.3") -> str:
        assert self._ir is not None
        for connection in self._ir.connections:
            if (
                connection.rule_id == "RULE_POWER_SUPPLY"
                and connection.dst_port.module_id == module_id
            ):
                src = self._ir.module_instances.get(connection.src_port.module_id)
                if src is not None and src.parameters.get("v_out", ""):
                    return src.parameters["v_out"]
        return default

    def _select_unique_power_source_by_voltage(self, voltage: str) -> str | None:
        assert self._ir is not None
        normalized = _normalize_electrical_value(voltage)
        matched = [
            module_id
            for module_id, instance in self._ir.module_instances.items()
            if instance.resolved_category in {"ldo", "buck", "boost"}
            and _normalize_electrical_value(instance.parameters.get("v_out", "")) == normalized
        ]
        if len(matched) == 1:
            return matched[0]
        return None

    def _select_unique_module_by_category(self, category: str) -> str | None:
        assert self._ir is not None
        matched = [
            module_id
            for module_id, instance in self._ir.module_instances.items()
            if instance.resolved_category == category
        ]
        if len(matched) == 1:
            return matched[0]
        return None

    def _sync_led_gpio_connection(self, led_id: str, gpio_pin: str) -> SystemDesignResult:
        assert self._ir is not None
        source_id = next(
            (
                connection.src_module_intent
                for connection in self._ir.request.connections
                if connection.connection_semantics == ConnectionSemantic.GPIO_DRIVE
                and connection.dst_module_intent == led_id
            ),
            None,
        )
        if source_id is None:
            source_id = self._select_unique_module_by_category("mcu")
        if source_id is None:
            return SystemDesignResult(
                status="error",
                message="当前无法唯一确定用于驱动 LED 的 MCU，请显式指定模块。",
            )

        self._ir.request.connections = [
            connection
            for connection in self._ir.request.connections
            if not (
                connection.connection_semantics == ConnectionSemantic.GPIO_DRIVE
                and connection.dst_module_intent == led_id
            )
        ]
        self._ir.request.connections.append(
            ConnectionIntent(
                connection_id=f"rev_gpio_{gpio_pin.lower()}_{led_id}",
                src_module_intent=source_id,
                src_port_hint=gpio_pin,
                dst_module_intent=led_id,
                dst_port_hint="ANODE",
                signal_type=SignalType.GPIO,
                connection_semantics=ConnectionSemantic.GPIO_DRIVE,
            )
        )

        led_instance = self._ir.get_module(led_id)
        if led_instance is not None:
            led_instance.parameters["v_supply"] = self._infer_module_supply_voltage(
                source_id,
                default="3.3",
            )
            led_instance.parameters["gpio_pin"] = gpio_pin
            led_instance.status = ModuleStatus.RESOLVED

        request_module = next(
            (
                module_intent
                for module_intent in self._ir.request.modules
                if module_intent.intent_id == led_id
            ),
            None,
        )
        if request_module is not None:
            request_module.control_targets["gpio_pin"] = gpio_pin

        self._rebuild_connections_from_request()
        self._ir = synthesize_all_modules(self._ir)
        return self._regenerate_outputs(f"修订完成：{led_id} 已改为 {gpio_pin} 控制")

    def _retarget_led_gpio(self, descriptor: str, gpio_pin: str) -> SystemDesignResult:
        matched_ids = self._find_modules_by_descriptor(descriptor)
        if len(matched_ids) != 1:
            return SystemDesignResult(
                status="error",
                message="当前无法唯一确定要修改的 LED，请显式指定模块。",
            )
        return self._sync_led_gpio_connection(matched_ids[0], gpio_pin)

    def _add_power_led_module(self, led_color: str) -> SystemDesignResult:
        assert self._ir is not None
        supply_candidates = [
            (module_id, instance)
            for module_id, instance in self._ir.module_instances.items()
            if instance.resolved_category in {"ldo", "buck", "boost"}
            and instance.parameters.get("v_out", "")
        ]
        preferred_order = {"ldo": 0, "buck": 1, "boost": 2}
        supply_candidates.sort(
            key=lambda item: (preferred_order.get(item[1].resolved_category, 99), item[0])
        )
        if not supply_candidates:
            return SystemDesignResult(
                status="error",
                message="当前没有可供 LED 挂接的供电模块，请先完成电源链设计。",
            )

        source_id, source_instance = supply_candidates[0]
        led_id = self._next_module_id("led")
        v_supply = source_instance.parameters.get("v_out", "3.3")
        intent = ModuleIntent(
            intent_id=led_id,
            role="指示灯",
            category_hint="led",
            electrical_targets={
                "v_supply": v_supply,
                "led_color": led_color,
            },
            placement_hint="control_side",
        )
        instance = self._resolve_module(intent)
        self._ir.module_instances[led_id] = instance
        self._ir.request.modules.append(intent)
        self._ir.request.connections.append(ConnectionIntent(
            connection_id=f"rev_{source_id}_{led_id}",
            src_module_intent=source_id,
            src_port_hint="VOUT",
            dst_module_intent=led_id,
            dst_port_hint="ANODE",
            signal_type=SignalType.POWER_SUPPLY,
            connection_semantics=ConnectionSemantic.SUPPLY_CHAIN,
        ))
        self._rebuild_connections_from_request()
        self._ir = synthesize_all_modules(self._ir)
        return self._regenerate_outputs(f"已添加模块 '{led_id}'")

    def _add_gpio_led_module(self, led_color: str, gpio_pin: str) -> SystemDesignResult:
        assert self._ir is not None
        mcu_id = self._select_unique_module_by_category("mcu")
        if mcu_id is None:
            return SystemDesignResult(
                status="error",
                message="当前无法唯一确定用于驱动 LED 的 MCU，请显式指定模块。",
            )

        led_id = self._next_module_id("led")
        v_supply = self._infer_module_supply_voltage(mcu_id, default="3.3")
        intent = ModuleIntent(
            intent_id=led_id,
            role="指示灯",
            category_hint="led",
            electrical_targets={"v_supply": v_supply, "led_color": led_color},
            control_targets={"gpio_pin": gpio_pin},
            placement_hint="control_side",
        )
        instance = self._resolve_module(intent)
        instance.parameters["gpio_pin"] = gpio_pin
        self._ir.module_instances[led_id] = instance
        self._ir.request.modules.append(intent)
        self._ir.request.connections.append(
            ConnectionIntent(
                connection_id=f"rev_gpio_{gpio_pin.lower()}_{led_id}",
                src_module_intent=mcu_id,
                src_port_hint=gpio_pin,
                dst_module_intent=led_id,
                dst_port_hint="ANODE",
                signal_type=SignalType.GPIO,
                connection_semantics=ConnectionSemantic.GPIO_DRIVE,
            )
        )
        self._rebuild_connections_from_request()
        self._ir = synthesize_all_modules(self._ir)
        return self._regenerate_outputs(f"已添加模块 '{led_id}'")

    def _add_downstream_power_module(
        self,
        part_number: str,
        v_in: str,
        v_out: str,
    ) -> SystemDesignResult:
        assert self._ir is not None
        source_id = self._select_unique_power_source_by_voltage(v_in)
        if source_id is None:
            return SystemDesignResult(
                status="error",
                message=f"当前无法唯一确定 {v_in}V 的上游供电模块，请显式补充上下文。",
            )

        device = self._store.get_device(part_number)
        category = device.category if device is not None else ""
        if not category:
            if "ams1117" in part_number.lower():
                category = "ldo"
            elif "tps54" in part_number.lower():
                category = "buck"
            elif "tps61" in part_number.lower():
                category = "boost"
            else:
                category = "power"

        module_id = self._next_module_id(category if category in {"ldo", "buck", "boost"} else "module")
        intent = ModuleIntent(
            intent_id=module_id,
            role="新增电源模块",
            part_number_hint=part_number,
            category_hint=category,
            electrical_targets={"v_in": v_in, "v_out": v_out},
            placement_hint="power_chain",
        )
        instance = self._resolve_module(intent)
        self._ir.module_instances[module_id] = instance
        self._ir.request.modules.append(intent)
        self._ir.request.connections.append(
            ConnectionIntent(
                connection_id=f"rev_{source_id}_{module_id}",
                src_module_intent=source_id,
                src_port_hint="VOUT",
                dst_module_intent=module_id,
                dst_port_hint="VIN",
                signal_type=SignalType.POWER_SUPPLY,
                connection_semantics=ConnectionSemantic.SUPPLY_CHAIN,
            )
        )
        self._rebuild_connections_from_request()
        self._ir = synthesize_all_modules(self._ir)
        return self._regenerate_outputs(f"已添加模块 '{module_id}'")

    def revise_from_image(self, base64_png: str) -> SystemDesignResult:
        """基于图片反馈提取修改意图，再走统一 revise 管线。"""
        if self._ir is None:
            return SystemDesignResult(
                status="error",
                message="当前没有可修改的设计，请先调用 start()。",
            )

        try:
            base64.b64decode(base64_png, validate=True)
        except Exception:  # noqa: BLE001
            return SystemDesignResult(
                status="error",
                message="图片数据无效，无法解析修改意图。",
            )

        try:
            revision_text, warnings = _infer_revision_text_from_image(
                base64_png,
                _design_revision_context(self._ir),
            )
        except Exception as exc:  # noqa: BLE001
            return SystemDesignResult(
                status="error",
                message=f"图片修订分析失败: {exc}",
            )

        if not revision_text:
            return SystemDesignResult(
                status="error",
                message="无法从图片中提取可执行修改，请补充文字说明。",
                warnings=warnings,
            )

        result = self.revise(revision_text)
        if result.status != "error":
            result.message = f"图片修订完成：{revision_text}"
        result.warnings = list(result.warnings) + warnings
        if self._ir is not None:
            self._ir.warnings.extend(warnings)
        return result

    # ----------------------------------------------------------
    # 器件补录: ingest_asset / confirm_import
    # ----------------------------------------------------------

    def ingest_asset(self, filepath: str) -> SystemDesignResult:
        """从 PDF/图片提取器件信息，返回预览结果供用户确认。

        Args:
            filepath: PDF 或图片文件路径

        Returns:
            SystemDesignResult with status "needs_confirmation" (成功解析)
            或 "error" (解析失败)
        """
        path = Path(filepath)
        hint = ""
        # 尝试从 IR 中获取缺失器件型号作为 hint
        if self._ir is not None:
            for instance in self._ir.module_instances.values():
                if instance.status == ModuleStatus.NEEDS_ASSET and instance.missing_part_number:
                    hint = instance.missing_part_number
                    break

        if path.suffix.lower() == ".pdf":
            result = extract_from_pdf(str(path), hint=hint)
        else:
            result = extract_from_image(str(path), hint=hint)

        if not result.success or result.draft is None:
            return SystemDesignResult(
                status="error",
                message=result.error_message or "资料解析失败。",
            )

        draft = result.draft
        if hint and not draft.part_number:
            draft = apply_user_answers(draft, {"part_number": hint})

        # 暂存草稿供 confirm_import 使用
        self._pending_draft = draft
        self._pending_app_circuit = result.application_circuit
        self._pending_source_filepath = str(path)  # 保留源文件路径，用于 PDF 持久化

        return SystemDesignResult(
            status="needs_confirmation",
            message="资料已解析，请确认导入信息后继续设计。",
        )

    def confirm_import(
        self,
        answers: dict[str, object] | None = None,
    ) -> SystemDesignResult:
        """确认导入: 校验草稿 → 生成 symbol → 入库 → 重跑管线。

        Args:
            answers: 用户对追问的回答

        Returns:
            SystemDesignResult
        """
        if not hasattr(self, "_pending_draft") or self._pending_draft is None:
            return SystemDesignResult(
                status="error",
                message="当前没有待确认的导入任务。",
            )

        draft: DeviceDraft = self._pending_draft
        if answers:
            draft = self._apply_draft_answers(draft, answers)

        service = LibraryService(self._store.store_dir)

        # 1. 校验草稿
        validation = service.validate_only(draft)
        if not validation.is_valid:
            return SystemDesignResult(
                status="needs_confirmation",
                message="草稿校验未通过，请补充信息: "
                + "; ".join(e.message for e in validation.errors),
            )

        # 2. 生成 symbol
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

        # 3. 试转换（不写盘）
        dry_run = service.add_device_from_draft(
            draft,
            force=True,
            skip_dedupe=True,
            persist=False,
        )
        if not dry_run.success or dry_run.device is None:
            return SystemDesignResult(
                status="error",
                message=dry_run.error_message or "器件模型转换失败，无法入库。",
            )

        # 4. 写盘
        add_result = service.add_device_from_draft(
            draft,
            force=True,
            skip_dedupe=True,
        )
        if not add_result.success or add_result.device is None:
            return SystemDesignResult(
                status="error",
                message=add_result.error_message or "器件入库失败。",
            )

        # 5. 附加 symbol
        if symbol is not None:
            service.update_device_symbol(draft.part_number, symbol)

        # 5.5 持久化 PDF datasheet（若源文件是 PDF）
        pending_filepath = getattr(self, "_pending_source_filepath", "")
        if pending_filepath and pending_filepath.lower().endswith(".pdf"):
            ds_rel = self._store.save_datasheet(
                draft.part_number, pending_filepath
            )
            if ds_rel:
                device_obj = self._store.get_device(draft.part_number)
                if device_obj is not None:
                    device_obj.datasheet_path = ds_rel
                    self._store.save_device(device_obj)

        # 6. 附加 datasheet 提取的应用电路 recipe
        pending_app = getattr(self, "_pending_app_circuit", {})
        if pending_app:
            from schemaforge.ingest.datasheet_extractor import (
                build_recipe_from_application_circuit,
            )

            recipe = build_recipe_from_application_circuit(
                pending_app,
                part_number=draft.part_number,
            )
            if recipe is not None:
                service.update_device_recipe(draft.part_number, recipe)

        # 7. 清理待办状态
        self._pending_draft = None
        self._pending_app_circuit = {}
        self._pending_source_filepath = ""

        # 8. 重建 ComponentStore 缓存并重跑管线（如果有 IR）
        self._store = ComponentStore(self._store.store_dir)

        if self._ir is not None:
            # 重新解析之前 NEEDS_ASSET 的模块
            changed_ids: set[str] = set()
            for module_id, instance in list(self._ir.module_instances.items()):
                if instance.status == ModuleStatus.NEEDS_ASSET:
                    intent = next(
                        (m for m in self._ir.request.modules if m.intent_id == module_id),
                        None,
                    )
                    if intent is not None:
                        new_instance = self._resolve_module(intent)
                        if new_instance.status != ModuleStatus.NEEDS_ASSET:
                            self._ir.module_instances[module_id] = new_instance
                            changed_ids.add(module_id)

            if changed_ids:
                self._rebuild_connections_from_request()
                self._ir = recompute_dependent_modules(self._ir, changed_ids)
                self._ir = synthesize_all_modules(self._ir)
                return self._regenerate_outputs(
                    f"已导入 {draft.part_number}，设计已更新"
                )

        return SystemDesignResult(
            status="generated",
            message=f"已导入 {draft.part_number}。",
        )

    def _apply_draft_answers(
        self,
        draft: DeviceDraft,
        answers: dict[str, object],
    ) -> DeviceDraft:
        """将用户回答应用到 DeviceDraft。"""
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

    # ----------------------------------------------------------
    # T095: replace_module
    # ----------------------------------------------------------

    def replace_module(
        self, module_id: str, new_part_number: str,
    ) -> SystemDesignResult:
        """替换特定模块的器件。"""
        if self._ir is None:
            return SystemDesignResult(
                status="error",
                message="当前没有可修改的设计，请先调用 start()。",
            )

        existing = self._ir.get_module(module_id)
        if existing is None:
            return SystemDesignResult(
                status="error",
                message=f"模块 '{module_id}' 不存在。",
            )

        # 解析新器件
        device = self._store.get_device(new_part_number)
        if device is None:
            return SystemDesignResult(
                status="needs_asset",
                message=f"器件 '{new_part_number}' 不在库中。",
                missing_modules=[module_id],
            )

        # 用原有意图参数构建新的 intent
        intent = ModuleIntent(
            intent_id=module_id,
            role=existing.role,
            part_number_hint=new_part_number,
            category_hint=existing.resolved_category,
            electrical_targets=dict(existing.parameters),
        )

        # 重新实例化
        new_instance = instantiate_module_from_device(intent, device)
        new_instance.parameters.update(existing.parameters)
        self._ir.module_instances[module_id] = new_instance
        request_module = next(
            (
                module_intent
                for module_intent in self._ir.request.modules
                if module_intent.intent_id == module_id
            ),
            None,
        )
        if request_module is not None:
            request_module.part_number_hint = new_part_number

        # 重新综合
        self._rebuild_connections_from_request()
        self._ir = recompute_dependent_modules(self._ir, {module_id})
        self._ir = synthesize_all_modules(self._ir)

        return self._regenerate_outputs(
            f"模块 '{module_id}' 已替换为 {new_part_number}"
        )

    # ----------------------------------------------------------
    # T096: add_module / remove_module
    # ----------------------------------------------------------

    def add_module(self, intent: ModuleIntent) -> SystemDesignResult:
        """添加新模块到系统。"""
        if self._ir is None:
            return SystemDesignResult(
                status="error",
                message="当前没有可修改的设计，请先调用 start()。",
            )

        if intent.intent_id in self._ir.module_instances:
            return SystemDesignResult(
                status="error",
                message=f"模块 '{intent.intent_id}' 已存在。",
            )

        instance = self._resolve_module(intent)
        self._ir.module_instances[intent.intent_id] = instance

        # 将意图追加到 request
        self._ir.request.modules.append(intent)

        # 重新综合
        self._rebuild_connections_from_request()
        self._ir = synthesize_all_modules(self._ir)

        status = "generated"
        missing: list[str] = []
        if instance.status == ModuleStatus.NEEDS_ASSET:
            status = "partial"
            missing = [intent.intent_id]

        result = self._regenerate_outputs(
            f"已添加模块 '{intent.intent_id}'"
        )
        result.status = status
        result.missing_modules = missing
        return result

    def remove_module(self, module_id: str) -> SystemDesignResult:
        """移除模块。"""
        if self._ir is None:
            return SystemDesignResult(
                status="error",
                message="当前没有可修改的设计，请先调用 start()。",
            )

        if module_id not in self._ir.module_instances:
            return SystemDesignResult(
                status="error",
                message=f"模块 '{module_id}' 不存在。",
            )

        # 删除模块
        del self._ir.module_instances[module_id]

        # 删除涉及该模块的连接
        self._ir.connections = [
            c for c in self._ir.connections
            if c.src_port.module_id != module_id
            and c.dst_port.module_id != module_id
        ]

        # 从网络中移除该模块的端口
        for net in self._ir.nets.values():
            net.members = [
                m for m in net.members if m.module_id != module_id
            ]
        self._prune_orphan_nets()

        # 从 request 中移除
        self._ir.request.modules = [
            m for m in self._ir.request.modules
            if m.intent_id != module_id
        ]
        self._ir.request.connections = [
            c for c in self._ir.request.connections
            if c.src_module_intent != module_id
            and (c.dst_module_intent or "") != module_id
        ]

        self._rebuild_connections_from_request()
        self._ir = synthesize_all_modules(self._ir)

        return self._regenerate_outputs(f"已移除模块 '{module_id}'")

    # ----------------------------------------------------------
    # 内部辅助
    # ----------------------------------------------------------

    def _resolve_module(self, intent: ModuleIntent) -> ModuleInstance:
        """解析单个模块意图 → ModuleInstance。

        尝试按 part_number_hint / category_hint 在库中查找器件。
        找不到则标记为 NEEDS_ASSET。
        """
        candidates = resolve_part_candidates(self._store, intent)

        if candidates:
            device = candidates[0]
            instance = instantiate_module_from_device(intent, device)
            return instance

        # 未命中：创建 NEEDS_ASSET 占位实例
        return ModuleInstance(
            module_id=intent.intent_id,
            role=intent.role,
            resolved_category=intent.category_hint,
            parameters=dict(intent.electrical_targets),
            status=ModuleStatus.NEEDS_ASSET,
            missing_part_number=intent.part_number_hint or intent.category_hint,
            warnings=[
                f"器件未命中: part='{intent.part_number_hint}', "
                f"category='{intent.category_hint}'"
            ],
        )

    def _regenerate_outputs(self, message: str) -> SystemDesignResult:
        """从现有 IR 重新生成渲染/导出。"""
        assert self._ir is not None
        warnings: list[str] = []

        # 实例收集
        comp_instances = create_component_instances(self._ir)
        if self._prev_component_instances:
            comp_instances = stabilize_references_after_revision(
                self._prev_component_instances, comp_instances,  # type: ignore[arg-type]
            )
        else:
            comp_instances = allocate_global_references(comp_instances)
        self._prev_component_instances = comp_instances  # type: ignore[assignment]

        # 布局 + 渲染
        self._layout_spec = create_default_layout(self._ir)
        svg_path = ""
        render_metadata = None

        if self._ai_svg_mode:
            # AI SVG 模式：使用本地确定性 SVG 渲染器
            try:
                svg_path = self._render_with_local_svg(self._ir)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"本地 SVG 渲染失败: {exc}")
        else:
            try:
                svg_path, render_metadata = render_system_svg_with_metadata(
                    self._ir, layout_spec=self._layout_spec,
                )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"SVG 渲染失败: {exc}")

        # BOM
        bom_text = ""
        bom_csv = ""
        try:
            bom_text = export_system_bom_markdown(comp_instances, self._ir)
            bom_csv = export_system_bom_csv(comp_instances)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"BOM 导出失败: {exc}")

        # SPICE
        spice_text = ""
        try:
            spice_text = export_system_spice(self._ir, comp_instances)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"SPICE 导出失败: {exc}")

        bundle = SystemBundle(
            design_ir=self._ir,
            svg_path=svg_path,
            bom_text=bom_text,
            bom_csv=bom_csv,
            spice_text=spice_text,
            render_metadata=render_metadata or RenderMetadata(),
        )

        if self._enable_visual_review and svg_path and self._ir.get_resolved_modules():
            try:
                from schemaforge.visual_review.loop import run_visual_review_loop

                bundle, _trace = run_visual_review_loop(self._ir, bundle)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"视觉审稿失败: {exc}")

        self._bundle = bundle

        missing = [
            m.module_id for m in self._ir.get_unresolved_modules()
        ]
        status = "partial" if missing else "generated"

        return SystemDesignResult(
            status=status,
            message=message,
            bundle=bundle,
            missing_modules=missing,
            warnings=warnings,
        )

    # ----------------------------------------------------------
    # 本地确定性 SVG 渲染（AI SVG 模式）
    # ----------------------------------------------------------

    @staticmethod
    def _render_with_local_svg(ir: SystemDesignIR) -> str:
        """使用本地确定性 SVG 渲染器生成原理图。

        延迟导入 design_tools_v3 中的渲染函数，避免循环引用。

        Returns:
            SVG 文件路径
        """
        import time

        from schemaforge.agent.design_tools_v3 import (
            _build_svg_template,
            _render_svg_from_template,
            _svg_to_png,
        )
        from schemaforge.render.base import output_path

        template = _build_svg_template(ir)
        svg_code = _render_svg_from_template(template)

        ts = int(time.time() * 1000) % 100000
        svg_filename = f"ai_schematic_{ts}.svg"
        svg_path = output_path(svg_filename)
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(svg_code)

        # 同时生成 PNG（供 visual review 和 GUI 预览）
        png_path = svg_path.replace(".svg", ".png")
        _svg_to_png(svg_path, png_path)

        return svg_path
