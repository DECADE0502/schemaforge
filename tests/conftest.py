"""pytest 全局配置。

当前运行环境下，pytest 默认的 `tmp_path` / `.pytest_cache` 目录存在权限异常，
这里统一接管测试临时目录，确保质量门可稳定运行。
"""

from __future__ import annotations

import itertools
import os
import random as _rng
import shutil
import tempfile
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import pytest

# 使用非交互式后端，避免 Tk/Tcl 依赖
matplotlib.use("Agg")

_TMP_ROOT = Path(__file__).resolve().parent.parent / ".test-runtime" / "tmp"
_TMP_ROOT.mkdir(parents=True, exist_ok=True)
# 使用随机起始偏移，避免跨会话目录名冲突
_TMP_COUNTER = itertools.count(start=_rng.randint(10000, 99999))

os.environ["TMP"] = str(_TMP_ROOT)
os.environ["TEMP"] = str(_TMP_ROOT)
os.environ["TMPDIR"] = str(_TMP_ROOT)
tempfile.tempdir = str(_TMP_ROOT)

# 测试环境跳过 AI 驱动解析，确保解析走正则 fallback，不受网络状态影响。
os.environ.setdefault("SCHEMAFORGE_SKIP_AI_PARSE", "1")


def _safe_mkdtemp(
    suffix: str | None = None,
    prefix: str | None = None,
    dir: str | None = None,
) -> str:
    base = Path(dir) if dir else _TMP_ROOT
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{prefix or 'tmp'}{next(_TMP_COUNTER):04d}{suffix or ''}"
    path.mkdir(parents=True, exist_ok=False)
    return str(path)


class _SafeTemporaryDirectory:
    def __init__(self, suffix: str | None = None, prefix: str | None = None, dir: str | None = None):
        self.name = _safe_mkdtemp(suffix=suffix, prefix=prefix, dir=dir)

    def __enter__(self) -> str:
        return self.name

    def __exit__(self, exc_type, exc, tb) -> None:
        shutil.rmtree(self.name, ignore_errors=True)

    def cleanup(self) -> None:
        shutil.rmtree(self.name, ignore_errors=True)


tempfile.mkdtemp = _safe_mkdtemp
tempfile.TemporaryDirectory = _SafeTemporaryDirectory


@pytest.fixture(autouse=True)
def _close_matplotlib_figures():
    """每个测试结束后关闭所有 matplotlib 图形，防止资源泄漏。"""
    yield
    plt.close("all")


@pytest.fixture(autouse=True)
def _mock_ai_calls(monkeypatch):
    """全局 mock AI 调用，防止测试命中真实 API。

    所有测试默认使用 mock AI 响应。需要真实 AI 的测试可以
    用 @pytest.mark.real_ai 标记并跳过此 fixture。
    """
    import json

    def _mock_call_llm(system_prompt: str, user_message: str, **kwargs) -> str:
        """返回一个通用的 Mock AI 响应字符串。

        根据 system_prompt 和 user_message 内容推断需要的响应格式，
        模拟真实 AI 行为但不发网络请求。
        """
        import re

        combined = f"{system_prompt}\n{user_message}"

        # --- 设计规划 (planner) — 模拟 AI 规划器的关键字理解能力 ---
        if "模块" in system_prompt and ("规划" in system_prompt or "拆解" in system_prompt):
            import re as _re
            lower_msg = user_message.lower()

            # 从 user_message 提取电压
            v_match = _re.search(r"(\d+(?:\.\d+)?)\s*[Vv]\s*(?:转|到|→|to)", user_message)
            v_in = v_match.group(1) if v_match else ""
            v_out_match = _re.search(r"(?:转|到|→|to)\s*(\d+(?:\.\d+)?)\s*[Vv]?", user_message)
            v_out = v_out_match.group(1) if v_out_match else ""

            # 提取料号
            _KEYWORD_ABBRS = {"LDO", "LED", "MCU", "ADC", "DAC", "USB", "SPI",
                              "I2C", "CAN", "PWM", "GPIO", "UART", "BUCK"}
            pn_match = _re.search(r"([A-Z][A-Z0-9]{2,}[-]?[A-Z0-9]*\d[A-Z0-9]*)", user_message)
            detected_pn = pn_match.group(1) if pn_match and pn_match.group(1) not in _KEYWORD_ABBRS else ""

            # 推断类型
            has_ldo = any(kw in lower_msg for kw in ["ldo", "稳压", "线性稳压"])
            has_buck = any(kw in lower_msg for kw in ["buck", "降压", "开关电源", "dcdc", "dc-dc"])
            has_boost = any(kw in lower_msg for kw in ["boost", "升压"])
            has_divider = any(kw in lower_msg for kw in ["分压", "divider", "采样"])
            has_filter = any(kw in lower_msg for kw in ["滤波", "filter", "rc"])
            has_led = any(kw in lower_msg for kw in ["led", "指示灯", "指示"])

            modules = []

            if detected_pn:
                cat = "buck" if has_buck else "ldo" if has_ldo else "boost" if has_boost else "other"
                params = {}
                if v_in:
                    params["v_in"] = v_in
                if v_out:
                    params["v_out"] = v_out
                modules.append({
                    "role": "main_regulator", "category": cat,
                    "description": f"用户指定器件 {detected_pn}",
                    "part_number": detected_pn,
                    "parameters": params, "connections_to": [],
                })
            elif has_ldo:
                params = {}
                if v_in:
                    params["v_in"] = v_in
                if v_out:
                    params["v_out"] = v_out
                modules.append({
                    "role": "main_regulator", "category": "ldo",
                    "description": f"LDO稳压器 {v_in or '?'}V→{v_out or '?'}V",
                    "part_number": "", "parameters": params, "connections_to": [],
                })
            elif has_buck:
                params = {}
                if v_in:
                    params["v_in"] = v_in
                if v_out:
                    params["v_out"] = v_out
                modules.append({
                    "role": "main_regulator", "category": "buck",
                    "description": "Buck降压转换器",
                    "part_number": "", "parameters": params, "connections_to": [],
                })
            elif has_boost:
                params = {}
                if v_in:
                    params["v_in"] = v_in
                if v_out:
                    params["v_out"] = v_out
                modules.append({
                    "role": "main_regulator", "category": "boost",
                    "description": "Boost升压转换器",
                    "part_number": "", "parameters": params, "connections_to": [],
                })
            elif has_divider and not has_ldo and not has_buck:
                params = {}
                if v_in:
                    params["v_in"] = v_in
                if v_out:
                    params["v_out"] = v_out
                modules.append({
                    "role": "voltage_sampler", "category": "voltage_divider",
                    "description": "电压分压采样",
                    "part_number": "", "parameters": params, "connections_to": [],
                })
            elif has_filter and not has_ldo and not has_buck:
                modules.append({
                    "role": "input_filter", "category": "rc_filter",
                    "description": "RC低通滤波器",
                    "part_number": "", "parameters": {"f_cutoff": "1000"}, "connections_to": [],
                })

            if has_led:
                # LED 颜色
                led_color = "green"
                for cn, en in [("红", "red"), ("绿", "green"), ("蓝", "blue"), ("白", "white")]:
                    if cn in lower_msg or en in lower_msg:
                        led_color = en
                        break
                led_params = {"led_color": led_color}
                if v_out:
                    led_params["v_supply"] = v_out
                elif v_in:
                    led_params["v_supply"] = v_in
                connections = [modules[0]["role"]] if modules else []
                modules.append({
                    "role": "power_led", "category": "led",
                    "description": "LED电源指示灯",
                    "part_number": "", "parameters": led_params,
                    "connections_to": connections,
                })

            # 回退
            if not modules:
                modules.append({
                    "role": "main_regulator", "category": "ldo",
                    "description": "默认LDO稳压电路",
                    "part_number": "",
                    "parameters": {"v_in": v_in or "5", "v_out": v_out or "3.3"},
                    "connections_to": [],
                })

            return json.dumps({
                "name": f"{v_in or '?'}V→{v_out or '?'}V 电源设计",
                "description": user_message[:80],
                "modules": modules,
                "notes": "Mock AI 规划",
            }, ensure_ascii=False)

        # --- 旧引擎 (core/engine.py) — 模板驱动格式 ---
        if "可用模板" in system_prompt or "ldo_regulator" in system_prompt:
            lower_msg = user_message.lower()
            has_led = any(kw in lower_msg for kw in ["led", "指示灯", "指示"])
            modules = [{
                "template": "ldo_regulator",
                "instance_name": "main_ldo",
                "parameters": {"v_in": "5", "v_out": "3.3", "c_in": "10μF", "c_out": "22μF"},
            }]
            if has_led:
                modules.append({
                    "template": "led_indicator",
                    "instance_name": "power_led",
                    "parameters": {"v_supply": "3.3", "led_color": "green", "led_current": "10"},
                })
            connections = []
            if has_led:
                connections.append({
                    "from_module": "main_ldo", "from_net": "VOUT",
                    "to_module": "power_led", "to_net": "VCC",
                    "merged_net_name": "VOUT_3V3",
                })
                connections.append({
                    "from_module": "main_ldo", "from_net": "GND",
                    "to_module": "power_led", "to_net": "GND",
                    "merged_net_name": "GND",
                })

            if "分压" in lower_msg or "divider" in lower_msg:
                modules = [{"template": "voltage_divider", "instance_name": "adc_divider",
                            "parameters": {"v_in": "12", "v_out": "3.3", "r_total": "20"}}]
                connections = []
            elif "滤波" in lower_msg or "filter" in lower_msg or "rc" in lower_msg:
                modules = [{"template": "rc_lowpass", "instance_name": "input_filter",
                            "parameters": {"f_cutoff": "1000", "r_value": "10"}}]
                connections = []
            elif not has_led and ("ldo" not in lower_msg and "稳压" not in lower_msg):
                # 纯 LED
                if "led" in lower_msg:
                    modules = [{"template": "led_indicator", "instance_name": "indicator",
                                "parameters": {"v_supply": "3.3", "led_color": "green", "led_current": "10"}}]
                    connections = []

            return json.dumps({
                "design_name": "Mock设计",
                "description": user_message[:50],
                "modules": modules,
                "connections": connections,
                "notes": "Mock AI 响应",
            }, ensure_ascii=False)

        # --- Datasheet 文本分析 (ai_analyzer) ---
        if "datasheet" in system_prompt.lower() or "引脚" in system_prompt:
            # 从 user_message 提取型号
            pn_match = re.search(r"([A-Z][A-Z0-9]{2,}[-]?[A-Z0-9]*\d[A-Z0-9]*)", user_message)
            part_number = pn_match.group(1) if pn_match else kwargs.get("hint", "UNKNOWN")
            # 推断类别
            cat = "other"
            lower_msg = user_message.lower()
            if any(kw in lower_msg for kw in ["buck", "降压", "step-down", "4.5v to 28v"]):
                cat = "buck"
            elif any(kw in lower_msg for kw in ["ldo", "low dropout", "线性稳压"]):
                cat = "ldo"
            elif any(kw in lower_msg for kw in ["boost", "升压"]):
                cat = "boost"

            return json.dumps({
                "part_number": part_number,
                "manufacturer": "Mock",
                "category": cat,
                "description": f"Mock AI 分析 {part_number}",
                "specs": {"v_in_max": "28V"},
                "pins": [
                    {"name": "VIN", "number": "1", "type": "power_in", "description": "输入"},
                    {"name": "VOUT", "number": "2", "type": "power_out", "description": "输出"},
                    {"name": "GND", "number": "3", "type": "ground", "description": "地"},
                ],
                "package": "SOT-223",
                "warnings": ["测试环境 Mock 响应"],
                "missing_fields": ["datasheet_url"],
                "application_circuit": {},
            }, ensure_ascii=False)

        # --- 设计规划 (planner) ---
        if "模块需求" in system_prompt or "电路需求" in system_prompt:
            return json.dumps({
                "name": "默认设计",
                "description": user_message[:50],
                "modules": [{
                    "role": "main_regulator",
                    "category": "ldo",
                    "description": "LDO稳压器",
                    "part_number": "",
                    "parameters": {"v_in": "5", "v_out": "3.3"},
                    "connections_to": [],
                }],
                "notes": "Mock AI 响应",
            }, ensure_ascii=False)

        # --- Orchestrator (AgentStep) ---
        if "AgentStep" in combined or "JSON 格式输出" in combined:
            return json.dumps({
                "action": "finalize",
                "message": "Mock AI 完成",
                "tool_calls": [],
                "questions": [],
                "proposal": {},
                "patch_ops": [],
                "checks": [],
                "next_state": "",
            })

        # --- 拓扑草稿 (topology_draft) ---
        if "拓扑" in system_prompt or "topology" in system_prompt.lower():
            return json.dumps({
                "circuit_type": "ldo",
                "components": [],
                "connections": [],
                "confidence": 0.5,
            })

        # --- 需求澄清 (clarifier) ---
        if "澄清" in system_prompt or "clarif" in system_prompt.lower():
            return json.dumps({
                "missing_constraints": [],
                "suggestions": [],
                "questions": [],
            })

        # 默认返回空 JSON 对象
        return "{}"

    def _mock_call_llm_json(system_prompt: str, user_message: str, **kwargs):
        """返回一个 mock JSON dict。"""
        text = _mock_call_llm(system_prompt, user_message, **kwargs)
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {}

    monkeypatch.setattr("schemaforge.ai.client.call_llm", _mock_call_llm)
    monkeypatch.setattr("schemaforge.ai.client.call_llm_json", _mock_call_llm_json)

    # Patch at all import sites where call_llm/call_llm_json are imported at module level
    _llm_import_sites = [
        "schemaforge.design.planner",
        "schemaforge.core.engine",
        "schemaforge.agent.orchestrator",
        "schemaforge.design.topology_draft",
    ]
    for site in _llm_import_sites:
        try:
            import importlib
            mod = importlib.import_module(site)
            if hasattr(mod, "call_llm"):
                monkeypatch.setattr(f"{site}.call_llm", _mock_call_llm)
            if hasattr(mod, "call_llm_json"):
                monkeypatch.setattr(f"{site}.call_llm_json", _mock_call_llm_json)
        except (ImportError, AttributeError):
            pass

    # Mock OpenAI client for vision/image analysis (bypasses call_llm)
    from unittest.mock import MagicMock

    _mock_client = MagicMock()
    _mock_response = MagicMock()
    _mock_choice = MagicMock()
    _mock_choice.message.content = json.dumps({
        "part_number": "MOCK_IC",
        "manufacturer": "Mock",
        "category": "other",
        "description": "Mock vision 分析",
        "pins": [
            {"name": "VIN", "number": "1", "type": "power_in", "description": "输入"},
            {"name": "VOUT", "number": "2", "type": "power_out", "description": "输出"},
            {"name": "GND", "number": "3", "type": "ground", "description": "地"},
        ],
        "package": "SOT-223",
        "specs": {},
        "warnings": [],
        "missing_fields": [],
    }, ensure_ascii=False)
    _mock_response.choices = [_mock_choice]
    _mock_client.chat.completions.create.return_value = _mock_response
    monkeypatch.setattr(
        "schemaforge.ai.client.get_client",
        lambda api_key=None, base_url=None: _mock_client,
    )


@pytest.fixture
def tmp_path() -> Path:
    """覆盖 pytest 内置 tmp_path，规避宿主环境目录权限问题。"""
    path = _TMP_ROOT / f"case_{next(_TMP_COUNTER):04d}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
