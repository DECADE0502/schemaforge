"""器件重复检测

入库前检查是否已有相同/相似器件，避免重复入库。

检测策略:
1. 精确匹配 — part_number 完全相同
2. 模糊匹配 — 料号相似度 + (制造商+封装) 组合匹配
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from schemaforge.library.store import ComponentStore


@dataclass
class DuplicateMatch:
    """单条重复匹配结果"""

    part_number: str
    similarity: float  # 0.0 ~ 1.0
    match_type: str  # exact, fuzzy_name, same_manufacturer_package
    reason: str  # 中文说明


@dataclass
class DuplicateCheckResult:
    """重复检测结果"""

    matches: list[DuplicateMatch] = field(default_factory=list)

    @property
    def has_exact(self) -> bool:
        """是否有精确匹配"""
        return any(m.match_type == "exact" for m in self.matches)

    @property
    def has_duplicates(self) -> bool:
        """是否有任何匹配"""
        return len(self.matches) > 0

    @property
    def best_match(self) -> DuplicateMatch | None:
        """最高相似度的匹配"""
        if not self.matches:
            return None
        return max(self.matches, key=lambda m: m.similarity)


def check_duplicate(
    store: ComponentStore,
    part_number: str,
    manufacturer: str = "",
    package: str = "",
) -> DuplicateCheckResult:
    """检查器件是否重复

    Args:
        store: 器件库存储
        part_number: 待查料号
        manufacturer: 制造商 (可选，用于模糊匹配)
        package: 封装 (可选，用于模糊匹配)

    Returns:
        DuplicateCheckResult
    """
    result = DuplicateCheckResult()
    part_clean = _normalize_part_number(part_number)

    if not part_clean:
        return result

    # 获取库中所有料号
    all_parts = store.list_devices()

    for existing_pn in all_parts:
        existing_clean = _normalize_part_number(existing_pn)

        # 1. 精确匹配
        if existing_clean == part_clean:
            result.matches.append(DuplicateMatch(
                part_number=existing_pn,
                similarity=1.0,
                match_type="exact",
                reason=f"库中已存在完全相同的料号: {existing_pn}",
            ))
            continue

        # 2. 模糊料号匹配 (去除后缀的相似度)
        sim = _part_number_similarity(part_clean, existing_clean)
        if sim >= 0.85:
            result.matches.append(DuplicateMatch(
                part_number=existing_pn,
                similarity=sim,
                match_type="fuzzy_name",
                reason=f"料号与 {existing_pn} 高度相似 (相似度 {sim:.0%})",
            ))

    # 3. 制造商+封装组合匹配 (同厂商同封装的同类器件)
    if manufacturer and package:
        similar_devices = store.search_devices(
            manufacturer=manufacturer,
            package=package,
        )
        for dev in similar_devices:
            existing_clean = _normalize_part_number(dev.part_number)
            if existing_clean == part_clean:
                continue  # 已在精确匹配中
            # 检查是否已在模糊匹配中
            already_matched = any(
                _normalize_part_number(m.part_number) == existing_clean
                for m in result.matches
            )
            if already_matched:
                continue
            result.matches.append(DuplicateMatch(
                part_number=dev.part_number,
                similarity=0.5,
                match_type="same_manufacturer_package",
                reason=f"同厂商 ({manufacturer}) 同封装 ({package}) 器件: {dev.part_number}",
            ))

    # 按相似度降序排列
    result.matches.sort(key=lambda m: m.similarity, reverse=True)

    return result


def _normalize_part_number(pn: str) -> str:
    """标准化料号用于比较

    - 转大写
    - 去除空格、短横线
    - 去除常见后缀 (如温度范围标记)
    """
    s = pn.strip().upper()
    s = re.sub(r"[\s\-/]+", "", s)
    return s


def _part_number_similarity(a: str, b: str) -> float:
    """计算两个标准化料号的相似度

    使用最长公共子序列 (LCS) 与较短字符串长度的比值。
    """
    if not a or not b:
        return 0.0

    if a == b:
        return 1.0

    # LCS 长度
    m, n = len(a), len(b)
    # 优化: 使用一维 DP
    prev = [0] * (n + 1)
    for i in range(1, m + 1):
        curr = [0] * (n + 1)
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev = curr

    lcs_len = prev[n]
    max_len = max(m, n)
    return lcs_len / max_len if max_len > 0 else 0.0
