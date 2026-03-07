# SchemaForge 系统级 IR 分层规范

## 三层分离

```
┌─────────────────────────────────────────────┐
│ 意图层 (Intent Layer)                        │
│ - SystemDesignRequest                        │
│ - ModuleIntent                               │
│ - ConnectionIntent                           │
│ 来源: AI 解析                                │
│ 特征: 可能不完整、有歧义、模块级粒度          │
└──────────────────┬──────────────────────────┘
                   │ 本地规则引擎
                   ▼
┌─────────────────────────────────────────────┐
│ 解析层 (Resolution Layer)                    │
│ - ModuleInstance (器件已命中、端口已映射)      │
│ - PortRef (引脚级端口引用)                    │
│ - ResolvedConnection (引脚级连接)             │
│ - SystemNet (全局网络)                        │
│ 来源: 本地确定性代码                          │
│ 特征: 完整、无歧义、引脚级粒度               │
└──────────────────┬──────────────────────────┘
                   │ 本地综合/渲染/导出
                   ▼
┌─────────────────────────────────────────────┐
│ 产物层 (Artifact Layer)                      │
│ - SystemDesignIR (唯一中间真值)               │
│ - SystemBundle (SVG + BOM + SPICE)           │
│ 来源: 本地渲染/导出引擎                       │
│ 特征: 可持久化、可回放、可增量更新            │
└─────────────────────────────────────────────┘
```

## 关键约束

1. **AI 只产出意图层**。解析层和产物层全部由本地代码生成。
2. **SystemDesignIR 是唯一真值**。所有下游操作（渲染、BOM、SPICE、审查）都从 IR 读取。
3. **意图层允许不完整**。缺失信息记为 `ambiguities`，由用户确认补全。
4. **解析层必须完整**。不完整的模块标记为 `NEEDS_ASSET` 或 `ERROR`，不参与综合。
5. **产物层可增量更新**。修改某个模块只重建受影响子图。

## SystemDesignIR 结构

```python
@dataclass
class SystemDesignIR:
    request: SystemDesignRequest           # 意图层快照
    module_instances: dict[str, ModuleInstance]  # module_id → 实例
    connections: list[ResolvedConnection]   # 引脚级连接
    nets: dict[str, SystemNet]             # 全局网络
    warnings: list[str]                    # 审查警告
    unresolved_items: list[dict]           # 待解决项
    evidence_map: dict[str, list[str]]     # 决策证据链
```

## 生命周期

```
EMPTY → INTENTS_PARSED → MODULES_RESOLVED → CONNECTIONS_RESOLVED
  → SYNTHESIZED → RENDERED → REVIEWED → PATCHED → FINAL
```

每个状态转换都由一个确定性本地函数驱动。
