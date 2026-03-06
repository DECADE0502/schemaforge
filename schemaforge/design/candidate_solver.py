"""候选方案求解器

从器件库中生成多个候选方案，并进行多维度评分和 Tradeoff 分析，
将设计系统从"单一答案"升级为"多候选方案+排名"。

用法::

    from schemaforge.design.candidate_solver import CandidateSolver

    solver = CandidateSolver(store)
    result = solver.solve(requirement, max_candidates=3)
    # result.candidates: 按总分降序排列的候选列表
    # result.recommended: 推荐方案（总分最高）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from schemaforge.design.planner import ModuleRequirement
from schemaforge.design.retrieval import DeviceRequirement, DeviceRetriever
from schemaforge.library.models import DeviceModel
from schemaforge.library.store import ComponentStore


# ============================================================
# 评分维度常量
# ============================================================

SCORE_WEIGHTS: dict[str, float] = {
    "constraint_satisfaction": 0.30,
    "device_match": 0.25,
    "electrical_reasonability": 0.20,
    "bom_complexity": 0.10,
    "thermal_risk": 0.10,
    "user_preference_match": 0.05,
}


# ============================================================
# 数据模型
# ============================================================


@dataclass
class ScoreDimension:
    """评分维度"""

    name: str
    """评分维度名称 (e.g. "constraint_satisfaction", "device_match")"""

    score: float
    """维度得分 0~1"""

    weight: float
    """维度权重"""

    detail: str = ""
    """评分依据说明"""


@dataclass
class CandidateSolution:
    """单个候选方案"""

    name: str
    """方案名称 (e.g. "AMS1117-3.3 LDO方案")"""

    device: DeviceModel
    """选用器件"""

    key_params: dict[str, str]
    """关键参数"""

    scores: list[ScoreDimension]
    """各维度评分"""

    total_score: float
    """综合评分（加权总分）"""

    risk_summary: str = ""
    """风险摘要"""

    suitable_for: list[str] = field(default_factory=list)
    """适用场景"""

    tradeoff_notes: str = ""
    """Tradeoff 说明"""

    estimated_power: str = ""
    """估计功耗"""

    estimated_cost: str = ""
    """估计成本等级 ("low"/"medium"/"high")"""

    bom_complexity: int = 0
    """BOM 复杂度（外围件数量）"""


@dataclass
class SolverResult:
    """候选方案求解结果"""

    module_role: str
    """模块角色"""

    module_category: str
    """模块分类"""

    candidates: list[CandidateSolution]
    """候选方案列表（按总分降序）"""

    recommended: CandidateSolution | None
    """推荐方案（= candidates[0] if any）"""

    recommendation_reason: str = ""
    """推荐理由"""


# ============================================================
# 候选方案求解器
# ============================================================


class CandidateSolver:
    """候选方案求解器

    对同一需求生成多个候选方案，每个方案包含多维度评分和 Tradeoff 分析。

    Args:
        store: 器件库存储
        use_mock: 是否使用 Mock 模式（仅规则评分，不调用 AI）
    """

    def __init__(self, store: ComponentStore, use_mock: bool = True) -> None:
        self._store = store
        self._retriever = DeviceRetriever(store)
        self._use_mock = use_mock

    def solve(
        self,
        requirement: ModuleRequirement,
        max_candidates: int = 3,
    ) -> SolverResult:
        """求解候选方案

        Args:
            requirement: 模块需求（由 DesignPlanner 生成）
            max_candidates: 最多生成的候选数量

        Returns:
            SolverResult 包含排序后的候选列表
        """
        # 1. 使用 DeviceRetriever 获取多个匹配器件
        dev_req = DeviceRequirement(
            role=requirement.role,
            category=requirement.category,
            query=requirement.description,
            part_number=requirement.part_number,
            specs={
                k: v
                for k, v in requirement.parameters.items()
                if k in {"v_out", "v_in", "i_out_max", "v_dropout"}
            },
        )
        retrieval_results = self._retriever.search_by_requirement(
            dev_req,
            limit=max_candidates * 2,  # 多取一些，后续过滤
        )

        # 2. 为每个器件生成候选方案
        candidates: list[CandidateSolution] = []
        for rr in retrieval_results[:max_candidates]:
            candidate = self._build_candidate(rr.device, requirement)
            candidates.append(candidate)

        # 3. 按总分降序排序
        candidates.sort(key=lambda c: c.total_score, reverse=True)

        # 4. 确定推荐方案
        recommended = candidates[0] if candidates else None

        # 5. 生成推荐理由
        reason = ""
        if recommended:
            reason = _build_recommendation_reason(recommended, candidates)

        return SolverResult(
            module_role=requirement.role,
            module_category=requirement.category,
            candidates=candidates,
            recommended=recommended,
            recommendation_reason=reason,
        )

    # ----------------------------------------------------------
    # 候选方案构建
    # ----------------------------------------------------------

    def _build_candidate(
        self,
        device: DeviceModel,
        requirement: ModuleRequirement,
    ) -> CandidateSolution:
        """为单个器件构建候选方案"""
        category = requirement.category or device.category
        params = requirement.parameters

        # 计算各维度评分
        scores = self._score_candidate(device, category, params)

        # 加权总分
        total = sum(s.score * s.weight for s in scores)
        total = round(min(max(total, 0.0), 1.0), 4)

        # BOM 复杂度
        bom = _count_external_components(device)

        # 估计功耗
        estimated_power = _estimate_power(device, category, params)

        # 估计成本
        estimated_cost = _estimate_cost(device, category, bom)

        # 关键参数
        key_params = _extract_key_params(device, category, params)

        # 风险摘要（来自 failure_modes + anti_patterns）
        risk_summary = _build_risk_summary(device)

        # 适用场景（来自 selection_hints）
        suitable_for = list(device.selection_hints)

        # Tradeoff 说明（来自 anti_patterns）
        tradeoff_notes = _build_tradeoff_notes(device, category)

        return CandidateSolution(
            name=f"{device.part_number} {_category_label(category)}方案",
            device=device,
            key_params=key_params,
            scores=scores,
            total_score=total,
            risk_summary=risk_summary,
            suitable_for=suitable_for,
            tradeoff_notes=tradeoff_notes,
            estimated_power=estimated_power,
            estimated_cost=estimated_cost,
            bom_complexity=bom,
        )

    # ----------------------------------------------------------
    # 评分逻辑
    # ----------------------------------------------------------

    def _score_candidate(
        self,
        device: DeviceModel,
        category: str,
        params: dict[str, str],
    ) -> list[ScoreDimension]:
        """计算所有评分维度"""
        scores = [
            self._score_constraint_satisfaction(device, category, params),
            self._score_device_match(device, category),
            self._score_electrical_reasonability(device, category, params),
            self._score_bom_complexity(device),
            self._score_thermal_risk(device, category, params),
            self._score_user_preference_match(device, params),
        ]
        return scores

    def _score_constraint_satisfaction(
        self,
        device: DeviceModel,
        category: str,
        params: dict[str, str],
    ) -> ScoreDimension:
        """约束满足度评分（权重 0.30）"""
        score = 0.5  # 基础分
        details: list[str] = []

        if category == "ldo":
            v_out_req = _parse_float(params.get("v_out", ""))
            v_out_dev = _parse_float(device.specs.get("v_out", ""))
            i_out_req = _parse_float(params.get("i_out_max", ""))
            i_out_dev = _parse_float(device.specs.get("i_out_max", ""))

            if v_out_req is not None and v_out_dev is not None:
                if abs(v_out_req - v_out_dev) < 0.01:
                    score += 0.3
                    details.append(f"输出电压精确匹配 {v_out_dev}V")
                elif abs(v_out_req - v_out_dev) < 0.1:
                    score += 0.15
                    details.append(f"输出电压近似匹配 {v_out_dev}V")
                else:
                    score -= 0.2
                    details.append(
                        f"输出电压不匹配: 需要 {v_out_req}V，器件 {v_out_dev}V"
                    )
            elif v_out_dev is not None:
                score += 0.1
                details.append(f"器件有 v_out 规格: {v_out_dev}V")

            if i_out_req is not None and i_out_dev is not None:
                if i_out_dev >= i_out_req:
                    score += 0.2
                    details.append(f"电流能力满足: {i_out_dev}A >= {i_out_req}A")
                else:
                    score -= 0.3
                    details.append(f"电流能力不足: {i_out_dev}A < {i_out_req}A")

        elif category == "led":
            v_supply = _parse_float(params.get("v_supply", ""))
            led_vf = _parse_float(device.specs.get("v_f", device.specs.get("vf", "")))
            if v_supply is not None and led_vf is not None:
                if v_supply > led_vf:
                    score += 0.3
                    details.append(f"供电电压 {v_supply}V > 正向压降 {led_vf}V")
                else:
                    score -= 0.2
                    details.append(f"供电电压 {v_supply}V 不足以驱动 LED ({led_vf}V)")
            else:
                score += 0.1
                details.append("LED 供电条件可配置")

        elif category == "voltage_divider":
            v_in_req = _parse_float(params.get("v_in", ""))
            v_out_req = _parse_float(params.get("v_out", ""))
            if v_in_req is not None and v_out_req is not None:
                if v_in_req > v_out_req > 0:
                    score += 0.3
                    ratio = v_out_req / v_in_req
                    details.append(f"分压比 {ratio:.2f} 可实现")
                else:
                    score -= 0.2
                    details.append("输入/输出电压比不合理")
            else:
                score += 0.1
                details.append("分压比可灵活配置")

        else:
            # 通用评分：有规格就加分
            if device.specs:
                score += 0.2
                details.append(f"器件有 {len(device.specs)} 项规格定义")

        return ScoreDimension(
            name="constraint_satisfaction",
            score=round(min(max(score, 0.0), 1.0), 3),
            weight=SCORE_WEIGHTS["constraint_satisfaction"],
            detail="; ".join(details) if details else "约束满足度评估",
        )

    def _score_device_match(
        self,
        device: DeviceModel,
        category: str,
    ) -> ScoreDimension:
        """器件分类和角色匹配度评分（权重 0.25）"""
        score = 0.0
        details: list[str] = []

        # 分类匹配
        if device.category == category:
            score += 0.5
            details.append(f"分类精确匹配: {category}")
        elif device.category and category:
            score += 0.1
            details.append(f"分类不完全匹配: {device.category} vs {category}")

        # 有拓扑定义加分
        if device.topology is not None:
            score += 0.3
            details.append("有拓扑定义")

        # 有符号定义加分
        if device.symbol is not None:
            score += 0.2
            details.append("有符号定义")

        return ScoreDimension(
            name="device_match",
            score=round(min(max(score, 0.0), 1.0), 3),
            weight=SCORE_WEIGHTS["device_match"],
            detail="; ".join(details) if details else "器件匹配度评估",
        )

    def _score_electrical_reasonability(
        self,
        device: DeviceModel,
        category: str,
        params: dict[str, str],
    ) -> ScoreDimension:
        """电气合理性评分（权重 0.20）"""
        score = 0.5
        details: list[str] = []

        if category == "ldo":
            v_in = _parse_float(params.get("v_in", ""))
            v_out = _parse_float(params.get("v_out", ""))
            v_dropout = _parse_float(device.specs.get("v_dropout", ""))
            v_in_max = _parse_float(device.specs.get("v_in_max", ""))

            if v_in is not None and v_out is not None and v_dropout is not None:
                margin = v_in - v_out
                if margin > v_dropout * 1.2:
                    score += 0.3
                    details.append(
                        f"压差裕量充足: {margin:.1f}V > {v_dropout}V (dropout)"
                    )
                elif margin >= v_dropout:
                    score += 0.1
                    details.append(f"压差刚好满足: {margin:.1f}V >= {v_dropout}V")
                else:
                    score -= 0.4
                    details.append(f"压差不足: {margin:.1f}V < {v_dropout}V (dropout)")
            elif v_in is not None and v_out is not None:
                if v_in > v_out:
                    score += 0.1
                    details.append(f"输入 {v_in}V > 输出 {v_out}V，方向正确")
                else:
                    score -= 0.3
                    details.append(f"输入 {v_in}V <= 输出 {v_out}V，不合理")

            if v_in is not None and v_in_max is not None:
                if v_in <= v_in_max:
                    score += 0.1
                    details.append(f"输入电压在额定范围内: {v_in}V <= {v_in_max}V")
                else:
                    score -= 0.3
                    details.append(f"输入超压: {v_in}V > {v_in_max}V")

        elif category == "led":
            i_led = _parse_float(params.get("i_led", device.specs.get("i_f", "")))
            i_max = _parse_float(
                device.specs.get("i_f_max", device.specs.get("i_max", ""))
            )
            if i_led is not None and i_max is not None:
                if i_led <= i_max:
                    score += 0.3
                    details.append(f"工作电流 {i_led}mA 在额定范围内")
                else:
                    score -= 0.3
                    details.append(f"工作电流 {i_led}mA 超额定 {i_max}mA")
            else:
                score += 0.1
                details.append("LED 工作电流可通过限流电阻配置")

        elif category == "voltage_divider":
            # 分压器本身电气合理性高
            score += 0.3
            details.append("电阻分压器电气结构天然合理")

        else:
            if device.specs:
                score += 0.1
                details.append("器件有规格参数")

        return ScoreDimension(
            name="electrical_reasonability",
            score=round(min(max(score, 0.0), 1.0), 3),
            weight=SCORE_WEIGHTS["electrical_reasonability"],
            detail="; ".join(details) if details else "电气合理性评估",
        )

    def _score_bom_complexity(
        self,
        device: DeviceModel,
    ) -> ScoreDimension:
        """BOM 复杂度评分（越简单得分越高，权重 0.10）"""
        bom_count = _count_external_components(device)
        details: list[str] = []

        # 外围件越少，BOM 越简单，得分越高
        if bom_count == 0:
            score = 0.8
            details.append("无额外外围件（集成度高）")
        elif bom_count <= 2:
            score = 1.0
            details.append(f"仅 {bom_count} 个外围件（极简设计）")
        elif bom_count <= 4:
            score = 0.7
            details.append(f"{bom_count} 个外围件（典型设计）")
        elif bom_count <= 6:
            score = 0.5
            details.append(f"{bom_count} 个外围件（中等复杂度）")
        else:
            score = 0.3
            details.append(f"{bom_count} 个外围件（复杂设计）")

        return ScoreDimension(
            name="bom_complexity",
            score=round(score, 3),
            weight=SCORE_WEIGHTS["bom_complexity"],
            detail="; ".join(details),
        )

    def _score_thermal_risk(
        self,
        device: DeviceModel,
        category: str,
        params: dict[str, str],
    ) -> ScoreDimension:
        """热风险评分（功耗越低得分越高，权重 0.10）"""
        score = 0.7
        details: list[str] = []
        power_w = 0.0

        if category == "ldo":
            v_in = _parse_float(params.get("v_in", ""))
            v_out = _parse_float(params.get("v_out", ""))
            # 用额定电流评估最大功耗
            i_out = _parse_float(
                params.get("i_out_max", device.specs.get("i_out_max", "0.5"))
            )
            if v_in is not None and v_out is not None and i_out is not None:
                power_w = (v_in - v_out) * i_out
                if power_w < 0.1:
                    score = 1.0
                    details.append(f"功耗极低: {power_w:.2f}W")
                elif power_w < 0.3:
                    score = 0.9
                    details.append(f"功耗低: {power_w:.2f}W")
                elif power_w < 0.5:
                    score = 0.7
                    details.append(f"功耗适中: {power_w:.2f}W")
                elif power_w < 1.0:
                    score = 0.5
                    details.append(f"功耗偏高: {power_w:.2f}W，建议加散热")
                else:
                    score = 0.2
                    details.append(f"功耗高: {power_w:.2f}W，需重点散热")
            else:
                details.append("LDO 功耗取决于压差和负载电流")

        elif category == "led":
            # LED 功耗通常很低
            score = 0.9
            details.append("LED 功耗极低（典型 <0.1W）")

        elif category == "voltage_divider":
            # 分压器会有静态损耗，但通常较小
            score = 0.8
            details.append("分压器有静态电流，功耗较小")

        else:
            details.append("热风险需根据实际应用评估")

        return ScoreDimension(
            name="thermal_risk",
            score=round(min(max(score, 0.0), 1.0), 3),
            weight=SCORE_WEIGHTS["thermal_risk"],
            detail="; ".join(details) if details else "热风险评估",
        )

    def _score_user_preference_match(
        self,
        device: DeviceModel,
        params: dict[str, str],
    ) -> ScoreDimension:
        """用户偏好匹配度评分（权重 0.05）"""
        score = 0.5
        details: list[str] = []

        # 检查 selection_hints 中是否包含用户关心的关键词
        hints_text = " ".join(device.selection_hints).lower()
        preference_keywords = {
            "低功耗": ["低功耗", "省电", "低静态"],
            "小尺寸": ["小尺寸", "小封装", "sot"],
            "低成本": ["低成本", "便宜", "经济"],
            "高精度": ["高精度", "精密", "精确"],
            "工业级": ["工业级", "宽温", "-40"],
        }

        matched_prefs: list[str] = []
        for pref_key, keywords in preference_keywords.items():
            if any(kw in hints_text for kw in keywords):
                matched_prefs.append(pref_key)

        if matched_prefs:
            score += min(0.3, len(matched_prefs) * 0.1)
            details.append(f"匹配用户偏好: {', '.join(matched_prefs)}")

        # 有 selection_hints 说明器件文档完整
        if device.selection_hints:
            score += 0.1
            details.append(f"有 {len(device.selection_hints)} 条适用场景说明")

        return ScoreDimension(
            name="user_preference_match",
            score=round(min(max(score, 0.0), 1.0), 3),
            weight=SCORE_WEIGHTS["user_preference_match"],
            detail="; ".join(details) if details else "用户偏好匹配评估",
        )


# ============================================================
# 辅助函数
# ============================================================


def _parse_float(text: str) -> float | None:
    """从字符串中提取浮点数值

    "3.3V" → 3.3
    "1A" → 1.0
    "" → None
    """
    if not text:
        return None
    match = re.search(r"[-+]?\d*\.?\d+", str(text))
    if match:
        try:
            return float(match.group())
        except ValueError:
            return None
    return None


def _count_external_components(device: DeviceModel) -> int:
    """统计拓扑中外围件数量"""
    if device.topology is None:
        return 0
    return len(device.topology.external_components)


def _estimate_power(
    device: DeviceModel,
    category: str,
    params: dict[str, str],
) -> str:
    """估计功耗字符串"""
    if category == "ldo":
        v_in = _parse_float(params.get("v_in", ""))
        v_out = _parse_float(params.get("v_out", ""))
        i_out = _parse_float(
            params.get("i_out_max", device.specs.get("i_out_max", "0.5"))
        )
        if v_in is not None and v_out is not None and i_out is not None:
            power = (v_in - v_out) * i_out
            return f"{power:.2f}W（典型）"
        return "取决于压差和负载"
    elif category == "led":
        return "<0.1W（典型）"
    elif category == "voltage_divider":
        return "取决于分压电阻阻值"
    elif category == "buck":
        return "较低（开关电源效率高）"
    return "未知"


def _estimate_cost(
    device: DeviceModel,
    category: str,
    bom_count: int,
) -> str:
    """估计成本等级"""
    # 根据 BOM 复杂度和分类估算
    if category in ("led", "voltage_divider", "rc_filter", "passive"):
        return "low"
    elif category == "ldo":
        if bom_count <= 2:
            return "low"
        return "medium"
    elif category == "buck":
        return "medium" if bom_count <= 5 else "high"
    return "medium"


def _extract_key_params(
    device: DeviceModel,
    category: str,
    params: dict[str, str],
) -> dict[str, str]:
    """提取关键参数"""
    key_params: dict[str, str] = {}

    # 来自器件规格
    important_specs = {
        "ldo": ["v_out", "v_dropout", "i_out_max", "v_in_max"],
        "buck": ["v_in_max", "i_out_max", "v_out_min", "v_out_max"],
        "led": ["v_f", "i_f", "color"],
        "voltage_divider": ["v_in", "v_out", "ratio"],
        "rc_filter": ["r", "c", "f_cutoff"],
    }

    for spec_key in important_specs.get(category, list(device.specs.keys())[:4]):
        if spec_key in device.specs:
            key_params[spec_key] = device.specs[spec_key]

    # 来自需求参数（覆盖或补充）
    for k, v in params.items():
        if k not in key_params:
            key_params[k] = v

    # 封装信息
    if device.package:
        key_params["package"] = device.package

    return key_params


def _build_risk_summary(device: DeviceModel) -> str:
    """构建风险摘要（来自 failure_modes + anti_patterns）"""
    risks: list[str] = []

    if device.failure_modes:
        risks.extend(device.failure_modes[:2])  # 最多取前2条

    if device.anti_patterns:
        risks.extend(device.anti_patterns[:1])  # 取1条不适用场景

    if not risks:
        return "无已知重大风险"

    return "注意: " + "；".join(risks)


def _build_tradeoff_notes(device: DeviceModel, category: str) -> str:
    """构建 Tradeoff 说明（来自 anti_patterns）"""
    notes: list[str] = []

    if device.anti_patterns:
        notes.append("不适用场景: " + "，".join(device.anti_patterns[:3]))

    # 分类特定的通用 Tradeoff
    tradeoff_map: dict[str, str] = {
        "ldo": "LDO 优点: 低噪声、简单可靠；缺点: 效率低（压差×电流=热耗）",
        "buck": "Buck 优点: 效率高（>90%）；缺点: 需要电感、有开关噪声",
        "led": "LED 电路简单，功耗低；需限流电阻保护",
        "voltage_divider": "分压器简单可靠，但有静态损耗，不适合大电流负载",
        "rc_filter": "RC 滤波器成本极低，但截止频率受元件精度影响",
    }

    generic = tradeoff_map.get(category, "")
    if generic:
        notes.append(generic)

    return "\n".join(notes) if notes else "无特别权衡说明"


def _category_label(category: str) -> str:
    """分类标签（中文）"""
    labels: dict[str, str] = {
        "ldo": "LDO稳压",
        "buck": "Buck降压",
        "boost": "Boost升压",
        "led": "LED驱动",
        "voltage_divider": "电压分压",
        "rc_filter": "RC滤波",
        "passive": "无源",
    }
    return labels.get(category, category)


def _build_recommendation_reason(
    recommended: CandidateSolution,
    all_candidates: list[CandidateSolution],
) -> str:
    """生成推荐理由"""
    reasons: list[str] = [
        f"{recommended.device.part_number} 综合评分最高 ({recommended.total_score:.2f})"
    ]

    # 找出最高分维度
    if recommended.scores:
        top_dim = max(recommended.scores, key=lambda s: s.score * s.weight)
        reasons.append(f"在 {top_dim.name} 维度表现突出 ({top_dim.score:.2f})")

    # 如果有多个候选，说明相对优势
    if len(all_candidates) > 1:
        second_score = all_candidates[1].total_score
        gap = recommended.total_score - second_score
        if gap > 0.1:
            reasons.append(f"领先次选方案 {gap:.2f} 分")

    return "；".join(reasons)
