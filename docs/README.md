# xadmin-server 文档中心

> 目标：**新人按本目录完成环境搭建与第一个功能开发**；开发中遇到"不报错但没效果"类问题先查
> [dev-pitfalls.md](dev-pitfalls.md)。外站 https://docs.dvcloud.xin/ 为补充资料，以本目录为准。
>
> 文档有 CI 守护（索引覆盖 / 事实一致性 / 教程镜像 / 手册路径 / 站点导航，见 §五），
> **新增文档必须登记本索引**。

## 一、二次开发：从这里开始（必读 9 篇）

| # | 文档 | 一句话 |
|---|------|--------|
| 1 | [guide/first-module-30min.md](guide/first-module-30min.md) | **30 分钟开发第一个业务模块**：建 app → `generate_crud` → 菜单授权 → `doctor` 自检（生成器主线） |
| 2 | [architecture/component-handbook.md](architecture/component-handbook.md) | **组件手册**：全景图 + 工程化设施 + 扩展点速查；后端 14 组见 [handbook-backend.md](architecture/handbook-backend.md)、前端 10 组见 [handbook-frontend.md](architecture/handbook-frontend.md)（含权威源路径） |
| 3 | [guide/recipes.md](guide/recipes.md) | **扩展流程处方集**：加字段 / 加按钮 / 自定义渲染器 / 定时任务 / AI 动作等 23 条任务步骤 |
| 4 | [architecture/overview.md](architecture/overview.md) | 架构总览：分层、元数据驱动、子体系速览、前后端协作时序 |
| 5 | [architecture/framework-cookbook.md](architecture/framework-cookbook.md) | 框架能力速查：ViewSet 选型 / Action↔BaseApi / 覆写红线 / 前端契约 |
| 6 | [architecture/方案选型与对比.md](architecture/方案选型与对比.md) | 选型决策依据：列表页三范式 / 组件选型速查 / 与同类方案对比 |
| 7 | [框架开发遵循准则.md](框架开发遵循准则.md) | 服务端 + 前端统一约定与检查清单（含数据字典 / i18n / 按钮交互） |
| 8 | [dev-pitfalls.md](dev-pitfalls.md) | 新手陷阱清单 24 条（**静默失败类问题先查这里**） |
| 9 | [guide/secondary-dev-quickref.md](guide/secondary-dev-quickref.md) | **二开速查**：门禁清单 / 模块注册 / 契约同步 / 审批接入 / 生成器使用——场景到权威入口的索引页 |

**三条先记住的心智模型**：① 列表页 = 后端元数据驱动（`search-columns` / `search-fields`）；
② 权限码 = `动作:组件名`（如 `list:SystemUser`，必须入库授权）；③ 契约变更先改 `docs/schema/` 再同步前端。

## 二、按需查阅（开发）

| 主题 | 文档 |
|------|------|
| 元数据协议 | [architecture/metadata-protocol.md](architecture/metadata-protocol.md)：字段语义 / `input_type` 推断链 / 四通道注册表 / 失败可见性 |
| 字段类型矩阵 | [architecture/field-types.md](architecture/field-types.md)：Django 模型字段 → DRF → `input_type` 全量映射、逐类型四通道归宿、校验归属与候选池 |
| 数据集白名单 | [architecture/dataset-field-whitelist.md](architecture/dataset-field-whitelist.md)：模型/字段白名单来源、保存与执行双侧 fail-closed、新模型登记步骤与守护测试 |
| 框架内核发包 | [architecture/kernel-package.md](architecture/kernel-package.md)：`xadmin-common` 工作区布局 / 构建与宿主接线 / **内核 settings 契约表**（宿主必给与缺省键）/ 升级注记 |
| 权限体系 | [architecture/permission.md](architecture/permission.md)（三层 + 应用级授权；配置与排障以本篇为准，原独立教程已清理） |
| 菜单维护口径 | [guide/menu-maintenance.md](guide/menu-maintenance.md)：种子 path↔组件目录基本口径 / URL≠目录 例外清单（对账门禁白名单）/ 新增页面规范 |
| 模块化与裁剪 | [architecture/模块化与功能裁剪.md](architecture/模块化与功能裁剪.md)：三级分层 / 发行预设 / 六层裁剪 / CLI 与管理页 |
| **二开插件开发** | [guide/plugin-development.md](guide/plugin-development.md)：契约注入（`register_contract` / entry points）/ 应用级扩展点 / 模块声明与发行预设 / 验证清单（可运行样本 `examples/plugins/xadmin-demo-plugin`） |
| 认证扩展 | [architecture/auth-flows.md](architecture/auth-flows.md)（登录 / 注册 / 找回密码全链路、签发与处置两大收口、防枚举口径）、[architecture/mfa.md](architecture/mfa.md)（MFA / 412 协议）、[architecture/oauth-login.md](architecture/oauth-login.md)（第三方登录 / IM 扫码） |
| 目录同步 | [architecture/ldap-readiness.md](architecture/ldap-readiness.md)、[architecture/scim.md](architecture/scim.md)、[architecture/scim-idp-readiness.md](architecture/scim-idp-readiness.md) |
| 通知渠道 | [architecture/notification-channels.md](architecture/notification-channels.md)（新增渠道 = 新增一个文件） |
| **开放平台控制台** | [architecture/open-platform-console.md](architecture/open-platform-console.md)：SCIM / 第三方登录 typed 配置、令牌轮换与自检、用户侧已授权应用撤销、权限点与安全边界 |
| 缓存 / 索引 | [architecture/cache.md](architecture/cache.md)（含键规范与失效矩阵；原审计/评审记录已清理） |
| 错误码 | [exception-handling.md](exception-handling.md)（新增错误码必须先登记） |
| 契约 Schema | [schema/README.md](schema/README.md)（search-columns / search-fields 真源） |
| 开放平台 | [open-platform/README.md](open-platform/README.md)（接入指南）、[open-platform/events.md](open-platform/events.md)（Webhook 事件契约） |
| 前端开发（xadmin-client） | 页面 / E2E / 契约入口见 [xadmin-client/docs/README.md](https://github.com/nineaiyu/xadmin-client/blob/dev/docs/README.md)；页面写法见 [recipes.md](guide/recipes.md) R9–R14 / R22、[handbook-frontend.md](architecture/handbook-frontend.md) |
| **故障排查 FAQ** | [guide/troubleshooting.md](guide/troubleshooting.md)：登录 / 容器 / 权限 / 模块裁剪 / CSP / 时区等高频现象 → 原因与处置（运维故障清册见 [ops/runbook.md](ops/runbook.md)，静默失效坑见 [dev-pitfalls.md](dev-pitfalls.md)） |
| **升级常见问题** | [guide/upgrade-faq.md](guide/upgrade-faq.md)：升级前后最常问 / 易错（判定与流程权威口径见 [ops/upgrade-stock.md](ops/upgrade-stock.md)、[ops/deployment-upgrade.md](ops/deployment-upgrade.md)） |

## 三、部署与运维（ops/）

| 文档 | 内容 |
|------|------|
| [ops/deployment.md](ops/deployment.md) | **部署与运维手册（概览）**：本地开发 + 常见问题排查；子页 [deployment-docker.md](ops/deployment-docker.md)（容器 / Celery 队列 / 备份恢复与容量 / 可观测 / 国产化）、[deployment-upgrade.md](ops/deployment-upgrade.md)（升级回滚）、[deployment-csp.md](ops/deployment-csp.md)（安全响应头 CSP）、[config-reference.md](ops/config-reference.md)（配置速查表） |
| [ops/upgrade-stock.md](ops/upgrade-stock.md) | 存量库升级通道：升级前体检（`upgrade_check`）/ 支持矩阵（可原地升级 vs 清库重建）/ 两条流程 |
| [ops/upgrade-4x-to-5x.md](ops/upgrade-4x-to-5x.md) | **4.x → 5.x 存量升级操作手册**：清库重建 + `legacy_export` / `legacy_import` 回灌（判定 / 逐步命令 / 验收清单 / 回滚 / 实测记录） |
| [ops/upgrade-5.0.md](ops/upgrade-5.0.md) | **4.x → 5.0 升级指南**：迁移链合并说明 / 账本认领（`upgrade_ledger --apply`）/ 三步升级（安装器 · compose · 源码）/ 验证与回滚 / FAQ |
| [ops/runbook.md](ops/runbook.md) | 故障处置（常见故障 → 处置步骤） |
| [ops/captcha-risk-runbook.md](ops/captcha-risk-runbook.md) | **验证码风险分级运行手册**：`off/shadow/enforce` 切换顺序与硬前置、观测指标与告警、误拦/票据失效/音频/PoW/Redis/Celery 故障处置、回退与升级注意 |
| [ops/scale-out.md](ops/scale-out.md) | 横向扩展 runbook（单机多副本：迁移一次性 / beat 单例 / nginx 多后端轮询 + 验证与回退） |
| [ops/blue-green.md](ops/blue-green.md) | 零停机发布 runbook（叠加滚动：迁移兼容性 / 加新摘旧 / 回滚与边界） |
| [ops/pitr.md](ops/pitr.md) | WAL 归档与时间点恢复（PITR） |
| [ops/log-archive.md](ops/log-archive.md) | 审计日志冷归档（归档水位驱动清理 / 离线恢复查询 / 校验与演练） |
| [ops/storage.md](ops/storage.md) | 文件存储后端：声明式可插拔（local / S3）+ 搬迁校验命令 + health 探针 + 排障 |
| [ops/kernel-release.md](ops/kernel-release.md) | 内核分发包（xadmin-common）发布渠道与版本策略：私有源接入 / 发版流程 / 宿主升级与回滚 |
| [ops/workspace-health.md](ops/workspace-health.md) | 工作区级跨仓一致性健康报告：五个同源面（CSP / 权限种子 / 契约 schema / 词条 / 版本矩阵）+ 门禁聚合的只读校验、退出码与周报 CI |
| [ops/observability.md](ops/observability.md) | 可观测性与 SLO（指标 / 告警分级 / 追踪评估；历史演练记录已清理） |
| [ops/monitoring-stack.md](ops/monitoring-stack.md) | 监控参考栈（Prometheus + Grafana + blackbox + 告警桥接 + systemd 单元） |
| [ops/release-checklist.md](ops/release-checklist.md) | 发布窗口 checklist（基线门禁 + 执行记录） |
| [ops/performance-baseline.md](ops/performance-baseline.md) | 性能基线测定流程（silk + k6） |

## 四、维护与决策（长期演进）

| 文档 | 内容 |
|------|------|
| [adr/README.md](adr/README.md) | **架构决策记录索引（92 篇；ADR-050~055 为废弃草案号，不回收）**——"当时为什么这样选"；新增决策按编号顺延并登记（篇数由 `check_doc_facts.py` 按 `docs/adr/ADR-*.md` 计数守护） |
| [plans/README.md](plans/README.md) | 规划与治理：活跃台账（**工作区级**开发任务总清单 / 长期任务方案 / 触发制任务清单 / 保留任务决策 / 5.0.1 发布清单 + 四域与种子映射 / 验证码实施细则 / 候选提案）；**已完成的一次性方案与审查快照已于 2026-10-27 清理**（去向见 [plans/README.md](plans/README.md) 末尾） |
| [ci-gates.md](ci-gates.md) | **CI 门禁统一清单**：全部流水线门禁与守护测试的登记处（守护语义 / 所在 workflow / 本地复跑；新增门禁必须登记） |
| [metrics.md](metrics.md) | 基线指标看板（测试 / 体积 / 性能 KPI 基线概览）；历史履历已清理 |
| [asvs-mapping.md](asvs-mapping.md) | **OWASP ASVS L1/L2 对照表**：安全现状映射 + 差距项归属（自动化可下沉 / 接受项 / 触发条件），用于回答客户安全问卷 |

## 五、目录结构与维护约定

```
docs/
├── README.md             本索引（二开必读 → 按需 → 运维 → 维护）
├── guide/                快速上手（30 分钟教程 + 扩展处方集）
├── architecture/         架构与组件（现状文档：组件手册 component-handbook + handbook-backend / handbook-frontend / 协议 / 权限 / 模块化…）
├── adr/                  架构决策记录（历史决策，含索引 README）
├── ops/                  部署与运维（deployment 概览 + deployment-docker / deployment-upgrade / deployment-csp / config-reference / observability / runbook / 演练记录（历史记录已清理））
├── plans/                规划与治理（活跃 17 篇：工作区级台账 + 长期任务 / 触发制清单 / 四域与种子映射 / 验证码实施细则 / 发布清单；历史一次性方案已清理，去向见 plans/README.md）
├── open-platform/        开放平台接入（指南 + 事件契约）
├── schema/               前后端契约 JSON Schema
├── imgs/                 文档配图
├── dev-pitfalls.md       新手陷阱清单
├── 框架开发遵循准则.md     开发统一约定与检查清单
├── exception-handling.md 错误码规范
├── ci-gates.md           CI 门禁统一清单（新增门禁必须登记）
├── metrics.md            基线指标看板（历史履历已清理）
└── （回收站）已完成的一次性方案/台账移入工作区根 `_delete/`（不在本目录）
```

**维护约定**：

1. 新增文档必须登记本索引（CI 守护 `scripts/check_doc_index.py`）；
2. 文档中的"当前事实"（版本 / 端口 / 覆盖率等）由 `scripts/check_doc_facts.py` 守护，
   跨仓事实覆盖 xadmin-docs 与 xadmin-client 文档；
3. 教程中的命令 / 参数 / demo 路径由 `scripts/check_tutorial_mirror.py` 守护；
   组件手册与活跃开发文档的引用路径由 `scripts/check_doc_paths.py` 守护；
   对外站点导航由 `scripts/check_docs_site_nav.py` 守护；
   站点内链接（**含锚点级死链**，构建产物核对）与跨仓路径引用由
   `scripts/check_docs_site_links.py` 守护（`docs:build` 只拦页级死链）；
4. **已完成且不再被引用的一次性方案 / 台账**移入工作区回收站 `_delete/`（[plans/README.md](plans/README.md) 登记去向），
   活跃区只保留"在维护"或"仍被代码/文档引用"的文档；ADR 状态变更同步更新 [adr/README.md](adr/README.md)；
5. API 文档随版本固化：每次 release 附带静态 `openapi.json`，部署环境可访问 `/api-docs/` 交互查阅。
