"""IRPatchEngine 测试

测试 IR 级修改操作：update_constraint / replace_device / add_module /
remove_module / update_parameter / change_preference
"""

from __future__ import annotations

from schemaforge.design.ir import (
    Assumption,
    CandidateDevice,
    Constraint,
    ConstraintPriority,
    DerivedParameters,
    DesignIR,
    DesignIntent,
    DeviceSelection,
    ModuleIR,
    ModuleIntent,
    PatchRecord,
)
from schemaforge.workflows.ir_patch import IRPatchEngine, IRPatchOp


# ============================================================
# 测试夹具
# ============================================================


def _make_simple_ir() -> DesignIR:
    """构建最小 DesignIR 用于测试"""
    ir = DesignIR(
        intent=DesignIntent(
            raw_input="5V转3.3V稳压电路",
            summary="5V→3.3V LDO 设计",
            design_type="power",
            known_constraints=[
                Constraint(
                    name="v_in", value="5V", priority=ConstraintPriority.REQUIRED
                ),
                Constraint(
                    name="v_out", value="3.3V", priority=ConstraintPriority.REQUIRED
                ),
            ],
            assumptions=[
                Assumption(
                    field="i_load",
                    assumed_value="500mA",
                    reason="典型负载电流",
                ),
            ],
        ),
        modules=[
            ModuleIR(
                intent=ModuleIntent(
                    role="main_regulator",
                    category="ldo",
                    description="主稳压器",
                    target_specs={"v_in": "5", "v_out": "3.3"},
                    depends_on=[],
                ),
                selection=DeviceSelection(
                    selected=CandidateDevice(
                        part_number="AMS1117-3.3",
                        manufacturer="AMS",
                        score=0.85,
                    ),
                ),
                parameters=DerivedParameters(
                    input_params={"v_in": "5", "v_out": "3.3"},
                    render_params={"r_limit": "120Ω"},
                ),
            ),
            ModuleIR(
                intent=ModuleIntent(
                    role="led_indicator",
                    category="led",
                    description="LED 指示灯",
                    target_specs={},
                    depends_on=["main_regulator"],
                ),
                selection=DeviceSelection(
                    selected=CandidateDevice(
                        part_number="LED-GREEN-0603",
                        manufacturer="Generic",
                        score=0.7,
                    ),
                ),
                parameters=DerivedParameters(
                    input_params={"v_f": "2.1"},
                    render_params={},
                ),
            ),
        ],
    )
    return ir


def _make_engine() -> IRPatchEngine:
    return IRPatchEngine()


# ============================================================
# update_constraint 测试
# ============================================================


class TestUpdateConstraint:
    def test_update_existing_constraint(self) -> None:
        """更新现有约束的值"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(op_type="update_constraint", field="v_in", value="12V"),
            ],
        )

        assert result.success
        updated_ir = result.modified_ir
        v_in = next(c for c in updated_ir.intent.known_constraints if c.name == "v_in")
        assert v_in.value == "12V"

    def test_update_constraint_adds_new_if_not_exists(self) -> None:
        """对不存在的约束进行更新，应自动新增"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(op_type="update_constraint", field="i_out_max", value="1A"),
            ],
        )

        assert result.success
        updated_ir = result.modified_ir
        names = [c.name for c in updated_ir.intent.known_constraints]
        assert "i_out_max" in names
        new_c = next(
            c for c in updated_ir.intent.known_constraints if c.name == "i_out_max"
        )
        assert new_c.value == "1A"

    def test_update_constraint_does_not_modify_original(self) -> None:
        """update_constraint 不修改原始 IR"""
        ir = _make_simple_ir()
        original_v_in = next(
            c.value for c in ir.intent.known_constraints if c.name == "v_in"
        )
        engine = _make_engine()

        engine.apply(
            ir,
            [
                IRPatchOp(op_type="update_constraint", field="v_in", value="12V"),
            ],
        )

        # 原始 IR 不变
        v_in_after = next(
            c.value for c in ir.intent.known_constraints if c.name == "v_in"
        )
        assert v_in_after == original_v_in

    def test_update_constraint_missing_field_rejected(self) -> None:
        """update_constraint 没有 field 时应被拒绝"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(op_type="update_constraint", field="", value="5V"),
            ],
        )

        assert not result.success
        assert len(result.rejected_ops) == 1

    def test_update_constraint_affected_modules_tracked(self) -> None:
        """更新约束时，依赖此约束的模块应出现在 affected_modules"""
        ir = _make_simple_ir()
        engine = _make_engine()

        # main_regulator 的 target_specs 包含 v_in
        result = engine.apply(
            ir,
            [
                IRPatchOp(op_type="update_constraint", field="v_in", value="12V"),
            ],
        )

        assert "main_regulator" in result.affected_modules


# ============================================================
# replace_device 测试
# ============================================================


class TestReplaceDevice:
    def test_replace_device_changes_part_number(self) -> None:
        """replace_device 更新模块选中的器件"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(
                    op_type="replace_device",
                    target_module="main_regulator",
                    value="LM3940-3.3",
                ),
            ],
        )

        assert result.success
        m = result.modified_ir.get_module("main_regulator")
        assert m.selection.selected.part_number == "LM3940-3.3"

    def test_replace_device_resets_parameters(self) -> None:
        """replace_device 重置模块参数"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(
                    op_type="replace_device",
                    target_module="main_regulator",
                    value="LM3940-3.3",
                ),
            ],
        )

        m = result.modified_ir.get_module("main_regulator")
        assert m.parameters.input_params == {}
        assert m.parameters.render_params == {}

    def test_replace_device_resets_review(self) -> None:
        """replace_device 重置模块审查"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(
                    op_type="replace_device",
                    target_module="main_regulator",
                    value="LM3940-3.3",
                ),
            ],
        )

        m = result.modified_ir.get_module("main_regulator")
        assert m.review.issues == []

    def test_replace_device_nonexistent_module_rejected(self) -> None:
        """替换不存在的模块应被拒绝"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(
                    op_type="replace_device",
                    target_module="nonexistent_module",
                    value="SOME-CHIP",
                ),
            ],
        )

        assert not result.success
        assert len(result.rejected_ops) == 1

    def test_replace_device_missing_target_rejected(self) -> None:
        """replace_device 没有 target_module 时应被拒绝"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(
                    op_type="replace_device", target_module="", value="SOME-CHIP"
                ),
            ],
        )

        assert not result.success


# ============================================================
# add_module 测试
# ============================================================


class TestAddModule:
    def test_add_module_increases_count(self) -> None:
        """add_module 新增模块后数量增加"""
        ir = _make_simple_ir()
        original_count = len(ir.modules)
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(
                    op_type="add_module",
                    value={"role": "protection_circuit", "category": "tvs"},
                ),
            ],
        )

        assert result.success
        assert len(result.modified_ir.modules) == original_count + 1

    def test_add_module_module_accessible_by_role(self) -> None:
        """新增的模块可以通过 role 查找"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(
                    op_type="add_module",
                    value={
                        "role": "filter_cap",
                        "category": "capacitor",
                        "description": "输出滤波电容",
                        "target_specs": {"c_out": "100uF"},
                        "depends_on": ["main_regulator"],
                    },
                ),
            ],
        )

        m = result.modified_ir.get_module("filter_cap")
        assert m is not None
        assert m.intent.category == "capacitor"
        assert m.intent.description == "输出滤波电容"
        assert "main_regulator" in m.intent.depends_on

    def test_add_module_duplicate_role_rejected(self) -> None:
        """尝试添加已存在 role 的模块应被拒绝"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(
                    op_type="add_module",
                    value={"role": "main_regulator", "category": "ldo"},
                ),
            ],
        )

        assert not result.success
        assert len(result.rejected_ops) == 1

    def test_add_module_missing_role_rejected(self) -> None:
        """add_module 的 value 中缺少 role 时应被拒绝"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(op_type="add_module", value={"category": "ldo"}),
            ],
        )

        assert not result.success

    def test_add_module_non_dict_value_rejected(self) -> None:
        """add_module 的 value 不是字典时应被拒绝"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(op_type="add_module", value="not_a_dict"),
            ],
        )

        assert not result.success

    def test_add_module_in_affected_modules(self) -> None:
        """新增模块的 role 应出现在 affected_modules"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(
                    op_type="add_module",
                    value={"role": "new_module", "category": "misc"},
                ),
            ],
        )

        assert "new_module" in result.affected_modules


# ============================================================
# remove_module 测试
# ============================================================


class TestRemoveModule:
    def test_remove_module_decreases_count(self) -> None:
        """remove_module 删除模块后数量减少"""
        ir = _make_simple_ir()
        original_count = len(ir.modules)
        engine = _make_engine()

        # 删除没有被依赖的 led_indicator（它依赖 main_regulator，但无人依赖它）
        result = engine.apply(
            ir,
            [
                IRPatchOp(op_type="remove_module", target_module="led_indicator"),
            ],
        )

        assert result.success
        assert len(result.modified_ir.modules) == original_count - 1

    def test_remove_module_not_accessible_after(self) -> None:
        """删除后模块不可通过 role 查找"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(op_type="remove_module", target_module="led_indicator"),
            ],
        )

        assert result.modified_ir.get_module("led_indicator") is None

    def test_remove_module_with_dependents_rejected(self) -> None:
        """删除有依赖者的模块应被拒绝"""
        ir = _make_simple_ir()
        engine = _make_engine()

        # led_indicator 依赖 main_regulator，所以 main_regulator 不能被删除
        result = engine.apply(
            ir,
            [
                IRPatchOp(op_type="remove_module", target_module="main_regulator"),
            ],
        )

        assert not result.success
        reason = result.rejected_ops[0][1]
        assert "led_indicator" in reason or "依赖" in reason

    def test_remove_module_nonexistent_rejected(self) -> None:
        """删除不存在的模块应被拒绝"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(op_type="remove_module", target_module="nonexistent"),
            ],
        )

        assert not result.success

    def test_remove_module_missing_target_rejected(self) -> None:
        """remove_module 没有 target_module 时应被拒绝"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(op_type="remove_module", target_module=""),
            ],
        )

        assert not result.success


# ============================================================
# update_parameter 测试
# ============================================================


class TestUpdateParameter:
    def test_update_existing_input_param(self) -> None:
        """更新模块 input_params 中的参数"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(
                    op_type="update_parameter",
                    target_module="main_regulator",
                    field="v_in",
                    value="12",
                ),
            ],
        )

        assert result.success
        m = result.modified_ir.get_module("main_regulator")
        assert m.parameters.input_params["v_in"] == "12"

    def test_update_existing_render_param(self) -> None:
        """更新模块 render_params 中的参数"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(
                    op_type="update_parameter",
                    target_module="main_regulator",
                    field="r_limit",
                    value="150Ω",
                ),
            ],
        )

        assert result.success
        m = result.modified_ir.get_module("main_regulator")
        assert m.parameters.render_params["r_limit"] == "150Ω"

    def test_update_parameter_adds_new_param(self) -> None:
        """对不存在的参数更新时应新增到 input_params"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(
                    op_type="update_parameter",
                    target_module="main_regulator",
                    field="c_in",
                    value="10uF",
                ),
            ],
        )

        assert result.success
        m = result.modified_ir.get_module("main_regulator")
        assert m.parameters.input_params["c_in"] == "10uF"

    def test_update_parameter_marks_module_for_reverification(self) -> None:
        """update_parameter 后模块审查应被清空（需要重新验证）"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(
                    op_type="update_parameter",
                    target_module="main_regulator",
                    field="v_in",
                    value="12",
                ),
            ],
        )

        m = result.modified_ir.get_module("main_regulator")
        assert m.review.issues == []

    def test_update_parameter_nonexistent_module_rejected(self) -> None:
        """更新不存在模块的参数应被拒绝"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(
                    op_type="update_parameter",
                    target_module="ghost_module",
                    field="v_in",
                    value="12",
                ),
            ],
        )

        assert not result.success

    def test_update_parameter_missing_field_rejected(self) -> None:
        """update_parameter 没有 field 时应被拒绝"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(
                    op_type="update_parameter",
                    target_module="main_regulator",
                    field="",
                    value="12",
                ),
            ],
        )

        assert not result.success


# ============================================================
# change_preference 测试
# ============================================================


class TestChangePreference:
    def test_change_existing_assumption(self) -> None:
        """更新现有假设的值"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(op_type="change_preference", field="i_load", value="1A"),
            ],
        )

        assert result.success
        a = next(
            (a for a in result.modified_ir.intent.assumptions if a.field == "i_load"),
            None,
        )
        assert a is not None
        assert a.assumed_value == "1A"

    def test_change_preference_adds_new_if_not_exists(self) -> None:
        """对不存在的偏好字段应新增假设"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(
                    op_type="change_preference", field="ambient_temp", value="85°C"
                ),
            ],
        )

        assert result.success
        a = next(
            (
                a
                for a in result.modified_ir.intent.assumptions
                if a.field == "ambient_temp"
            ),
            None,
        )
        assert a is not None
        assert a.assumed_value == "85°C"

    def test_change_preference_missing_field_rejected(self) -> None:
        """change_preference 没有 field 时应被拒绝"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(op_type="change_preference", field="", value="123"),
            ],
        )

        assert not result.success


# ============================================================
# apply() 通用行为测试
# ============================================================


class TestApplyBehavior:
    def test_apply_creates_patch_record(self) -> None:
        """apply 成功后应在 ir.history 中创建 PatchRecord"""
        ir = _make_simple_ir()
        original_history_len = len(ir.history)
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(op_type="update_constraint", field="v_in", value="12V"),
            ],
        )

        assert result.success
        assert len(result.modified_ir.history) == original_history_len + 1
        assert isinstance(result.modified_ir.history[-1], PatchRecord)

    def test_apply_bumps_version(self) -> None:
        """apply 成功后 IR 版本号应增加"""
        ir = _make_simple_ir()
        original_version = ir.version
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(op_type="update_constraint", field="v_in", value="12V"),
            ],
        )

        assert result.modified_ir.version == original_version + 1

    def test_apply_returns_deep_copy(self) -> None:
        """apply 返回深拷贝，原始 IR 不受影响"""
        ir = _make_simple_ir()
        original_v_in = next(
            c.value for c in ir.intent.known_constraints if c.name == "v_in"
        )
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(op_type="update_constraint", field="v_in", value="12V"),
            ],
        )

        assert result.success
        # 原始 IR 不变
        v_in_original = next(
            c.value for c in ir.intent.known_constraints if c.name == "v_in"
        )
        assert v_in_original == original_v_in
        # 修改后的 IR 已变
        v_in_modified = next(
            c.value
            for c in result.modified_ir.intent.known_constraints
            if c.name == "v_in"
        )
        assert v_in_modified == "12V"

    def test_apply_multiple_ops_in_sequence(self) -> None:
        """多个操作依次应用"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(op_type="update_constraint", field="v_in", value="12V"),
                IRPatchOp(
                    op_type="replace_device",
                    target_module="main_regulator",
                    value="LM1117-3.3",
                ),
                IRPatchOp(
                    op_type="add_module",
                    value={"role": "protection", "category": "tvs"},
                ),
            ],
        )

        assert result.success
        assert len(result.applied_ops) == 3
        # 确认三个操作都已应用
        updated = result.modified_ir
        v_in = next(
            c.value for c in updated.intent.known_constraints if c.name == "v_in"
        )
        assert v_in == "12V"
        m = updated.get_module("main_regulator")
        assert m.selection.selected.part_number == "LM1117-3.3"
        assert updated.get_module("protection") is not None

    def test_apply_empty_ops_success_no_changes(self) -> None:
        """空操作列表应返回 success=False（无操作执行）"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(ir, [])

        assert not result.success
        assert result.applied_ops == []

    def test_apply_invalid_op_type_rejected(self) -> None:
        """无效操作类型应被拒绝"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(op_type="unknown_op", field="v_in", value="5V"),
            ],
        )

        assert not result.success
        assert len(result.rejected_ops) == 1
        assert "unknown_op" in result.rejected_ops[0][1]

    def test_apply_mixed_valid_invalid_ops(self) -> None:
        """混合有效和无效操作时，有效操作应执行，无效操作被记录"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(
                    op_type="update_constraint", field="v_in", value="12V"
                ),  # 有效
                IRPatchOp(
                    op_type="replace_device", target_module="ghost", value="X"
                ),  # 无效
            ],
        )

        # 有有效操作，success=True
        assert result.success
        assert len(result.applied_ops) == 1
        assert len(result.rejected_ops) == 1

    def test_apply_affected_modules_correctly_tracked(self) -> None:
        """受影响模块列表应正确追踪"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(
                    op_type="replace_device",
                    target_module="main_regulator",
                    value="NEW-CHIP",
                ),
                IRPatchOp(
                    op_type="update_parameter",
                    target_module="led_indicator",
                    field="v_f",
                    value="2.5",
                ),
            ],
        )

        assert "main_regulator" in result.affected_modules
        assert "led_indicator" in result.affected_modules

    def test_patch_record_contains_op_details(self) -> None:
        """PatchRecord 应包含操作详情"""
        ir = _make_simple_ir()
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(
                    op_type="update_constraint",
                    field="v_in",
                    value="12V",
                    reason="用户要求修改输入电压",
                ),
            ],
        )

        assert result.patch_record is not None
        assert len(result.patch_record.ops) == 1
        assert result.patch_record.ops[0]["op_type"] == "update_constraint"
        assert result.patch_record.ops[0]["field"] == "v_in"

    def test_apply_version_not_bumped_on_all_failures(self) -> None:
        """全部操作失败时版本号不应增加"""
        ir = _make_simple_ir()
        original_version = ir.version
        engine = _make_engine()

        result = engine.apply(
            ir,
            [
                IRPatchOp(op_type="invalid_op", field="v_in", value="5V"),
            ],
        )

        assert not result.success
        # 修改后的副本版本号不变
        assert result.modified_ir.version == original_version


# ============================================================
# preview() 测试
# ============================================================


class TestPreview:
    def test_preview_returns_descriptions(self) -> None:
        """preview 应返回人类可读的描述列表"""
        ir = _make_simple_ir()
        engine = _make_engine()

        descriptions = engine.preview(
            ir,
            [
                IRPatchOp(op_type="update_constraint", field="v_in", value="12V"),
                IRPatchOp(
                    op_type="replace_device",
                    target_module="main_regulator",
                    value="LM1117",
                ),
            ],
        )

        assert len(descriptions) == 2
        assert all(isinstance(d, str) for d in descriptions)

    def test_preview_update_constraint_existing(self) -> None:
        """preview update_constraint（已存在约束）描述应含旧值和新值"""
        ir = _make_simple_ir()
        engine = _make_engine()

        descriptions = engine.preview(
            ir,
            [
                IRPatchOp(op_type="update_constraint", field="v_in", value="12V"),
            ],
        )

        assert "v_in" in descriptions[0]
        assert "5V" in descriptions[0]
        assert "12V" in descriptions[0]

    def test_preview_update_constraint_new(self) -> None:
        """preview update_constraint（新约束）描述应含新增提示"""
        ir = _make_simple_ir()
        engine = _make_engine()

        descriptions = engine.preview(
            ir,
            [
                IRPatchOp(op_type="update_constraint", field="new_field", value="42"),
            ],
        )

        assert "new_field" in descriptions[0]

    def test_preview_does_not_modify_ir(self) -> None:
        """preview 不应修改原始 IR"""
        ir = _make_simple_ir()
        original_version = ir.version
        engine = _make_engine()

        engine.preview(
            ir,
            [
                IRPatchOp(op_type="update_constraint", field="v_in", value="12V"),
            ],
        )

        assert ir.version == original_version

    def test_preview_remove_module(self) -> None:
        """preview remove_module 应描述删除操作"""
        ir = _make_simple_ir()
        engine = _make_engine()

        descriptions = engine.preview(
            ir,
            [
                IRPatchOp(op_type="remove_module", target_module="led_indicator"),
            ],
        )

        assert "led_indicator" in descriptions[0]
        assert "remove_module" in descriptions[0] or "删除" in descriptions[0]

    def test_preview_add_module(self) -> None:
        """preview add_module 应描述新增操作"""
        ir = _make_simple_ir()
        engine = _make_engine()

        descriptions = engine.preview(
            ir,
            [
                IRPatchOp(
                    op_type="add_module",
                    value={"role": "new_cap", "category": "capacitor"},
                ),
            ],
        )

        assert "new_cap" in descriptions[0]


# ============================================================
# validate() 测试
# ============================================================


class TestValidate:
    def test_validate_returns_empty_for_valid_ops(self) -> None:
        """有效操作序列应返回空错误列表"""
        ir = _make_simple_ir()
        engine = _make_engine()

        errors = engine.validate(
            ir,
            [
                IRPatchOp(op_type="update_constraint", field="v_in", value="12V"),
                IRPatchOp(
                    op_type="replace_device",
                    target_module="main_regulator",
                    value="LM1117",
                ),
                IRPatchOp(op_type="remove_module", target_module="led_indicator"),
            ],
        )

        assert errors == []

    def test_validate_returns_errors_for_invalid_op_type(self) -> None:
        """无效操作类型应返回错误"""
        ir = _make_simple_ir()
        engine = _make_engine()

        errors = engine.validate(
            ir,
            [
                IRPatchOp(op_type="bad_op", field="v_in", value="5V"),
            ],
        )

        assert len(errors) == 1
        assert "bad_op" in errors[0]

    def test_validate_replace_device_nonexistent_module(self) -> None:
        """replace_device 指向不存在模块时应返回错误"""
        ir = _make_simple_ir()
        engine = _make_engine()

        errors = engine.validate(
            ir,
            [
                IRPatchOp(
                    op_type="replace_device",
                    target_module="ghost",
                    value="CHIP",
                ),
            ],
        )

        assert len(errors) == 1

    def test_validate_remove_module_with_dependents(self) -> None:
        """删除有依赖者的模块时应返回错误"""
        ir = _make_simple_ir()
        engine = _make_engine()

        errors = engine.validate(
            ir,
            [
                IRPatchOp(op_type="remove_module", target_module="main_regulator"),
            ],
        )

        assert len(errors) == 1
        assert "led_indicator" in errors[0] or "依赖" in errors[0]

    def test_validate_update_parameter_nonexistent_module(self) -> None:
        """update_parameter 指向不存在模块时应返回错误"""
        ir = _make_simple_ir()
        engine = _make_engine()

        errors = engine.validate(
            ir,
            [
                IRPatchOp(
                    op_type="update_parameter",
                    target_module="ghost",
                    field="v_in",
                    value="5",
                ),
            ],
        )

        assert len(errors) == 1

    def test_validate_add_module_duplicate_role(self) -> None:
        """add_module 时 role 已存在应返回错误"""
        ir = _make_simple_ir()
        engine = _make_engine()

        errors = engine.validate(
            ir,
            [
                IRPatchOp(
                    op_type="add_module",
                    value={"role": "main_regulator", "category": "ldo"},
                ),
            ],
        )

        assert len(errors) == 1

    def test_validate_multiple_errors_reported(self) -> None:
        """多个无效操作时应返回多条错误"""
        ir = _make_simple_ir()
        engine = _make_engine()

        errors = engine.validate(
            ir,
            [
                IRPatchOp(op_type="bad_op_1", field="x"),
                IRPatchOp(op_type="bad_op_2", field="y"),
            ],
        )

        assert len(errors) == 2

    def test_validate_does_not_modify_ir(self) -> None:
        """validate 不应修改原始 IR"""
        ir = _make_simple_ir()
        original_version = ir.version
        engine = _make_engine()

        engine.validate(
            ir,
            [
                IRPatchOp(op_type="update_constraint", field="v_in", value="12V"),
            ],
        )

        assert ir.version == original_version
