# ADR-039：开放平台二期（应用级权限体系 / OAuth 授权码 / 用量配额 / Webhook 契约治理）

- 状态：已接受（2026-09-15 设计落地）
- 日期：2026-09-15
- 关联：ADR-030（开放平台雏形）、ADR-035（API 契约治理）、ADR-022（webhook 签名）、
  下一年度规划 2027.10–2028.09 §四.B / W7–W8；
  `system/models/token.py`、`system/views/open.py`、`common/core/permission.py`、
  `common/core/filter.py`、`common/core/data_scope.py`、`system/utils/pat_scope.py`、
  `system/utils/webhook.py`

## 背景

G11 雏形（ADR-030）已交付：应用 = 发卡机，凭证以 owner 身份走 PAT 认证链；权限面 =
owner 菜单权限 ∩ 接口 scope（路径正则）；限流 = 认证处按应用每分钟计数；回调 = 登记 +
探测（不进投递链）。

二期启动门控已命中（≥2 个真实外部接入方），需补齐四项能力：

1. 接入方权限面过宽（= owner 全量权限），无资源级收敛；
2. 无法「代表用户」访问（只有 client-credentials）；
3. 无用量可见性与配额告警；
4. Webhook 事件无版本、无字段契约、无订阅方文档。

## 决策

### B1 应用级权限体系（模型 × 动作 × 字段 × 行，四级）

#### 1. 授权主体：`ApiApplicationGrant`（应用资源授权规则）

| 字段 | 说明 |
|---|---|
| `application` | FK → ApiApplication（CASCADE），related_name=`grants` |
| `model` | 目标模型标签（`system.dataset`）或 `*`（全部模型） |
| `actions` | JSON list，权限点动作段（`list` / `retrieve` / `create` / …）或 `["*"]` |
| `fields` | JSON list，允许的字段名（空 = 全部字段） |
| `row_filter` | JSON list，行级规则（`DataPermission.rules` 同格式，空 = 不限） |
| `is_active` / `description` | 启用开关与备注 |

#### 2. 生效语义（只收敛不提权，fail-closed）

- **兼容模式**：应用不存在任何 `is_active=True` 的 grant → 维持一期行为
  （owner 权限 + scopes），存量接入方零影响；
- **白名单模式**：应用存在 ≥1 条 grant 时启用四级收敛：
  1. **模型级**：请求目标模型（`view.queryset.model`；裸 APIView 回退菜单绑定模型）
     必须被某条 grant 覆盖（`model` 精确或 `*`），否则 403；
  2. **动作级**：请求命中菜单权限点的动作段（`Menu.name` 的 `action:Resource` 段）
     必须在覆盖 grant 的 `actions` 内（或 `*`）；动作段缺失/未知不放行；
  3. **字段级**：覆盖 grant 的 `fields` 非空时收敛字段（输出裁剪 + 输入拒绝未授权字段）；
  4. **行级**：覆盖 grant 的 `row_filter` 非空时编译为 `Q` 叠加在数据权限过滤之后
     （AND 语义）。
- **不越权红线**：四级之上仍走原有菜单权限、字段权限、数据权限（全部取交集）；
  应用授权只能收紧，任何一层都不能把权限放大回 owner 全量。
- **豁免穿透**：字段收敛与行级收敛对「字段权限豁免」（超管 / 白名单 URL /
  `PERMISSION_FIELD_ENABLED=False`）与「数据权限超管早退」同样生效——授权约束挂在
  凭证（应用）维度，不随用户身份豁免。

#### 3. 管理面

- `GET /api/system/api-applications/{pk}/grants`（读）+ `PUT`（全量替换，事务）；
- `GET /api/system/api-applications/grant-options`（模型 → 动作段/字段目录，
  粒度同 `scope-options`：只返回当前用户可授权面，白名单登记）；
- 新权限点：`listGrants:IntegrationApiApp` / `updateGrants:IntegrationApiApp`；
- 保存校验：`model` 必须命中模型标签节点；`actions` 必须是该模型真实存在的动作段
  （从菜单权限点派生）或 `*`；`fields` 必须在模型字段集内；`row_filter` 走
  `data_scope.validate_rules`（`table` 由 grant.model 注入）。

#### 4. 动作段解析口径

- 动作段来自请求命中的菜单权限点 `name`（`list:SystemUser` → `list`），
  与 `_resolve_menu_pk` 同一套命中口径（`search-columns` 与 list 同权、
  import/export 在未绑定模型时回退 list/create 菜单）；
- 菜单元信息（name / 绑定模型）按 menu_pk 短缓存；
- 未知动作段（无法解析）在白名单模式下拒绝，需显式授 `*`。

### B2 OAuth 授权码（代表用户访问）

#### 1. 端点（`/api/system/open/oauth/*`，匿名可达 + 视图内 fail-closed 校验）

| 端点 | 语义 |
|---|---|
| `GET /oauth/authorize` | 校验 client_id / redirect_uri（∈ callback_urls）/ response_type=code / scope（⊆ 应用 scope 与 grant 面）/ PKCE `code_challenge`(S256)，返回同意页所需信息（应用名、scope 中文清单、当前授权用户） |
| `POST /oauth/approve` | 登录态 + 用户同意 → 生成一次性授权码（缓存 300s，绑定 user/application/redirect_uri/scope/code_challenge），返回 `{code, state}` |
| `POST /oauth/token` | `authorization_code`：校验码（一次性、绑定 client/redirect_uri、PKCE verifier）→ 签发 access（PAT）+ refresh；`refresh_token`：一次性轮换签发新对 |
| `POST /oauth/revoke` | RFC 7009：撤销 refresh 及关联 access（置 is_active=False） |

#### 2. 凭证与权限面

- access = PAT（creator = 授权用户，name = `oauth:<client_id>`，`api_application` = 应用，
  scopes = 应用 scope 展开）→ 走既有 PAT 认证链（审计 `auth_type=pat` + `token_pk` 归集）；
- **权限面 = 应用 scope（接口）× 应用 grant（四级）× 授权用户自身权限**（交集）——
  B1 判定对 OAuth 凭证同样生效（凡带 `api_application` 的凭证都过四级门）；
- refresh = 新模型 `OAuthRefreshToken`（token_hash / application / user / scopes /
  expires_at / is_revoked），轮换即失效旧值。

#### 3. 明示边界

- 不做 implicit / OIDC（id_token）/ 动态客户端注册 / device flow；
- 同意页为前端独立路由（无侧栏），拒绝也记录审计（module=OAuth）；
- 授权码与 refresh 均只存 hash，明文仅响应一次。

### B3 用量与配额（报表 + 软告警）

1. **统计报表**：`GET /api/system/api-applications/{pk}/stats?days=N`（默认 7，上限 30）：
   按天调用量 / 成功 / 失败 / 平均耗时 + Top 路径 + 状态码分布；
   数据源 `OperationLog`（`token_pk ∈ 应用全部凭证 pk`，凭证只失效不删除，口径完整）；
2. **配额**：`ApiApplication.daily_quota`（每日请求上限，0 = 不限）+
   `quota_alert_percent`（默认 80）；
   - 计数：认证处按应用 + 当日窗口缓存计数（日切自然滚动）；
   - **软告警**：当日首次越过阈值 → 站内信（超管 + 应用 owner）+
     webhook 事件 `api_quota.warning`；不阻断请求；
3. 前端：应用页新增「用量」抽屉（图表）与配额字段；新权限点 `stats:IntegrationApiApp`。

### B4 Webhook payload schema 治理

1. **事件契约注册表**：`EVENT_CATALOG` 每事件升级为
   `{key, label, version, fields: {name: {type, description, required}}}`；
2. **payload 外壳**：`{"event", "schema_version": 1, "occurred_at", "data"}`——
   仅新增 `schema_version`，其余字段与语义不变（向后兼容）；
3. **版本策略**：事件 key 不带版本后缀；破坏性变更 = 新增 `xxx.v2` 事件 +
   契约登记 + 旧 key 至少保留一个发布窗口（本 ADR 即规则源）；
4. **契约测试**：契约完备性（每事件必有 version/fields，data 必填项非空）+
   各信号源实际 payload 与契约一致性；
5. **订阅方文档**：`docs/open-platform/events.md` 由脚本从 EVENT_CATALOG 生成
   （CI 校验未漂移）+ `docs/open-platform/README.md` 接入指南
   （认证 / 换发 / OAuth / 限流 / 签名 / 重试 / 示例）。

### 出口：第三方接入示例端到端

- `docs/open-platform/` 接入指南（含示例代码片段）；
- `scripts/open_platform_demo.py`：可直接运行的示例客户端
  （换发 → 调接口 → 订阅 webhook → 验签 → OAuth 授权码全流程）；
- 集成测试 `tests/integration/system/test_open_platform_e2e.py`：
  换发 → 四级授权 → OAuth → 配额告警 → 事件投递端到端。

## 后果

- 应用权限面从「接口级」升级为「模型 × 动作 × 字段 × 行」四级，且**只收敛不提权**；
- 第三方可经 OAuth 授权码代表用户访问，权限面 = 应用 × 用户交集；
- 用量可观测 + 配额软告警（不阻断），接入方可自助控风险；
- Webhook 事件契约化 + 文档自动生成，接入方可自助接入；
- 兼容性：无 grant 应用零行为变化；payload 新增字段向后兼容；存量 scope 语义不变。
