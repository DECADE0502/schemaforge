# SchemaForge AI-Only 改造 — 实现文档

## 一、目标

将 SchemaForge 从一个有大量 Mock 脚手架的原型，改造为**真正由 AI 驱动、能处理任意器件的可用工具**。

核心改造：

1. **砍掉所有 Mock/离线/演示路径** — 只保留真实 AI 流程
2. **统一到一条后端** — SchemaForgeSession 作为唯一后端，删除经典链和链路选择
3. **打通器件导入闭环** — PDF 上传 → AI 提取 → 入库 → 自动续设计，端到端可用
4. **AI 驱动需求解析** — 用真实 LLM 替代正则/关键字匹配
5. **GUI 全流程可用** — 从打开到输入到看到原理图，截图验证
6. **多轮对话可用** — 改电压、换器件、加模块都能正确响应

**核心哲学不变：AI 只做决策和提问，本地工具负责执行。**

---

## 二、当前架构问题

### 2.1 三条链路并存

```
经典链 (core/engine.py)        ← 模板驱动，完全基于 Mock
新主链 (design_session.py)     ← 候选评分+审查，有 Mock+AI 双模式
统一工作台 (schemaforge_session.py)  ← 精确型号+公式驱动，推荐
```

**改造后：只保留统一工作台，其他两条降级为内部工具模块。**

### 2.2 Mock 代码遍布各处

| 位置 | Mock 逻辑 | 改造方案 |
|------|-----------|----------|
| `planner.py` `_plan_mock()` | 关键字匹配伪装 AI | 删除 Mock 分支，始终走 `_plan_ai()` |
| `ai/client.py` `call_llm_mock()` | 预定义 JSON 响应 | 删除 `DEMO_RESPONSES` 和 `call_llm_mock()` |
| `synthesis.py` `parse_design_request()` | 纯正则解析 | 改为 AI 解析，正则作为 fallback |
| `topology_draft.py` Mock 模式 | 固定拓扑输出 | 删除 Mock 分支 |
| `orchestrator.py` Mock 模式 | 预设工具调用 | 删除 Mock 分支 |
| `design_page.py` 模式选择 | "离线Mock" / "在线LLM" | 删除模式选择 combo |
| `design_page.py` 链路选择 | 4 条链路选择 | 删除链路选择 combo |
| `main.py` 经典链/Demo | 旧引擎+Demo 模式 | 统一到 SchemaForgeSession |

### 2.3 需求解析是纯正则

`parse_design_request()` 和 `parse_revision_request_v2()` 完全靠正则和关键字匹配。复杂/模糊需求会被误解或丢失。

**改造方案：** 用 AI (call_llm_json) 解析自然语言需求为结构化 `UserDesignRequest`，正则作为 AI 返回 None 时的 fallback。

### 2.4 导入闭环未跑通

`SchemaForgeSession.ingest_asset()` 和 `confirm_import()` 从未被 GUI 调用。GUI 补录器件走的是 `PdfImportDialog` + `LibraryService` 直接入库，绕过了 SchemaForgeSession 的校验→预览→确认流程。

**改造方案：** GUI 补录流程接入 `ingest_asset()` → 展示预览 → `confirm_import()`。

---

## 三、改造后的架构

```
用户输入 (自然语言)
    │
    ├─[GUI]── design_page.py (无模式/链路选择)
    ├─[CLI]── main.py (--online 默认，无 --demo/--new-chain)
    └─[Agent]── orchestrator.py (始终用真实 AI)
                    │
            SchemaForgeSession (唯一后端)
                    │
            AI 解析需求 (call_llm_json)
                    │
              ExactPartResolver ─── ComponentStore ─── devices/*.json
                    │
              (找到器件?)
               ╱        ╲
            是             否
             │              │
    Clarifier (AI 补全约束)   needs_asset
             │              │
    CandidateSolver (评分)   用户上传 PDF
             │              │
    Review (42 条审查)      AI 提取引脚/参数/应用电路
             │              │
    DesignRecipeSynthesizer   confirm_import → 入库
             │              │
    FormulaEvaluator        → 回到 Clarifier
             │
    TopologyRenderer
             │
    DesignBundle (SVG+BOM+SPICE+审查报告)
             │
        revise() ← 多轮对话 ← AI 解析修改请求
```

---

## 四、数据流

### 4.1 新建设计

```
用户: "用 TPS54202 搭一个 20V转5V 的 DCDC 电路"
  → AI 解析 → UserDesignRequest(part_number="TPS54202", category="buck", v_in=20, v_out=5)
  → ExactPartResolver.resolve("TPS54202")
  → 库里没有 → return needs_asset
  → GUI 弹出导入面板
  → 用户上传 TPS54202.pdf
  → ingest_asset("TPS54202.pdf") → AI 提取引脚/参数/应用电路 → ImportPreview
  → GUI 展示预览 → 用户确认
  → confirm_import() → 入库 → _build_from_device()
  → Clarifier 补全缺失约束
  → CandidateSolver 评估方案质量
  → Review 42 条规则审查
  → Synthesizer 计算外围参数
  → Renderer 生成 SVG
  → return DesignBundle
```

### 4.2 多轮修改

```
用户: "把输出电压改成 3.3V"
  → AI 解析修改请求 → RevisionRequest(param_updates={"v_out": "3.3"})
  → revise() → 重新计算 → 重新渲染
  → return 更新后的 DesignBundle
```

### 4.3 器件替换

```
用户: "换成 TPS5430"
  → AI 解析 → RevisionRequest(replace_device="TPS5430")
  → resolve("TPS5430") → 库里有
  → _build_from_device(TPS5430) → 重新设计
```

---

## 五、GUI 改造

### 改造前

```
┌─ 输入面板 ──────────┐
│ [运行模式: 离线Mock ▼] │  ← 删除
│ [后端链路: 统一工作台 ▼]│  ← 删除
│ [⚡ 生成] [🎯 Demo]   │  ← 删除 Demo
└─────────────────────┘
```

### 改造后

```
┌─ 输入面板 ──────────┐
│ 电路需求输入           │
│ [快捷模板: 选择预设 ▼] │  ← 保留，用于填充文本
│ [⚡ 生成]              │  ← 唯一按钮
│ 状态: 就绪             │
└─────────────────────┘
```

所有生成都走 `SchemaForgeSession.start()` → 真实 AI。

### 缺失器件导入流程

改造后的流程走 `SchemaForgeSession.ingest_asset()` → `confirm_import()`：

1. `start()` 返回 `needs_asset` → 显示导入面板
2. 用户上传 PDF → 调用 `ingest_asset(filepath)`
3. 返回 `ImportPreview`（引脚预览 + 待确认问题）
4. GUI 展示预览 → 用户确认/补充
5. 调用 `confirm_import(answers)` → 入库 → 自动续设计

---

## 六、CLI 改造

### 改造前

```bash
python main.py                          # 经典链交互
python main.py --demo                   # Demo 模式
python main.py --new-chain -i "..."     # 新主链单次
python main.py --unified -i "..."       # 统一工作台
python main.py --orchestrated -i "..."  # AI 编排
```

### 改造后

```bash
python main.py                          # 统一工作台交互（支持多轮）
python main.py -i "用 TPS5430 搭 12V转3.3V DCDC"  # 单次模式
python main.py --orchestrated           # AI 编排交互模式
```

删除 `--demo`、`--new-chain`、`--online` (默认在线)、`--unified` (默认就是)。

---

## 七、需要删除的代码

| 文件 | 删除内容 |
|------|----------|
| `ai/client.py` | `DEMO_RESPONSES` dict, `call_llm_mock()` 函数 |
| `design/planner.py` | `_plan_mock()` 方法, `use_mock` 参数 |
| `design/clarifier.py` | `use_mock` 参数及 Mock 分支 |
| `design/topology_draft.py` | Mock 分支 |
| `agent/orchestrator.py` | `use_mock` 参数及 `_mock_turn()` |
| `workflows/schemaforge_session.py` | `use_mock` 参数 |
| `workflows/design_session.py` | `use_mock` 参数 |
| `gui/pages/design_page.py` | `_mode_combo`, `_chain_combo`, Demo 按钮 |
| `gui/workers/engine_worker.py` | `ClassicEngineWorker`, `RetryDesignWorker`, `use_mock` 参数 |
| `main.py` | `--demo`, `--new-chain`, `--online`, `--unified` 参数; 经典链/Demo 代码 |
| `core/engine.py` | `use_mock` 参数 (保留引擎但不再作为入口) |

---

## 八、AI 解析需求接入方案

### parse_design_request (新版)

```python
def parse_design_request(user_input: str) -> UserDesignRequest:
    """AI 驱动的需求解析，正则 fallback"""
    # 1. 先尝试 AI 解析
    result = call_llm_json(
        system_prompt=PARSE_REQUEST_PROMPT,
        user_message=user_input,
    )
    if result is not None:
        return UserDesignRequest.from_ai_result(result)

    # 2. AI 失败，fallback 到正则
    return _parse_design_request_regex(user_input)
```

### parse_revision_request_v2 (新版)

同理，AI 解析修改意图，正则 fallback。

---

## 九、实现顺序

```
Phase 1: 删除 Mock/Demo/多链路代码，统一后端 (Task 1-3)
Phase 2: AI 驱动需求解析，替换正则 (Task 4)
Phase 3: 统一工作台集成 Clarifier/Review (Task 5)
Phase 4: GUI 导入闭环接入 SchemaForgeSession (Task 6)
Phase 5: 修复测试，全量回归 (Task 7)
Phase 6: GUI 截图验证 + CLI 端到端验证 (Task 8)
```
