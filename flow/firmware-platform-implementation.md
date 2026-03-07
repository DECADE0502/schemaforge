# 固件管理改造 + 飞书通知 — 实现文档

## 一、目标

在现有包管理系统基础上：

1. **固件管理改造** — 上传固件时写入结构化元数据（OSS `x-oss-meta-*`），支持按模块浏览、筛选、编辑 release note
2. **飞书通知** — 关键事件通过飞书机器人推送模板卡片消息

不建任何新表。不做项目管理。现有 `t_release_packages` 表不改动。

---

## 二、固件存储方案

### 现状

固件文件已按 `part_code` 目录存在 OSS 上：

```
OSS Bucket: mondo-package-manager
└── origin-package/test/
    ├── 0200/           (电机电调)
    ├── 0300-Core/      (系统)
    ├── 0702-RC-BTN/    (遥控)
    └── ...
```

列表接口扫描目录获取文件名、大小、修改时间。唯一已有的元数据字段是 `x-oss-meta-releasenote`。

### 改造：用 OSS 元数据存固件信息

上传/编辑固件时，把元数据写入 OSS 对象头（`x-oss-meta-*`）。查询时通过 `GetObjectDetailedMeta` 读回来。

#### 元数据字段定义

| OSS Header Key | 类型 | 必填 | 说明 |
|---|---|---|---|
| `x-oss-meta-version` | string | 是 | 固件版本号，如 `26.02.25.01` |
| `x-oss-meta-type` | string | 是 | 固件类型：`DB` / `FAT` / `RC` |
| `x-oss-meta-releasenote` | string | 否 | Release Note（Markdown），已有字段保持兼容 |
| `x-oss-meta-uploader` | string | 是 | 上传人用户名 |
| `x-oss-meta-upload-time` | string | 否 | 上传时间（ISO 8601），不传则取 OSS LastModified |
| `x-oss-meta-checksum` | string | 否 | SHA256 校验和 |
| `x-oss-meta-status` | string | 否 | `active`（默认） / `archived` |

> OSS `x-oss-meta-*` 限制：每个对象最多 8KB 自定义元数据总量。release note 过长时截断或存正文前 N 字符，完整版单独存一个同名 `.releasenote.md` 文件。

#### 前端表格排序

固件列表表格的以下列支持点击列头进行升序/降序排序（使用 Ant Design Table 的 `sorter` 属性，纯前端排序）：

| 列 | 排序规则 |
|---|---|
| 固件名称 | 按字母序（`localeCompare`） |
| 上传人 | 按字母序（`localeCompare`） |
| 文件大小 | 按数值大小 |
| 上传时间 | 按时间先后（优先取 `upload_time`，fallback 到 `modified_time`） |

点击列头切换排序方向：无排序 → 升序 → 降序 → 无排序。排序图标使用 Ant Design 默认的上下箭头样式。

#### 写入时机

- **上传固件**：前端上传文件到 OSS 时，通过 `PutObject` 的 options 带上 meta headers
- **DB→RC 提升**：后端 `CopyObject` 复制出新文件，`x-oss-meta-type` 改为 `RC`，必须写入 `x-oss-meta-releasenote`
- **编辑 release note**：后端调用 `CopyObject`（原地复制）更新元数据
- **归档/删除**：更新 `x-oss-meta-status` 为 `archived`

#### 读取方式

- **固件列表**：扫描目录获取对象列表后，对每个对象调 `GetObjectDetailedMeta` 取元数据（或批量用 `ListObjectsV2` 返回的 UserMeta）
- **单个固件详情**：`GetObjectDetailedMeta(ossKey)` 获取全部 meta

---

## 三、API

### 改造现有接口（2 个）

| 接口 | 改动 |
|---|---|
| `POST /api/v1/firmware/list` | 返回值增加 `version`、`type`、`uploader`、`status` 字段（从 OSS meta 读取） |
| `POST /api/v1/firmware/category/list` | 同上，按模块分类列表也返回 meta 字段 |

### 新增接口（3 个）

统一前缀 `POST /api/v1/firmware/`。

| 接口 | 入参 | 出参 | 说明 |
|---|---|---|---|
| `upload` | `{ part_code, file_name, version, type, release_note?, uploader }` | `{ oss_path }` | 生成带 meta 的 presigned URL 或后端代传 |
| `promoteToRC` | `{ oss_key, release_note }` | `{ new_oss_path }` | DB→RC 提升：复制文件，type 改为 RC，**release_note 必填** |
| `updateMeta` | `{ oss_key, release_note?, status? }` | `{}` | CopyObject 原地更新元数据 |
| ~~`getNotificationConfig`~~ | — | — | 移至通用通知模块，见第五章 |
| ~~`updateNotificationConfig`~~ | — | — | 同上 |

### 不动的接口

现有 `/api/v1/firmware/metadata`（读 release note）、`/api/v1/firmware/package/*`（包管理全套）保持不变。

---

## 四、数据结构

```typescript
// 固件信息（从 OSS 对象 + meta 组装）
interface FirmwareInfo {
  firmware_name: string;   // OSS object key 的文件名部分
  part_code: string;       // 所属模块（从目录路径提取）
  module_name: string;     // 模块中文名（前端映射或后端字典）
  version: string;         // x-oss-meta-version
  type: string;            // x-oss-meta-type (DB/FAT/RC)
  file_size: number;       // OSS obj.Size
  oss_path: string;        // 完整 OSS key
  checksum: string;        // x-oss-meta-checksum
  release_note: string;    // x-oss-meta-releasenote
  uploader: string;        // x-oss-meta-uploader
  status: string;          // x-oss-meta-status (active/archived)
  upload_time: string;     // x-oss-meta-upload-time 或 OSS LastModified
}

// 通知频道配置（通用，见第五章）
interface NotificationChannel {
  channel_id: string;            // 频道唯一标识，如 "firmware", "device-manager"
  channel_name: string;          // 显示名称，如 "固件平台"
  chat_groups: ChatGroup[];      // 通知目标群组
  events: EventConfig[];         // 该频道下的事件列表
}

interface ChatGroup {
  group_id: string;              // 群 ID，如 "oc_xxx"
  group_name: string;            // 群名称（展示用）
  group_type: string;            // "release" | "alert" | "all"
}

interface EventConfig {
  event_name: string;            // 事件标识，如 "BUILD_COMPLETED"
  event_label: string;           // 显示名称，如 "构建成功"
  category: string;              // "release" | "alert"
  template_id: string;           // 飞书卡片模板 ID
  enabled: boolean;              // 是否启用
}
```

---

## 五、飞书通知（通用模块）

通知做成平台级通用服务，不绑定固件模块。任何业务模块都可以注册自己的频道和事件。

### 核心概念

```
Channel（频道）── 一个业务模块对应一个频道，如 "firmware"、"device-manager"
  ├── ChatGroup[] ── 该频道的通知目标群（按 release / alert 分类）
  └── EventConfig[] ── 该频道支持的事件列表（每个事件可独立开关）
```

### API（3 个）

独立前缀 `POST /api/v1/notification/`，不挂在 firmware 下。

| 接口 | 入参 | 出参 | 说明 |
|---|---|---|---|
| `listChannels` | `{}` | `{ channels: ChannelSummary[] }` | 列出所有已注册频道（配置页左侧列表） |
| `getChannelConfig` | `{ channel_id }` | `{ channel: NotificationChannel }` | 获取某频道完整配置 |
| `updateChannelConfig` | `{ channel_id, channel: NotificationChannel }` | `{}` | 更新某频道配置 |

### 配置存储

每个频道一个 OSS JSON 文件：`config/notification/{channel_id}.json`

示例 `config/notification/firmware.json`：

```json
{
  "channel_id": "firmware",
  "channel_name": "固件平台",
  "chat_groups": [
    { "group_id": "oc_xxx", "group_name": "固件发布群", "group_type": "release" },
    { "group_id": "oc_yyy", "group_name": "固件告警群", "group_type": "alert" }
  ],
  "events": [
    { "event_name": "FIRMWARE_CI_UPLOAD", "event_label": "CI 自动上传", "category": "alert", "template_id": "tpl_a", "enabled": true },
    { "event_name": "FIRMWARE_RC_DIRECT_UPLOAD", "event_label": "直传 RC", "category": "release", "template_id": "tpl_b", "enabled": true },
    { "event_name": "PROMOTE_DB_TO_RC", "event_label": "DB→RC 提升", "category": "release", "template_id": "tpl_c", "enabled": true },
    { "event_name": "BUILD_COMPLETED", "event_label": "构建成功", "category": "release", "template_id": "tpl_d", "enabled": true },
    { "event_name": "BUILD_FAILED", "event_label": "构建失败", "category": "alert", "template_id": "tpl_e", "enabled": true },
    { "event_name": "CREATE_BUILD", "event_label": "构建创建", "category": "alert", "template_id": "tpl_f", "enabled": false },
    { "event_name": "BUILD_CANCELED", "event_label": "取消打包", "category": "alert", "template_id": "", "enabled": false },
    { "event_name": "PACKAGE_EDIT_RELEASE_NOTE", "event_label": "Release Note 变更", "category": "alert", "template_id": "tpl_g", "enabled": true },
    { "event_name": "PACKAGE_EDIT_VISIBILITY", "event_label": "可见范围变更", "category": "alert", "template_id": "tpl_h", "enabled": true },
    { "event_name": "PACKAGE_ADD_TAG", "event_label": "添加标签", "category": "alert", "template_id": "tpl_i", "enabled": false },
    { "event_name": "PACKAGE_REMOVE_TAG", "event_label": "移除标签", "category": "alert", "template_id": "tpl_i", "enabled": false }
  ]
}
```

`listChannels` 扫描 `config/notification/` 目录下所有 JSON 文件，返回 `channel_id` + `channel_name` 列表。

### 后端调用方式

任何模块触发通知只需一行调用：

```go
notification.Send("firmware", "BUILD_COMPLETED", map[string]string{
    "package_name": "sisyphus",
    "version":      "V26.02.28.01",
    "operator":     "zhangsan",
})
```

`Send` 内部逻辑：
1. 读取 `config/notification/firmware.json`（可加内存缓存）
2. 查找 `event_name = "BUILD_COMPLETED"`，检查 `enabled`
3. 根据 `category` 找到对应 `group_type` 的群
4. 用 `template_id` + 传入的变量调飞书机器人 API

### 配置页面

独立路由 `/settings/notification`，不挂在固件管理下。

页面结构：

```
┌─────────────────────────────────────────────────┐
│  通知配置                                        │
├──────────┬──────────────────────────────────────┤
│ 频道列表  │  频道详情                              │
│          │                                      │
│ ● 固件平台│  通知群组                              │
│ ○ 设备管理│  ┌──────────┬──────────┬──────────┐  │
│ ○ ...    │  │ 群名称    │ 群 ID    │ 类型     │  │
│          │  │ 固件发布群 │ oc_xxx  │ release  │  │
│          │  │ 固件告警群 │ oc_yyy  │ alert    │  │
│          │  └──────────┴──────────┴──────────┘  │
│          │  [+ 添加群组]                         │
│          │                                      │
│          │  事件开关                              │
│          │  ┌──────────────────┬────┬────────┐  │
│          │  │ 事件              │开关│ 模板ID │  │
│          │  │ CI 自动上传       │ ✓  │ tpl_a  │  │
│          │  │ 直传 RC          │ ✓  │ tpl_b  │  │
│          │  │ DB→RC 提升       │ ✓  │ tpl_c  │  │
│          │  │ 构建成功          │ ✓  │ tpl_d  │  │
│          │  │ 构建失败          │ ✓  │ tpl_e  │  │
│          │  │ 构建创建          │ ✗  │ tpl_f  │  │
│          │  │ ...              │    │        │  │
│          │  └──────────────────┴────┴────────┘  │
│          │                          [保存]       │
└──────────┴──────────────────────────────────────┘
```

其他模块要接入通知：在 `config/notification/` 下新建一个 JSON 文件即可，配置页自动识别。

---

## 六、实现顺序

```
Phase 1: 改造固件列表接口，读取 OSS meta 并返回扩展字段
Phase 2: 新增 upload + promoteToRC + updateMeta 接口
Phase 3: 通用通知模块（notification.Send + 3 个配置 API + OSS JSON 读写）
Phase 4: 前端固件管理页面改造 + 通知配置页面（/settings/notification）
```
