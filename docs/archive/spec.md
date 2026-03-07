# SchemaForge — 规格说明（Specification）

> 版本: v1.0  
> 日期: 2026-03-05  
> 状态: 初版

---

## 1. 项目概述

**SchemaForge** 是一个约束驱动的AI原理图生成器。用户输入自然语言电路需求，系统输出专业级原理图SVG + BOM清单 + SPICE网表。

**核心设计哲学**: AI不直接画原理图。AI只做"理解"和"决策"，本地系统做"约束"和"渲染"。

---

## 2. 验收标准（机器可判定）

### 2.1 核心功能验收

| ID | 验收项 | 验收命令 | PASS定义 | FAIL定义 |
|----|--------|----------|----------|----------|
| F-01 | 数据模型完整 | `python -c "from schemaforge.core.models import *; print('OK')"` | 输出 `OK`，退出码0 | 导入错误或异常 |
| F-02 | 分压器模板可渲染 | `python -c "from schemaforge.render.divider import render_divider; render_divider()"` | 生成SVG文件，退出码0 | 异常或无文件输出 |
| F-03 | LDO模板可渲染 | `python -c "from schemaforge.render.ldo import render_ldo; render_ldo()"` | 生成SVG文件 | 异常 |
| F-04 | LED模板可渲染 | `python -c "from schemaforge.render.led import render_led; render_led()"` | 生成SVG文件 | 异常 |
| F-05 | RC滤波器模板可渲染 | `python -c "from schemaforge.render.rc_filter import render_rc_filter; render_rc_filter()"` | 生成SVG文件 | 异常 |
| F-06 | 组合模板可渲染 | `python -c "from schemaforge.render.composite import render_composite; render_composite()"` | 生成SVG文件 | 异常 |
| F-07 | ERC检查全通过 | `python -m pytest tests/test_erc.py -v` | 所有测试PASS | 任意FAIL |
| F-08 | BOM导出正确 | `python -m pytest tests/test_exporter.py::test_bom -v` | 输出包含所有器件 | 缺失器件 |
| F-09 | SPICE导出正确 | `python -m pytest tests/test_exporter.py::test_spice -v` | 输出合法SPICE语法 | 语法错误 |
| F-10 | AI JSON验证 | `python -m pytest tests/test_validator.py -v` | 合法JSON通过，非法JSON被拒 | 漏判 |
| F-11 | CLI端到端 | `python main.py --demo` | 完整流程无异常，生成所有输出 | 异常退出 |

### 2.2 代码质量验收

| ID | 验收项 | 验收命令 | PASS定义 | FAIL定义 |
|----|--------|----------|----------|----------|
| Q-01 | 类型检查 | `python -m mypy schemaforge/ --ignore-missing-imports` | 0 error | 任意error |
| Q-02 | 代码风格 | `python -m ruff check schemaforge/` | 0 violation | 任意violation |
| Q-03 | 单元测试 | `python -m pytest tests/ -v` | 全部PASS | 任意FAIL |

### 2.3 文档验收

| ID | 验收项 | 判定方式 | PASS定义 |
|----|--------|----------|----------|
| D-01 | devlog.md | 文件存在且有≥5条Round记录 | 每条含Goal/Plan/Changes/Commands |
| D-02 | ai_interaction.md | 文件存在且有≥3个Round | 每Round含输入/输出摘要/决策理由 |
| D-03 | prompts/ | 至少有v001版本 | agent_system_v001.md存在 |

---

## 3. 技术栈

- Python 3.10+
- schemdraw: 原理图SVG渲染
- pydantic: 数据模型校验
- openai: LLM API调用（OpenAI兼容接口）
- rich: CLI界面（中文TUI）

**不使用**: Gradio/Web框架（用户要求非Web）

---

## 4. 安全约束

- 不执行用户输入的任意命令
- AI输出必须经过本地JSON Schema验证
- 模板系统保证连线正确性，不信任AI连线
- API Key通过环境变量传入，不硬编码

---

## 5. 输出物

1. `output/*.svg` — 原理图
2. `output/*_bom.md` — BOM清单
3. `output/*.spice` — SPICE网表
4. `devlog.md` — 开发日志
5. `ai_interaction.md` — AI交互记录
6. `prompts/` — Prompt版本化资产
