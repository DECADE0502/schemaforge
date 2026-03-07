"""SchemaForge 设计合成层。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import re

from schemaforge.design.planner import _extract_voltages
from schemaforge.library.models import (
    DesignRecipe,
    DeviceModel,
    ExternalComponent,
    RecipeComponent,
    RecipeEvidence,
    RecipeFormula,
    TopologyConnection,
    TopologyDef,
)
from schemaforge.library.store import ComponentStore
from schemaforge.render.base import find_nearest_e24
from schemaforge.schematic.renderer import TopologyRenderer

_PART_NUMBER_RE = re.compile(
    r"\b([A-Z][A-Z0-9.-]{2,}\d[A-Z0-9.-]*)\b",
    re.IGNORECASE,
)
_CURRENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(mA|A)\b", re.IGNORECASE)
_VALUE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(uF|μF|µF|nF|pF|mF|uH|μH|µH|mH|H|kΩ|Ω|k|V)",
    re.IGNORECASE,
)
_CAP_SERIES = [1.0, 2.2, 4.7, 10.0, 22.0, 47.0, 100.0, 220.0]
_INDUCTOR_SERIES = [1.0, 1.5, 2.2, 3.3, 4.7, 6.8, 10.0, 15.0, 22.0, 33.0, 47.0]


@dataclass(slots=True)
class UserDesignRequest:
    raw_text: str
    part_number: str = ""
    category: str = ""
    v_in: str = ""
    v_out: str = ""
    i_out: str = ""
    wants_led: bool = False
    led_color: str = "green"
    led_current_ma: str = "2"


@dataclass(slots=True)
class DesignBundle:
    device: DeviceModel
    recipe: DesignRecipe
    parameters: dict[str, str] = field(default_factory=dict)
    svg_path: str = ""
    bom_text: str = ""
    spice_text: str = ""
    rationale: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "device": self.device.part_number,
            "topology": self.recipe.topology_family,
            "parameters": dict(self.parameters),
            "svg_path": self.svg_path,
            "bom_text": self.bom_text,
            "spice_text": self.spice_text,
            "rationale": list(self.rationale),
        }


def parse_design_request(user_input: str) -> UserDesignRequest:
    """从自然语言中提取精确型号与关键约束。"""
    part_number = _extract_part_number(user_input)
    v_in, v_out = _extract_voltages(user_input)
    if not v_in or not v_out:
        fallback = re.search(
            r"(\d+(?:\.\d+)?)\s*[Vv]\s*(?:转|到|→|->|to)\s*"
            r"(\d+(?:\.\d+)?)\s*[Vv]?",
            user_input,
            re.IGNORECASE,
        )
        if fallback:
            v_in = v_in or fallback.group(1)
            v_out = v_out or fallback.group(2)
    text = user_input.lower()

    category = ""
    if any(token in text for token in ["buck", "降压", "dcdc", "dc-dc"]):
        category = "buck"
    elif any(token in text for token in ["ldo", "线性稳压", "稳压器"]):
        category = "ldo"
    elif any(token in text for token in ["运放", "opamp", "op-amp"]):
        category = "opamp"
    elif any(token in text for token in ["mcu", "单片机", "最小系统"]):
        category = "mcu"

    wants_led = "led" in text or "指示灯" in text
    led_color = _extract_led_color(user_input)

    current_match = _CURRENT_RE.search(user_input)
    i_out = ""
    if current_match:
        value = float(current_match.group(1))
        unit = current_match.group(2).lower()
        if unit == "ma":
            value /= 1000.0
        i_out = _trim_float(value)

    return UserDesignRequest(
        raw_text=user_input,
        part_number=part_number,
        category=category,
        v_in=_normalize_numeric_string(v_in),
        v_out=_normalize_numeric_string(v_out),
        i_out=i_out,
        wants_led=wants_led,
        led_color=led_color,
    )


def _has_evaluable_formula(formula_text: str) -> bool:
    """检查公式文本是否包含可求解的代数表达式（不是纯常量/约束）。"""
    from schemaforge.design.formula_eval import normalize_formula_expression

    return normalize_formula_expression(formula_text) is not None


class ExactPartResolver:
    """精确型号解析器。"""

    def __init__(self, store: ComponentStore) -> None:
        self._store = store

    @staticmethod
    def extract(user_input: str) -> str:
        return _extract_part_number(user_input)

    def resolve(self, part_number: str) -> DeviceModel | None:
        if not part_number:
            return None

        exact = self._store.get_device(part_number)
        if exact is not None:
            return exact

        target = _normalize_part_number(part_number)
        for candidate in self._store.list_devices():
            device = self._store.get_device(candidate)
            if device is None:
                continue
            names = [device.part_number, *device.aliases]
            if any(_normalize_part_number(name) == target for name in names):
                return device
        return None


class DesignRecipeSynthesizer:
    """根据器件和请求生成 recipe / topology / 产物。"""

    def prepare_device(
        self,
        device: DeviceModel,
        request: UserDesignRequest,
    ) -> tuple[DeviceModel, DesignRecipe]:
        category = (device.category or request.category).lower()

        # 优先尝试 datasheet 驱动的公式计算（器件已有 recipe + 可求解公式时）
        recipe_result = self._try_recipe_driven_build(device, request, category)
        if recipe_result is not None:
            recipe, topology = recipe_result
            enriched = device.model_copy(
                update={"topology": topology, "design_recipe": recipe}
            )
            return enriched, recipe

        # 回退到内置硬编码计算
        if category == "buck":
            recipe, topology = self._build_buck_recipe(device, request)
        elif category == "ldo":
            recipe, topology = self._build_ldo_recipe(device, request)
        elif device.design_recipe is not None and device.topology is not None:
            # 未知类型且库里已有完整 recipe，直接沿用
            return device, device.design_recipe
        else:
            recipe, topology = self._build_generic_recipe(device)

        enriched = device.model_copy(
            update={"topology": topology, "design_recipe": recipe}
        )
        return enriched, recipe

    def _try_recipe_driven_build(
        self,
        device: DeviceModel,
        request: UserDesignRequest,
        category: str,
    ) -> tuple[DesignRecipe, TopologyDef] | None:
        """尝试用器件自带的 design_recipe 公式驱动参数计算。

        仅当器件已有 recipe 且 recipe 含有可求解公式时才使用。
        返回 None 表示不满足条件，应回退到硬编码计算。
        """
        if device.design_recipe is None or device.topology is None:
            return None

        recipe = device.design_recipe
        has_evaluable = any(
            comp.formula and _has_evaluable_formula(comp.formula)
            for comp in recipe.sizing_components
        )
        if not has_evaluable and not recipe.formulas:
            return None

        # 构建求解上下文
        from schemaforge.design.formula_eval import FormulaEvaluator

        context = self._build_eval_context(device, request, category)
        evaluator = FormulaEvaluator()
        eval_result = evaluator.evaluate_recipe(recipe, context)

        if not eval_result.computed_params:
            return None

        # 将求解结果合并到 recipe 的 default_parameters
        merged_params = dict(recipe.default_parameters)

        # 基础参数
        if request.v_in:
            merged_params["v_in"] = request.v_in
        if request.v_out:
            merged_params["v_out"] = request.v_out
        if request.i_out:
            merged_params["i_out_max"] = request.i_out
        merged_params["ic_model"] = device.part_number

        # 公式求解的元件值覆盖默认值
        _ROLE_TO_PARAM: dict[str, str] = {
            "input_cap": "c_in",
            "output_cap": "c_out",
            "inductor": "l_value",
            "boot_cap": "c_boot",
            "fb_upper": "r_fb_upper",
            "fb_lower": "r_fb_lower",
        }
        for role, formatted_value in eval_result.computed_params.items():
            param_key = _ROLE_TO_PARAM.get(role, role)
            merged_params[param_key] = formatted_value

        # 更新 recipe 的 default_parameters 和 sizing_components 的 value
        updated_components = []
        for comp in recipe.sizing_components:
            if comp.role in eval_result.computed_params:
                updated_components.append(
                    comp.model_copy(update={"value": eval_result.computed_params[comp.role]})
                )
            else:
                updated_components.append(comp)

        # 追加公式驱动的 evidence
        evidence_list = list(recipe.evidence)
        evidence_list.append(RecipeEvidence(
            source_type="formula_eval",
            summary=f"公式驱动计算: {len(eval_result.computed_params)} 个参数由 recipe 公式求解",
            confidence=0.9 if eval_result.success else 0.6,
        ))

        updated_recipe = recipe.model_copy(update={
            "default_parameters": merged_params,
            "sizing_components": updated_components,
            "evidence": evidence_list,
        })

        return updated_recipe, device.topology

    def _build_eval_context(
        self,
        device: DeviceModel,
        request: UserDesignRequest,
        category: str,
    ) -> dict[str, float]:
        """从设备 specs 和用户请求构建公式求解的变量上下文。"""
        context: dict[str, float] = {}

        # 从用户请求
        v_in = _coalesce_numeric(
            request.v_in,
            device.specs.get("v_in_typ"),
            _derate_abs_max(device.specs.get("v_in_max"), 0.6),
            12.0,
        )
        v_out = _coalesce_numeric(
            request.v_out,
            device.specs.get("v_out_typ"),
            device.specs.get("v_out"),
            3.3 if category == "ldo" else 5.0,
        )
        i_out = _coalesce_numeric(
            request.i_out,
            device.specs.get("i_out_typ"),
            _derate_abs_max(device.specs.get("i_out_max"), 0.7),
            1.0,
        )

        context["v_in"] = v_in
        context["v_out"] = v_out
        context["i_out"] = i_out

        # 开关频率
        fsw = _coalesce_numeric(device.specs.get("fsw"), 500000.0)
        if fsw < 10000:
            fsw *= 1000.0
        context["fsw"] = fsw

        # 参考电压
        v_ref = _coalesce_numeric(
            device.operating_constraints.get("v_fb"),
            device.specs.get("v_ref"),
            0.8,
        )
        context["v_ref"] = v_ref

        # 所有 specs 中的数值参数也注入上下文
        for key, value in device.specs.items():
            if key not in context:
                parsed = _parse_engineering_value(value)
                if parsed is not None:
                    context[key] = parsed

        return context

    def build_bundle(
        self,
        device: DeviceModel,
        request: UserDesignRequest,
        *,
        parameter_overrides: dict[str, str] | None = None,
    ) -> DesignBundle:
        enriched, recipe = self.prepare_device(device, request)
        params = dict(recipe.default_parameters)

        if request.v_in:
            params["v_in"] = request.v_in
        if request.v_out:
            params["v_out"] = request.v_out
        if request.i_out:
            params["i_out_max"] = request.i_out
        if request.wants_led:
            params["power_led"] = "true"
            params["led_color"] = request.led_color
            params["led_current_ma"] = request.led_current_ma

        for key, value in (parameter_overrides or {}).items():
            params[key] = value

        svg_path = TopologyRenderer().render(
            enriched,
            params,
            filename=_make_svg_filename(enriched, params),
        )
        return DesignBundle(
            device=enriched,
            recipe=recipe,
            parameters=params,
            svg_path=svg_path,
            bom_text=_render_bom(enriched, params),
            spice_text=_render_spice(enriched, params),
            rationale=_build_rationale(recipe),
        )

    def _build_buck_recipe(
        self,
        device: DeviceModel,
        request: UserDesignRequest,
    ) -> tuple[DesignRecipe, TopologyDef]:
        v_in = _coalesce_numeric(
            request.v_in,
            device.specs.get("v_in_typ"),
            _derate_abs_max(device.specs.get("v_in_max"), 0.6),
            12.0,
        )
        v_out = _coalesce_numeric(
            request.v_out,
            device.specs.get("v_out_typ"),
            device.specs.get("v_out"),
            5.0,
        )
        i_out = _coalesce_numeric(
            request.i_out,
            device.specs.get("i_out_typ"),
            _derate_abs_max(device.specs.get("i_out_max"), 0.7),
            1.0,
        )
        fsw_hz = _coalesce_numeric(device.specs.get("fsw"), 500000.0)
        if fsw_hz < 10000:
            fsw_hz *= 1000.0

        v_ref = _coalesce_numeric(
            device.operating_constraints.get("v_fb"),
            device.specs.get("v_ref"),
            0.8,
        )
        duty = min(max(v_out / max(v_in, 0.1), 0.05), 0.95)
        ripple_current = max(i_out * 0.3, 0.2)
        l_h = (v_out * (1.0 - duty)) / (fsw_hz * ripple_current)
        l_h = _nearest_series_value(max(l_h, 1e-6), _INDUCTOR_SERIES, 1e-6)

        ripple_voltage = max(v_out * 0.01, 0.05)
        c_out_f = ripple_current / (8.0 * fsw_hz * ripple_voltage)
        c_out_f = max(c_out_f, 22e-6)
        c_out_f = _nearest_series_value(c_out_f, _CAP_SERIES, 1e-6)

        input_ripple = max(v_in * 0.05, 0.5)
        c_in_f = (i_out * duty * (1.0 - duty)) / (fsw_hz * input_ripple)
        c_in_f = max(c_in_f, 10e-6)
        c_in_f = _nearest_series_value(c_in_f, _CAP_SERIES, 1e-6)

        r_lower = find_nearest_e24(10000.0)
        r_upper = find_nearest_e24(
            max(r_lower * (v_out / max(v_ref, 0.1) - 1.0), 1000.0)
        )

        params = {
            "v_in": _trim_float(v_in),
            "v_out": _trim_float(v_out),
            "i_out_max": _trim_float(i_out),
            "fsw": _trim_float(fsw_hz / 1000.0),
            "ic_model": device.part_number,
            "c_in": _format_cap_ascii(c_in_f),
            "c_out": _format_cap_ascii(c_out_f),
            "l_value": _format_inductor_ascii(l_h),
            "c_boot": "100nF",
            "v_ref": _trim_float(v_ref),
            "r_fb_upper": _format_resistor_ascii(r_upper),
            "r_fb_lower": _format_resistor_ascii(r_lower),
            "r_fb_total": _trim_float((r_upper + r_lower) / 1000.0),
            "led_resistor": "1kΩ",
        }

        topology = device.topology or TopologyDef(
            circuit_type="buck",
            external_components=[
                ExternalComponent(
                    role="input_cap",
                    ref_prefix="C",
                    default_value=params["c_in"],
                    value_expression="{c_in}",
                    schemdraw_element="Capacitor",
                ),
                ExternalComponent(
                    role="output_cap",
                    ref_prefix="C",
                    default_value=params["c_out"],
                    value_expression="{c_out}",
                    schemdraw_element="Capacitor",
                ),
                ExternalComponent(
                    role="inductor",
                    ref_prefix="L",
                    default_value=params["l_value"],
                    value_expression="{l_value}",
                    schemdraw_element="Inductor2",
                ),
                ExternalComponent(
                    role="boot_cap",
                    ref_prefix="C",
                    default_value="100nF",
                    value_expression="{c_boot}",
                    schemdraw_element="Capacitor",
                ),
                ExternalComponent(
                    role="fb_upper",
                    ref_prefix="R",
                    default_value=params["r_fb_upper"],
                    value_expression="{r_fb_upper}",
                    schemdraw_element="Resistor",
                ),
                ExternalComponent(
                    role="fb_lower",
                    ref_prefix="R",
                    default_value=params["r_fb_lower"],
                    value_expression="{r_fb_lower}",
                    schemdraw_element="Resistor",
                ),
            ],
            connections=[
                TopologyConnection(
                    net_name="VIN",
                    device_pin="VIN",
                    external_refs=["input_cap.1"],
                    is_power=True,
                ),
                TopologyConnection(
                    net_name="SW",
                    device_pin="SW",
                    external_refs=["inductor.1", "boot_cap.2"],
                ),
                TopologyConnection(
                    net_name="VOUT",
                    external_refs=["inductor.2", "output_cap.1", "fb_upper.1"],
                    is_power=True,
                ),
                TopologyConnection(
                    net_name="FB",
                    device_pin="FB",
                    external_refs=["fb_upper.2", "fb_lower.1"],
                ),
                TopologyConnection(
                    net_name="BST",
                    device_pin=_preferred_pin(device, ["BST", "BOOT"]),
                    external_refs=["boot_cap.1"],
                ),
                TopologyConnection(
                    net_name="GND",
                    device_pin=_preferred_pin(device, ["GND", "PGND"]),
                    external_refs=["input_cap.2", "output_cap.2", "fb_lower.2"],
                    is_ground=True,
                ),
            ],
        )

        recipe = DesignRecipe(
            topology_family="buck",
            summary=(
                f"{device.part_number} 的降压设计 recipe，基于典型 Buck 拓扑自动生成外围参数。"
            ),
            pin_roles=_extract_pin_roles(device),
            default_parameters=params,
            sizing_components=[
                RecipeComponent(
                    role="input_cap",
                    value=params["c_in"],
                    formula="Cin = Iout * D * (1 - D) / (fsw * ΔVin)",
                    rationale="按输入纹波电压估算，并设置 10μF 的工程下限。",
                ),
                RecipeComponent(
                    role="inductor",
                    value=params["l_value"],
                    formula="L = Vout * (1 - D) / (fsw * ΔIL)",
                    rationale="按 30% 输出电流纹波设计，再圆整到常用电感系列。",
                ),
                RecipeComponent(
                    role="output_cap",
                    value=params["c_out"],
                    formula="Cout = ΔIL / (8 * fsw * ΔVout)",
                    rationale="按 1% 输出纹波估算，并设置 22μF 的工程下限。",
                ),
                RecipeComponent(
                    role="fb_upper",
                    value=params["r_fb_upper"],
                    formula="Rupper = Rlower * (Vout / Vref - 1)",
                    rationale="固定下拉电阻 10kΩ，再按反馈参考电压求上拉值。",
                ),
                RecipeComponent(
                    role="fb_lower",
                    value=params["r_fb_lower"],
                    formula="Rlower = 10kΩ",
                    rationale="优先选用稳定、噪声适中的常见阻值作为反馈下拉。",
                ),
            ],
            formulas=[
                RecipeFormula(
                    name="占空比",
                    expression="D = Vout / Vin",
                    value=_trim_float(duty),
                    rationale="用于估算电感电流纹波与输入电容 RMS 应力。",
                )
            ],
            evidence=[
                RecipeEvidence(
                    source_type="datasheet",
                    summary="优先沿用典型 Buck 拓扑中的输入电容、电感、输出电容与反馈网络。",
                    source_ref=device.datasheet_url,
                    confidence=0.85,
                )
            ],
        )
        return recipe, topology

    def _build_ldo_recipe(
        self,
        device: DeviceModel,
        request: UserDesignRequest,
    ) -> tuple[DesignRecipe, TopologyDef]:
        v_in = _coalesce_numeric(request.v_in, device.specs.get("v_in_typ"), 5.0)
        v_out = _coalesce_numeric(
            request.v_out,
            device.specs.get("v_out"),
            device.specs.get("v_out_typ"),
            3.3,
        )
        params = {
            "v_in": _trim_float(v_in),
            "v_out": _trim_float(v_out),
            "ic_model": device.part_number,
            "c_in": "10uF",
            "c_out": "22uF",
            "led_resistor": "1kΩ",
        }
        topology = device.topology or TopologyDef(
            circuit_type="ldo",
            external_components=[
                ExternalComponent(
                    role="input_cap",
                    ref_prefix="C",
                    default_value="10uF",
                    value_expression="{c_in}",
                    schemdraw_element="Capacitor",
                ),
                ExternalComponent(
                    role="output_cap",
                    ref_prefix="C",
                    default_value="22uF",
                    value_expression="{c_out}",
                    schemdraw_element="Capacitor",
                ),
            ],
            connections=[
                TopologyConnection(
                    net_name="VIN",
                    device_pin=_preferred_pin(device, ["VIN", "IN"]),
                    external_refs=["input_cap.1"],
                    is_power=True,
                ),
                TopologyConnection(
                    net_name="VOUT",
                    device_pin=_preferred_pin(device, ["VOUT", "OUT"]),
                    external_refs=["output_cap.1"],
                    is_power=True,
                ),
                TopologyConnection(
                    net_name="GND",
                    device_pin=_preferred_pin(device, ["GND", "AGND", "PGND"]),
                    external_refs=["input_cap.2", "output_cap.2"],
                    is_ground=True,
                ),
            ],
        )
        recipe = DesignRecipe(
            topology_family="ldo",
            summary=f"{device.part_number} 的 LDO 典型输入/输出去耦 recipe。",
            pin_roles=_extract_pin_roles(device),
            default_parameters=params,
            sizing_components=[
                RecipeComponent(
                    role="input_cap",
                    value="10uF",
                    formula="Cin ≥ 10μF",
                    rationale="按典型应用建议在输入端放置贴近芯片的去耦电容。",
                ),
                RecipeComponent(
                    role="output_cap",
                    value="22uF",
                    formula="Cout ≥ 22μF",
                    rationale="按典型应用建议维持输出稳定性与瞬态响应。",
                ),
            ],
            evidence=[
                RecipeEvidence(
                    source_type="datasheet",
                    summary="LDO 常规应用包含输入与输出电容，且要求靠近芯片布局。",
                    source_ref=device.datasheet_url,
                    confidence=0.85,
                )
            ],
        )
        return recipe, topology

    def _build_generic_recipe(
        self,
        device: DeviceModel,
    ) -> tuple[DesignRecipe, TopologyDef]:
        if device.topology is None:
            raise ValueError(f"器件 {device.part_number} 缺少可用拓扑，当前无法自动合成。")

        params = {
            key: str(param.default)
            for key, param in device.topology.parameters.items()
            if str(param.default)
        }
        recipe = DesignRecipe(
            topology_family=device.topology.circuit_type,
            summary=f"沿用 {device.part_number} 已有拓扑定义。",
            pin_roles=_extract_pin_roles(device),
            default_parameters=params,
            evidence=[
                RecipeEvidence(
                    source_type="library",
                    summary="器件库中已存在可直接复用的拓扑定义。",
                    confidence=1.0,
                )
            ],
        )
        return recipe, device.topology


def apply_request_updates(
    request: UserDesignRequest,
    **updates: object,
) -> UserDesignRequest:
    return replace(request, **updates)


@dataclass(slots=True)
class RevisionResult:
    """修改解析结果（增强版）。"""

    param_updates: dict[str, str] = field(default_factory=dict)
    """直接参数覆盖 (key → value)"""

    request_updates: dict[str, object] = field(default_factory=dict)
    """UserDesignRequest 字段更新"""

    replace_device: str = ""
    """如果非空，表示需要换用该型号器件"""

    structural_ops: list[dict[str, object]] = field(default_factory=list)
    """结构化操作描述 (add_module / remove_module 等)"""

    explanation: str = ""
    """修改说明"""


def parse_revision_request(user_input: str) -> tuple[dict[str, str], dict[str, object]]:
    """将常见自然语言修改解析为参数与请求更新。

    返回 (param_updates, request_updates) 兼容旧接口。
    """
    result = parse_revision_request_v2(user_input)
    return result.param_updates, result.request_updates


def parse_revision_request_v2(user_input: str) -> RevisionResult:
    """增强版修改解析 — 支持结构化操作、器件替换等。"""
    text = user_input.lower()
    result = RevisionResult()

    # ---- 参数级修改 ----

    # 输出电容
    if "输出电容" in user_input or "output cap" in text:
        value = _extract_value_token(user_input)
        if value:
            result.param_updates["c_out"] = value

    # 输入电容
    if "输入电容" in user_input or "input cap" in text:
        value = _extract_value_token(user_input)
        if value:
            result.param_updates["c_in"] = value

    # 电感
    if "电感" in user_input or "inductor" in text:
        value = _extract_value_token(user_input)
        if value:
            result.param_updates["l_value"] = value

    # 反馈电阻上
    if any(kw in user_input for kw in ["上分压", "上臂", "反馈上"]) or "r_fb_upper" in text:
        value = _extract_value_token(user_input)
        if value:
            result.param_updates["r_fb_upper"] = value

    # 反馈电阻下
    if any(kw in user_input for kw in ["下分压", "下臂", "反馈下"]) or "r_fb_lower" in text:
        value = _extract_value_token(user_input)
        if value:
            result.param_updates["r_fb_lower"] = value

    # 通用电阻/电容/电感值修改: "把 xxx 改成 yyy"
    generic_change = re.search(
        r"把\s*(\w+)\s*(?:改成|改为|换成|设为|设置为)\s*(\d+(?:\.\d+)?\s*(?:uF|μF|µF|nF|pF|uH|μH|µH|mH|kΩ|Ω|k|V|A|mA)\b)",
        user_input,
        re.IGNORECASE,
    )
    if generic_change:
        param_name = _normalize_param_name(generic_change.group(1))
        param_value = generic_change.group(2).strip()
        if param_name:
            result.param_updates[param_name] = param_value

    # 开关频率
    fsw_match = re.search(
        r"(?:开关频率|fsw|频率)\s*(?:改成|改为|换成|设为|设置为|=|:)?\s*(\d+(?:\.\d+)?)\s*(kHz|MHz|Hz)\b",
        user_input,
        re.IGNORECASE,
    )
    if fsw_match:
        fsw_val = float(fsw_match.group(1))
        fsw_unit = fsw_match.group(2).lower()
        if fsw_unit == "khz":
            fsw_val *= 1000
        elif fsw_unit == "mhz":
            fsw_val *= 1e6
        result.param_updates["fsw"] = _trim_float(fsw_val)

    # ---- 请求级修改 ----

    # 输出电压变更
    if "输出" in user_input and "v" in text:
        voltage = _extract_voltage_token(user_input)
        if voltage:
            result.request_updates["v_out"] = voltage

    # 输入电压变更
    if "输入" in user_input and "v" in text and "输出" not in user_input:
        voltage = _extract_voltage_token(user_input)
        if voltage:
            result.request_updates["v_in"] = voltage

    # 更一般的 "电压改成 XY" 模式
    voltage_change = re.search(
        r"(?:输出|v_?out)\s*(?:改成|改为|换成|设为|调到|调整到|设置为)?\s*(\d+(?:\.\d+)?)\s*[Vv]",
        user_input,
    )
    if voltage_change and "v_out" not in result.request_updates:
        result.request_updates["v_out"] = voltage_change.group(1)

    # 输出电流变更
    current_change = re.search(
        r"(?:输出电流|电流|i_?out)\s*(?:改成|改为|换成|设为|调到|设置为)?\s*(\d+(?:\.\d+)?)\s*(mA|A)\b",
        user_input,
        re.IGNORECASE,
    )
    if current_change:
        i_val = float(current_change.group(1))
        i_unit = current_change.group(2).lower()
        if i_unit == "ma":
            i_val /= 1000.0
        result.request_updates["i_out"] = _trim_float(i_val)

    # LED 添加
    if ("加" in user_input or "添加" in user_input) and (
        "led" in text or "指示灯" in user_input
    ):
        result.request_updates["wants_led"] = True
        result.request_updates["led_color"] = _extract_led_color(user_input)

    # LED 删除
    if ("去掉" in user_input or "删除" in user_input) and (
        "led" in text or "指示灯" in user_input
    ):
        result.request_updates["wants_led"] = False

    # ---- 器件替换 ----
    replace_match = re.search(
        r"(?:换成|替换为|改用|改成用|换用|替换成)\s*([A-Z][A-Z0-9._-]{2,}\d[A-Z0-9._-]*)",
        user_input,
        re.IGNORECASE,
    )
    if replace_match:
        result.replace_device = replace_match.group(1).upper()
        result.explanation = f"替换器件为 {result.replace_device}"

    # ---- 结构化操作 ----

    # 添加模块: "加一个滤波器/分压器/..."
    add_module_match = re.search(
        r"(?:加|添加|增加)\s*(?:一个|个)?\s*(滤波器|分压器|稳压器|LDO|DCDC|运放|LED|指示灯|去耦电容)",
        user_input,
    )
    if add_module_match:
        module_type = add_module_match.group(1)
        category_map = {
            "滤波器": "rc_filter",
            "分压器": "voltage_divider",
            "稳压器": "ldo",
            "ldo": "ldo",
            "dcdc": "buck",
            "运放": "opamp",
            "led": "led_indicator",
            "指示灯": "led_indicator",
            "去耦电容": "decoupling",
        }
        cat = category_map.get(module_type.lower(), module_type.lower())
        result.structural_ops.append({
            "op_type": "add_module",
            "category": cat,
            "description": module_type,
        })

    # 删除模块: "去掉滤波器/分压器/..."
    remove_module_match = re.search(
        r"(?:去掉|删除|移除|去除)\s*(?:那个|这个)?\s*(滤波器|分压器|稳压器|LDO|DCDC|运放|LED模块|指示灯模块|去耦电容)",
        user_input,
    )
    if remove_module_match:
        module_type = remove_module_match.group(1)
        result.structural_ops.append({
            "op_type": "remove_module",
            "target": module_type,
        })

    return result


def _normalize_param_name(raw: str) -> str:
    """将中文/缩写参数名规范化为 recipe 字段名。"""
    mapping: dict[str, str] = {
        "输出电容": "c_out",
        "cout": "c_out",
        "c_out": "c_out",
        "输入电容": "c_in",
        "cin": "c_in",
        "c_in": "c_in",
        "电感": "l_value",
        "l": "l_value",
        "l_value": "l_value",
        "上臂电阻": "r_fb_upper",
        "r_fb_upper": "r_fb_upper",
        "下臂电阻": "r_fb_lower",
        "r_fb_lower": "r_fb_lower",
        "led电阻": "led_resistor",
        "led限流电阻": "led_resistor",
    }
    return mapping.get(raw.lower().strip(), raw.lower().strip())


def _extract_part_number(user_input: str) -> str:
    match = _PART_NUMBER_RE.search(user_input)
    return match.group(1).upper() if match else ""


def _normalize_part_number(part_number: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", part_number.upper())


def _normalize_numeric_string(value: str) -> str:
    if not value:
        return ""
    return value.strip().replace("V", "").replace("v", "")


def _parse_engineering_value(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (float, int)):
        return float(value)
    text = str(value).strip().replace("μ", "u").replace("µ", "u")
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*([A-Za-zΩ]*)", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).lower()
    multipliers = {
        "": 1.0,
        "v": 1.0,
        "a": 1.0,
        "ma": 1e-3,
        "khz": 1e3,
        "mhz": 1e6,
        "uf": 1e-6,
        "nf": 1e-9,
        "pf": 1e-12,
        "mf": 1e-3,
        "uh": 1e-6,
        "mh": 1e-3,
        "h": 1.0,
        "k": 1e3,
        "ω": 1.0,
        "kω": 1e3,
    }
    return number * multipliers.get(unit, 1.0)


def _derate_abs_max(value: str | None, ratio: float) -> str | None:
    """对绝对最大值按比例降额，返回工程合理的典型工况值。

    例如 v_in_max="36V", ratio=0.6 → "21.6" (60% of 36)。
    如果解析失败则返回 None，让 _coalesce_numeric 继续回退。
    """
    if value is None:
        return None
    parsed = _parse_engineering_value(value)
    if parsed is None or parsed <= 0:
        return None
    return _trim_float(parsed * ratio)


def _coalesce_numeric(*values: object) -> float:
    for value in values:
        parsed = _parse_engineering_value(value)  # type: ignore[arg-type]
        if parsed is not None:
            return parsed
    return 0.0


def _nearest_series_value(value: float, series: list[float], scale: float) -> float:
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


def _format_cap_ascii(value_f: float) -> str:
    if value_f >= 1e-6:
        return f"{_trim_float(value_f * 1e6)}uF"
    if value_f >= 1e-9:
        return f"{_trim_float(value_f * 1e9)}nF"
    return f"{_trim_float(value_f * 1e12)}pF"


def _format_inductor_ascii(value_h: float) -> str:
    if value_h >= 1e-3:
        return f"{_trim_float(value_h * 1e3)}mH"
    return f"{_trim_float(value_h * 1e6)}uH"


def _format_resistor_ascii(value_ohm: float) -> str:
    if value_ohm >= 1000:
        return f"{_trim_float(value_ohm / 1000.0)}kΩ"
    return f"{_trim_float(value_ohm)}Ω"


def _trim_float(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _extract_pin_roles(device: DeviceModel) -> dict[str, str]:
    roles: dict[str, str] = {}
    if device.symbol is None:
        return roles
    for pin in device.symbol.pins:
        name = pin.name.upper()
        if name in {"VIN", "IN"}:
            roles[pin.name] = "power_input"
        elif name in {"VOUT", "OUT"}:
            roles[pin.name] = "power_output"
        elif name in {"GND", "PGND", "AGND"}:
            roles[pin.name] = "ground"
        elif name in {"FB", "COMP"}:
            roles[pin.name] = "feedback"
        elif name in {"SW", "LX", "PH"}:
            roles[pin.name] = "switch"
        elif name in {"BOOT", "BST"}:
            roles[pin.name] = "bootstrap"
        elif name in {"EN", "SHDN"}:
            roles[pin.name] = "enable"
        else:
            roles[pin.name] = "signal"
    return roles


def _preferred_pin(device: DeviceModel, names: list[str]) -> str:
    if device.symbol is None:
        return names[0]
    upper_names = {name.upper() for name in names}
    for pin in device.symbol.pins:
        if pin.name.upper() in upper_names:
            return pin.name
    return names[0]


def _make_svg_filename(device: DeviceModel, params: dict[str, str]) -> str:
    vin = params.get("v_in", "")
    vout = params.get("v_out", "")
    safe = device.part_number.replace("/", "_").replace(" ", "_")
    if vin and vout:
        return f"forge_{safe}_{vin}V_to_{vout}V.svg"
    return f"forge_{safe}.svg"


def _build_rationale(recipe: DesignRecipe) -> list[str]:
    lines = [
        component.rationale
        for component in recipe.sizing_components
        if component.rationale
    ]
    lines.extend(formula.rationale for formula in recipe.formulas if formula.rationale)
    lines.extend(item.summary for item in recipe.evidence if item.summary)
    return lines


def _render_bom(device: DeviceModel, params: dict[str, str]) -> str:
    lines = [
        f"# BOM — {device.part_number}",
        "",
        "| 位号 | 名称 | 数值 | 备注 |",
        "|---|---|---|---|",
    ]
    lines.append(f"| U1 | {device.part_number} | {device.package or '-'} | 主控器件 |")

    ref_count: dict[str, int] = {}
    if device.topology is not None:
        for component in device.topology.external_components:
            ref_count[component.ref_prefix] = ref_count.get(component.ref_prefix, 0) + 1
            ref = f"{component.ref_prefix}{ref_count[component.ref_prefix]}"
            value = _resolve_component_value(component, params)
            lines.append(
                f"| {ref} | {_component_label(component.role)} | {_display_value(value)} | {component.role} |"
            )

    if params.get("power_led", "").lower() == "true":
        lines.append(
            f"| RLED1 | LED限流电阻 | {params.get('led_resistor', '1kΩ')} | 电源指示支路 |"
        )
        lines.append(
            f"| DLED1 | LED({params.get('led_color', 'green')}) | 指示灯 | 输出电源指示 |"
        )
    return "\n".join(lines)


def _render_spice(device: DeviceModel, params: dict[str, str]) -> str:
    """生成 SPICE 网表。

    优先级:
    1. device.spice_model 模板 + topology.connections 映射引脚
    2. topology.connections 自动推导（无 spice_model 时）
    3. 旧版硬编码回退（无 topology 时）
    """
    topology = device.topology
    lines = ["* SchemaForge synthesized netlist", f"* Device: {device.part_number}", ""]

    # 总是加输入电源
    lines.append(f"V1 VIN 0 DC {params.get('v_in', '12')}")

    if topology is not None and topology.connections:
        # 构建 net_name → SPICE 节点名 映射
        net_map: dict[str, str] = {}
        for conn in topology.connections:
            if conn.is_ground:
                net_map[conn.net_name] = "0"
            else:
                net_map[conn.net_name] = conn.net_name

        # --- 主 IC 行 ---
        if device.spice_model:
            # spice_model 格式: "XU{ref} {VIN} {GND} {EN} {BST} {SW} {FB} TPS5430"
            # 将 {PIN_NAME} 占位符映射到拓扑中的网络名
            ic_line = device.spice_model.replace("{ref}", "1")
            for conn in topology.connections:
                if conn.device_pin:
                    placeholder = "{" + conn.device_pin + "}"
                    ic_line = ic_line.replace(placeholder, net_map.get(conn.net_name, conn.net_name))
            # 清理未映射的占位符（用 0 代替）
            ic_line = re.sub(r"\{[^}]+\}", "0", ic_line)
            lines.append(ic_line)
        else:
            # 无 spice_model: 按 topology connections 中有 device_pin 的生成 XU1
            ic_pins = [
                net_map.get(conn.net_name, conn.net_name)
                for conn in topology.connections
                if conn.device_pin
            ]
            lines.append(f"XU1 {' '.join(ic_pins)} {device.part_number}")

        # --- 外围元件行 ---
        ref_count: dict[str, int] = {}

        for comp in topology.external_components:
            ref_count[comp.ref_prefix] = ref_count.get(comp.ref_prefix, 0) + 1
            ref = f"{comp.ref_prefix}{ref_count[comp.ref_prefix]}"
            value = _resolve_component_value(comp, params)

            # 找该元件连接的两个网络节点
            comp_nets = _find_component_spice_nets(comp.role, topology.connections, net_map)
            if len(comp_nets) >= 2:
                lines.append(f"{ref} {comp_nets[0]} {comp_nets[1]} {_spice_passive(value)}")
            elif len(comp_nets) == 1:
                lines.append(f"{ref} {comp_nets[0]} 0 {_spice_passive(value)}")
    else:
        # 旧版硬编码回退（无 topology 时）
        circuit_type = device.category or ""
        if circuit_type == "buck":
            lines.append(f"XU1 VIN SW FB 0 {device.part_number}")
            lines.append(f"CIN VIN 0 {_spice_passive(params.get('c_in', '10uF'))}")
            lines.append(f"L1 SW VOUT {_spice_passive(params.get('l_value', '10uH'))}")
            lines.append(f"COUT VOUT 0 {_spice_passive(params.get('c_out', '22uF'))}")
            lines.append(f"RFB1 VOUT FB {_spice_passive(params.get('r_fb_upper', '52.3kΩ'))}")
            lines.append(f"RFB2 FB 0 {_spice_passive(params.get('r_fb_lower', '10kΩ'))}")
        elif circuit_type == "ldo":
            lines.append(f"XU1 VIN VOUT 0 {device.part_number}")
            lines.append(f"CIN VIN 0 {_spice_passive(params.get('c_in', '10uF'))}")
            lines.append(f"COUT VOUT 0 {_spice_passive(params.get('c_out', '22uF'))}")
        else:
            lines.append("* 当前仅对 Buck / LDO 生成结构化 SPICE 网表")

    if params.get("power_led", "").lower() == "true":
        lines.append(
            f"RLED1 VOUT LEDA {_spice_passive(params.get('led_resistor', '1kΩ'))}"
        )
        lines.append("DLED1 LEDA 0 LEDMODEL")
        lines.append(".model LEDMODEL D")
    lines.append("")
    lines.append(".end")
    return "\n".join(lines)


def _find_component_spice_nets(
    role: str,
    connections: list[TopologyConnection],
    net_map: dict[str, str],
) -> list[str]:
    """根据拓扑连接找到外围元件的两个 SPICE 网络节点。

    在 topology connections 中，外围元件以 "role.1" / "role.2" 引用。
    返回 [pin1_net, pin2_net]。
    """
    pin1_ref = f"{role}.1"
    pin2_ref = f"{role}.2"
    pin1_net = ""
    pin2_net = ""
    for conn in connections:
        for ext_ref in conn.external_refs:
            if ext_ref == pin1_ref:
                pin1_net = net_map.get(conn.net_name, conn.net_name)
            elif ext_ref == pin2_ref:
                pin2_net = net_map.get(conn.net_name, conn.net_name)
    result: list[str] = []
    if pin1_net:
        result.append(pin1_net)
    if pin2_net:
        result.append(pin2_net)
    return result


def _component_label(role: str) -> str:
    return {
        "input_cap": "输入电容",
        "output_cap": "输出电容",
        "inductor": "功率电感",
        "boot_cap": "自举电容",
        "fb_upper": "反馈上拉电阻",
        "fb_lower": "反馈下拉电阻",
    }.get(role, role)


def _resolve_component_value(component: ExternalComponent, params: dict[str, str]) -> str:
    expression = component.value_expression.strip()
    if expression.startswith("{") and expression.endswith("}"):
        key = expression[1:-1]
        return params.get(key, component.default_value)
    return component.default_value


def _display_value(value: str) -> str:
    return value.replace("u", "μ") if "u" in value else value


def _spice_passive(value: str) -> str:
    normalized = value.replace("μ", "u").replace("Ω", "")
    normalized = normalized.replace("uF", "u").replace("nF", "n")
    normalized = normalized.replace("pF", "p").replace("uH", "u")
    normalized = normalized.replace("mH", "m")
    return normalized


def _extract_value_token(text: str) -> str:
    match = _VALUE_RE.search(text)
    if not match:
        return ""
    unit = match.group(2).replace("μ", "u").replace("µ", "u")
    return f"{match.group(1)}{unit}"


def _extract_voltage_token(text: str) -> str:
    match = re.search(r"(\d+(?:\.\d+)?)\s*V", text, re.IGNORECASE)
    return _trim_float(float(match.group(1))) if match else ""


def _extract_led_color(text: str) -> str:
    lower = text.lower()
    for label, color in [
        ("红", "red"),
        ("绿", "green"),
        ("蓝", "blue"),
        ("白", "white"),
    ]:
        if label in text or color in lower:
            return color
    return "green"
