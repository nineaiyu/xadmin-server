# SCIM 2.0 用户目录同步（对接与运维）

> 实现：`system/scim/`（RFC 7643/7644 核心子集）· 鉴权 `system/scim/auth.py` · 路由 `/api/scim/v2/`
> 相关：安全登记见 [../security-review.md](../security-review.md)「四期登记 S1」；
> 身份联邦（登录侧）见 `system/views/auth/`（OAuth2/OIDC 绑定）与 [ADR-011](../adr/ADR-011-aes-protocol-v2.md) 邻域文档

## 1. 定位与边界

SCIM 只负责**身份生命周期**（开通 / 改属性 / 改组 / 停用），**不负责登录**：

- 用户创建后若 IdP 未下发 `password`，账号置**不可用密码**，只能经既有 JWT 登录或
  OAuth2/OIDC 联邦登录（因此要求 IdP 侧的 `userName` 与联邦登录的用户标识一致）；
- 停用（`active=false`）或删除（`DELETE`）会**立即踢掉该用户全部会话**
  （令牌失效时间戳 + refresh 拉黑 + WebSocket 踢线 + 会话置离线），与「在线用户强制下线」同一链路。

实现范围（未实现项在 `ServiceProviderConfig` 中显式声明 `false`，不静默忽略）：

| 能力 | 支持 | 说明 |
|---|---|---|
| Users CRUD / PATCH | ✅ | 含 `active`、`displayName`、`emails`、`phoneNumbers`、`userName` |
| Groups CRUD / PATCH | ✅ | Group ↔ 角色（`UserRole`），成员增删同步 `users.roles` |
| `filter` | ⚠️ 子集 | 仅 `attr eq "value"`：Users 支持 `userName`/`id`；Groups 支持 `displayName`/`externalId`/`id` |
| 分页 | ✅ | `startIndex`（1 起）/`count`（默认 100，上限 500） |
| bulk / sort / etag / changePassword | ❌ | 声明 false；IdP 侧请关闭对应开关 |

## 2. 启用步骤

1. 生成一个高强度随机令牌（建议 ≥32 字节，如 `openssl rand -base64 48`）；
2. 在**设置中心 → 系统配置**（或直接写 `systemconfig` 表）设置：

| 配置项 | 建议值 | 说明 |
|---|---|---|
| `SCIM_ENABLED` | `true` | 总开关；`false`（默认）时端点一律 403 |
| `SCIM_TOKEN` | 随机串 | 独立 Bearer Token；空 = 任何请求 401；`access=false` 不对外回传 |
| `SCIM_RATE_LIMIT` | `600/min`（默认） | 凭证级限流；空或 `0` = 不限 |
| `SCIM_DEFAULT_ROLE_CODE` | 如 `common` 或空 | 新建用户的默认角色；空 = 不分配（由 IdP 分组另行下发） |

3. 用 curl 自检（`-k` 仅自签证书时使用）：

```shell
curl -sS -H "Authorization: Bearer $SCIM_TOKEN" https://<host>/api/scim/v2/ServiceProviderConfig | jq .
curl -sS -H "Authorization: Bearer $SCIM_TOKEN" 'https://<host>/api/scim/v2/Users?filter=userName%20eq%20%22admin%22' | jq '.totalResults'
```

4. 生产要求：走 HTTPS；令牌只放在 IdP 的密钥库与部署密钥管理中，**不要**提交到仓库/日志。

## 3. 字段映射

### 3.1 User

| SCIM 属性 | xadmin 字段 | 语义 |
|---|---|---|
| `id` | `users.pk`（整数，字符串化） | 只读 |
| `userName` | `username` | 必填；唯一性按 `all_objects` 校验（含回收站占用），重复返回 409 `uniqueness`；缺省时由 `emails[0]` 的本地部分派生 |
| `displayName` / `name.formatted` | `nickname` | 展示名 |
| `emails[0].value` | `email` | |
| `phoneNumbers[0].value` | `phone` | |
| `active` | `is_active` | `false` → 停用 + 踢会话 |
| `password` | 写入时使用 | 仅用于创建/改密；响应永不回传 |
| `groups` | 用户角色 code 列表 | 只读投影 |
| `externalId` | — | 不落库（IdP 侧标识，仅用于审计上下文） |

### 3.2 Group

| SCIM 属性 | xadmin 字段 | 语义 |
|---|---|---|
| `id` | `userrole.pk`（UUID） | 只读 |
| `displayName` | `name` | 必填 |
| `externalId` | `code` | 缺省时由 `displayName` 规范化生成（非字母数字转 `_`、小写） |
| `members[].value` | `users.pk` 或 `username` | 写入支持两种取值 |
| `members[].display` / `$ref` | — | 只读 |

组操作语义：`add` = 追加成员；`replace`（含 PUT）= 整体替换成员列表；
`remove members[value eq "<pk>"]` = 移除指定成员；删除组 = 删除该角色
（角色的菜单/字段权限关系随之删除，不影响用户本身）。

## 4. IdP 配置示例

### 4.1 Okta（SCIM 2.0 Provisioning）

1. Applications → 选择应用 → **Provisioning** → Integration type = **SCIM 2.0**；
2. **Base URL**：`https://<host>/api/scim/v2`；**API Token**：`SCIM_TOKEN`；
3. Test Connection 应通过（Okta 会读 `ServiceProviderConfig`/`Schemas`/`ResourceTypes`）；
4. Provisioning → To App：启用 **Create Users / Update User Attributes / Deactivate Users**；
5. 若要同步组：**Push Groups** → 选择需要下发的组（对应角色）。

### 4.2 Microsoft Entra ID（原 Azure AD）

1. Enterprise applications → 应用 → **Provisioning** → Provisioning Mode = **Automatic**；
2. **Tenant URL**：`https://<host>/api/scim/v2`；**Secret Token**：`SCIM_TOKEN` → Test Connection；
3. Mappings：Users 至少映射 `userName` → `userPrincipalName`（或 `mailNickname`，须与联邦登录标识一致）、
   `active`、`displayName`、`emails`、`phoneNumbers`；Groups 映射 `displayName` + `members`；
4. Settings：Provisioning Status = On，按需开启 "Send an email notification when provisioning fails"。

### 4.3 其他

任何符合 SCIM 2.0 的客户端/中间件（飞书、钉钉、JumpCloud、一身份 IAM 等）都可接入：
Base URL + Bearer Token 即可；若 IdP 只支持「SCIM 1.1/自定义协议」，需在其侧做字段转换。

## 5. 观测与排错

| 现象 | 排查 |
|---|---|
| 401 | 令牌缺失/错误，或 `SCIM_TOKEN` 未配置（此时端点恒 401） |
| 403 | `SCIM_ENABLED=false`；或使用业务身份（JWT）访问 SCIM 端点 |
| 409 `uniqueness` | `userName` 或组 `externalId/code` 已存在（含停用/回收站记录） |
| 400 `invalidFilter` | IdP 使用了不支持的过滤表达式（如 `co`/`sw`/`and`）或属性 |
| 400 `invalidValue` | 缺 `userName`/`displayName`，或组成员不存在 |
| 429 | 触发 `SCIM_RATE_LIMIT`，调大或降低同步频率 |
| 同步成功但用户无法登录 | 该用户未走联邦登录：确认 `userName` 与 IdP 登录标识一致，或为其设置密码 |

审计：所有写操作落 `OperationLog`（`auth_type=scim`，`module=SCIM:*`，`object_pk` = 用户/角色 pk，
`changes` 记字段级差异，**不记请求体**）。排错时按 `module=SCIM:*` + 时间窗过滤操作日志即可。

## 6. 已知限制

- 不支持 `externalId` 持久化（用户侧）：若 IdP 以 `externalId` 作为唯一匹配键，请改用 `userName` 匹配；
- 不支持 bulk 操作：大规模首次同步（数万用户）请分批或降低并发；
- 组删除不级联回收用户权限快照：用户角色变更会走既有权限缓存失效链路，但**已下发的 access token
  在有效期内仍是旧权限快照**（与后台改角色一致，属既有设计）；
- 未实现 `/Me`、`/Bulk`、`/ServiceProviderConfig` 之外的协议端点。
