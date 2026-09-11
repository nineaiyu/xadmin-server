# xadmin-server 文档中心

> 半年规划 T6.1 建立的 docs/ 知识库索引。目标：新人按本目录可完成环境搭建与第一个功能开发，无需依赖外站文档。
> 外站文档 https://docs.dvcloud.xin/ 降级为补充资料，逐步以本目录为准。

## 目录结构

```
docs/
├── README.md            本索引
├── adr/                 架构决策记录（ADR）
├── architecture/        架构设计文档
├── ops/                 部署与运维（deployment.md + runbook）
├── schema/              前后端契约 JSON Schema
├── imgs/                文档配图
├── exception-handling.md  异常处理与错误码规范
└── security-review.md   安全自查清单（按轮次追加归档）
```

## 新人上手路径

1. **环境搭建**：根 [README](../README.md)（快速启动命令）→ [ops/deployment.md](ops/deployment.md)（配置项详解 / Docker /
   生产部署 / 升级回滚）；
2. **理解架构**：[architecture/overview.md](architecture/overview.md)
   （总览导航）→ [architecture/permission.md](architecture/permission.md)（三层权限，本项目核心）；
3. **动手开发**：参考 demo app（Book 四件套 + 菜单初始化；前端 `BaseApi` + `RePlusPage`
   两行代码一个页面），契约与错误码遵循 [schema/](schema/README.md) 与 [exception-handling.md](exception-handling.md)；
4. **修改核心框架前**：读 [architecture/cache.md](architecture/cache.md)
   （缓存红线）与 [architecture/indexes.md](architecture/indexes.md)（索引规范），确保 pytest 全绿。

## 架构设计（architecture/）

| 文档                                                      | 内容                                           |
|---------------------------------------------------------|----------------------------------------------|
| [overview.md](architecture/overview.md)                 | 架构总览：分层、元数据驱动、子系统速览、协作时序（1289 行深度分析文档的精炼导航版） |
| [permission.md](architecture/permission.md)             | 三层权限体系设计：生效顺序、14 种数据规则速查、缓存/信号失效链路、调试指引与测试地图 |
| [data-permission.md](architecture/data-permission.md)   | 数据权限配置操作教程（配图）                               |
| [field-permission.md](architecture/field-permission.md) | 字段权限配置操作教程（配图）                               |
| [cache.md](architecture/cache.md)                       | 缓存策略统一审计：四套缓存键规范/TTL/失效矩阵/绕过 ORM 红线          |
| [indexes.md](architecture/indexes.md)                   | 索引评审记录：清单、不加索引的理由、EXPLAIN 回归                 |
| [mfa.md](architecture/mfa.md)                           | MFA 敏感操作二次验证设计：四后端 / 412 协议 / 权限工厂           |
| [scim.md](architecture/scim.md)                         | SCIM 2.0 用户目录同步：启用步骤 / 字段与组映射 / Okta、Entra 配置示例 / 排错 |
| [notification-channels.md](architecture/notification-channels.md) | 通知渠道体系：三件套模型、新增渠道步骤、两层可达性过滤、短信通知模板配置与排错 |

## 部署与运维（ops/）

| 文档                                                     | 内容                                              |
|--------------------------------------------------------|-------------------------------------------------|
| [deployment.md](ops/deployment.md)                     | 配置项详解、Docker 部署、备份恢复、升级回滚、监控告警                  |
| [runbook.md](ops/runbook.md)                           | 常见故障 → 处置步骤（≥10 个场景）                            |
| [performance-baseline.md](ops/performance-baseline.md) | 性能基线测定流程（T3.1）：silk 剖析接入 + k6 六接口压测 + 登记口径与回归判定 |
| [backup-drill-2027-03.md](ops/backup-drill-2027-03.md) | 备份演练（异地副本/媒体目录/RPO 6h 收口）：`utils/backup_drill.sh` 一键闭环与结果 |
| [backup-drill-2026-Q4.md](ops/backup-drill-2026-Q4.md) | 季度演练（Q4，提前执行）：67 表逐表 0 不一致、RTO 0.28s，一并验收备份失败告警（S2） |
| [backup-drill-2026-09.md](ops/backup-drill-2026-09.md) | 首次备份演练记录（RTO 0.88s、52 表一致）与当时遗留缺口                 |

## 架构决策记录（adr/）

| ADR                                          | 主题                                 |
|----------------------------------------------|------------------------------------|
| [ADR-001](adr/ADR-001-csrf-jwt-only.md)      | CSRF 中间件不启用（JWT-only 架构）           |
| [ADR-002](adr/ADR-002-demo-app.md)           | demo app 去留：保留但默认关闭                |
| [ADR-003](adr/ADR-003-websocket-protocol.md) | WebSocket 协议保持自定义格式并补类型约束          |
| [ADR-004](adr/ADR-004-django-60-upgrade.md)  | Django 升级：停留 5.2 LTS（6.2 升级已取消：celery 未支持） |
| [ADR-005](adr/ADR-005-redis-split.md)        | Redis 拆分（缓存/队列/会话分实例）              |
| [ADR-006](adr/ADR-006-asgi-db-connection-pool.md) | ASGI 形态启用 Django server 端 DB 连接池      |
| [ADR-007](adr/ADR-007-multipart-form-data-v1-protocol.md) | FormData 上传协议 v1（点分键序列化契约）       |
| [ADR-008](adr/ADR-008-pat-auth.md)           | 个人访问令牌（PAT）：scope + 精确审计           |
| [ADR-009](adr/ADR-009-data-mask-exemption.md) | 数据脱敏豁免清单机制                          |
| [ADR-010](adr/ADR-010-crypto-es.md)          | crypto-js 弃用处置：替换为 crypto-es        |
| [ADR-011](adr/ADR-011-aes-protocol-v2.md)    | 凭证加密协议升级 v2（WebCrypto PBKDF2+AES-GCM 双格式过渡） |
| [ADR-012](adr/ADR-012-approval-flow-engine.md) | 审批流引擎（模板/实例/任务/加签/催办，含触发器与数据权限） |
| [ADR-013](adr/ADR-013-office-online-preview.md) | Office 在线预览选型：LibreOffice headless 转 PDF（重队列 + 缓存回收） |
| [ADR-014](adr/ADR-014-typescript-7-evaluation.md) | TypeScript 7 升级评估：暂不升级（vue-tsc 与 TS 7 不兼容，附实测数据） |
| [ADR-015](adr/ADR-015-reference-project-adoption.md) | 参考项目借鉴决策：vue-pure-admin 点状移植边界 / jumpserver 机制借鉴矩阵 / 审批流可视化不引入 |

## 契约与规范（schema/ + 根级）

| 文档                                                                     | 内容                                  |
|------------------------------------------------------------------------|-------------------------------------|
| [schema/search-columns.schema.json](schema/search-columns.schema.json) | search-columns 响应契约                 |
| [schema/search-fields.schema.json](schema/search-fields.schema.json)   | search-fields 响应契约                  |
| [exception-handling.md](exception-handling.md)                         | 错误脱敏原则 + 错误码登记表（新增错误码必须先登记）         |
| [security-review.md](security-review.md)                               | 安全自查归档（Flower/XFrame/Referer/上传校验/JWT 审计等） |
| [cache-keys-audit.md](cache-keys-audit.md)                             | 缓存键审计（N5）：`scripts/check_cache_keys.py --strict` 冲突清零记录 |

## 维护约定

- 新增文档先在本索引登记；架构类文档入 `architecture/`，决策类入 `adr/`（新建 ADR 编号顺延），部署运维入 `ops/`；
- ADR 状态变更需同步更新本索引表格；
- API 文档随版本固化（T6.3）：每次 release 自动附带静态 `openapi.json`（drf-spectacular 导出，见 `build-image.yml`），并可在部署环境访问
  `/api-docs/` 交互查阅；
- 根目录 `XADMIN_FRAMEWORK_ANALYSIS.md` 为历史深度分析，内容已由 [architecture/overview.md](architecture/overview.md)
  导航收录，以代码与 overview 为准。
