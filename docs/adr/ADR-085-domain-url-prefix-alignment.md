# ADR-085：identity / file / audit / task 四域 URL 前缀与 app 对齐

- 日期：2026-10-09
- 状态：**已交付**（server：pytest 全量 exit 0 + ruff / 行数 / 跨 app / 缓存键 / makemigrations；client：typecheck / vitest / eslint / prettier / stylelint / i18n / 契约 / bundle / 行数；E2E 双浏览器回归）
- 背景：ADR-057 拆分 approval / ai / dataset 三域时以「权限点路径不变」为硬约束，路由经 `path("", include(...))` 同前缀挂回 `/api/system/`；ADR-059 按用户决策把三域改为独立前缀（`/api/approval/`、`/api/ai/`、`/api/dataset/`）。此后 identity / file / audit / task 四域自 system 切分时（清库重建口径）沿用了 ADR-057 D1.2 的零变化口径，URL 仍走 `/api/system/...`，形成「部分域独立前缀、部分域同前缀」两套口径并存的局面。用户决策四域一并拆分，与 app 边界对齐。

## 决策

### D1 路由与命名空间

- `server/urls.py` 新增四个独立前缀挂载：`^api/identity/`、`^api/file/`、`^api/audit/`、`^api/task/`（namespace 与 app 同名）；`system/urls.py` 摘除四行空前缀 include，只保留 system 内核域（菜单/权限/字典/字段/配置/凭据/监控/标签/代码生成/全局搜索）；
- 四 app `urls.py` 显式 `app_name`；视图名由 `system:user-list` 迁为 `identity:user-list` 等（全仓无 `reverse("system:user...")` 消费点；`captcha/helpers.py` 的 `system:captcha-*` 属 system 内核域不受影响）；
- **注册串口径**：
  - identity / audit 保持语义原名（`/api/identity/user`、`/api/audit/logs/operation`）；
  - task 拍平原 `tasks/` 注册层（`/api/system/tasks/periodic` → `/api/task/periodic`，消除域前缀重复）；
  - file 保留注册串 `file`（`/api/file/file`，与 `/api/approval/approvals` 同类轻微冗余）。**不用空前缀注册**：DRF 对空前缀路由会削掉 pattern 前导斜杠（`SimpleRouter.get_urls` 的 `if not prefix` 分支），detail 路由会退化成 `/api/file<pk>` 形态，与段边界语义冲突。

### D2 联动平移（server 380+ 处 / client 395+ 处）

- **权限点种子**：`loadjson/menu.json` 内四域权限点 path 全量平移 232 条（identity 119 / task 69 / audit 26 / file 18），留 system 内核域 127 条不动；
- **权限同步内核**：`system/services/permission_sync/constants.py`（PARENT_MENU_MAP / AUDIT_KNOWN_DUPLICATES / SHARED_METHOD_PATHS）；
- **访问门配置**：`server/settings/custom.py` 的 `PERMISSION_WHITE_URL`（登录/登出/userinfo/PAT/Passkey/模拟退出/OAuth/开放平台换发等）、`ROUTE_IGNORE_URL`、`PERMISSION_SHOW_PREFIX`（新增四前缀——缺失会让路由枚举跳过四域，权限巡检/AI 审计全失明）；
- **模块裁剪**：`common/core/modules/catalog.py` 的 ModuleSpec `routes` 正则（ops/datamask/webhook/open_platform）；
- **AI 工具层**：`ai_tool_audit` 豁免前缀、`ai_tool_triage` 资源域登记（四域整域一条）、`ai_registry_org/infra/ops`、`ai_api_registry` 的动作声明路径；
- **凭据与令牌**：`identity/utils/pat_scope.py` 与 `identity/serializers/token.py` 的锚定正则口径（示例与注释）、`identity/views/admin/user.py::INVITE_PERMISSION_PATH`；
- **平台消费面**：全局搜索 provider `list_url`、标签中心 `TAGGABLE_MODELS.visit`、`server/settings/base.py` 的 Silky 忽略前缀、`server/conf/config.py` 的 API 模块映射（refresh）、路由审计与监控（`ops/monitoring/alerts.yml` 的 view 标签、`loadtest/k6` 路径）；
- **客户端**：API 模块文件自 `src/api/system/` 迁至 `src/api/{identity,file,audit,task}/`（15 文件，引用站点同批改写），请求路径 375 处平移，i18n 文案与 E2E spec / README 同步；
- **文档**：`docs/open-platform`、`docs/architecture/*`、`docs/guide/*`、`docs/ops/*` 的现行示例路径同步；历史 ADR 保持原貌。

### D3 存量库平移

新增 `manage.py migrate_api_prefixes`（缺省 dry-run，`--apply` 落库，幂等）覆盖四类持久化引用：

- `Menu.path`（权限点）；
- `PersonalAccessToken` / `ApiApplication` / `OAuthRefreshToken` 的 `scopes`（锚定正则文本）；
- `ApprovalRule.path_patterns`（审批规则路径清单）；
- `SysConfig.APPROVAL_REQUIRED_PATHS`（操作审批必需路径）。

部署顺序：重启后端容器（新路由装配）→ `migrate_api_prefixes --apply` → 前端重新构建部署。角色-菜单授权按 `Menu.pk` 关联，path 平移不影响授权关系；`django_content_type` / `auth_permission` 不涉及。

### D4 认证面提示（本次与 ADR-059 的最大差异）

identity 域包含登录/注册/重置/会话/OAuth 端点，前缀变更（`/api/identity/login/basic` 等）需注意：

- **第三方 OAuth 应用注册的回调地址**需同步改（`/api/identity/auth/oauth/{provider}/callback`）——存量第三方集成需人工更新配置；
- **存量 PAT 令牌**的 scope 锚定正则含旧路径，经 `migrate_api_prefixes` 改写（未改写的令牌在实际调用新路径时 scope 不再命中，表现为 403）；
- 邀请激活链接指向前端页面（`/invite/accept?token=`），不承载 API 前缀，无需处理。

## 后果

- 全部业务 app（approval / ai / dataset / identity / file / audit / task / chat / settings / mfa / notifications / scim）URL 前缀与 app 边界对齐，二开者按目录/前缀即可定位代码；`/api/system/` 收敛为平台内核域；
- ADR-057 D1.2 的「路径不变」约束对四域解除，此后四域路由演进不再牵动 system 权限点面；
- 权限点扫描（`scan_gaps`）、模块裁剪（含 WS 通道）、AI 工具面巡检在四域上恢复全覆盖，且为后续新域拆分建立了「独立前缀 + 迁移命令」的可复用范式。
