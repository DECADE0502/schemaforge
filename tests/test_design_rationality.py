"""合理性检查模块测试"""

from __future__ import annotations

from schemaforge.design.rationality import (
    RationalityChecker,
    RationalityReport,
    _parse_number,
)
from schemaforge.library.models import (
    DeviceModel,
    TopologyDef,
)
from schemaforge.core.models import ParameterDef


def _make_ldo(
    v_out: str = "3.3V",
    v_dropout: str = "1.1V",
    i_out_max: str = "1A",
    v_in_max: str = "15V",
) -> DeviceModel:
    """创建测试用 LDO"""
    return DeviceModel(
        part_number="TEST-LDO",
        category="ldo",
        specs={
            "v_out": v_out,
            "v_dropout": v_dropout,
            "i_out_max": i_out_max,
            "v_in_max": v_in_max,
        },
        topology=TopologyDef(
            circuit_type="ldo",
            parameters={
                "v_in": ParameterDef(name="v_in", default="5"),
            },
        ),
    )


def _make_generic_device() -> DeviceModel:
    """创建无拓扑的通用器件"""
    return DeviceModel(
        part_number="GENERIC",
        category="other",
    )


class TestRationalityChecker:
    """合理性检查器测试"""

    def setup_method(self) -> None:
        self.checker = RationalityChecker()

    # --- 拓扑检查 ---

    def test_no_topology_is_error(self) -> None:
        device = _make_generic_device()
        report = self.checker.check(device)
        assert report.has_errors
        assert any(i.rule_id == "TOPO_MISSING" for i in report.issues)

    def test_with_topology_no_topo_error(self) -> None:
        device = _make_ldo()
        report = self.checker.check(device, {"v_in": "5", "v_out": "3.3"})
        assert not any(i.rule_id == "TOPO_MISSING" for i in report.issues)

    # --- 电压范围 ---

    def test_vin_over_max_is_error(self) -> None:
        device = _make_ldo(v_in_max="15V")
        report = self.checker.check(device, {"v_in": "20"})
        assert any(i.rule_id == "VIN_OVER_MAX" for i in report.issues)
        assert report.has_errors

    def test_vin_near_max_is_warning(self) -> None:
        device = _make_ldo(v_in_max="15V")
        report = self.checker.check(device, {"v_in": "14"})
        assert any(i.rule_id == "VIN_NEAR_MAX" for i in report.issues)
        assert report.has_warnings

    def test_vin_within_range_no_issue(self) -> None:
        device = _make_ldo(v_in_max="15V")
        report = self.checker.check(device, {"v_in": "5"})
        assert not any(
            i.rule_id in ("VIN_OVER_MAX", "VIN_NEAR_MAX")
            for i in report.issues
        )

    # --- 压差检查 ---

    def test_vout_ge_vin_is_error(self) -> None:
        device = _make_ldo()
        report = self.checker.check(device, {"v_in": "3", "v_out": "3.3"})
        assert any(i.rule_id == "VOUT_GE_VIN" for i in report.issues)

    def test_dropout_insufficient_is_error(self) -> None:
        device = _make_ldo(v_dropout="1.1V")
        report = self.checker.check(device, {"v_in": "4", "v_out": "3.3"})
        # 4 - 3.3 = 0.7 < 1.1
        assert any(i.rule_id == "DROPOUT_INSUFFICIENT" for i in report.issues)

    def test_dropout_marginal_is_warning(self) -> None:
        device = _make_ldo(v_dropout="1.1V")
        report = self.checker.check(device, {"v_in": "4.5", "v_out": "3.3"})
        # 4.5 - 3.3 = 1.2, 比 1.1 大但比 1.65 (1.5×1.1) 小
        assert any(i.rule_id == "DROPOUT_MARGINAL" for i in report.issues)

    def test_dropout_sufficient_no_issue(self) -> None:
        device = _make_ldo(v_dropout="1.1V")
        report = self.checker.check(device, {"v_in": "5", "v_out": "3.3"})
        # 5 - 3.3 = 1.7 > 1.65
        assert not any(
            i.rule_id in ("DROPOUT_INSUFFICIENT", "DROPOUT_MARGINAL")
            for i in report.issues
        )

    # --- 电流检查 ---

    def test_iout_over_max_is_error(self) -> None:
        device = _make_ldo(i_out_max="1A")
        report = self.checker.check(device, {"i_out": "1.5"})
        assert any(i.rule_id == "IOUT_OVER_MAX" for i in report.issues)

    def test_iout_near_max_is_warning(self) -> None:
        device = _make_ldo(i_out_max="1A")
        report = self.checker.check(device, {"i_out": "0.9"})
        assert any(i.rule_id == "IOUT_NEAR_MAX" for i in report.issues)

    # --- 功耗检查 ---

    def test_power_excessive_is_error(self) -> None:
        device = _make_ldo()
        # (12 - 3.3) * 0.5 = 4.35W
        report = self.checker.check(
            device, {"v_in": "12", "v_out": "3.3", "i_out": "0.5"},
        )
        assert any(i.rule_id == "POWER_EXCESSIVE" for i in report.issues)

    def test_power_high_is_warning(self) -> None:
        device = _make_ldo()
        # (5 - 3.3) * 0.8 = 1.36W
        report = self.checker.check(
            device, {"v_in": "5", "v_out": "3.3", "i_out": "0.8"},
        )
        assert any(i.rule_id == "POWER_HIGH" for i in report.issues)

    def test_power_ok_no_issue(self) -> None:
        device = _make_ldo()
        # (5 - 3.3) * 0.1 = 0.17W
        report = self.checker.check(
            device, {"v_in": "5", "v_out": "3.3", "i_out": "0.1"},
        )
        assert not any(
            i.rule_id in ("POWER_EXCESSIVE", "POWER_HIGH")
            for i in report.issues
        )

    # --- 参数完整性 ---

    def test_missing_param_info(self) -> None:
        device = DeviceModel(
            part_number="TEST",
            category="ldo",
            topology=TopologyDef(
                circuit_type="ldo",
                parameters={
                    "v_in": ParameterDef(name="v_in"),  # 无默认值
                },
            ),
        )
        report = self.checker.check(device, {})
        assert any(i.rule_id == "PARAM_MISSING" for i in report.issues)

    def test_param_with_default_no_warning(self) -> None:
        device = _make_ldo()
        report = self.checker.check(device, {})
        # v_in 有默认值 "5"，不应告警
        assert not any(
            i.rule_id == "PARAM_MISSING" and "v_in" in i.message
            for i in report.issues
        )

    # --- 批量检查 ---

    def test_check_multi(self) -> None:
        device1 = _make_ldo()
        device2 = _make_generic_device()
        reports = self.checker.check_multi([
            (device1, {"v_in": "5", "v_out": "3.3"}),
            (device2, {}),
        ])
        assert len(reports) == 2
        assert reports[0].is_acceptable  # LDO ok
        assert not reports[1].is_acceptable  # 无拓扑

    # --- 报告属性 ---

    def test_report_is_acceptable(self) -> None:
        device = _make_ldo()
        report = self.checker.check(device, {"v_in": "5", "v_out": "3.3"})
        assert report.is_acceptable

    def test_report_summary(self) -> None:
        device = _make_generic_device()
        report = self.checker.check(device)
        summary = report.summary()
        assert "TOPO_MISSING" in summary

    def test_report_to_dict(self) -> None:
        device = _make_ldo()
        report = self.checker.check(device, {"v_in": "5", "v_out": "3.3"})
        d = report.to_dict()
        assert "is_acceptable" in d
        assert "issues" in d

    def test_empty_report_summary(self) -> None:
        report = RationalityReport()
        assert "通过" in report.summary()

    # --- 非 LDO 跳过压差检查 ---

    def test_non_ldo_skips_dropout(self) -> None:
        device = DeviceModel(
            part_number="BUCK_TEST",
            category="buck",
            specs={"v_in_max": "30V"},
            topology=TopologyDef(circuit_type="buck"),
        )
        report = self.checker.check(device, {"v_in": "12", "v_out": "3.3"})
        assert not any(
            i.rule_id in ("VOUT_GE_VIN", "DROPOUT_INSUFFICIENT")
            for i in report.issues
        )


class TestParseNumber:
    """数值解析测试"""

    def test_voltage(self) -> None:
        assert _parse_number("3.3V") == 3.3

    def test_current(self) -> None:
        assert _parse_number("1A") == 1.0

    def test_plain(self) -> None:
        assert _parse_number("42") == 42.0

    def test_negative(self) -> None:
        assert _parse_number("-5V") == -5.0

    def test_no_number(self) -> None:
        assert _parse_number("abc") is None

    def test_empty(self) -> None:
        assert _parse_number("") is None
