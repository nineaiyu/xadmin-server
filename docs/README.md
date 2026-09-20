# xadmin-server 文档中心

> 目标：**新人按本目录完成环境搭建与第一个功能开发**；开发中遇到"不报错但没效果"类问题先查
> [dev-pitfalls.md](dev-pitfalls.md)。外站 https://docs.dvcloud.xin/ 为补充资料，以本目录为准。
>
> 文档有 CI 守护（索引覆盖 / 事实一致性 / 教程镜像 / 手册路径 / 站点导航，见 §五），
> **新增文档必须登记本索引**。

## 一、二次开发：从这里开始（必读 8 篇）

| # | 文档 | 一句话 |
|---|------|--------|
| 1 | [guide/first-module-30min.md](guide/first-module-30min.md) | **30 分钟开发第一个业务模块**：建 app → `generate_crud` → 菜单授权 → `doctor` 自检（生成器主线） |
| 2 | [architecture/component-handbook.md](architecture/component-handbook.md) | **组件手册**：后端 14 组 / 前端 10 组组件的职责·用法·依赖·配置项·扩展点（含权威源路径） |
| 3 | [guide/recipes.md](guide/recipes.md) | **扩展流程处方集**：加字段 / 加按钮 / 自定义渲染器 / 定时任务 / AI 动作等 23 条任务步骤 |
| 4 | [architecture/overview.md](architecture/overview.md) | 架构总览：分层、元数据驱动、子体系速览、前后端协作时序 |
| 5 | [architecture/framework-cookbook.md](architecture/framework-cookbook.md) | 框架能力速查：ViewSet 选型 / Action↔BaseApi / 覆写红线 / 前端契约 |
| 6 | [architecture/方案选型与对比.md](architecture/方案选型与对比.md) | 选型决策依据：列表页三范式 / 组件选型速查 / 与同类方案对比 |
| 7 | [框架开发遵循准则.md](框架开发遵循准则.md) | 服务端 + 前端统一约定与检查清单（含数据字典 / i18n / 按钮交互） |
| 8 | [dev-pitfalls.md](dev-pitfalls.md) | 新手陷阱清单 24 条（**静默失败类问题先查这里**） |

**三条先记住的心智模型**：① 列表页 = 后端元数据驱动（`search-columns` / `search-fields`）；
② 权限码 = `动作:组件名`（如 `list:SystemUser`，必须入库授权）；③ 契约变更先改 `docs/schema/` 再同步前端。

## 二、按需查阅（开发）

| 主题 | 文档 |
|------|------|
| 元数据协议 | [architecture/metadata-protocol.md](architecture/metadata-protocol.md)：字段语义 / `input_type` 推断链 / 四通道注册表 / 失败可见性 |
| 权限体系 | [architecture/permission.md](architecture/permission.md)（三层 + 应用级授权）；配置操作教程 [architecture/data-permission.md](architecture/data-permission.md) / [architecture/field-permission.md](architecture/field-permission.md) |
| 模块化与裁剪 | [architecture/模块化与功能裁剪.md](architecture/模块化与功能裁剪.md)：三级分层 / 发行预设 / 六层裁剪 / CLI 与管理页 |
| 认证扩展 | [architecture/mfa.md](architecture/mfa.md)（MFA / 412 协议）、[architecture/oauth-login.md](architecture/oauth-login.md)（第三方登录 / IM 扫码） |
| 目录同步 | [architecture/ldap-readiness.md](architecture/ldap-readiness.md)、[architecture/scim.md](architecture/scim.md)、[architecture/scim-idp-readiness.md](architecture/scim-idp-readiness.md) |
| 通知渠道 | [architecture/notification-channels.md](architecture/notification-channels.md)（新增渠道 = 新增一个文件） |
| 缓存 / 索引 | [architecture/cache.md](architecture/cache.md)（含键规范与失效矩阵）、[architecture/indexes.md](architecture/indexes.md) |
| 错误码 | [exception-handling.md](exception-handling.md)（新增错误码必须先登记） |
| 契约 Schema | [schema/README.md](schema/README.md)（search-columns / search-fields 真源） |
| 开放平台 | [open-platform/README.md](open-platform/README.md)（接入指南）、[open-platform/events.md](open-platform/events.md)（Webhook 事件契约） |
| 前端开发（xadmin-client） | 页面 / E2E / 契约入口见 [xadmin-client/docs/README.md](https://github.com/nineaiyu/xadmin-client/blob/dev/docs/README.md)；页面写法见 [recipes.md](guide/recipes.md) R9–R14 / R22、[component-handbook.md](architecture/component-handbook.md) §二 |

## 三、部署与运维（ops/）

| 文档 | 内容 |
|------|------|
| [ops/deployment.md](ops/deployment.md) | **部署与运维手册**：配置速查表（§9）/ Docker / 备份恢复 / 升级回滚 |
| [ops/runbook.md](ops/runbook.md) | 故障处置（常见故障 → 处置步骤） |
| [ops/pitr.md](ops/pitr.md) | WAL 归档与时间点恢复（PITR） |
| [ops/observability.md](ops/observability.md) | 可观测性与 SLO（指标 / 告警分级 / 演练记录） |
| [ops/release-checklist.md](ops/release-checklist.md) | 发布窗口 checklist（基线门禁 + 执行记录） |
| [ops/performance-baseline.md](ops/performance-baseline.md) | 性能基线测定流程（silk + k6） |
| [ops/backup-drill-2026-09.md](ops/backup-drill-2026-09.md)、[ops/backup-drill-2026-09-16.md](ops/backup-drill-2026-09-16.md)、[ops/backup-drill-2026-Q4.md](ops/backup-drill-2026-Q4.md)、[ops/backup-drill-2027-03.md](ops/backup-drill-2027-03.md) | 备份恢复演练记录（历史归档） |

## 四、维护与决策（长期演进）

| 文档 | 内容 |
|------|------|
| [adr/README.md](adr/README.md) | **架构决策记录索引（46 篇）**——"当时为什么这样选"；新增决策按编号顺延并登记 |
| [plans/README.md](plans/README.md) | 规划与治理：长期优化方案 / 当前年度计划 / 最近年度回顾；**已完成的一次性台账在 `plans/archive/`** |
| [metrics.md](metrics.md) | 基线指标看板（测试 / 体积 / 性能 KPI 基线 → 实测履历） |
| [security-review.md](security-review.md) | 安全自查归档（按轮次追加） |
| [cache-keys-audit.md](cache-keys-audit.md) | 缓存键与 JWT 审计（`scripts/check_cache_keys.py --strict`） |

## 五、目录结构与维护约定

```
docs/
├── README.md             本索引（二开必读 → 按需 → 运维 → 维护）
├── guide/                快速上手（30 分钟教程 + 扩展处方集）
├── architecture/         架构与组件（现状文档：组件手册 / 协议 / 权限 / 模块化…）
├── adr/                  架构决策记录（历史决策，含索引 README）
├── ops/                  部署与运维（手册 / runbook / 演练记录）
├── plans/                规划与治理（活跃 3 篇；历史归档在 archive/）
├── open-platform/        开放平台接入（指南 + 事件契约）
├── schema/               前后端契约 JSON Schema
├── imgs/                 文档配图
├── dev-pitfalls.md       新手陷阱清单
├── 框架开发遵循准则.md     开发统一约定与检查清单
├── exception-handling.md 错误码规范
├── metrics.md / security-review.md / cache-keys-audit.md   维护者参考
└── （归档）plans/archive/ 已完成的一次性方案 / 台账 / 历史盘点
```

**维护约定**：

1. 新增文档必须登记本索引（CI 守护 `scripts/check_doc_index.py`）；
2. 文档中的"当前事实"（版本 / 端口 / 覆盖率等）由 `scripts/check_doc_facts.py` 守护，
   跨仓事实覆盖 xadmin-docs 与 xadmin-client 文档；
3. 教程中的命令 / 参数 / demo 路径由 `scripts/check_tutorial_mirror.py` 守护；
   组件手册与活跃开发文档的引用路径由 `scripts/check_doc_paths.py` 守护；
   对外站点导航由 `scripts/check_docs_site_nav.py` 守护；
4. **已完成的一次性方案 / 台账**移入 `plans/archive/`（[plans/README.md](plans/README.md) 登记去向），
   活跃区只保留"在维护"的文档；ADR 状态变更同步更新 [adr/README.md](adr/README.md)；
5. API 文档随版本固化：每次 release 附带静态 `openapi.json`，部署环境可访问 `/api-docs/` 交互查阅。
