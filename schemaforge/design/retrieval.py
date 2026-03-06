"""器件库检索模块

从器件库中根据需求检索匹配的器件。

支持多种检索策略：
- 精确料号匹配
- 分类匹配
- 关键字模糊搜索
- 多条件评分排序

用法::

    from schemaforge.design.retrieval import DeviceRetriever

    retriever = DeviceRetriever(store)
    results = retriever.search(
        query="3.3V稳压",
        category="ldo",
        specs={"v_out": "3.3V"},
    )
    # results: list[RetrievalResult], 按相关度降序
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from schemaforge.library.models import DeviceModel
from schemaforge.library.store import ComponentStore


# ============================================================
# 检索结果
# ============================================================


@dataclass
class RetrievalResult:
    """检索结果条目"""

    device: DeviceModel
    score: float = 0.0  # 综合匹配得分 (0~1)
    match_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "part_number": self.device.part_number,
            "manufacturer": self.device.manufacturer,
            "category": self.device.category,
            "description": self.device.description,
            "score": round(self.score, 3),
            "match_reasons": self.match_reasons,
            "has_topology": self.device.topology is not None,
            "has_symbol": self.device.symbol is not None,
        }


# ============================================================
# 需求描述
# ============================================================


@dataclass
class DeviceRequirement:
    """单个器件需求描述

    由规划器 (planner) 生成，传入检索器匹配。
    """

    role: str = ""  # 角色标识 ("main_regulator", "power_led", ...)
    category: str = ""  # 期望分类 ("ldo", "buck", "led", ...)
    query: str = ""  # 关键字搜索词
    part_number: str = ""  # 精确料号（优先级最高）
    specs: dict[str, str] = field(default_factory=dict)  # 期望规格
    must_have_topology: bool = False  # 是否必须有拓扑定义

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "category": self.category,
            "query": self.query,
            "part_number": self.part_number,
            "specs": self.specs,
            "must_have_topology": self.must_have_topology,
        }


# ============================================================
# 器件检索器
# ============================================================


class DeviceRetriever:
    """器件库检索器

    从 ComponentStore 检索匹配的器件，按相关度评分排序。

    评分规则:
    - 精确料号匹配: +1.0
    - 分类匹配: +0.3
    - 描述关键字命中: +0.2 per keyword
    - 规格匹配: +0.15 per spec
    - 有拓扑定义: +0.1
    - 有符号定义: +0.05
    """

    # 分类别名映射（自然语言 → 标准分类）
    CATEGORY_ALIASES: dict[str, str] = {
        "稳压": "ldo",
        "线性稳压": "ldo",
        "ldo": "ldo",
        "降压": "buck",
        "buck": "buck",
        "开关电源": "buck",
        "分压": "voltage_divider",
        "divider": "voltage_divider",
        "led": "led",
        "指示灯": "led",
        "滤波": "rc_filter",
        "rc": "rc_filter",
        "电阻": "passive",
        "电容": "passive",
        "resistor": "passive",
        "capacitor": "passive",
    }

    def __init__(self, store: ComponentStore) -> None:
        self._store = store

    def search(
        self,
        query: str = "",
        category: str = "",
        part_number: str = "",
        specs: dict[str, str] | None = None,
        must_have_topology: bool = False,
        role: str = "",
        limit: int = 10,
    ) -> list[RetrievalResult]:
        """搜索器件库并返回评分排序的结果

        Args:
            query: 关键字搜索
            category: 按分类过滤
            part_number: 精确料号匹配（优先级最高）
            specs: 规格过滤
            must_have_topology: 是否必须有拓扑定义
            role: 设计角色过滤（匹配 design_roles）
            limit: 最大返回数

        Returns:
            按 score 降序排列的 RetrievalResult 列表
        """
        # 1. 精确料号匹配
        if part_number:
            device = self._store.get_device(part_number)
            if device:
                result = self._score_device(
                    device,
                    query=query,
                    category=category,
                    specs=specs or {},
                    exact_pn=True,
                    role=role,
                )
                return [result]
            # 料号不精确，降级到搜索
            query = query or part_number

        # 2. 标准化分类
        resolved_category = self._resolve_category(category)

        # 3. 从 store 拉取候选器件
        candidates = self._fetch_candidates(query, resolved_category)

        # 4. 评分
        results: list[RetrievalResult] = []
        for device in candidates:
            result = self._score_device(
                device,
                query=query,
                category=resolved_category,
                specs=specs or {},
                role=role,
            )
            if must_have_topology and device.topology is None:
                continue
            results.append(result)

        # 5. 排序 + 截断
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def search_by_requirement(
        self,
        requirement: DeviceRequirement,
        limit: int = 5,
    ) -> list[RetrievalResult]:
        """根据需求描述检索器件

        Args:
            requirement: 器件需求
            limit: 最大返回数

        Returns:
            匹配结果列表
        """
        return self.search(
            query=requirement.query,
            category=requirement.category,
            part_number=requirement.part_number,
            specs=requirement.specs,
            must_have_topology=requirement.must_have_topology,
            role=requirement.role,
            limit=limit,
        )

    def get_best_match(
        self,
        requirement: DeviceRequirement,
    ) -> RetrievalResult | None:
        """获取最佳匹配器件

        Args:
            requirement: 器件需求

        Returns:
            最佳匹配结果，无匹配时返回 None
        """
        results = self.search_by_requirement(requirement, limit=1)
        return results[0] if results else None

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _resolve_category(self, category: str) -> str:
        """标准化分类名"""
        if not category:
            return ""
        lower = category.lower().strip()
        return self.CATEGORY_ALIASES.get(lower, lower)

    def _fetch_candidates(
        self,
        query: str,
        category: str,
    ) -> list[DeviceModel]:
        """从 store 获取候选器件"""
        # 如果有分类，先按分类搜
        if category:
            by_category = self._store.search_devices(category=category)
            if query:
                by_query = self._store.search_devices(query=query)
                # 合并去重
                seen = {d.part_number for d in by_category}
                for d in by_query:
                    if d.part_number not in seen:
                        by_category.append(d)
                        seen.add(d.part_number)
            return by_category

        # 无分类，纯关键字搜
        if query:
            return self._store.search_devices(query=query)

        # 无条件，返回全部
        all_pns = self._store.list_devices()
        results: list[DeviceModel] = []
        for pn in all_pns:
            device = self._store.get_device(pn)
            if device:
                results.append(device)
        return results

    def _score_device(
        self,
        device: DeviceModel,
        query: str = "",
        category: str = "",
        specs: dict[str, str] | None = None,
        exact_pn: bool = False,
        role: str = "",
    ) -> RetrievalResult:
        """对单个器件评分"""
        score = 0.0
        reasons: list[str] = []
        specs = specs or {}

        # 精确料号匹配
        if exact_pn:
            score += 1.0
            reasons.append(f"精确料号匹配: {device.part_number}")

        # 分类匹配
        if category and device.category == category:
            score += 0.3
            reasons.append(f"分类匹配: {category}")

        # 关键字匹配
        if query:
            keywords = query.lower().split()
            searchable = (
                f"{device.part_number} {device.description} "
                f"{device.manufacturer} {device.category}"
            ).lower()
            for kw in keywords:
                if kw in searchable:
                    score += 0.2
                    reasons.append(f"关键字命中: {kw}")

        # 规格匹配
        for spec_key, spec_val in specs.items():
            device_spec = device.specs.get(spec_key, "")
            if device_spec and self._spec_matches(device_spec, spec_val):
                score += 0.15
                reasons.append(f"规格匹配: {spec_key}={spec_val}")

        # 设计角色匹配
        if role and role in device.design_roles:
            score += 0.15
            reasons.append(f"设计角色匹配: {role}")

        # 拓扑加分
        if device.topology is not None:
            score += 0.1
            reasons.append("有拓扑定义")

        # 符号加分
        if device.symbol is not None:
            score += 0.05
            reasons.append("有符号定义")

        return RetrievalResult(
            device=device,
            score=min(score, 1.0),
            match_reasons=reasons,
        )

    @staticmethod
    def _spec_matches(device_spec: str, required_spec: str) -> bool:
        """比较规格值是否匹配

        简单策略：去单位后比较数值部分。
        "3.3V" matches "3.3V"
        "3.3" matches "3.3V"
        """
        # 提取数值
        d_num = _extract_numeric(device_spec)
        r_num = _extract_numeric(required_spec)
        if d_num is not None and r_num is not None:
            return abs(d_num - r_num) < 0.001
        # 退化为字符串比较
        return device_spec.strip().lower() == required_spec.strip().lower()


def _extract_numeric(text: str) -> float | None:
    """从字符串中提取数值部分

    "3.3V" → 3.3
    "1A" → 1.0
    "10uF" → 10.0
    "abc" → None
    """
    import re

    match = re.search(r"[-+]?\d*\.?\d+", text)
    if match:
        try:
            return float(match.group())
        except ValueError:
            return None
    return None
