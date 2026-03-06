# SchemaForge — 开发计划（Plan）

> 版本: v1.0  
> 日期: 2026-03-05  
> 总预期: 6个阶段

---

## 阶段总览

| 阶段 | 内容 | 状态 | 验收 |
|------|------|------|------|
| Phase 0 | 项目初始化、文档骨架 | ✅ 完成 | 目录存在、文档创建 |
| Phase 1 | 核心骨架：数据模型 + 分压器模板 + 渲染 | ✅ 完成 | F-01, F-02 |
| Phase 2 | 模板系统：LDO/LED/RC + calculator + 注册表 | ✅ 完成 | F-03, F-04, F-05 |
| Phase 3 | AI集成：LLM client + prompts + validator + engine | ✅ 完成 | F-10 |
| Phase 4 | ERC验证 + BOM/SPICE导出 | ✅ 完成 | F-07, F-08, F-09 |
| Phase 5 | CLI界面 + 组合模板 + 端到端演示 | ✅ 完成 | F-06, F-11 |

---

## Phase 0 — 项目初始化

**目标**: 创建完整目录结构、规格文档、计划文档、日志文件

**产出物**:
- [x] 目录结构: schemaforge/{core,render,ai,output}
- [x] spec.md — 验收标准
- [x] plan.md — 本文件
- [x] devlog.md — 开发日志
- [x] ai_interaction.md — AI交互记录
- [x] prompts/agent_system_v001.md — 初始系统prompt
- [x] prompts/agent_user_template_v001.md — 用户prompt模板
- [x] requirements.txt — 依赖声明

---

## Phase 1 — 核心骨架

**目标**: 实现所有Pydantic数据模型 + 最简单的分压器模板 + schemdraw渲染验证

**产出物**:
- [x] core/models.py — PinType, PinDef, ComponentDef, Net, NetConnection, CircuitInstance, CircuitTemplate, ParameterDef
- [x] core/templates.py — 模板注册表骨架 + 分压器模板
- [x] render/base.py — 渲染基类
- [x] render/divider.py — 分压器渲染函数
- [x] tests/test_models.py — 模型单元测试（10个测试全部PASS）

**验收**: `python -c "from schemaforge.core.models import *"` → OK  
**验收**: 分压器SVG生成到 output/

---

## Phase 2 — 模板系统

**目标**: 实现全部4个基础模板 + 参数计算器

**产出物**:
- [x] LDO模板 + render/ldo.py — AMS1117 IC渲染
- [x] LED模板 + render/led.py — 带限流电阻
- [x] RC滤波器模板 + render/rc_filter.py
- [x] core/calculator.py — E24标准阻值、分压计算、LED限流电阻、RC滤波器（16个测试全PASS）
- [x] 每个模板的SVG渲染验证 — 全部4模板SVG生成正常

**验收**: 每个模板独立渲染SVG无异常

---

## Phase 3 — AI集成

**目标**: LLM调用封装 + 系统prompt + JSON验证 + 引擎串联

**产出物**:
- [x] ai/client.py — LLM API调用（kimi-k2.5硬编码，含离线mock + 在线模式）
- [x] ai/prompts.py — 系统prompt + few-shot（从prompts/目录加载）
- [x] ai/validator.py — AI输出JSON Schema验证（8个测试全PASS）
- [x] core/engine.py — 核心引擎：AI输出 → 验证 → 实例化 → ERC → 渲染 → 导出

**验收**: 给定合法JSON → 能走通全流程

---

## Phase 4 — ERC验证 + 导出

**目标**: 6条ERC规则 + BOM/SPICE导出

**产出物**:
- [x] core/erc.py — ERCChecker类（6条规则，9个测试全PASS，修复了floating pin cross-product bug）
- [x] core/exporter.py — BOM + SPICE导出（修复了SPICE单位后缀bug: 110Ω→110）
- [x] tests/test_erc.py — 9个测试全PASS
- [x] tests/test_exporter.py — 17个测试全PASS（含SPICE值格式化验证）

**验收**: ERC能拦截错误参数，导出格式正确

---

## Phase 5 — CLI + 组合模板 + 演示

**目标**: 中文CLI界面、组合模板、端到端完整演示

**产出物**:
- [x] render/composite.py — LDO+LED组合渲染
- [x] main.py — CLI入口（rich TUI，支持 --demo / --online / --input / --templates）
- [ ] examples/ — 示例输入输出（待完成）
- [x] 完整端到端演示 — `python main.py --demo` 全流程无异常

**验收**: `python main.py --demo` 无异常完成
