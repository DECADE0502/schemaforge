# SchemaForge — AI 驱动的电子原理图设计工具

> 自然语言需求 → SVG 原理图 + BOM 清单 + SPICE 网表。AI 理解需求，本地代码执行一切。

## 核心理念

**AI 只做理解和决策，所有计算/渲染/约束/规则都是本地确定性代码。**

用户说一句自然语言（如"用 TPS5430 搭一个 12V 转 3.3V 的 DCDC 电路"），系统自动完成：

1. **AI 解析需求** — 从自然语言提取器件型号、电路类型、电压/电流参数
2. **精确型号匹配** — 用户说什么型号就是什么型号，不替换、不近似
3. **缺失器件导入** — 库里没有的器件，引导上传 PDF datasheet，AI 提取引脚参数入库
4. **公式驱动计算** — 从 datasheet 计算外围元件参数（电感、电容、反馈电阻），有工程依据
5. **42 条工程审查** — 自动审查设计的电气合理性（电感饱和、电容耐压、热功耗等）
6. **完整原理图输出** — 主芯片 + 外围元件，正确连线，SVG + BOM + SPICE
7. **多轮对话修改** — "把输出电压改成 5V"、"换成 TPS54202"，在已有设计上改

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 启动 GUI
python gui.py

# CLI 模式
python main.py                                    # 交互模式（支持多轮修改）
python main.py -i "用 TPS5430 搭 12V转3.3V DCDC"  # 单次模式
python main.py --orchestrated                      # AI 编排模式

# 质量门
python -m pytest -q                                     # 1186 passed
python -m ruff check schemaforge gui.py tests main.py   # All checks passed
```

## 工作流

```
用户自然语言 → AI 解析需求 (kimi-k2.5)
  → 查本地器件库 → 有器件 → 审查(42条) → 公式计算 → 渲染 → SVG+BOM+SPICE
                 → 没器件 → 引导上传 PDF → AI 提取引脚/参数 → 入库 → 续设计
  → 多轮修改 → AI 解析修改请求 → 重新计算 → 更新设计
```

## 技术栈

| 层级 | 技术 |
|------|------|
| GUI | PySide6 (Qt6 桌面应用) |
| AI 模型 | kimi-k2.5 (DashScope OpenAI 兼容接口) |
| 原理图渲染 | schemdraw (SVG 生成) |
| 数据模型 | Pydantic v2 |
| 存储 | JSON + SQLite |
| 在线数据 | EasyEDA 开放 API |
| 测试 | pytest + ruff |

## 当前状态

**1186 tests passed** | **ruff 全绿** | AI-Only 架构（无 Mock/Demo）

### 支持的电路类别（12 种）

Buck, LDO, Boost, Flyback, SEPIC, Charge Pump, OpAmp, MCU, Sensor, Connector, MOSFET, Diode

### 器件库

7 个器件（含 1 个通过 PDF 导入自动生成）：

| 型号 | 类型 | 来源 |
|------|------|------|
| AMS1117-3.3 | LDO | 预置 |
| TPS5430 | Buck | 预置 |
| TPS54202 | Buck | **PDF 导入** |
| LED_INDICATOR | LED | 预置 |
| VOLTAGE_DIVIDER | 分压器 | 预置 |
| RC_LOWPASS | RC 滤波器 | 预置 |
| W25Q32JV | Flash | 预置 |

> 器件库是动态的。任何型号都可以通过上传 PDF datasheet 自动导入。

## 项目结构

```
schemaforge/
├── ai/              # AI 客户端 (kimi-k2.5)
├── agent/           # AI 编排层 (Orchestrator + 18 个工具)
├── common/          # 共享基础设施 (事件/错误/进度)
├── design/          # 设计治理层
│   ├── synthesis.py       # AI 解析 → 匹配 → 计算 → 渲染 → 打包
│   ├── review.py          # 42 条工程审查规则
│   ├── candidate_solver.py    # 6 维候选评分
│   ├── clarifier.py       # 需求澄清 + AI 增强
│   ├── planner.py         # NL → 模块需求
│   ├── ir.py              # Design IR (唯一中间真值)
│   └── ...
├── gui/             # PySide6 GUI
│   ├── pages/           # 设计页 + 器件库页
│   ├── widgets/         # Chat 面板 + SVG 查看器
│   └── workers/         # 5 个 QThread Worker
├── ingest/          # 数据导入 (PDF/图片→器件)
├── library/         # 器件库 (DeviceModel + 8 设计知识字段)
├── schematic/       # 渲染器 (TopologyRenderer + 通用布局)
├── store/           # 器件数据 + 参考设计
└── workflows/       # 工作流编排
    └── schemaforge_session.py  # 统一工作台（唯一后端）

tests/                     # 43 个测试文件, 1186 个测试用例
main.py                    # CLI 入口
gui.py                     # GUI 入口
```

## 核心设计约束

1. **AI 只做理解，不做执行** — AI 提取结构化字段，本地代码做所有计算/渲染/约束
2. **器件库驱动** — 所有器件数据从库中读取，不硬编码；任意器件可通过 PDF 导入
3. **确定性输出** — 相同输入 + 相同器件库 = 相同原理图（AI 不参与计算/渲染）
4. **所有入口共用同一后端** — CLI / GUI / Agent 用同一套 SchemaForgeSession
5. **新增功能必须同时回答"如何审查"** — 不能只实现生成，还要验证、审查、解释

## AI 配置

模型配置在 `schemaforge/ai/client.py` L14-17（**禁止修改**）：

| 配置项 | 值 |
|--------|-----|
| Model | kimi-k2.5 |
| Base URL | `https://coding.dashscope.aliyuncs.com/v1` |
| API Key | 硬编码在代码中 |
