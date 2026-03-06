"""设计 IR 测试

测试 Design IR 模型的创建、序列化/反序列化、快照/恢复、
以及 IRHistory 管理器的完整生命周期。
"""

from __future__ import annotations

import json

from schemaforge.design.ir import (
    Assumption,
    CalculatedValue,
    CandidateDevice,
    Constraint,
    ConstraintPriority,
    DerivedParameters,
    DesignIR,
    DesignIntent,
    DesignOutputs,
    DesignReview,
    DeviceSelection,
    IRHistory,
    IRSnapshot,
    IssueCategory,
    ModuleIR,
    ModuleIntent,
    ModuleReview,
    NetIR,
    PatchRecord,
    ReviewIssue,
    ReviewSeverity,
    TopologyIR,
    UnresolvedQuestion,
)


# ============================================================
# 测试工具
# ============================================================


def _make_ldo_module() -> ModuleIR:
    """创建一个 LDO 模块 IR 用于测试"""
    return ModuleIR(
        intent=ModuleIntent(
            role="main_regulator",
            category="ldo",
            description="LDO 稳压器 5V→3.3V",
            target_specs={"v_in": "5", "v_out": "3.3"},
        ),
        selection=DeviceSelection(
            selected=CandidateDevice(
                part_number="AMS1117-3.3",
                manufacturer="AMS",
                score=0.85,
                match_reasons=["分类匹配: ldo", "规格匹配: v_out=3.3"],
            ),
            candidates=[
                CandidateDevice(
                    part_number="AMS1117-3.3",
                    score=0.85,
                ),
                CandidateDevice(
                    part_number="AP2112K-3.3",
                    score=0.65,
                    tradeoff_notes="低功耗但电流较小",
                ),
            ],
            selection_reason="AMS1117-3.3 综合匹配度最高",
        ),
        parameters=DerivedParameters(
            input_params={"v_in": "5", "v_out": "3.3"},
            calculated=[
                CalculatedValue(
                    name="c_in",
                    value="10μF",
                    formula="推荐值",
                    source="default",
                ),
                CalculatedValue(
                    name="c_out",
                    value="22μF",
                    formula="推荐值",
                    source="default",
                ),
            ],
            render_params={
                "v_in": "5",
                "v_out": "3.3",
                "c_in": "10uF",
                "c_out": "22uF",
            },
        ),
    )


def _make_led_module() -> ModuleIR:
    """创建一个 LED 模块 IR 用于测试"""
    return ModuleIR(
        intent=ModuleIntent(
            role="power_led",
            category="led",
            description="电源指示 LED",
            target_specs={"v_supply": "3.3", "led_color": "green"},
            depends_on=["main_regulator"],
        ),
        selection=DeviceSelection(
            selected=CandidateDevice(
                part_number="LED_INDICATOR",
                score=0.7,
            ),
        ),
    )


def _make_full_ir() -> DesignIR:
    """创建一个完整的 DesignIR 用于测试"""
    return DesignIR(
        intent=DesignIntent(
            raw_input="5V转3.3V稳压电路，带LED指示灯",
            summary="LDO 稳压电源 + LED 电源指示",
            design_type="power",
            known_constraints=[
                Constraint(name="v_in", value="5V", source="user"),
                Constraint(name="v_out", value="3.3V", source="user"),
            ],
            assumptions=[
                Assumption(
                    field="i_load",
                    assumed_value="500mA",
                    reason="未指定负载电流，按中等负载估计",
                    risk="若实际负载超过 1A，LDO 可能过热",
                ),
            ],
            confidence=0.8,
        ),
        modules=[_make_ldo_module(), _make_led_module()],
        topology=TopologyIR(
            nets=[
                NetIR(name="VIN", connections=["U1.VIN", "C1.1"], is_power=True),
                NetIR(name="VOUT", connections=["U1.VOUT", "C2.1"], is_power=True),
                NetIR(
                    name="GND", connections=["U1.GND", "C1.2", "C2.2"], is_ground=True
                ),
            ],
            design_spec={
                "design_name": "5V→3.3V 稳压电源 + LED指示",
                "modules": [
                    {
                        "template": "ldo_regulator",
                        "instance_name": "main_ldo",
                        "parameters": {},
                    },
                    {
                        "template": "led_indicator",
                        "instance_name": "power_led",
                        "parameters": {},
                    },
                ],
            },
        ),
        outputs=DesignOutputs(
            svg_paths=["/tmp/test_ldo.svg"],
            bom_text="AMS1117-3.3 x1, C 10uF x1, C 22uF x1",
        ),
        stage="done",
        success=True,
    )


# ============================================================
# 基本创建测试
# ============================================================


class TestDesignIRCreation:
    """IR 模型创建测试"""

    def test_empty_ir(self) -> None:
        """空 IR 可以创建"""
        ir = DesignIR()
        assert ir.ir_id
        assert ir.version == 1
        assert ir.stage == "init"
        assert not ir.success
        assert len(ir.modules) == 0

    def test_full_ir(self) -> None:
        """完整 IR 创建"""
        ir = _make_full_ir()
        assert ir.success
        assert len(ir.modules) == 2
        assert ir.modules[0].intent.role == "main_regulator"
        assert ir.modules[1].intent.role == "power_led"
        assert len(ir.topology.nets) == 3

    def test_get_module(self) -> None:
        """按 role 查找模块"""
        ir = _make_full_ir()
        ldo = ir.get_module("main_regulator")
        assert ldo is not None
        assert ldo.selection.selected is not None
        assert ldo.selection.selected.part_number == "AMS1117-3.3"

        missing = ir.get_module("nonexistent")
        assert missing is None

    def test_module_roles(self) -> None:
        """获取所有模块 role"""
        ir = _make_full_ir()
        roles = ir.module_roles()
        assert roles == ["main_regulator", "power_led"]

    def test_bump_version(self) -> None:
        """版本号递增"""
        ir = DesignIR()
        assert ir.version == 1
        ir.bump_version()
        assert ir.version == 2
        ir.bump_version()
        assert ir.version == 3

    def test_to_summary(self) -> None:
        """摘要生成"""
        ir = _make_full_ir()
        summary = ir.to_summary()
        assert summary["success"] is True
        assert summary["module_count"] == 2
        assert summary["svg_count"] == 1
        assert "main_regulator" in summary["module_roles"]


# ============================================================
# DesignIntent 测试
# ============================================================


class TestDesignIntent:
    """设计意图测试"""

    def test_can_proceed_no_questions(self) -> None:
        """无问题时可继续"""
        intent = DesignIntent(raw_input="test")
        assert intent.can_proceed

    def test_can_proceed_with_answered_required(self) -> None:
        """必选问题已回答时可继续"""
        intent = DesignIntent(
            unresolved_questions=[
                UnresolvedQuestion(
                    field="v_in",
                    question="输入电压是多少？",
                    priority=ConstraintPriority.REQUIRED,
                    answered=True,
                    answer="5V",
                ),
            ],
        )
        assert intent.can_proceed

    def test_cannot_proceed_with_unanswered_required(self) -> None:
        """必选问题未回答时不可继续"""
        intent = DesignIntent(
            unresolved_questions=[
                UnresolvedQuestion(
                    field="v_in",
                    question="输入电压是多少？",
                    priority=ConstraintPriority.REQUIRED,
                    answered=False,
                ),
            ],
        )
        assert not intent.can_proceed

    def test_can_proceed_with_unanswered_optional(self) -> None:
        """可选问题未回答时仍可继续"""
        intent = DesignIntent(
            unresolved_questions=[
                UnresolvedQuestion(
                    field="efficiency",
                    question="是否关心效率？",
                    priority=ConstraintPriority.OPTIONAL,
                    answered=False,
                ),
            ],
        )
        assert intent.can_proceed

    def test_all_resolved(self) -> None:
        """所有问题是否已解决"""
        intent = DesignIntent(
            unresolved_questions=[
                UnresolvedQuestion(field="a", question="?", answered=True),
                UnresolvedQuestion(field="b", question="?", answered=False),
            ],
        )
        assert not intent.all_resolved

        intent.unresolved_questions[1].answered = True
        assert intent.all_resolved


# ============================================================
# Review 测试
# ============================================================


class TestDesignReview:
    """审查报告测试"""

    def test_empty_review(self) -> None:
        """空审查报告"""
        review = DesignReview()
        assert review.overall_passed
        assert len(review.blocking_issues) == 0
        assert len(review.warnings) == 0

    def test_review_with_blocking(self) -> None:
        """包含 blocking 问题的审查"""
        review = DesignReview(
            issues=[
                ReviewIssue(
                    severity=ReviewSeverity.BLOCKING,
                    category=IssueCategory.ELECTRICAL,
                    message="输入电压超过最大额定值",
                ),
                ReviewIssue(
                    severity=ReviewSeverity.WARNING,
                    message="压差余量不足",
                ),
                ReviewIssue(
                    severity=ReviewSeverity.RECOMMENDATION,
                    message="建议使用低 ESR 电容",
                ),
            ],
            overall_passed=False,
        )
        assert len(review.blocking_issues) == 1
        assert len(review.warnings) == 1
        assert len(review.recommendations) == 1

    def test_module_review_has_blocking(self) -> None:
        """模块审查检测 blocking"""
        review = ModuleReview(
            issues=[
                ReviewIssue(
                    severity=ReviewSeverity.WARNING,
                    message="test warning",
                ),
            ],
        )
        assert not review.has_blocking

        review.issues.append(
            ReviewIssue(
                severity=ReviewSeverity.BLOCKING,
                message="test blocking",
            ),
        )
        assert review.has_blocking


# ============================================================
# 序列化/反序列化测试
# ============================================================


class TestSerialization:
    """IR 序列化测试"""

    def test_round_trip_json(self) -> None:
        """JSON 序列化 → 反序列化保持一致"""
        ir = _make_full_ir()
        json_str = ir.model_dump_json(indent=2)

        ir2 = DesignIR.model_validate_json(json_str)

        assert ir2.ir_id == ir.ir_id
        assert ir2.version == ir.version
        assert ir2.success == ir.success
        assert len(ir2.modules) == len(ir.modules)
        assert ir2.modules[0].intent.role == "main_regulator"
        assert ir2.modules[0].selection.selected is not None
        assert ir2.modules[0].selection.selected.part_number == "AMS1117-3.3"
        assert len(ir2.topology.nets) == 3

    def test_model_dump(self) -> None:
        """model_dump() 输出纯 dict"""
        ir = _make_full_ir()
        d = ir.model_dump()
        assert isinstance(d, dict)
        assert d["success"] is True
        assert isinstance(d["modules"], list)
        assert d["modules"][0]["intent"]["role"] == "main_regulator"

    def test_json_parseable(self) -> None:
        """输出的 JSON 是合法 JSON"""
        ir = _make_full_ir()
        json_str = ir.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["success"] is True

    def test_empty_ir_round_trip(self) -> None:
        """空 IR 也能序列化/反序列化"""
        ir = DesignIR()
        json_str = ir.model_dump_json()
        ir2 = DesignIR.model_validate_json(json_str)
        assert ir2.ir_id == ir.ir_id
        assert len(ir2.modules) == 0

    def test_patch_record_serialization(self) -> None:
        """PatchRecord 序列化"""
        ir = _make_full_ir()
        ir.history.append(
            PatchRecord(
                user_request="把输出电容改成47μF",
                ops=[
                    {
                        "op": "set",
                        "path": "modules[0].parameters.c_out",
                        "value": "47μF",
                    }
                ],
                affected_modules=["main_regulator"],
            )
        )
        json_str = ir.model_dump_json()
        ir2 = DesignIR.model_validate_json(json_str)
        assert len(ir2.history) == 1
        assert ir2.history[0].user_request == "把输出电容改成47μF"


# ============================================================
# 快照测试
# ============================================================


class TestSnapshot:
    """IR 快照测试"""

    def test_snapshot_creates_copy(self) -> None:
        """快照是独立的深拷贝"""
        ir = _make_full_ir()
        snap = ir.snapshot("v1 初始")

        assert isinstance(snap, IRSnapshot)
        assert snap.label == "v1 初始"
        assert snap.version == ir.version
        assert snap.module_count == 2

        # 修改原 IR 不影响快照
        ir.modules.clear()
        assert snap.module_count == 2
        restored = snap.restore()
        assert len(restored.modules) == 2

    def test_snapshot_restore(self) -> None:
        """从快照恢复"""
        ir = _make_full_ir()
        snap = ir.snapshot("test")

        restored = snap.restore()
        assert restored.ir_id == ir.ir_id
        assert restored.success == ir.success
        assert len(restored.modules) == 2
        assert restored.modules[0].intent.role == "main_regulator"

    def test_snapshot_restore_is_deep_copy(self) -> None:
        """恢复的 IR 是独立副本"""
        ir = _make_full_ir()
        snap = ir.snapshot("test")

        r1 = snap.restore()
        r2 = snap.restore()

        # 修改 r1 不影响 r2
        r1.modules.clear()
        assert len(r2.modules) == 2


# ============================================================
# IRHistory 管理器测试
# ============================================================


class TestIRHistory:
    """IR 快照历史管理器测试"""

    def test_save_and_list(self) -> None:
        """保存快照并列出"""
        history = IRHistory()
        ir = _make_full_ir()

        sid = history.save(ir, "v1")
        assert history.count == 1
        assert len(history.snapshots) == 1
        assert history.snapshots[0].label == "v1"
        assert history.snapshots[0].snapshot_id == sid

    def test_restore(self) -> None:
        """恢复指定快照"""
        history = IRHistory()
        ir = _make_full_ir()

        sid = history.save(ir, "v1")
        ir.modules.clear()
        ir.bump_version()
        history.save(ir, "v2")

        restored = history.restore(sid)
        assert restored is not None
        assert len(restored.modules) == 2

    def test_restore_nonexistent(self) -> None:
        """恢复不存在的快照"""
        history = IRHistory()
        assert history.restore("nonexistent") is None

    def test_undo(self) -> None:
        """撤销到上一快照"""
        history = IRHistory()
        ir = _make_full_ir()

        history.save(ir, "v1")
        ir.modules.pop()
        ir.bump_version()
        history.save(ir, "v2")

        undone = history.undo()
        assert undone is not None
        assert len(undone.modules) == 2  # 恢复到 v1

    def test_undo_single_snapshot(self) -> None:
        """只有一个快照时无法撤销"""
        history = IRHistory()
        ir = _make_full_ir()
        history.save(ir, "v1")

        assert history.undo() is None

    def test_undo_empty(self) -> None:
        """空历史无法撤销"""
        history = IRHistory()
        assert history.undo() is None

    def test_latest(self) -> None:
        """获取最新快照"""
        history = IRHistory()
        ir = _make_full_ir()

        history.save(ir, "v1")
        ir.bump_version()
        history.save(ir, "v2")

        latest = history.latest()
        assert latest is not None
        assert latest.label == "v2"

    def test_latest_empty(self) -> None:
        """空历史无最新快照"""
        history = IRHistory()
        assert history.latest() is None

    def test_multiple_snapshots_order(self) -> None:
        """多个快照按时间排序"""
        history = IRHistory()
        ir = _make_full_ir()

        history.save(ir, "v1")
        ir.bump_version()
        history.save(ir, "v2")
        ir.bump_version()
        history.save(ir, "v3")

        assert history.count == 3
        labels = [s.label for s in history.snapshots]
        assert labels == ["v1", "v2", "v3"]
