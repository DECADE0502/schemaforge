"""测试AI输出验证器"""

import pytest
from schemaforge.ai.validator import validate_design_spec


class TestValidDesigns:
    def test_single_module(self):
        data = {
            "design_name": "测试分压器",
            "modules": [
                {
                    "template": "voltage_divider",
                    "instance_name": "div1",
                    "parameters": {"v_in": "5", "v_out": "2.5", "r_total": "20"},
                },
            ],
            "connections": [],
        }
        result = validate_design_spec(data)
        assert result.is_valid

    def test_multi_module(self):
        data = {
            "design_name": "LDO+LED",
            "modules": [
                {
                    "template": "ldo_regulator",
                    "instance_name": "ldo1",
                    "parameters": {"v_in": "5", "v_out": "3.3", "c_in": "10uF", "c_out": "22uF"},
                },
                {
                    "template": "led_indicator",
                    "instance_name": "led1",
                    "parameters": {"v_supply": "3.3", "led_color": "green", "led_current": "10"},
                },
            ],
            "connections": [
                {
                    "from_module": "ldo1",
                    "from_net": "VOUT",
                    "to_module": "led1",
                    "to_net": "VCC",
                    "merged_net_name": "VOUT_3V3",
                },
            ],
        }
        result = validate_design_spec(data)
        assert result.is_valid


class TestInvalidDesigns:
    def test_missing_design_name(self):
        data = {
            "modules": [
                {
                    "template": "voltage_divider",
                    "instance_name": "div1",
                    "parameters": {"v_in": "5", "v_out": "2.5", "r_total": "20"},
                },
            ],
        }
        result = validate_design_spec(data)
        assert not result.is_valid

    def test_unknown_template(self):
        data = {
            "design_name": "测试",
            "modules": [
                {
                    "template": "nonexistent_template",
                    "instance_name": "x1",
                    "parameters": {},
                },
            ],
        }
        result = validate_design_spec(data)
        assert not result.is_valid

    def test_empty_modules(self):
        data = {
            "design_name": "空设计",
            "modules": [],
        }
        result = validate_design_spec(data)
        assert not result.is_valid

    def test_duplicate_instance_name(self):
        data = {
            "design_name": "重复名",
            "modules": [
                {
                    "template": "voltage_divider",
                    "instance_name": "same_name",
                    "parameters": {"v_in": "5", "v_out": "2.5", "r_total": "20"},
                },
                {
                    "template": "voltage_divider",
                    "instance_name": "same_name",
                    "parameters": {"v_in": "10", "v_out": "5", "r_total": "20"},
                },
            ],
        }
        result = validate_design_spec(data)
        assert not result.is_valid

    def test_invalid_choice_param(self):
        data = {
            "design_name": "无效参数",
            "modules": [
                {
                    "template": "ldo_regulator",
                    "instance_name": "ldo1",
                    "parameters": {"v_in": "5", "v_out": "7.7", "c_in": "10uF", "c_out": "22uF"},
                },
            ],
        }
        result = validate_design_spec(data)
        assert not result.is_valid

    def test_param_out_of_range(self):
        data = {
            "design_name": "超范围",
            "modules": [
                {
                    "template": "voltage_divider",
                    "instance_name": "div1",
                    "parameters": {"v_in": "500", "v_out": "2.5", "r_total": "20"},
                },
            ],
        }
        result = validate_design_spec(data)
        assert not result.is_valid

    def test_unknown_connection_module(self):
        data = {
            "design_name": "连接错误",
            "modules": [
                {
                    "template": "voltage_divider",
                    "instance_name": "div1",
                    "parameters": {"v_in": "5", "v_out": "2.5", "r_total": "20"},
                },
            ],
            "connections": [
                {
                    "from_module": "div1",
                    "from_net": "VOUT",
                    "to_module": "nonexistent",
                    "to_net": "VCC",
                },
            ],
        }
        result = validate_design_spec(data)
        assert not result.is_valid


class TestDefaultValues:
    def test_default_params_applied(self):
        data = {
            "design_name": "默认值测试",
            "modules": [
                {
                    "template": "voltage_divider",
                    "instance_name": "div1",
                    "parameters": {"v_in": "5", "v_out": "2.5"},
                    # r_total missing -> should use default
                },
            ],
        }
        result = validate_design_spec(data)
        assert result.is_valid
        assert len(result.warnings) > 0  # Should warn about default usage
