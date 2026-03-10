# SchemaForge

AI 驱动的电路原理图生成工具。自然语言输入，自动输出 SVG 原理图 + BOM + SPICE 网表。

**项目状态：已暂停。** 详见 [docs/PROJECT_LOG.md](docs/PROJECT_LOG.md)。

## 它能做什么

用户说一句话（比如"用 TPS54202 把 12V 降到 3.3V"），系统自动完成：

- AI 解析需求，提取器件型号和参数
- 查本地器件库匹配器件，缺失的可以上传 PDF datasheet 自动入库
- 公式计算外围元件参数（电感、电容、反馈电阻等）
- 42 条工程规则自动审查
- 确定性渲染原理图 SVG + 导出 BOM 和 SPICE
- 支持多轮对话修改（"把输出改成 5V"、"加一颗 LED"）

## 核心设计

AI 只做理解和决策，所有计算、渲染、约束检查都是本地确定性代码。

架构：Orchestrator + 13 个原子工具（OpenAI function calling），AI 通过工具调用驱动整个设计管线。渲染有两种模式：schemdraw 通用渲染和本地确定性 SVG 渲染（Pin + Body 架构，目前支持 Buck 和 LDO 拓扑）。

## 快速开始

```bash
pip install -r requirements.txt
python gui.py          # GUI 模式
python main.py         # CLI 模式
```

需要配置 AI 模型 API（见 `schemaforge/ai/client.py`）。

## 项目结构

```
schemaforge/
├── agent/       # AI 编排层（Orchestrator + 13 个工具）
├── ai/          # LLM 客户端
├── system/      # 核心管线（会话、连接规则、参数综合、渲染、布局、导出）
├── library/     # 器件库（DeviceModel + 符号构建 + 校验）
├── ingest/      # 数据导入（PDF/图片/EasyEDA）
├── design/      # 设计治理（IR、审查、规划、澄清）
├── gui/         # PySide6 桌面应用
├── visual_review/ # 视觉审查
├── store/       # 器件数据 + 参考设计
├── core/        # 基础模型和模板
├── common/      # 共享基础设施
├── render/      # 渲染工具函数
└── schematic/   # 拓扑渲染器
```

## 为什么暂停

核心卡点是原理图渲染。试过 KiCad 接口（.kicad_sch 生成，连线精度问题导致 30% ERC 报错）、嘉立创 EDA（只有器件查询 API，没有原理图绘制接口）、让 AI 生成 SVG 坐标（重叠和断开问题无法稳定解决）。最后做了纯本地确定性渲染，效果可以但每种拓扑要手写几百行布局代码，扩展成本太高。

详细的技术复盘和 AI 能力局限性分析见 [docs/PROJECT_LOG.md](docs/PROJECT_LOG.md)。

## 文档

- [docs/PROJECT_LOG.md](docs/PROJECT_LOG.md) — 完整项目记录（需求拆解、架构迭代、技术难点、放弃原因）
- [docs/HANDOVER.md](docs/HANDOVER.md) — 技术交接文档（架构、数据流、文件索引）
