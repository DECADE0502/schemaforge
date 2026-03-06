"""PatchEngine 单元测试

覆盖 set / add / remove / replace 操作，
以及路径解析、越界处理、预览不变异、混合操作部分成功等场景。
"""

from __future__ import annotations

import copy

from schemaforge.agent.protocol import PatchOp
from schemaforge.design.patch_engine import PatchEngine
from schemaforge.workflows.design_session import DesignSessionResult


# ============================================================
# 辅助工厂
# ============================================================


def _make_result(extra_modules: int = 0) -> DesignSessionResult:
    """构造包含典型 design_spec 的 DesignSessionResult"""
    modules = [
        {
            "template": "buck_converter",
            "instance_name": "buck1",
            "parameters": {
                "v_in": "12V",
                "v_out": "5V",
                "c_out": "47uF",
                "i_out": "2A",
            },
        },
        {
            "template": "ldo_regulator",
            "instance_name": "ldo1",
            "parameters": {
                "v_in": "5V",
                "v_out": "3.3V",
            },
        },
    ]
    for i in range(extra_modules):
        modules.append(
            {
                "template": "rc_lowpass",
                "instance_name": f"rc{i}",
                "parameters": {"r": "10k", "c": "100nF"},
            }
        )

    return DesignSessionResult(
        success=True,
        design_spec={
            "design_name": "test_design",
            "description": "测试用设计",
            "modules": modules,
            "connections": [{"from": "buck1.VOUT", "to": "ldo1.VIN"}],
            "notes": "自动生成",
        },
    )


# ============================================================
# 测试用例
# ============================================================


class TestPatchEngineSet:
    """set 操作测试"""

    def test_set_parameter(self) -> None:
        """1. 修改模块参数值"""
        engine = PatchEngine()
        result = _make_result()
        ops = [PatchOp(op="set", path="modules[0].parameters.c_out", value="100uF")]

        pr = engine.apply(result, ops)

        assert pr.success
        assert len(pr.applied_ops) == 1
        assert pr.modified_result is not None
        assert (
            pr.modified_result.design_spec["modules"][0]["parameters"]["c_out"]
            == "100uF"
        )
        # 原始未被修改
        assert result.design_spec["modules"][0]["parameters"]["c_out"] == "47uF"

    def test_set_nested_deep(self) -> None:
        """2. 深层嵌套路径修改"""
        engine = PatchEngine()
        result = _make_result()
        ops = [PatchOp(op="set", path="modules[1].parameters.v_out", value="2.5V")]

        pr = engine.apply(result, ops)

        assert pr.success
        assert (
            pr.modified_result.design_spec["modules"][1]["parameters"]["v_out"]
            == "2.5V"
        )

    def test_set_top_level(self) -> None:
        """3. 修改顶层字段"""
        engine = PatchEngine()
        result = _make_result()
        ops = [PatchOp(op="set", path="design_name", value="renamed_design")]

        pr = engine.apply(result, ops)

        assert pr.success
        assert pr.modified_result.design_spec["design_name"] == "renamed_design"

    def test_set_new_parameter_key(self) -> None:
        """4. 在 parameters 中新增一个不存在的键"""
        engine = PatchEngine()
        result = _make_result()
        ops = [
            PatchOp(
                op="set",
                path="modules[0].parameters.efficiency",
                value="95%",
            )
        ]

        pr = engine.apply(result, ops)

        assert pr.success
        assert (
            pr.modified_result.design_spec["modules"][0]["parameters"]["efficiency"]
            == "95%"
        )


class TestPatchEngineAdd:
    """add 操作测试"""

    def test_add_module(self) -> None:
        """5. 向 modules 列表追加新模块"""
        engine = PatchEngine()
        result = _make_result()
        new_module = {
            "template": "led_indicator",
            "instance_name": "led1",
            "parameters": {"r_led": "330R"},
        }
        ops = [PatchOp(op="add", path="modules", value=new_module)]

        pr = engine.apply(result, ops)

        assert pr.success
        assert len(pr.modified_result.design_spec["modules"]) == 3
        assert pr.modified_result.design_spec["modules"][-1]["instance_name"] == "led1"

    def test_add_connection(self) -> None:
        """6. 向 connections 列表追加新连接"""
        engine = PatchEngine()
        result = _make_result()
        ops = [
            PatchOp(
                op="add",
                path="connections",
                value={"from": "ldo1.VOUT", "to": "led1.ANODE"},
            )
        ]

        pr = engine.apply(result, ops)

        assert pr.success
        assert len(pr.modified_result.design_spec["connections"]) == 2


class TestPatchEngineRemove:
    """remove 操作测试"""

    def test_remove_module(self) -> None:
        """7. 删除指定索引的模块"""
        engine = PatchEngine()
        result = _make_result()
        original_len = len(result.design_spec["modules"])
        ops = [PatchOp(op="remove", path="modules[1]", value=None)]

        pr = engine.apply(result, ops)

        assert pr.success
        assert len(pr.modified_result.design_spec["modules"]) == original_len - 1
        # 确认删除的是 ldo1
        names = [m["instance_name"] for m in pr.modified_result.design_spec["modules"]]
        assert "ldo1" not in names

    def test_remove_only_module(self) -> None:
        """8. 删除列表中仅剩的最后一个模块"""
        engine = PatchEngine()
        result = DesignSessionResult(
            success=True,
            design_spec={
                "design_name": "single",
                "description": "",
                "modules": [
                    {
                        "template": "buck_converter",
                        "instance_name": "b1",
                        "parameters": {},
                    }
                ],
                "connections": [],
                "notes": "",
            },
        )
        ops = [PatchOp(op="remove", path="modules[0]", value=None)]

        pr = engine.apply(result, ops)

        assert pr.success
        assert pr.modified_result.design_spec["modules"] == []


class TestPatchEngineReplace:
    """replace 操作测试"""

    def test_replace_module(self) -> None:
        """9. 替换整个模块"""
        engine = PatchEngine()
        result = _make_result()
        new_module = {
            "template": "voltage_divider",
            "instance_name": "vdiv1",
            "parameters": {"r1": "10k", "r2": "10k"},
        }
        ops = [PatchOp(op="replace", path="modules[0]", value=new_module)]

        pr = engine.apply(result, ops)

        assert pr.success
        replaced = pr.modified_result.design_spec["modules"][0]
        assert replaced["instance_name"] == "vdiv1"
        assert replaced["template"] == "voltage_divider"


class TestPatchEngineErrors:
    """错误处理测试"""

    def test_invalid_path_rejects(self) -> None:
        """10. 不存在的路径应被拒绝"""
        engine = PatchEngine()
        result = _make_result()
        ops = [PatchOp(op="set", path="nonexistent_key.deep", value="x")]

        pr = engine.apply(result, ops)

        assert not pr.success
        assert len(pr.rejected_ops) == 1
        assert len(pr.applied_ops) == 0

    def test_index_out_of_bounds(self) -> None:
        """11. 数组索引越界应被拒绝"""
        engine = PatchEngine()
        result = _make_result()
        ops = [PatchOp(op="set", path="modules[99].parameters.x", value="y")]

        pr = engine.apply(result, ops)

        assert not pr.success
        assert len(pr.rejected_ops) == 1
        _, reason = pr.rejected_ops[0]
        assert "越界" in reason or "索引" in reason

    def test_validate_catches_errors(self) -> None:
        """12. validate() 应返回包含错误信息的列表"""
        engine = PatchEngine()
        result = _make_result()
        ops = [
            PatchOp(op="set", path="modules[99].parameters.x", value="y"),
            PatchOp(op="unknown_op", path="design_name", value="z"),
        ]

        errors = engine.validate(result, ops)

        assert len(errors) >= 2

    def test_unsupported_op_rejects(self) -> None:
        """13. 不支持的操作类型应被拒绝"""
        engine = PatchEngine()
        result = _make_result()
        ops = [PatchOp(op="delete_all", path="modules", value=None)]

        pr = engine.apply(result, ops)

        assert not pr.success
        assert len(pr.rejected_ops) == 1

    def test_add_to_non_list_rejects(self) -> None:
        """14. 对非列表路径执行 add 应被拒绝"""
        engine = PatchEngine()
        result = _make_result()
        # design_name 是字符串，不是列表
        ops = [PatchOp(op="add", path="design_name", value="extra")]

        pr = engine.apply(result, ops)

        assert not pr.success
        assert len(pr.rejected_ops) == 1


class TestPatchEnginePreview:
    """preview / 不变异测试"""

    def test_preview_does_not_mutate(self) -> None:
        """15. preview() 不应修改原始 result"""
        engine = PatchEngine()
        result = _make_result()
        original_spec = copy.deepcopy(result.design_spec)
        ops = [
            PatchOp(op="set", path="modules[0].parameters.c_out", value="999uF"),
            PatchOp(
                op="add",
                path="modules",
                value={"template": "t", "instance_name": "x", "parameters": {}},
            ),
        ]

        pr = engine.preview(result, ops)

        # 原始 result 未被修改
        assert result.design_spec == original_spec
        # 预览结果已应用
        assert (
            pr.modified_result.design_spec["modules"][0]["parameters"]["c_out"]
            == "999uF"
        )
        assert len(pr.modified_result.design_spec["modules"]) == 3


class TestPatchEngineMixed:
    """混合操作测试"""

    def test_mixed_ops_partial_success(self) -> None:
        """16. 部分操作成功，部分失败 — 成功的继续执行"""
        engine = PatchEngine()
        result = _make_result()
        ops = [
            PatchOp(
                op="set", path="modules[0].parameters.c_out", value="220uF"
            ),  # 成功
            PatchOp(op="set", path="modules[99].parameters.x", value="y"),  # 失败：越界
            PatchOp(op="set", path="design_name", value="updated"),  # 成功
        ]

        pr = engine.apply(result, ops)

        assert pr.success
        assert len(pr.applied_ops) == 2
        assert len(pr.rejected_ops) == 1
        assert (
            pr.modified_result.design_spec["modules"][0]["parameters"]["c_out"]
            == "220uF"
        )
        assert pr.modified_result.design_spec["design_name"] == "updated"

    def test_all_ops_fail_success_false(self) -> None:
        """17. 所有操作均失败时 success=False"""
        engine = PatchEngine()
        result = _make_result()
        ops = [
            PatchOp(op="set", path="modules[99].x", value="a"),
            PatchOp(op="remove", path="modules[99]", value=None),
        ]

        pr = engine.apply(result, ops)

        assert not pr.success
        assert len(pr.rejected_ops) == 2
        assert len(pr.applied_ops) == 0

    def test_remove_then_add(self) -> None:
        """18. 先删除再追加，模块数量保持不变"""
        engine = PatchEngine()
        result = _make_result()
        ops = [
            PatchOp(op="remove", path="modules[0]", value=None),
            PatchOp(
                op="add",
                path="modules",
                value={
                    "template": "led_indicator",
                    "instance_name": "led1",
                    "parameters": {},
                },
            ),
        ]

        pr = engine.apply(result, ops)

        assert pr.success
        assert len(pr.applied_ops) == 2
        assert len(pr.modified_result.design_spec["modules"]) == 2
