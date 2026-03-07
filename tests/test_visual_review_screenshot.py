"""V021-V040 测试：截图生成 + AI 视觉审稿。

覆盖:
- render_review_images 返回 ReviewImageSet
- build_review_manifest 包含模块列表
- _render_png_from_ir 生成 PNG 文件
- _crop_module_regions 裁剪模块区域
- _crop_connection_regions 裁剪连接区域
- AI critic 返回 VisualReviewReport（mock AI）
- validate_visual_review_report 捕获禁止动作
- validate 捕获缺失字段
- validate 捕获分数越界
- _parse_ai_response 处理边界情况
- build_review_manifest 处理空 IR
- ReviewManifest.to_text 输出格式
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from schemaforge.system.models import (
    ModuleInstance,
    ModuleStatus,
    PortRef,
    RenderMetadata,
    ResolvedConnection,
    SystemBundle,
    SystemDesignIR,
    SystemDesignRequest,
    SystemNet,
)
from schemaforge.visual_review.models import (
    IssueSeverity,
    ReviewImageSet,
    ReviewManifest,
    VisualIssue,
    VisualReviewConfig,
    VisualReviewReport,
)
from schemaforge.visual_review.screenshot import (
    _crop_connection_regions,
    _crop_module_regions,
    _render_png_from_ir,
    build_review_manifest,
    render_review_images,
)
from schemaforge.visual_review.critic import (
    VISUAL_REVIEW_PROMPT,
    _parse_ai_response,
    review_rendered_schematic,
    validate_visual_review_report,
)


# ============================================================
# 辅助工厂
# ============================================================


def _make_ir(
    modules: dict[str, ModuleInstance] | None = None,
    connections: list[ResolvedConnection] | None = None,
    nets: dict[str, SystemNet] | None = None,
) -> SystemDesignIR:
    """构造测试用 SystemDesignIR。"""
    return SystemDesignIR(
        request=SystemDesignRequest(raw_text="test"),
        module_instances=modules or {},
        connections=connections or [],
        nets=nets or {},
    )


def _make_buck_instance(module_id: str = "buck1") -> ModuleInstance:
    return ModuleInstance(
        module_id=module_id,
        role="降压",
        resolved_category="buck",
        status=ModuleStatus.RESOLVED,
        parameters={"v_in": "12", "v_out": "5"},
        external_components=[
            {"role": "input_cap", "type": "capacitor", "value": "10uF"},
            {"role": "output_cap", "type": "capacitor", "value": "22uF"},
            {"role": "inductor", "type": "inductor", "value": "4.7uH"},
        ],
    )


def _make_ldo_instance(module_id: str = "ldo1") -> ModuleInstance:
    return ModuleInstance(
        module_id=module_id,
        role="稳压",
        resolved_category="ldo",
        status=ModuleStatus.RESOLVED,
        parameters={"v_in": "5", "v_out": "3.3"},
        external_components=[
            {"role": "input_cap", "type": "capacitor", "value": "10uF"},
            {"role": "output_cap", "type": "capacitor", "value": "22uF"},
        ],
    )


def _make_bundle(ir: SystemDesignIR | None = None) -> SystemBundle:
    """构造测试用 SystemBundle。"""
    if ir is None:
        buck = _make_buck_instance()
        ldo = _make_ldo_instance()
        ir = _make_ir(modules={"buck1": buck, "ldo1": ldo})
    return SystemBundle(
        design_ir=ir,
        render_metadata=RenderMetadata(),
    )


# ============================================================
# V021-V025: 截图生成测试
# ============================================================


class TestRenderPngFromIR:
    """测试 _render_png_from_ir 生成 PNG 文件。"""

    def test_renders_png_file(self, tmp_path: Path):
        """PNG 文件成功生成且非空。"""
        ir = _make_ir(modules={"buck1": _make_buck_instance()})
        out = str(tmp_path / "test_render.png")
        _render_png_from_ir(ir, out, dpi=72)
        assert Path(out).exists()
        assert Path(out).stat().st_size > 0

    def test_empty_ir_renders(self, tmp_path: Path):
        """空 IR 也能生成 PNG（占位标签）。"""
        ir = _make_ir()
        out = str(tmp_path / "empty.png")
        _render_png_from_ir(ir, out, dpi=72)
        assert Path(out).exists()

    def test_returns_anchors(self, tmp_path: Path):
        """返回模块锚点映射。"""
        ir = _make_ir(modules={"buck1": _make_buck_instance()})
        out = str(tmp_path / "anchors.png")
        anchors = _render_png_from_ir(ir, out, dpi=72)
        assert "buck1" in anchors
        assert "VIN" in anchors["buck1"] or "VOUT" in anchors["buck1"]


class TestRenderReviewImages:
    """测试 render_review_images 返回 ReviewImageSet。"""

    def test_returns_image_set(self, tmp_path: Path):
        """返回包含路径的 ReviewImageSet。"""
        bundle = _make_bundle()
        config = VisualReviewConfig(image_dpi=72, hd_dpi=96)
        result = render_review_images(bundle, config)

        assert isinstance(result, ReviewImageSet)
        assert result.full_image_path != ""
        assert result.full_image_hd_path != ""
        assert Path(result.full_image_path).exists()
        assert Path(result.full_image_hd_path).exists()
        assert result.dpi == 72

    def test_default_config(self, tmp_path: Path):
        """默认配置下也能正常工作。"""
        bundle = _make_bundle()
        result = render_review_images(bundle)
        assert isinstance(result, ReviewImageSet)
        assert result.dpi == 150


class TestCropModuleRegions:
    """测试模块区域裁剪。"""

    def test_crops_with_bboxes(self, tmp_path: Path):
        """有 bbox 时生成裁剪图。"""
        # 先生成一张 PNG
        ir = _make_ir(modules={"buck1": _make_buck_instance()})
        png_path = str(tmp_path / "full.png")
        _render_png_from_ir(ir, png_path, dpi=72)

        metadata = RenderMetadata(
            module_bboxes={"buck1": (10.0, 10.0, 50.0, 30.0)},
        )
        crops = _crop_module_regions(png_path, metadata, str(tmp_path), dpi=72)
        assert "buck1" in crops
        assert Path(crops["buck1"]).exists()

    def test_no_bboxes_returns_empty(self, tmp_path: Path):
        """无 bbox 时返回空字典。"""
        ir = _make_ir(modules={"buck1": _make_buck_instance()})
        png_path = str(tmp_path / "full.png")
        _render_png_from_ir(ir, png_path, dpi=72)

        metadata = RenderMetadata()
        crops = _crop_module_regions(png_path, metadata, str(tmp_path), dpi=72)
        assert crops == {}


class TestCropConnectionRegions:
    """测试连接区域裁剪。"""

    def test_needs_two_modules(self, tmp_path: Path):
        """少于两个模块时返回空列表。"""
        ir = _make_ir(modules={"buck1": _make_buck_instance()})
        png_path = str(tmp_path / "full.png")
        _render_png_from_ir(ir, png_path, dpi=72)

        metadata = RenderMetadata(
            module_bboxes={"buck1": (0.0, 0.0, 50.0, 30.0)},
        )
        paths = _crop_connection_regions(png_path, metadata, str(tmp_path), dpi=72)
        assert paths == []


# ============================================================
# V027-V030: ReviewManifest 测试
# ============================================================


class TestBuildReviewManifest:
    """测试 build_review_manifest 构建审稿清单。"""

    def test_includes_module_list(self):
        """清单包含模块列表。"""
        ir = _make_ir(modules={
            "buck1": _make_buck_instance(),
            "ldo1": _make_ldo_instance(),
        })
        manifest = build_review_manifest(ir)

        assert isinstance(manifest, ReviewManifest)
        assert len(manifest.module_list) == 2
        module_ids = {m["module_id"] for m in manifest.module_list}
        assert "buck1" in module_ids
        assert "ldo1" in module_ids

    def test_includes_connections(self):
        """清单包含连接关系。"""
        conn = ResolvedConnection(
            resolved_connection_id="c1",
            src_port=PortRef(module_id="buck1", port_role="power_out", pin_name="VOUT"),
            dst_port=PortRef(module_id="ldo1", port_role="power_in", pin_name="VIN"),
            net_name="NET_5V",
            rule_id="RULE_POWER_SUPPLY",
        )
        ir = _make_ir(
            modules={"buck1": _make_buck_instance(), "ldo1": _make_ldo_instance()},
            connections=[conn],
        )
        manifest = build_review_manifest(ir)
        assert len(manifest.connection_list) == 1
        assert manifest.connection_list[0]["net"] == "NET_5V"

    def test_counts_components(self):
        """元件总数正确统计。"""
        ir = _make_ir(modules={"buck1": _make_buck_instance()})
        manifest = build_review_manifest(ir)
        # buck1 有 3 个外围件 + 1 个 IC = 4
        assert manifest.total_components == 4

    def test_empty_ir(self):
        """空 IR 生成空清单。"""
        ir = _make_ir()
        manifest = build_review_manifest(ir)
        assert len(manifest.module_list) == 0
        assert manifest.total_components == 0
        assert manifest.total_nets == 0

    def test_to_text_format(self):
        """to_text 输出可读文本。"""
        ir = _make_ir(modules={"buck1": _make_buck_instance()})
        manifest = build_review_manifest(ir)
        text = manifest.to_text()
        assert "模块数: 1" in text
        assert "buck1" in text

    def test_canvas_size_from_metadata(self):
        """从 metadata 推断画布尺寸。"""
        ir = _make_ir(modules={"buck1": _make_buck_instance()})
        metadata = RenderMetadata(
            module_bboxes={"buck1": (0.0, 0.0, 100.0, 50.0)},
        )
        manifest = build_review_manifest(ir, metadata)
        assert manifest.canvas_size == (100.0, 50.0)


# ============================================================
# V031-V040: AI Critic 测试
# ============================================================


class TestParseAiResponse:
    """测试 _parse_ai_response 解析 AI 响应。"""

    def test_parses_valid_response(self):
        """正常 AI 响应解析为 VisualReviewReport。"""
        raw = {
            "overall_score": 7.5,
            "summary": "布局良好",
            "issues": [
                {
                    "issue_id": "v1",
                    "severity": "warning",
                    "category": "overlap",
                    "description": "C1标签与R2标签重叠",
                    "affected_elements": ["C1", "R2"],
                    "suggested_fix": "move_label",
                },
            ],
        }
        report = _parse_ai_response(raw)
        assert isinstance(report, VisualReviewReport)
        assert report.overall_score == 7.5
        assert report.summary == "布局良好"
        assert len(report.issues) == 1
        assert report.issues[0].severity == IssueSeverity.WARNING
        assert report.issues[0].category == "overlap"

    def test_handles_empty_response(self):
        """空响应生成空报告。"""
        report = _parse_ai_response({})
        assert report.overall_score == 0.0
        assert len(report.issues) == 0

    def test_handles_invalid_severity(self):
        """无效 severity 回退为 info。"""
        raw = {
            "overall_score": 5.0,
            "issues": [{"issue_id": "v1", "severity": "INVALID", "category": "x", "description": "y"}],
        }
        report = _parse_ai_response(raw)
        assert report.issues[0].severity == IssueSeverity.INFO

    def test_critical_count(self):
        """critical_count 和 warning_count 计算正确。"""
        raw = {
            "overall_score": 3.0,
            "issues": [
                {"issue_id": "v1", "severity": "critical", "category": "overlap", "description": "a"},
                {"issue_id": "v2", "severity": "warning", "category": "spacing", "description": "b"},
                {"issue_id": "v3", "severity": "critical", "category": "visibility", "description": "c"},
            ],
        }
        report = _parse_ai_response(raw)
        assert report.critical_count == 2
        assert report.warning_count == 1
        assert report.has_critical is True


class TestReviewRenderedSchematic:
    """测试 review_rendered_schematic AI 调用。"""

    def test_returns_report_with_mock(self):
        """mock vision API 调用返回 VisualReviewReport。"""
        import json
        from unittest.mock import MagicMock

        mock_json = json.dumps({
            "overall_score": 8.0,
            "summary": "Mock 审稿结果",
            "issues": [],
        })
        mock_choice = MagicMock()
        mock_choice.message.content = mock_json
        mock_completion = MagicMock()
        mock_completion.choices = [mock_choice]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_completion

        images = ReviewImageSet(full_image_path="fake.png", dpi=150)
        manifest = ReviewManifest(module_list=[{"module_id": "buck1", "device": "TPS5430", "role": "降压"}])

        with patch("schemaforge.visual_review.critic.get_client", return_value=mock_client), \
             patch("schemaforge.visual_review.critic.Path") as mock_path_cls:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path_instance.read_bytes.return_value = b"\x89PNG\r\n"
            mock_path_cls.return_value = mock_path_instance
            report = review_rendered_schematic(images, manifest)

        assert isinstance(report, VisualReviewReport)
        assert report.overall_score == 8.0

    def test_handles_ai_failure(self):
        """AI 调用异常时生成失败报告。"""
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("connection error")

        images = ReviewImageSet(full_image_path="fake.png")
        manifest = ReviewManifest()

        with patch("schemaforge.visual_review.critic.get_client", return_value=mock_client), \
             patch("schemaforge.visual_review.critic.Path") as mock_path_cls:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path_instance.read_bytes.return_value = b"\x89PNG\r\n"
            mock_path_cls.return_value = mock_path_instance
            report = review_rendered_schematic(images, manifest)

        assert "失败" in report.summary


class TestValidateVisualReviewReport:
    """测试 validate_visual_review_report 验证逻辑。"""

    def test_valid_report_passes(self):
        """合规报告无违规。"""
        report = VisualReviewReport(
            overall_score=7.5,
            summary="布局良好",
            issues=[
                VisualIssue(
                    issue_id="v1",
                    severity=IssueSeverity.WARNING,
                    category="overlap",
                    description="标签重叠",
                    suggested_fix="move_label",
                ),
            ],
        )
        violations = validate_visual_review_report(report)
        assert violations == []

    def test_rejects_forbidden_action(self):
        """禁止动作被拒绝。"""
        report = VisualReviewReport(
            overall_score=5.0,
            issues=[
                VisualIssue(
                    issue_id="v1",
                    severity=IssueSeverity.WARNING,
                    category="overlap",
                    description="元件重叠",
                    suggested_fix="add_component",  # 在 FORBIDDEN_ACTIONS 中
                ),
            ],
        )
        violations = validate_visual_review_report(report)
        assert len(violations) > 0
        assert any("禁止列表" in v for v in violations)

    def test_rejects_unlisted_fix(self):
        """不在白名单中的修复建议被拒绝。"""
        report = VisualReviewReport(
            overall_score=5.0,
            issues=[
                VisualIssue(
                    issue_id="v1",
                    severity=IssueSeverity.WARNING,
                    category="overlap",
                    description="元件重叠",
                    suggested_fix="some_random_action",
                ),
            ],
        )
        violations = validate_visual_review_report(report)
        assert any("白名单" in v for v in violations)

    def test_rejects_score_out_of_range(self):
        """分数越界被拒绝。"""
        report = VisualReviewReport(overall_score=15.0)
        violations = validate_visual_review_report(report)
        assert any("超出范围" in v for v in violations)

    def test_rejects_missing_issue_id(self):
        """缺少 issue_id 被拒绝。"""
        report = VisualReviewReport(
            overall_score=5.0,
            issues=[
                VisualIssue(
                    issue_id="",
                    severity=IssueSeverity.INFO,
                    category="spacing",
                    description="间距不均匀",
                ),
            ],
        )
        violations = validate_visual_review_report(report)
        assert any("issue_id" in v for v in violations)

    def test_rejects_forbidden_description_keywords(self):
        """描述中包含禁止操作关键词被拒绝。"""
        report = VisualReviewReport(
            overall_score=5.0,
            issues=[
                VisualIssue(
                    issue_id="v1",
                    severity=IssueSeverity.WARNING,
                    category="overlap",
                    description="建议删除元件 C3 以减少重叠",
                    suggested_fix="move_label",
                ),
            ],
        )
        violations = validate_visual_review_report(report)
        assert any("删除元件" in v for v in violations)

    def test_empty_fix_is_allowed(self):
        """空修复建议不触发违规。"""
        report = VisualReviewReport(
            overall_score=7.0,
            issues=[
                VisualIssue(
                    issue_id="v1",
                    severity=IssueSeverity.INFO,
                    category="spacing",
                    description="间距略有不均匀",
                    suggested_fix="",
                ),
            ],
        )
        violations = validate_visual_review_report(report)
        assert violations == []

    def test_prompt_contains_constraints(self):
        """VISUAL_REVIEW_PROMPT 包含约束声明。"""
        assert "VC01" in VISUAL_REVIEW_PROMPT
        assert "VC02" in VISUAL_REVIEW_PROMPT
        assert "VC03" in VISUAL_REVIEW_PROMPT
        assert "increase_module_spacing" in VISUAL_REVIEW_PROMPT
