# `system/utils/platform/` 分区说明

平台通用工具（字典 / 菜单 / 标签 / 代码生成 / 监控 / 种子等）。本目录按
「服务型 / 只读查询型 / 纯工具型」三类分区，逐步把**服务型**模块下沉到
`system/services/`，本目录最终只保留只读查询与纯工具两类。归类口径与术语与
[`docs/plans/system-utils域拆分映射-2026.10.md`](../../../docs/plans/system-utils域拆分映射-2026.10.md)
（utils 五域归位）及 [`docs/plans/system-services服务层下沉-2026.10.md`](../../../docs/plans/system-services服务层下沉-2026.10.md)
（服务层目标形态）保持一致。

## 判据

| 类别 | 判据 | 归属 |
|---|---|---|
| **服务型** | 写库 / 事务 / 文件落盘 / 外部副作用（发送、改配置、生成产物）；承载业务编排 | 下沉 `system/services/` |
| **只读查询型** | 只读库 / 只读外部系统，聚合或预演后产出数据，无写副作用 | 留在本目录 |
| **纯工具型** | 无 IO，纯计算 / 协议 / 常量 / 适配（序列化、编码、文案、推导、mixin 编排面） | 留在本目录 |

判据以「模块自述职责 + 是否存在写副作用」为准：仅因被视图调用、或名字含
`monitor`/`codegen` 等不改变类别；`cache.set`（缓存写）不计入服务型写副作用。

## 逐文件归属清单

### 服务型（下沉候选，滚动迁移）

| 文件 | 职责 | 迁移状态 |
|---|---|---|
| `modelfield.py` | 模型字段标签同步（写 `ModelLabelField`） | 待滚（下一步候选） |
| `modelset.py` | ViewSet 共用动作编排（含角色权限变更写库、配置缓存失效） | 待滚 |
| `seed.py` | 内置种子装配（写 fixture 文件 + 空时间戳回填落库） | 待滚 |
| `tags.py` | 通用标签中心（打标读写落库） | 待滚 |
| `permission_sync/` | 权限点同步（写菜单 / 权限点） | 待滚（整包） |
| `credential.py` | 凭据治理（轮换 / 重加密 + 审计落库） | **已迁** `system/services/credential.py` |
| `credential_rotate.py` | 凭据轮换动作（落库 + 审计 + 缓存失效） | **已迁** `system/services/credential_rotate.py` |

### 只读查询型（留在本目录）

| 文件 | 职责 |
|---|---|
| `dict.py` | 数据字典读取（带缓存，信号失效） |
| `codegen_gui.py` | 代码生成器 GUI 适配（模型内省 + 字典查询 + 产物预览/打包，不落盘不写库） |
| `module_impact.py` | 功能裁剪影响面预演（只读复算） |
| `monitor_events.py` | 监控告警 / 异常 / 任务事件查询 |
| `monitor_history.py` | 监控历史趋势时间桶聚合 |
| `monitor_metrics.py` | 监控面板指标采集（psutil/inspect + 只读库） |
| `permission_preview/` | 数据权限规则预览 |

### 纯工具型（留在本目录）

| 文件 | 职责 |
|---|---|
| `codegen_fields.py` | 生成器字段计划翻译 / 覆盖写回（纯 ctx 计算） |
| `menu.py` | 菜单 / 权限点推导（URL registry + 模型内省，无 IO） |
| `monitor_export.py` | 监控报表编码（CSV / Excel，返回字节流） |
| `rule_meta.py` | 数据权限规则可读文案常量 |

## 迁移滚动方向

- 服务型模块**逐一下沉**，每个模块独立保持行为零变化（接口/文案/业务码不变，
  仅移动模块并更新全仓引用，含测试 `monkeypatch` 目标）；
- 调用方（视图 / 管理命令 / 其他 app）改从 `system.services.<模块>` 导入；
  `system/services/__init__.py` 契约门面按需增补惰性导出；
- 迁移完成即在「迁移状态」列标注 `已迁`，本目录不再保留同名实现（禁止双实现）。
