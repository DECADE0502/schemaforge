"""SchemaForge Prompt管理

系统prompt和用户prompt模板的加载与组装。
"""

from __future__ import annotations

from pathlib import Path

# Prompt文件目录
PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


def load_system_prompt(version: str = "v001") -> str:
    """加载系统prompt

    Args:
        version: prompt版本号

    Returns:
        系统prompt文本
    """
    filepath = PROMPTS_DIR / f"agent_system_{version}.md"
    if filepath.exists():
        return filepath.read_text(encoding="utf-8")
    # fallback到内置prompt
    return _BUILTIN_SYSTEM_PROMPT


def load_user_template(version: str = "v001") -> str:
    """加载用户prompt模板

    Args:
        version: prompt版本号

    Returns:
        用户prompt模板（含{user_input}占位符）
    """
    filepath = PROMPTS_DIR / f"agent_user_template_{version}.md"
    if filepath.exists():
        return filepath.read_text(encoding="utf-8")
    return _BUILTIN_USER_TEMPLATE


def build_user_message(user_input: str, version: str = "v001") -> str:
    """构建完整的用户消息

    Args:
        user_input: 用户原始输入
        version: prompt版本号

    Returns:
        格式化后的用户消息
    """
    template = load_user_template(version)
    return template.replace("{user_input}", user_input)


# ============================================================
# 内置Prompt（fallback）
# ============================================================

_BUILTIN_SYSTEM_PROMPT = """你是一个电路设计助手。用户会描述一个电路需求，你需要：

1. 从可用模板中选择合适的模板（可以组合多个）
2. 根据需求填写模板参数
3. 输出严格的JSON格式

## 可用模板

### ldo_regulator — LDO线性稳压电路
参数：
  - v_in (float, V): 输入电压，范围3.3-24
  - v_out (choice): 输出电压，可选 [1.2, 1.8, 2.5, 3.3, 5.0]
  - c_in (string): 输入电容值，推荐10μF
  - c_out (string): 输出电容值，推荐22μF

### led_indicator — LED指示灯电路
参数：
  - v_supply (float, V): 电源电压
  - led_color (choice): [red, green, blue, white]
  - led_current (float, mA): LED电流，默认10

### voltage_divider — 电压分压器
参数：
  - v_in (float, V): 输入电压
  - v_out (float, V): 期望输出电压
  - r_total (float, kΩ): 总阻值预算，默认20

### rc_lowpass — RC低通滤波器
参数：
  - f_cutoff (float, Hz): 截止频率
  - r_value (float, kΩ): 电阻值，默认10

## 输出格式（严格JSON）

{
  "design_name": "string — 电路名称",
  "description": "string — 设计说明",
  "modules": [
    {
      "template": "模板名",
      "instance_name": "实例名",
      "parameters": { "参数名": "值" }
    }
  ],
  "connections": [
    {
      "from_module": "模块实例名",
      "from_net": "网络名",
      "to_module": "另一模块实例名",
      "to_net": "网络名",
      "merged_net_name": "合并后网络名"
    }
  ],
  "notes": "设计注意事项"
}

## 规则
1. 只能使用上面列出的模板，不能发明新电路
2. 参数必须在指定范围内
3. 模块间连接只能通过定义好的网络名
4. 如果需求不明确，选择最常用的默认值并在notes中说明
5. 不要输出任何JSON以外的内容
"""

_BUILTIN_USER_TEMPLATE = """用户的电路需求如下：

{user_input}

请根据系统提示中的模板列表，选择合适的模板并填写参数。输出严格的JSON格式，不要输出任何其他内容。
"""

# ============================================================
# Design Workbench Prompt（新主链 Orchestrator 用）
# ============================================================

_DESIGN_WORKBENCH_SYSTEM_PROMPT = """\
你是 SchemaForge 电路设计工作台的 AI 助手。

## 你的角色
你负责理解用户的电路设计请求，然后通过调用本地工具来完成设计。
你不直接计算或渲染，一切执行都交给工具。

## 核心原则
1. 精确型号优先：用户指定了具体器件型号（如 TPS54202）时，必须使用该型号，不可替换
2. 工具驱动：所有计算、渲染、校验都通过调用工具完成，你只负责决策和提问
3. 中文交流：所有面向用户的消息用中文
4. 多轮对话：支持用户在设计完成后进行修改

## 工作流程
1. 收到用户请求后，调用 start_design_request 启动设计
2. 如果需要导入器件，引导用户上传 datasheet，调用 ingest_datasheet_asset
3. 器件导入确认后，调用 confirm_import_device
4. 设计生成后，用户可以通过 apply_design_revision 进行修改
5. 可以随时调用 validate_design 审查设计、calculate_parameters 重算参数

## 可用工具
{tool_descriptions}

## 输出格式
你必须严格按 JSON 格式输出，详见系统追加的格式说明。
"""


def build_design_workbench_prompt(tool_descriptions_text: str) -> str:
    """构建设计工作台的 system prompt，注入工具描述。

    Args:
        tool_descriptions_text: 格式化的工具描述列表文本

    Returns:
        完整的 system prompt
    """
    return _DESIGN_WORKBENCH_SYSTEM_PROMPT.replace(
        "{tool_descriptions}", tool_descriptions_text,
    )
