# SchemaForge 架构冻结文档

## 兼容层（冻结，不再扩展）

以下模块冻结为兼容层，只做维护修复，不添加新功能：

| 模块 | 路径 | 状态 |
|------|------|------|
| 旧引擎 | `core/engine.py` | 冻结 |
| 旧渲染 | `render/*.py` | 冻结 |
| 旧会话 | `workflows/design_session.py` | 冻结 |
| 单器件工作台 | `workflows/schemaforge_session.py` | 兼容层（单器件继续可用） |

## 新系统层（活跃开发）

| 模块 | 路径 | 状态 |
|------|------|------|
| 系统模型 | `system/models.py` | ✅ 已完成 |
| AI 协议 | `system/ai_protocol.py` | 待建 |
| 器件解析 | `system/resolver.py` | 待建 |
| 连接规则 | `system/connection_rules.py` | 待建 |
| 模块综合 | `system/synthesis.py` | 待建 |
| 全局实例 | `system/instances.py` | 待建 |
| 系统渲染 | `system/rendering.py` | 待建 |
| BOM 导出 | `system/export_bom.py` | 待建 |
| SPICE 导出 | `system/export_spice.py` | 待建 |
| 系统会话 | `system/session.py` | 待建 |
| 视觉审稿 | `visual_review/*.py` | 待建 |

## 不能继续扩展的函数（T008）

| 函数 | 位置 | 原因 |
|------|------|------|
| `SchemaForgeSession.start()` | schemaforge_session.py | 单器件架构，无法扩展为多器件 |
| `SchemaForgeSession._build_from_device()` | schemaforge_session.py | 只处理一个 device + bundle |
| `_render_spice()` | synthesis.py | 单模块 SPICE，无法合并多模块 |
| `build_bundle()` | synthesis.py | 单器件 bundle |
| `layout_buck/ldo/...()` | topology.py | 各自创建独立 Drawing，无法合并 |
| `parse_design_request()` | synthesis.py | 只返回单个 UserDesignRequest |

## 必须拆分的"上帝类"（T009）

| 类 | 位置 | 问题 | 拆分方向 |
|-------|------|------|----------|
| `SchemaForgeSession` | schemaforge_session.py | 同时负责 start/revise/ingest/confirm/orchestrate | 拆为 SystemSession + ImportPipeline + Orchestrator |
| `DesignRecipeSynthesizer` | synthesis.py | 同时负责解析请求 + 计算参数 + 渲染 + 导出 | 拆为 AI Protocol + Module Synthesizer + Renderer + Exporter |

## 系统设计生命周期（T007）

```
EMPTY                    # 空 IR
  → INTENTS_PARSED       # AI 解析完成，意图层就绪
  → MODULES_RESOLVING    # 正在解析器件
  → MODULES_RESOLVED     # 所有可用器件已命中
  → CONNECTIONS_RESOLVED # 引脚级连接已解析
  → SYNTHESIZED          # 参数已综合
  → RENDERED             # SVG 已生成
  → REVIEWED             # 审查已完成
  → PATCHED              # 视觉修补已完成
  → FINAL                # 最终产物就绪
```

特殊状态：
- `NEEDS_ASSET`: 某个模块器件缺失，等待用户上传
- `NEEDS_CONFIRMATION`: 有歧义需要用户确认
- `PARTIAL`: 部分模块已完成，部分待解决
