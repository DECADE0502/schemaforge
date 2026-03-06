"""测试参数计算器"""

import pytest
from schemaforge.core.calculator import (
    calculate_divider,
    calculate_led_resistor,
    calculate_rc_filter,
)
from schemaforge.render.base import find_nearest_e24, format_value


class TestFormatValue:
    def test_ohm_small(self):
        assert format_value(120, "Ω") == "120Ω"

    def test_ohm_kilo(self):
        assert format_value(10000, "Ω") == "10.0kΩ"

    def test_ohm_mega(self):
        assert format_value(1000000, "Ω") == "1.0MΩ"

    def test_farad_micro(self):
        assert format_value(10e-6, "F") == "10μF"

    def test_farad_nano(self):
        assert format_value(15.9e-9, "F") == "15.9nF"

    def test_farad_pico(self):
        assert format_value(100e-12, "F") == "100pF"


class TestE24:
    def test_exact_match(self):
        assert find_nearest_e24(100) == 100.0

    def test_close_match(self):
        # 110 should match 110 in E24 (1.1 * 100)
        assert find_nearest_e24(110) == pytest.approx(110.0, rel=1e-6)

    def test_round_up(self):
        # 115 should round to 110 or 120
        result = find_nearest_e24(115)
        assert result == pytest.approx(110.0, rel=1e-6) or result == pytest.approx(120.0, rel=1e-6)

    def test_kilo_range(self):
        result = find_nearest_e24(14500)
        assert result == 15000.0

    def test_zero_input(self):
        assert find_nearest_e24(0) == 1.0


class TestDividerCalculation:
    def test_half_voltage(self):
        result = calculate_divider(10.0, 5.0, 20.0)
        assert result["v_out_actual"] == pytest.approx(5.0, abs=0.5)
        assert result["r1"] > 0
        assert result["r2"] > 0

    def test_error_percent_reasonable(self):
        result = calculate_divider(12.0, 3.3, 20.0)
        assert result["error_percent"] < 5.0

    def test_strings_generated(self):
        result = calculate_divider(5.0, 2.5, 20.0)
        assert "r1_str" in result
        assert "r2_str" in result
        assert "Ω" in result["r1_str"]


class TestLEDCalculation:
    def test_green_led(self):
        result = calculate_led_resistor(3.3, "green", 10.0)
        assert result["v_forward"] == 2.2
        assert result["r_value"] > 0
        assert "r_str" in result

    def test_red_led(self):
        result = calculate_led_resistor(5.0, "red", 10.0)
        assert result["v_forward"] == 2.0
        assert result["actual_current_ma"] > 0

    def test_insufficient_voltage(self):
        result = calculate_led_resistor(1.5, "blue", 10.0)
        assert "error" in result

    def test_current_accuracy(self):
        result = calculate_led_resistor(3.3, "green", 10.0)
        # 实际电流应接近目标
        assert result["actual_current_ma"] == pytest.approx(10.0, abs=3.0)


class TestRCFilterCalculation:
    def test_1khz(self):
        result = calculate_rc_filter(1000.0, 10.0)
        assert result["f_actual"] == pytest.approx(1000.0, abs=1.0)
        assert result["c_raw"] > 0

    def test_high_frequency(self):
        result = calculate_rc_filter(100000.0, 1.0)
        assert result["f_actual"] == pytest.approx(100000.0, abs=10.0)

    def test_strings_generated(self):
        result = calculate_rc_filter(1000.0, 10.0)
        assert "r_str" in result
        assert "c_str" in result
