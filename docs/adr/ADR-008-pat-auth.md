# ADR-008：个人访问令牌（PAT）鉴权与凭证模型

- 状态：已接受
- 日期：2026-09-09
- 关联：N3 第二期 F3（`docs/superpowers/plans/2026-09-09-n3-business-features-phase2.md`）

## 背景

系统此前仅有 JWT 用户登录一种鉴权方式。第三方系统 / 脚本要调用 OpenAPI（T6.3 已静态导出）
只能共享真人账号密码，存在密码轮换连坐、无法单独吊销、无法审计归属等问题。

## 决策

1. **凭证形态**：`Authorization: Pat <token>`，明文格式 `pat_<token_urlsafe(32)>`，
   明文仅在创建响应返回一次；库内只存 sha256 哈希（unique）+ 前 12 位前缀用于辨识。
2. **认证位置**：新增 `PersonalAccessTokenAuthentication` 挂在
   `DEFAULT_AUTHENTICATION_CLASSES` 末位，与 JWT 认证链并列：
   - 无 `Pat` 头 → 静默返回 None，JWT/Session 照常工作，零影响；
   - 有 `Pat` 头但凭证无效/过期/用户停用 → 显式 401（fail-closed，不给降级猜测空间）。
3. **权限模型**：PAT 完全继承所属用户（`creator`）既有权限——三层权限
   （菜单/API + 数据 + 字段）、数据权限过滤、操作日志审计全链路按该用户身份生效。
   **第一期不做菜单级 / 只读 scope**：scope 需结合真实集成场景再设计，过早引入
   会造成"看似最小权限实则配错"的运维负担。
4. **与会话体系的关系**：PAT 是机器凭证，不建 `UserSession`、不写 `sid` claim、
   不受 `UserTokenRevokedCache`/`SessionTokenRevokedCache` 影响（两者按 JWT 的
   iat/sid 判定）。吊销 = 置 `is_active=False`；认证每次查库，吊销即时生效
   （PAT 请求频率低，无缓存可接受；高频场景再引入 Redis 缓存 + 吊销失效）。
5. **取值域**：凭证严格个人所有（`get_queryset` 收口 `creator=request.user`，
   含超管），无管理员代管；路由挂 `PERMISSION_WHITE_URL`（个人安全操作口径，
   同 MFA），仍需登录认证。
6. **限流与痕迹**：复用全局限流；`last_used_time` 经 Redis 60s 节流回写；
   操作日志照常记录（creator 为凭证所属用户），可完整溯源。
7. **清理口径**：`auto_clean_pat_job`（每日）物理删除「过期超 30 天」
   （`expired_at < now-30d`）**或**「停用且 30 天未更新」（`is_active=False` 且
   `updated_time < now-30d`）的凭证——两类凭证分别保留 30 天审计痕迹，
   比早期计划描述（仅过期且停用）覆盖更完整。

## 后果

- 正面：第三方集成不再共享真人密码；单个凭证可独立吊销/过期；归属与审计清晰。
- 负面/边界：PAT 权限 = 用户权限，若给集成账号授予过多菜单权限，PAT 亦随之扩大
  （建议为集成场景建专用最小权限账号）；`changes` diff 白名单等既有审计口径不变。
- 演进：后续可加 `scopes`（菜单子集 / 只读标记）与创建时 IP 允许清单，均在
  `PersonalAccessToken` 上扩展字段即可，无破坏性变更。
