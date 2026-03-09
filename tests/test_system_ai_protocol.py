"""测试 schemaforge.system.ai_protocol

覆盖: T021-T030 系统级 AI 意图解析协议
- schema 校验 (validate_ai_schema)
- 归一化 (normalize_ai_intents)
- 歧义检测 (detect_ambiguities)
- 正则 fallback (regex_fallback_parse)
- 完整流程 (parse_system_intent)
"""

from __future__ import annotations

from schemaforge.system.ai_protocol import (
    SYSTEM_PARSE_PROMPT,
    AISystemParseResponse,
    detect_ambiguities,
    normalize_ai_intents,
    parse_system_intent,
    regex_fallback_parse,
    validate_ai_schema,
)
from schemaforge.system.models import (
    ConnectionIntent,
    ConnectionSemantic,
    ModuleIntent,
    SignalType,
    SystemDesignRequest,
)


# ============================================================
# 测试数据
# ============================================================

_VALID_AI_JSON = {
    "modules": [
        {
            "intent_id": "buck1",
            "role": "第一级降压",
            "part_number_hint": "TPS54202",
            "category_hint": "buck",
            "electrical_targets": {"v_in": "20", "v_out": "5"},
            "placement_hint": "power_chain",
            "priority": 0,
        },
        {
            "intent_id": "ldo1",
            "role": "二级稳压",
            "part_number_hint": "AMS1117",
            "category_hint": "ldo",
            "electrical_targets": {"v_in": "5", "v_out": "3.3"},
            "placement_hint": "power_chain",
            "priority": 1,
        },
    ],
    "connections": [
        {
            "connection_id": "c1",
            "src_module_intent": "buck1",
            "src_port_hint": "VOUT",
            "dst_module_intent": "ldo1",
            "dst_port_hint": "VIN",
            "signal_type": "power_supply",
            "connection_semantics": "supply_chain",
        },
    ],
    "global_v_in": "20",
    "ambiguities": [],
    "design_notes": "两级降压架构",
}


# ============================================================
# T021: 系统提示词
# ============================================================


class TestSystemPrompt:
    def test_prompt_is_nonempty_string(self):
        assert isinstance(SYSTEM_PARSE_PROMPT, str)
        assert len(SYSTEM_PARSE_PROMPT) > 100

    def test_prompt_mentions_json_format(self):
        assert "JSON" in SYSTEM_PARSE_PROMPT

    def test_prompt_forbids_component_values(self):
        """C01: AI 不允许输出最终电阻电容电感数值。"""
        assert "电阻" in SYSTEM_PARSE_PROMPT or "数值" in SYSTEM_PARSE_PROMPT

    def test_prompt_requires_modules(self):
        """C06: AI 必须输出模块列表。"""
        assert "modules" in SYSTEM_PARSE_PROMPT

    def test_prompt_requires_ambiguities(self):
        """C09/C13: AI 必须显式标出不确定项。"""
        assert "ambiguities" in SYSTEM_PARSE_PROMPT

    def test_prompt_preserves_part_numbers(self):
        """C12: AI 对精确型号必须零容忍替换。"""
        assert "保留" in SYSTEM_PARSE_PROMPT or "原样" in SYSTEM_PARSE_PROMPT


# ============================================================
# T026: Schema 校验
# ============================================================


class TestAISystemParseResponse:
    def test_valid_json_parses(self):
        resp = AISystemParseResponse.model_validate(_VALID_AI_JSON)
        assert len(resp.modules) == 2
        assert len(resp.connections) == 1
        assert resp.global_v_in == "20"

    def test_empty_json_parses_with_defaults(self):
        resp = AISystemParseResponse.model_validate({})
        assert resp.modules == []
        assert resp.connections == []
        assert resp.global_v_in == ""

    def test_serialization_roundtrip(self):
        resp = AISystemParseResponse.model_validate(_VALID_AI_JSON)
        dumped = resp.model_dump()
        resp2 = AISystemParseResponse.model_validate(dumped)
        assert len(resp2.modules) == len(resp.modules)


# ============================================================
# T027: validate_ai_schema
# ============================================================


class TestValidateAiSchema:
    def test_valid_json_no_errors(self):
        errors = validate_ai_schema(_VALID_AI_JSON)
        assert errors == []

    def test_empty_dict_no_errors(self):
        """空字典有默认值，应该通过。"""
        errors = validate_ai_schema({})
        assert errors == []

    def test_bad_modules_type(self):
        """modules 不是列表应该报错。"""
        errors = validate_ai_schema({"modules": "not_a_list"})
        assert len(errors) > 0
        assert any("modules" in e for e in errors)

    def test_module_missing_intent_id(self):
        """模块缺少 intent_id 应该报错。"""
        bad = {"modules": [{"role": "降压"}]}
        errors = validate_ai_schema(bad)
        assert len(errors) > 0

    def test_bad_priority_type(self):
        """priority 不是 int 应该报错。"""
        bad = {
            "modules": [{
                "intent_id": "buck1",
                "priority": "not_a_number",
            }],
        }
        errors = validate_ai_schema(bad)
        assert len(errors) > 0

    def test_connection_missing_id(self):
        """连接缺少 connection_id 应该报错。"""
        bad = {
            "connections": [{"src_module_intent": "buck1"}],
        }
        errors = validate_ai_schema(bad)
        assert len(errors) > 0


# ============================================================
# T028: normalize_ai_intents
# ============================================================


class TestNormalizeAiIntents:
    def test_basic_normalization(self):
        req = normalize_ai_intents(_VALID_AI_JSON, raw_text="20V到5V再到3.3V")
        assert isinstance(req, SystemDesignRequest)
        assert req.raw_text == "20V到5V再到3.3V"
        assert len(req.modules) == 2
        assert len(req.connections) == 1

    def test_module_fields_preserved(self):
        req = normalize_ai_intents(_VALID_AI_JSON)
        m0 = req.modules[0]
        assert m0.intent_id == "buck1"
        assert m0.part_number_hint == "TPS54202"
        assert m0.category_hint == "buck"
        assert m0.electrical_targets["v_in"] == "20"
        assert m0.electrical_targets["v_out"] == "5"
        assert m0.priority == 0

    def test_connection_fields_normalized(self):
        req = normalize_ai_intents(_VALID_AI_JSON)
        c0 = req.connections[0]
        assert c0.signal_type == SignalType.POWER_SUPPLY
        assert c0.connection_semantics == ConnectionSemantic.SUPPLY_CHAIN

    def test_unknown_signal_type_defaults_to_other(self):
        data = {
            "connections": [{
                "connection_id": "c1",
                "src_module_intent": "a",
                "signal_type": "invalid_type_xyz",
                "connection_semantics": "invalid_sem",
            }],
        }
        req = normalize_ai_intents(data)
        assert req.connections[0].signal_type == SignalType.OTHER
        assert req.connections[0].connection_semantics == ConnectionSemantic.UNKNOWN

    def test_missing_fields_use_defaults(self):
        data = {
            "modules": [{"intent_id": "x1"}],
        }
        req = normalize_ai_intents(data)
        assert len(req.modules) == 1
        assert req.modules[0].role == ""
        assert req.modules[0].part_number_hint == ""
        assert req.modules[0].category_hint == ""

    def test_category_hint_lowercased(self):
        data = {
            "modules": [{"intent_id": "b1", "category_hint": "BUCK"}],
        }
        req = normalize_ai_intents(data)
        assert req.modules[0].category_hint == "buck"

    def test_whitespace_stripped(self):
        data = {
            "modules": [{
                "intent_id": "  ldo1  ",
                "role": "  稳压  ",
                "part_number_hint": " AMS1117 ",
            }],
            "global_v_in": " 5 ",
        }
        req = normalize_ai_intents(data)
        assert req.modules[0].intent_id == "ldo1"
        assert req.modules[0].role == "稳压"
        assert req.modules[0].part_number_hint == "AMS1117"
        assert req.global_v_in == "5"

    def test_invalid_json_returns_empty_request(self):
        """完全无效的输入应返回空但有效的 SystemDesignRequest。"""
        req = normalize_ai_intents({"modules": "bad"})
        assert isinstance(req, SystemDesignRequest)
        assert req.modules == []


# ============================================================
# T029: detect_ambiguities
# ============================================================


class TestDetectAmbiguities:
    def test_no_ambiguities_on_complete_data(self):
        req = normalize_ai_intents(_VALID_AI_JSON)
        ambiguities = detect_ambiguities(req)
        assert ambiguities == []

    def test_module_without_part_and_category(self):
        req = SystemDesignRequest(
            raw_text="test",
            modules=[ModuleIntent(intent_id="x1", role="未知模块")],
        )
        ambiguities = detect_ambiguities(req)
        assert len(ambiguities) == 1
        assert "x1" in ambiguities[0]
        assert "part_number_hint" in ambiguities[0]

    def test_module_with_part_only_is_ok(self):
        req = SystemDesignRequest(
            raw_text="test",
            modules=[ModuleIntent(
                intent_id="x1",
                role="模块",
                part_number_hint="TPS54202",
            )],
        )
        ambiguities = detect_ambiguities(req)
        assert ambiguities == []

    def test_module_with_category_only_is_ok(self):
        req = SystemDesignRequest(
            raw_text="test",
            modules=[ModuleIntent(
                intent_id="x1",
                role="模块",
                category_hint="buck",
            )],
        )
        # buck without v_in/v_out triggers electrical target ambiguity
        ambiguities = detect_ambiguities(req)
        assert all("part_number_hint" not in a for a in ambiguities)

    def test_connection_missing_src(self):
        req = SystemDesignRequest(
            raw_text="test",
            connections=[ConnectionIntent(
                connection_id="c1",
                src_module_intent="",
                dst_module_intent="ldo1",
            )],
        )
        ambiguities = detect_ambiguities(req)
        assert any("src" in a.lower() or "源" in a for a in ambiguities)

    def test_connection_missing_dst(self):
        req = SystemDesignRequest(
            raw_text="test",
            connections=[ConnectionIntent(
                connection_id="c1",
                src_module_intent="buck1",
            )],
        )
        ambiguities = detect_ambiguities(req)
        assert any("dst" in a.lower() or "目标" in a for a in ambiguities)

    def test_power_module_missing_vin(self):
        req = SystemDesignRequest(
            raw_text="test",
            modules=[ModuleIntent(
                intent_id="b1",
                role="降压",
                category_hint="buck",
                electrical_targets={"v_out": "5"},
            )],
        )
        ambiguities = detect_ambiguities(req)
        assert any("v_in" in a for a in ambiguities)

    def test_power_module_missing_vout(self):
        req = SystemDesignRequest(
            raw_text="test",
            modules=[ModuleIntent(
                intent_id="b1",
                role="降压",
                category_hint="ldo",
                electrical_targets={"v_in": "5"},
            )],
        )
        ambiguities = detect_ambiguities(req)
        assert any("v_out" in a for a in ambiguities)

    def test_non_power_module_no_voltage_ambiguity(self):
        """非电源模块（如 LED）缺少 v_in/v_out 不应报歧义。"""
        req = SystemDesignRequest(
            raw_text="test",
            modules=[ModuleIntent(
                intent_id="led1",
                role="指示灯",
                category_hint="led",
            )],
        )
        ambiguities = detect_ambiguities(req)
        assert all("v_in" not in a and "v_out" not in a for a in ambiguities)


# ============================================================
# 正则 fallback
# ============================================================


class TestRegexFallbackParse:
    def test_extracts_part_numbers(self):
        req = regex_fallback_parse("用TPS54202降压到5V，再用AMS1117到3.3V")
        part_numbers = [m.part_number_hint for m in req.modules]
        assert "TPS54202" in part_numbers
        assert "AMS1117" in part_numbers

    def test_builds_system_gold_path_from_long_sentence(self):
        req = regex_fallback_parse(
            "20V 输入，用 TPS54202 降压到 5V，再用 AMS1117 降到 3.3V，"
            "给 STM32F103C8T6 供电，并且用 MCU 的 PA1 控制一颗 LED"
        )

        categories = [m.category_hint for m in req.modules]
        assert categories == ["buck", "ldo", "mcu", "led"]

        power_conns = [
            c for c in req.connections if c.signal_type == SignalType.POWER_SUPPLY
        ]
        gpio_conns = [
            c for c in req.connections if c.signal_type == SignalType.GPIO
        ]

        assert len(power_conns) == 2
        assert len(gpio_conns) == 1
        assert gpio_conns[0].src_port_hint == "PA1"
        assert gpio_conns[0].dst_port_hint == "ANODE"

    def test_extracts_voltage_chain(self):
        req = regex_fallback_parse("20V到5V")
        assert req.global_v_in == "20"
        if req.modules:
            elec = req.modules[0].electrical_targets
            assert elec.get("v_in") == "20"
            assert elec.get("v_out") == "5"

    def test_detects_category(self):
        req = regex_fallback_parse("设计一个LDO稳压电路，5V到3.3V")
        assert any(m.category_hint == "ldo" for m in req.modules)

    def test_creates_connections_between_modules(self):
        req = regex_fallback_parse("用TPS54202把20V降压到5V，再用AMS1117降到3.3V")
        assert len(req.modules) >= 2, f"Expected >=2 modules, got {len(req.modules)}"
        assert len(req.connections) >= 1
        c = req.connections[0]
        assert c.signal_type == SignalType.POWER_SUPPLY

    def test_empty_input_returns_ambiguity(self):
        req = regex_fallback_parse("")
        assert len(req.ambiguities) > 0

    def test_design_notes_marks_fallback(self):
        req = regex_fallback_parse("5V到3.3V")
        assert "fallback" in req.design_notes.lower()


# ============================================================
# T030: 完整流程
# ============================================================


class TestParseSystemIntent:
    def test_full_flow_with_regex_fallback(self, monkeypatch):
        """regex_fallback_parse 直接调用时应返回有效请求。

        注：skip_ai_parse 逻辑已从 parse_system_intent 中移除，
        由 SystemDesignSession 在调用前决定使用哪个解析器。
        """
        from schemaforge.system.ai_protocol import regex_fallback_parse

        req = regex_fallback_parse("用TPS54202把20V降压到5V")
        assert isinstance(req, SystemDesignRequest)
        assert req.raw_text == "用TPS54202把20V降压到5V"
        assert len(req.modules) > 0

    def test_full_flow_ai_returns_valid(self, monkeypatch):
        """AI 返回合法 JSON 时应正常解析。"""
        monkeypatch.delenv("SCHEMAFORGE_SKIP_AI_PARSE", raising=False)

        def _mock_call_llm_json(system_prompt, user_message, **kwargs):
            return _VALID_AI_JSON

        monkeypatch.setattr(
            "schemaforge.ai.client.call_llm_json",
            _mock_call_llm_json,
        )

        req = parse_system_intent("20V到5V再到3.3V")
        assert len(req.modules) == 2
        assert req.modules[0].part_number_hint == "TPS54202"
        assert req.global_v_in == "20"

    def test_full_flow_ai_returns_none_fallback(self, monkeypatch):
        """AI 返回 None 时应回退到正则。"""
        monkeypatch.delenv("SCHEMAFORGE_SKIP_AI_PARSE", raising=False)

        def _mock_call_llm_json(system_prompt, user_message, **kwargs):
            return None

        monkeypatch.setattr(
            "schemaforge.ai.client.call_llm_json",
            _mock_call_llm_json,
        )

        req = parse_system_intent("用AMS1117做5V到3.3V")
        assert isinstance(req, SystemDesignRequest)

    def test_full_flow_ai_raises_exception_fallback(self, monkeypatch):
        """AI 调用抛异常时应回退到正则。"""
        monkeypatch.delenv("SCHEMAFORGE_SKIP_AI_PARSE", raising=False)

        def _mock_call_llm_json(system_prompt, user_message, **kwargs):
            msg = "连接超时"
            raise ConnectionError(msg)

        monkeypatch.setattr(
            "schemaforge.ai.client.call_llm_json",
            _mock_call_llm_json,
        )

        req = parse_system_intent("20V到5V")
        assert isinstance(req, SystemDesignRequest)
        assert "fallback" in req.design_notes.lower()

    def test_full_flow_ai_returns_invalid_schema_fallback(self, monkeypatch):
        """AI 返回无效 schema 时应回退到正则。"""
        monkeypatch.delenv("SCHEMAFORGE_SKIP_AI_PARSE", raising=False)

        def _mock_call_llm_json(system_prompt, user_message, **kwargs):
            return {"modules": "not_a_list"}

        monkeypatch.setattr(
            "schemaforge.ai.client.call_llm_json",
            _mock_call_llm_json,
        )

        req = parse_system_intent("20V到5V")
        assert isinstance(req, SystemDesignRequest)

    def test_full_flow_ambiguities_merged(self, monkeypatch):
        """AI 结果的 ambiguities 和检测到的 ambiguities 应合并。"""
        monkeypatch.delenv("SCHEMAFORGE_SKIP_AI_PARSE", raising=False)

        ai_json_with_ambiguity = {
            "modules": [{
                "intent_id": "x1",
                "role": "未知",
            }],
            "ambiguities": ["用户未指定电压"],
        }

        def _mock_call_llm_json(system_prompt, user_message, **kwargs):
            return ai_json_with_ambiguity

        monkeypatch.setattr(
            "schemaforge.ai.client.call_llm_json",
            _mock_call_llm_json,
        )

        req = parse_system_intent("做个电路")
        # 应该同时包含 AI 返回的和 detect_ambiguities 检测到的
        assert len(req.ambiguities) >= 2
        assert any("用户未指定电压" in a for a in req.ambiguities)
        assert any("x1" in a for a in req.ambiguities)
