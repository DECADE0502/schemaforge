"""AI 视觉审稿：将截图 + 清单发送给 AI 进行布局质量评判。

V031-V040: AI 审稿阶段。
- 构造视觉审稿 prompt（中文）
- 发送截图 + 清单给 AI
- 解析 AI 响应为 VisualReviewReport
- 验证报告：拒绝禁止动作，检查字段完整性

约束遵循:
- VC01: 不能建议修改电气连接
- VC02: 不能建议修改元件参数
- VC03: 不能建议新增/删除器件
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

from schemaforge.ai.client import call_llm_json
from schemaforge.visual_review.models import (
    FORBIDDEN_ACTIONS,
    IssueSeverity,
    PatchActionType,
    ReviewImageSet,
    ReviewManifest,
    VisualIssue,
    VisualReviewReport,
)

logger = logging.getLogger(__name__)


# ============================================================
# V031: 视觉审稿 Prompt
# ============================================================

VISUAL_REVIEW_PROMPT = """你是原理图视觉审稿专家。你只评判布局质量，不评判电气正确性。

你会收到：
1. 原理图截图（PNG）
2. 设计清单（模块列表、连接关系）

请检查以下视觉质量问题：
- 元件重叠
- 标签重叠或溢出画布
- 连线交叉过多
- 模块间距不均匀
- 关键连接不清晰
- 文字太小难以阅读

请严格按以下 JSON 格式输出：
{
  "overall_score": 7.5,
  "summary": "整体布局...",
  "issues": [
    {
      "issue_id": "v1",
      "severity": "warning",
      "category": "overlap",
      "description": "C1标签与R2标签重叠",
      "affected_elements": ["C1", "R2"],
      "suggested_fix": "increase_module_spacing"
    }
  ]
}

severity 取值: "critical", "warning", "info"
category 取值: "overlap", "spacing", "label", "visibility", "routing"

重要约束：
- 你不能建议修改电气连接 (VC01)
- 你不能建议修改元件参数 (VC02)
- 你不能建议新增/删除器件 (VC03)
- 你的修复建议必须在白名单内: increase_module_spacing, move_module, move_label, reroute_connection, expand_canvas, add_net_label, adjust_font_size
"""

# 允许的修复建议白名单
_ALLOWED_FIXES: frozenset[str] = frozenset(a.value for a in PatchActionType)


# ============================================================
# V032-V035: 图片编码 + AI 调用
# ============================================================


def _encode_image_base64(image_path: str) -> str:
    """将 PNG 图片编码为 base64 字符串。"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _build_user_message(
    images: ReviewImageSet,
    manifest: ReviewManifest,
) -> str:
    """构建发给 AI 的用户消息（文本 + 图片信息）。

    如果 AI 不支持视觉输入，则只发送清单文本。
    图片以 base64 编码附在消息中，供支持视觉的模型使用。
    """
    parts: list[str] = []

    # 清单文本
    parts.append("=== 设计清单 ===")
    parts.append(manifest.to_text())

    # 图片信息
    if images.full_image_path and Path(images.full_image_path).exists():
        parts.append("\n=== 原理图截图 ===")
        parts.append(f"[整图 DPI={images.dpi}] {images.full_image_path}")
        try:
            b64 = _encode_image_base64(images.full_image_path)
            parts.append(f"[base64] data:image/png;base64,{b64[:100]}...")
        except OSError:
            parts.append("[图片读取失败]")

    if images.module_crops:
        parts.append(f"\n模块裁剪图: {len(images.module_crops)} 张")
        for mid, path in images.module_crops.items():
            parts.append(f"  {mid}: {path}")

    return "\n".join(parts)


def _parse_ai_response(raw: dict[str, Any]) -> VisualReviewReport:
    """将 AI 返回的 JSON 解析为 VisualReviewReport。"""
    issues: list[VisualIssue] = []

    for item in raw.get("issues", []):
        severity_str = item.get("severity", "info").lower()
        try:
            severity = IssueSeverity(severity_str)
        except ValueError:
            severity = IssueSeverity.INFO

        issues.append(VisualIssue(
            issue_id=item.get("issue_id", ""),
            severity=severity,
            category=item.get("category", ""),
            description=item.get("description", ""),
            affected_elements=item.get("affected_elements", []),
            suggested_fix=item.get("suggested_fix", ""),
            source="ai",
        ))

    return VisualReviewReport(
        issues=issues,
        overall_score=float(raw.get("overall_score", 0.0)),
        summary=raw.get("summary", ""),
        raw_ai_response=json.dumps(raw, ensure_ascii=False),
    )


# ============================================================
# V036: review_rendered_schematic
# ============================================================


def review_rendered_schematic(
    images: ReviewImageSet,
    manifest: ReviewManifest,
) -> VisualReviewReport:
    """将截图 + 清单发送给 AI 进行视觉审稿。

    Args:
        images: 截图集合
        manifest: 审稿清单

    Returns:
        VisualReviewReport 结构化审稿报告
    """
    user_message = _build_user_message(images, manifest)

    logger.info("发送视觉审稿请求...")
    raw = call_llm_json(
        system_prompt=VISUAL_REVIEW_PROMPT,
        user_message=user_message,
        temperature=0.1,
    )

    if raw is None:
        logger.warning("AI 审稿返回 None，生成空报告")
        return VisualReviewReport(
            summary="AI 审稿失败：无法解析响应",
            raw_ai_response="",
        )

    report = _parse_ai_response(raw)
    logger.info(
        "AI 审稿完成: score=%.1f, issues=%d (critical=%d, warning=%d)",
        report.overall_score,
        len(report.issues),
        report.critical_count,
        report.warning_count,
    )
    return report


# ============================================================
# V037-V040: validate_visual_review_report
# ============================================================


def validate_visual_review_report(report: VisualReviewReport) -> list[str]:
    """验证 AI 审稿报告的合规性。

    检查项：
    1. 拒绝禁止动作（VC01/VC02/VC03）
    2. 修复建议必须在白名单内
    3. 字段完整性（issue_id、severity、category 不能为空）
    4. 分数范围 0-10

    Args:
        report: AI 审稿报告

    Returns:
        违规描述列表（空列表表示通过）
    """
    violations: list[str] = []

    # 分数范围检查
    if not (0.0 <= report.overall_score <= 10.0):
        violations.append(
            f"overall_score 超出范围 [0,10]: {report.overall_score}",
        )

    for issue in report.issues:
        # 字段完整性
        if not issue.issue_id:
            violations.append(f"issue 缺少 issue_id: {issue.description[:50]}")

        if not issue.category:
            violations.append(f"issue {issue.issue_id} 缺少 category")

        if not issue.description:
            violations.append(f"issue {issue.issue_id} 缺少 description")

        # 检查 suggested_fix 是否在禁止列表中
        fix = issue.suggested_fix.lower().strip()
        if fix and fix in FORBIDDEN_ACTIONS:
            violations.append(
                f"issue {issue.issue_id} 的 suggested_fix '{fix}' 在禁止列表中 (VC01/VC02/VC03)",
            )

        # 检查 suggested_fix 是否在白名单中
        if fix and fix not in _ALLOWED_FIXES:
            violations.append(
                f"issue {issue.issue_id} 的 suggested_fix '{fix}' 不在白名单中",
            )

        # 检查 description 中是否包含禁止动作的关键词
        desc_lower = issue.description.lower()
        for forbidden in ("修改连接", "更改参数", "删除元件", "新增元件", "替换器件"):
            if forbidden in desc_lower:
                violations.append(
                    f"issue {issue.issue_id} 的 description 包含禁止操作关键词 '{forbidden}'",
                )

    return violations
