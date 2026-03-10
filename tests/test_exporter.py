"""测试BOM和SPICE导出"""

from schemaforge.core.exporter import _spice_value


class TestSpiceValue:
    """测试SPICE值格式化函数"""

    def test_ohm_stripped(self):
        assert _spice_value("110Ω") == "110"

    def test_kohm_to_k(self):
        assert _spice_value("10kΩ") == "10k"

    def test_mohm_to_meg(self):
        assert _spice_value("4.7MΩ") == "4.7Meg"

    def test_uf_to_u(self):
        assert _spice_value("10μF") == "10u"

    def test_nf_to_n(self):
        assert _spice_value("100nF") == "100n"

    def test_pf_to_p(self):
        assert _spice_value("22pF") == "22p"

    def test_plain_number_unchanged(self):
        assert _spice_value("120") == "120"
