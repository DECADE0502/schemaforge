# SchemaForge 示例

本目录包含 SchemaForge 的示例 JSON 输入文件，可用于测试和参考。

## 文件说明

| 文件 | 说明 |
|------|------|
| `example_ldo_led.json` | LDO+LED组合：5V→3.3V稳压 + 绿色电源指示灯 |
| `example_divider.json` | 电阻分压器：12V→3.3V ADC采样 |
| `example_rc_filter.json` | RC低通滤波器：截止频率1kHz |

## 使用方式

```bash
# 使用示例JSON文件作为输入（在线模式）
python main.py --online --input "5V转3.3V稳压电路，带LED指示灯"

# 离线Demo模式（使用内置Mock）
python main.py --demo

# 查看可用模板
python main.py --templates
```

## JSON格式说明

每个JSON文件遵循 SchemaForge 的设计规格格式：

```json
{
  "design_name": "设计名称",
  "description": "设计描述",
  "modules": [
    {
      "template": "模板名称",
      "instance_name": "实例名称",
      "parameters": { ... }
    }
  ],
  "connections": [ ... ],
  "notes": "备注"
}
```

### 可用模板

- `voltage_divider` — 电阻分压器
- `ldo_regulator` — LDO线性稳压器
- `led_indicator` — LED指示灯
- `rc_lowpass` — RC低通滤波器
