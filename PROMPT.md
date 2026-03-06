# 完整工程Prompt：AI约束化原理图生成器（SchemaForge）

> **用途**：将此文件完整交给AI编程助手（Claude/Cursor/Copilot等），让它从零实现整个项目。
> **预计耗时**：2小时。
> **背景**：嘉立创EDA入职考核——展示AI协作深度、工程架构能力、调试过程。

---

## 一、项目概述

### 项目名：SchemaForge — 约束驱动的AI原理图生成器

**一句话描述**：用户输入自然语言电路需求（如"设计一个5V转3.3V的LDO电路，带电源指示LED"），系统输出**专业级原理图SVG + BOM清单 + SPICE网表**。

### 核心设计哲学（这是本项目的灵魂，也是面试亮点）

**AI不直接画原理图。AI只做"理解"和"决策"，本地系统做"约束"和"渲染"。**

为什么？因为：
1. LLM不知道引脚是什么、该连哪里、连没连上
2. LLM画出的原理图布局不可读
3. LLM会幻觉出不存在的器件型号

所以架构是：
```
用户自然语言
    ↓
[AI理解层] LLM解析需求 → 输出结构化JSON（选哪个模板、填什么参数）
    ↓
[约束层] 本地模板系统 → 实例化电路（每个连接都是预定义的、正确的）
    ↓  
[验证层] ERC检查 → 引脚连接完整性、短路检测、参数合法性
    ↓
[渲染层] schemdraw → 专业级SVG原理图
    ↓
[导出层] BOM清单 + SPICE网表 + 参数计算说明
```

**AI负责"选择"和"参数化"，不负责"连线"。连线正确性由本地模板保证。**

---

## 二、技术栈

```
Python 3.10+
schemdraw          # 原理图SVG渲染（pip install schemdraw）——纯Python，零外部依赖
gradio             # Web UI（pip install gradio）——一行代码出界面
openai             # LLM调用（pip install openai）——OpenAI兼容API
pydantic           # 数据校验（pip install pydantic）
```

**LLM配置**（用DashScope的OpenAI兼容接口）：
- Base URL: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- Model: `qwen-plus`（或任何OpenAI兼容模型）
- API Key: 环境变量 `DASHSCOPE_API_KEY`

---

## 三、核心数据模型

### 3.1 引脚模型

```python
from enum import Enum
from pydantic import BaseModel

class PinType(str, Enum):
    """引脚电气类型——决定ERC规则"""
    POWER_IN = "power_in"       # 电源输入（VIN, VCC）
    POWER_OUT = "power_out"     # 电源输出（VOUT）
    GROUND = "ground"           # 地
    INPUT = "input"             # 信号输入
    OUTPUT = "output"           # 信号输出
    PASSIVE = "passive"         # 无源（电阻/电容的引脚）
    BIDIRECTIONAL = "bidirectional"
    NO_CONNECT = "no_connect"

class PinDef(BaseModel):
    """引脚定义——模板中每个元器件引脚的静态描述"""
    name: str                   # 引脚名（如 "VIN", "VOUT", "GND", "1", "2"）
    pin_type: PinType           # 电气类型
    required: bool = True       # 是否必须连接（NC引脚为False）
    description: str = ""       # 描述（如 "输入电压，范围4.5V-12V"）
```

### 3.2 元器件模型

```python
class ComponentDef(BaseModel):
    """元器件定义——模板中使用的元器件类型"""
    ref_prefix: str             # 参考标号前缀（R, C, U, D, LED）
    name: str                   # 器件名（如 "AMS1117-3.3", "电阻", "LED"）
    description: str            # 描述
    pins: list[PinDef]          # 引脚定义列表
    parameters: dict[str, str]  # 可配置参数（如 {"value": "10kΩ", "package": "0805"}）
    lcsc_part: str = ""         # LCSC器件编号（如 "C347222"），用于BOM
    schemdraw_element: str      # 对应的schemdraw元素名（如 "elm.Resistor", "elm.LED", "elm.Ic"）
    spice_model: str = ""       # SPICE模型模板（如 "R{ref} {p1} {p2} {value}"）
```

### 3.3 网络（Net）模型——连接的核心

```python
class NetConnection(BaseModel):
    """网络连接点——标识"哪个器件的哪个引脚""""
    component_ref: str          # 器件参考标号（如 "U1", "R1", "C1"）
    pin_name: str               # 引脚名（如 "VIN", "1", "GND"）

class Net(BaseModel):
    """网络——一组电气相连的引脚
    
    这是连接正确性的核心：
    - 每个Net代表一根"电气导线"
    - Net内的所有引脚互相连通
    - 验证层检查每个Net的引脚类型兼容性
    """
    name: str                   # 网络名（如 "VCC_5V", "GND", "VOUT_3V3", "N001"）
    connections: list[NetConnection]  # 该网络连接的所有引脚
    
    # ERC约束
    is_power: bool = False      # 是否为电源网络
    is_ground: bool = False     # 是否为地网络
```

### 3.4 电路实例模型

```python
class ComponentInstance(BaseModel):
    """电路中的一个器件实例"""
    ref: str                    # 参考标号（如 "U1", "R1"）
    component_type: str         # 引用ComponentDef的name
    parameters: dict[str, str]  # 实际参数值（如 {"value": "10kΩ"}）

class CircuitInstance(BaseModel):
    """完整的电路实例——从模板实例化后的结果"""
    name: str                   # 电路名
    description: str            # 描述
    components: list[ComponentInstance]
    nets: list[Net]
    
    # 元信息
    template_name: str          # 源模板名
    input_parameters: dict      # 用户/AI提供的参数
    calculated_values: dict     # 计算得到的值（如电阻分压比）
```

### 3.5 电路模板模型——这是约束系统的核心

```python
class LayoutHint(BaseModel):
    """布局提示——告诉渲染器器件的相对位置"""
    component_ref: str
    position: str               # "right", "down", "left", "up"
    relative_to: str = ""       # 相对于哪个器件（空=上一个）
    at_pin: str = ""            # 从哪个引脚开始（用于.at()定位）

class CircuitTemplate(BaseModel):
    """电路模板——预定义的电路拓扑
    
    关键设计：模板定义了所有合法的连接关系。
    AI只能"选择模板+填参数"，不能自己发明连接。
    这保证了连线100%正确。
    """
    name: str                   # 模板名（如 "ldo_regulator", "voltage_divider"）
    display_name: str           # 显示名（如 "LDO线性稳压电路"）
    description: str            # 描述
    category: str               # 分类（power, signal, interface, filter...）
    
    # 参数定义——AI需要填的东西
    parameters: dict[str, "ParameterDef"]
    
    # 固定的器件列表
    components: list[ComponentDef]
    
    # 固定的网络连接——这就是"约束"
    # 模板的nets用参数占位符，实例化时填入具体值
    net_template: list[Net]
    
    # 布局提示——指导schemdraw渲染
    layout_hints: list[LayoutHint]
    
    # 参数计算规则（Python表达式）
    calculations: dict[str, str]  # 如 {"r1_value": "v_in / (led_current * 1000)"}

class ParameterDef(BaseModel):
    """模板参数定义"""
    name: str                   # 参数名
    display_name: str           # 显示名
    type: str                   # "float", "int", "string", "choice"
    unit: str = ""              # 单位（V, A, Ω, F）
    default: str = ""           # 默认值
    min_val: float | None = None
    max_val: float | None = None
    choices: list[str] = []     # type=choice时的选项
    description: str = ""
```

---

## 四、具体电路模板（实现这些）

### 模板1：LDO线性稳压电路

```
用户说："5V转3.3V稳压" 或 "LDO电路"

电路拓扑（固定）：
  VIN ──┬── [C_in] ── GND
        │
       [U1: AMS1117-3.3]
        │  VIN → VOUT
        │  GND → GND
        │
  VOUT ─┴── [C_out] ── GND

参数：
  - v_in: 输入电压（默认5V）
  - v_out: 输出电压（由IC型号决定：1.2/1.8/2.5/3.3/5.0）
  - c_in: 输入电容（默认10μF）
  - c_out: 输出电容（默认22μF）

网络（固定4个）：
  - VIN: U1.VIN, C_in.1
  - VOUT: U1.VOUT, C_out.1
  - GND: U1.GND, C_in.2, C_out.2
```

### 模板2：LED指示灯电路

```
用户说："LED指示灯" 或 "电源指示"

电路拓扑（固定）：
  VCC ── [R_limit] ── [LED] ── GND

参数：
  - v_supply: 电源电压（默认3.3V）
  - led_color: LED颜色（red/green/blue/white）→ 决定正向压降
  - led_current: LED电流（默认10mA）

计算：
  - v_forward: 从led_color查表（red=2.0V, green=2.2V, blue=3.0V, white=3.0V）
  - r_value: (v_supply - v_forward) / led_current

网络（固定2个）：
  - VCC: R_limit.1
  - LED_ANODE: R_limit.2, LED.anode
  - GND: LED.cathode
```

### 模板3：电压分压器

```
用户说："分压电路" 或 "电压采样"

电路拓扑（固定）：
  VIN ── [R1] ──┬── [R2] ── GND
                │
               VOUT（采样点）

参数：
  - v_in: 输入电压
  - v_out: 期望输出电压
  - r_total: 总阻值预算（默认20kΩ，决定功耗）

计算：
  - ratio: v_out / v_in
  - r2: r_total * ratio
  - r1: r_total - r2
  - 就近取E24系列标准阻值

网络（固定3个）：
  - VIN: R1.1
  - VMID: R1.2, R2.1（分压输出点）
  - GND: R2.2
```

### 模板4：RC低通滤波器

```
参数：
  - f_cutoff: 截止频率（Hz）
  - r_value: 电阻值（默认10kΩ，或自动计算）

计算：
  - c_value: 1 / (2 * pi * f_cutoff * r_value)

网络：
  - IN: R1.1
  - OUT: R1.2, C1.1
  - GND: C1.2
```

### 模板5：组合模板——LDO + LED指示灯

```
这是重点！展示模板可以组合。
用户说："带LED指示灯的3.3V稳压电路"

实际上是：模板1（LDO）+ 模板2（LED），共享VOUT和GND网络。

组合规则：
  - LED电路的VCC连接到LDO的VOUT
  - 两者共享GND网络
```

---

## 五、验证/ERC管线

在渲染前执行，按顺序：

```python
class ERCChecker:
    """电气规则检查器"""
    
    def check_all(self, circuit: CircuitInstance) -> list[ERCError]:
        errors = []
        errors += self.check_floating_pins(circuit)      # 1. 必连引脚未连接
        errors += self.check_net_minimum(circuit)         # 2. 每个net至少2个连接
        errors += self.check_power_ground(circuit)        # 3. 电源网络有源、地网络有地
        errors += self.check_short_circuit(circuit)       # 4. 电源直连地
        errors += self.check_pin_type_conflict(circuit)   # 5. 两个output连同一net
        errors += self.check_parameter_range(circuit)     # 6. 参数值在合法范围内
        return errors
```

**每条规则的具体逻辑**：

1. **浮空引脚检查**：遍历所有component的所有`required=True`的pin，检查是否至少出现在一个net中
2. **最少连接数**：每个net的connections列表长度必须≥2（否则这根线没意义）
3. **电源/地完整性**：标记为`is_power`的net中必须有至少一个`power_out`类型引脚；`is_ground`的net中必须有`ground`类型引脚
4. **短路检测**：不允许同一个net同时包含`power_out`和`ground`类型引脚
5. **输出冲突**：同一个net不允许有两个`power_out`或两个`output`类型引脚（会打架）
6. **参数范围**：电阻>0，电容>0，电压在器件额定范围内

---

## 六、schemdraw渲染管线

### 核心难点：Net（逻辑连接）→ Wire（物理走线）的映射

**设计决策：每个模板自带布局函数，不做通用自动布局。**

原因：
- 通用自动布局极难做好（这是EDA工具的核心竞争力，不是2小时能搞的）
- 每种电路拓扑的"好看"布局是固定的（LDO就是左进右出、电容挂地）
- 模板布局函数 = 一段确定性的schemdraw代码，参数化器件值

### 渲染函数结构

```python
def render_ldo_circuit(circuit: CircuitInstance, filename: str) -> str:
    """渲染LDO电路为SVG
    
    布局是固定的：
    
         C_in          U1           C_out
    VIN──┤├──┬──[AMS1117-3.3]──┬──┤├──VOUT
             │    GND           │
             └────┴─────────────┘
                 GND
    
    只有器件值（电容值、IC型号）是参数化的。
    """
    params = circuit.input_parameters
    
    with schemdraw.Drawing(file=filename) as d:
        d.config(fontsize=12, unit=3)
        
        # 输入端
        vin = elm.Dot(open=True).label('VIN\n{}V'.format(params['v_in']), 'left')
        
        # 输入电容（向下到地）
        d.push()
        elm.Line().right(1)
        c_in_top = d.here
        elm.Capacitor(polar=True).down().label(params['c_in'], 'right')
        elm.Ground()
        d.pop()
        
        # LDO IC
        elm.Line().right(2)
        # ...用elm.Ic定义AMS1117的引脚...
        
        # 输出电容
        # ...类似c_in的结构...
        
        # 输出端
        elm.Dot(open=True).label('VOUT\n{}V'.format(params['v_out']), 'right')
    
    return filename
```

**关键点**：
- 每个模板有**自己的render函数**，布局是手写的、确定正确的
- schemdraw的`.at(pin)`、`.tox()`、`.toy()`确保走线连到正确位置
- AI完全不参与布局——AI的输出（CircuitInstance）只决定"画什么值"，不决定"画在哪"

---

## 七、AI集成策略

### AI的职责边界（极其明确）

| AI负责 | AI不负责 |
|--------|----------|
| 理解用户需求 | 画原理图 |
| 选择电路模板 | 决定布局 |
| 推理参数值 | 连线 |
| 解释设计选择 | 器件引脚定义 |

### Prompt结构

```
你是一个电路设计助手。用户会描述一个电路需求，你需要：

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
  "description": "string — 设计说明（为什么选这个方案）",
  "modules": [
    {
      "template": "模板名",
      "instance_name": "实例名（如main_ldo, power_led）",
      "parameters": { "参数名": "值", ... }
    }
  ],
  "connections": [
    {
      "from_module": "模块实例名",
      "from_net": "模块内网络名",
      "to_module": "另一个模块实例名",
      "to_net": "另一个模块内网络名",
      "merged_net_name": "合并后的网络名"
    }
  ],
  "notes": "string — 设计注意事项（散热、PCB布局建议等）"
}

## 规则
1. 只能使用上面列出的模板，不能发明新电路
2. 参数必须在指定范围内
3. 模块间连接只能通过定义好的网络名（VIN/VOUT/GND/VMID等）
4. 如果需求不明确，选择最常用的默认值并在notes中说明
5. 不要输出任何JSON以外的内容
```

### AI输出的验证（本地做，不信任AI）

```python
def validate_ai_output(ai_json: dict) -> tuple[bool, list[str]]:
    """验证AI输出的合法性"""
    errors = []
    
    for module in ai_json["modules"]:
        # 1. 模板名必须存在
        if module["template"] not in TEMPLATE_REGISTRY:
            errors.append(f"未知模板: {module['template']}")
        
        # 2. 参数必须完整且类型正确
        template = TEMPLATE_REGISTRY[module["template"]]
        for param_name, param_def in template.parameters.items():
            if param_name not in module["parameters"]:
                if param_def.default:
                    module["parameters"][param_name] = param_def.default
                else:
                    errors.append(f"{module['instance_name']}: 缺少必填参数 {param_name}")
            # 范围检查、类型检查...
    
    for conn in ai_json.get("connections", []):
        # 3. 连接的模块和网络名必须存在
        # 4. 连接的网络类型必须兼容（不能把VCC连到GND）
        ...
    
    return len(errors) == 0, errors
```

---

## 八、BOM和SPICE导出

### BOM清单

```python
def generate_bom(circuit: CircuitInstance) -> str:
    """生成BOM清单（Markdown表格）"""
    # | 序号 | 参考标号 | 器件名称 | 封装 | 值 | LCSC编号 | 数量 |
    # | 1    | U1       | AMS1117-3.3 | SOT-223 | - | C347222 | 1 |
    # | 2    | C1       | 电解电容 | 0805 | 10μF | C15849 | 1 |
    ...
```

### SPICE网表

```python
def generate_spice(circuit: CircuitInstance) -> str:
    """生成SPICE网表"""
    # * SchemaForge Generated SPICE Netlist
    # * Circuit: LDO with LED indicator
    # R1 VOUT LED_ANODE 120
    # D1 LED_ANODE GND LED_RED
    # .model LED_RED D(Is=1e-20 N=1.8 Rs=2 Vj=2.0)
    # V1 VIN GND DC 5.0
    # .end
    ...
```

---

## 九、项目文件结构

```
schemaforge/
├── main.py                    # Gradio Web UI入口
├── requirements.txt           # schemdraw, gradio, openai, pydantic
│
├── core/
│   ├── __init__.py
│   ├── models.py              # 所有Pydantic数据模型（Pin, Component, Net, Circuit, Template）
│   ├── templates.py           # 模板注册表 + 所有模板定义
│   ├── engine.py              # 核心引擎：AI输出 → 验证 → 实例化 → ERC → 渲染
│   ├── erc.py                 # ERC检查器
│   ├── calculator.py          # 参数计算（E24取值、分压计算等）
│   └── exporter.py            # BOM + SPICE导出
│
├── render/
│   ├── __init__.py
│   ├── base.py                # 渲染基类
│   ├── ldo.py                 # LDO电路渲染
│   ├── led.py                 # LED电路渲染
│   ├── divider.py             # 分压器渲染
│   ├── rc_filter.py           # RC滤波器渲染
│   └── composite.py           # 组合电路渲染（多模板拼接）
│
├── ai/
│   ├── __init__.py
│   ├── client.py              # LLM API调用封装
│   ├── prompts.py             # 系统prompt + few-shot示例
│   └── validator.py           # AI输出JSON验证
│
├── output/                    # 生成的文件输出目录
│   └── .gitkeep
│
└── examples/                  # 示例输入/输出
    ├── example_ldo.json       # AI输出示例
    ├── example_ldo.svg        # 渲染结果示例
    └── example_ldo_bom.md     # BOM示例
```

---

## 十、Gradio Web UI

```python
import gradio as gr

def process_request(user_input: str, api_key: str) -> tuple[str, str, str, str]:
    """主处理函数
    Returns: (svg_image_path, bom_markdown, spice_text, design_notes)
    """
    # 1. 调LLM获取结构化设计
    # 2. 验证AI输出
    # 3. 实例化电路
    # 4. ERC检查
    # 5. 渲染SVG
    # 6. 导出BOM + SPICE
    # 7. 返回所有结果

demo = gr.Interface(
    fn=process_request,
    inputs=[
        gr.Textbox(label="描述你的电路需求", placeholder="例如：设计一个5V转3.3V的稳压电路，带绿色LED电源指示灯", lines=3),
        gr.Textbox(label="API Key", type="password"),
    ],
    outputs=[
        gr.Image(label="原理图", type="filepath"),
        gr.Markdown(label="BOM清单"),
        gr.Code(label="SPICE网表", language=None),
        gr.Markdown(label="设计说明"),
    ],
    title="SchemaForge — AI约束化原理图生成器",
    description="输入自然语言电路需求，自动生成专业原理图、BOM清单和SPICE网表。",
)

demo.launch()
```

---

## 十一、开发顺序（2小时规划）

### Phase 1（30分钟）：核心骨架
1. 创建项目结构，`pip install schemdraw gradio openai pydantic`
2. 实现`core/models.py`——所有数据模型
3. 实现一个最简单的模板（voltage_divider）——硬编码参数
4. 实现对应的`render/divider.py`——手写schemdraw代码
5. **验证**：硬编码调用，确认能输出正确的SVG

### Phase 2（30分钟）：模板系统
6. 实现`core/templates.py`——模板注册表
7. 实现LDO模板 + LED模板 + RC滤波器模板
8. 实现对应的render函数
9. 实现`core/calculator.py`——E24标准阻值、分压计算
10. **验证**：每个模板单独渲染，确认SVG正确

### Phase 3（30分钟）：AI集成
11. 实现`ai/client.py`——LLM调用
12. 实现`ai/prompts.py`——系统prompt + few-shot
13. 实现`ai/validator.py`——JSON验证
14. 实现`core/engine.py`——串联全流程
15. **验证**：自然语言→JSON→SVG全链路

### Phase 4（20分钟）：ERC + 导出
16. 实现`core/erc.py`——6条检查规则
17. 实现`core/exporter.py`——BOM + SPICE
18. **验证**：故意传入错误参数，确认ERC能拦截

### Phase 5（10分钟）：Gradio UI
19. 实现`main.py`——Gradio界面
20. **验证**：完整流程演示

---

## 十二、示例IO（用于验证正确性）

### 输入
```
设计一个5V转3.3V的稳压电路，带绿色LED电源指示灯
```

### AI应输出的JSON
```json
{
  "design_name": "5V-3.3V稳压电源（带LED指示）",
  "description": "使用AMS1117-3.3 LDO线性稳压器将5V转为3.3V，并用绿色LED指示电源状态。",
  "modules": [
    {
      "template": "ldo_regulator",
      "instance_name": "main_ldo",
      "parameters": {
        "v_in": "5",
        "v_out": "3.3",
        "c_in": "10μF",
        "c_out": "22μF"
      }
    },
    {
      "template": "led_indicator",
      "instance_name": "power_led",
      "parameters": {
        "v_supply": "3.3",
        "led_color": "green",
        "led_current": "10"
      }
    }
  ],
  "connections": [
    {
      "from_module": "main_ldo",
      "from_net": "VOUT",
      "to_module": "power_led",
      "to_net": "VCC",
      "merged_net_name": "VOUT_3V3"
    },
    {
      "from_module": "main_ldo",
      "from_net": "GND",
      "to_module": "power_led",
      "to_net": "GND",
      "merged_net_name": "GND"
    }
  ],
  "notes": "AMS1117-3.3最大输入电压15V，压差约1.2V。输入电容建议靠近IC放置。LED限流电阻计算：(3.3V - 2.2V) / 10mA = 110Ω，取标准值120Ω。"
}
```

### 期望输出

1. **SVG原理图**：左侧VIN入，经C_in到LDO U1，输出VOUT经C_out，同时VOUT分支到R_led→LED→GND
2. **BOM**：

| # | Ref | 名称 | 值 | 封装 | LCSC |
|---|-----|------|-----|------|------|
| 1 | U1 | AMS1117-3.3 | - | SOT-223 | C347222 |
| 2 | C1 | 电解电容 | 10μF | 0805 | C15849 |
| 3 | C2 | 电解电容 | 22μF | 0805 | C159801 |
| 4 | R1 | 贴片电阻 | 120Ω | 0402 | C25079 |
| 5 | D1 | LED(绿) | - | 0805 | C2297 |

3. **SPICE网表**：
```spice
* SchemaForge: 5V-3.3V LDO with LED
V1 VIN GND DC 5.0
XU1 VIN VOUT_3V3 GND AMS1117
C1 VIN GND 10u
C2 VOUT_3V3 GND 22u
R1 VOUT_3V3 LED_A 120
D1 LED_A GND LED_GREEN
.model LED_GREEN D(Is=1e-20 N=1.8 Vj=2.2)
.end
```

---

## 十三、面试叙事要点

在录屏/解说中重点展示这些思考：

1. **为什么不让AI直接画原理图**：AI不懂电气规则，连线正确性无法保证。举Reddit那个SKiDL反面案例。

2. **约束驱动设计**：模板系统保证连线100%正确，AI只负责"选择"和"参数化"——这是工业级AI应用的正确姿势。

3. **分层架构**：理解层→约束层→验证层→渲染层，每层职责清晰，可独立测试。

4. **ERC验证**：不信任AI输出，本地做完整的电气规则检查——对标真实EDA工具的DRC/ERC流程。

5. **可扩展性**：新增电路只需加模板+渲染函数，不需要改核心引擎。

6. **对嘉立创生态的理解**：BOM用LCSC编号，可直接对接嘉立创SMT贴片服务。

---

## 十四、开发日志模板

在开发过程中持续记录（这是交付物之一）：

```markdown
# SchemaForge 开发日志

## Round 1 — 项目骨架 + 分压器模板
### 目标
建立项目结构，实现最简单的电压分压器模板验证整个管线。

### 思考过程
- [为什么选schemdraw而不是SKiDL]
- [为什么用模板系统而不是让AI自由生成]

### AI协作
- Prompt: [描述]
- AI输出: [截取关键部分]
- 我的修改: [哪里不对，怎么修的]

### 结果
- [截图/SVG]
- [遇到的问题和解决方案]
```

---

## 十五、关键风险和应对

| 风险 | 概率 | 应对 |
|------|------|------|
| schemdraw渲染复杂电路布局困难 | 中 | 每个模板手写布局，不做通用布局 |
| AI输出JSON格式不稳定 | 高 | 严格JSON Schema验证 + 错误重试(max 3次) |
| 2小时不够 | 中 | 优先保证2个模板能端到端跑通，其余为加分项 |
| LLM API调用失败 | 低 | 内置fallback示例JSON，可离线演示 |
| 组合模板渲染拼接困难 | 高 | 组合模板单独写渲染函数，不做通用拼接 |
