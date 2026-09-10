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

## scope 语义与限流（2026-09-10 增补，N3 第三期 F4）

7. **scope 语义 = 允许的接口路径前缀/正则清单**（`PersonalAccessToken.scopes` JSON
   字符串数组，默认空 = 不限，一期既有 token 向后兼容）。否决了菜单级 scope
   （菜单是前端概念）与方法级只读 scope（机器集成真实诉求是「只调某几个接口」，
   路径前缀即可满足），判定口径与 `SENSITIVE_OPERATION_PATHS` 一致（`re.search`
   子串命中；非法正则跳过并告警，不 500）。
8. **校验时机下沉到认证之后的统一权限层**（`common/core.permission.IsAuthenticated`
   的 `has_permission`，共用 `resolve_pat_scopes` / `check_pat_scope` 纯函数）：
   认证类内只把 scopes 挂 `request.pat_scopes` 不做拒绝——DRF 认证链在首个成功
   认证器处短路，同请求带 JWT + Pat 双 header 时 JWT 胜出会绕过认证类内的校验；
   权限层从原始 Authorization 头补解析凭证（只认启用且未过期的凭证），对所有
   认证方式生效（评审复盘 P1-4 口径）。

   **2026-09-10 审查修正**：原实现把校验放在独立权限类 `PatScopePermission` 并追加
   到 `DEFAULT_PERMISSION_CLASSES`，但 DRF 的 **action 级 `permission_classes`
   会整体替换默认链**（改密/换绑/解绑 MFA/重置他人 MFA 等 6 处正是这种写法），
   独立权限类会被漏掉，scope 形同虚设。故校验内联进 `IsAuthenticated`（默认链与
   显式链都必经），`PatScopePermission` 保留为兼容类（显式清单仍可引用）；
   对 `permission_classes` 覆写为不含 `IsAuthenticated` 的登录后接口
   （如个人配置 `ConfigsViewSet`）需显式挂载该类。
9. **scope 边界**：只做路径维度，不做数据权限收窄（与所属用户一致）与方法级
   只读标记；越界请求 403。scope 限制的是**凭证**而非用户，超管用 PAT 调接口
   同样受限。
10. **凭证级限流**：`common/core.throttle.PatThrottle`（`SimpleRateThrottle`）全局
    挂载，按 `token_hash` 取缓存 key，非 PAT 请求直接放行；速率走系统配置
    `PAT_RATE_LIMIT`（默认 `60/min`，空或 0 = 不限）。
11. **调用审计**：不新增埋点表——`OperationLog` 已记录 PAT 请求（creator=属主）。
    `PersonalAccessTokenViewSet` 新增 `logs`（本人日志按时间窗/路径过滤，分页）与
    `stats`（近 7 天调用数/失败数/末次调用）action。**近似口径**：OperationLog 无
    token 标识字段，不同 token 的请求无法精确区分，按「creator + 时间窗」近似
    关联（前端弹窗文案写明）；如需精确关联再评估加字段。

## 精确审计、IP 白名单与方法维度 scope（2026-09-10 增补，N3 第四期 F3）

12. **精确审计标识**：`OperationLog` 新增 `auth_type`（`jwt` / `pat`，可空）与
    `token_pk`（凭证主键快照，`BigIntegerField`，可空，**不建 FK**——凭证被清理任务
    删除后历史日志不断链，主键值本身即可回溯），索引 `(token_pk, created_time)`。
    类型对齐 `PersonalAccessToken` 的真实主键（`DbBaseModel` 的 `BigAutoField`）。
    写入点在操作日志中间件
    （`resolve_auth_identity`）：DRF 的 `request.auth` 只挂在 DRF Request 包装对象上，
    中间件从渲染上下文的 DRF request 取；认证失败（凭证失效 / IP 白名单未命中）时
    认证链已清空 auth，此时按 `Authorization` 头判定类型，并读取认证类拒绝前写入的
    埋点 `_pat_rejected_token_pk` 保住凭证归属。`logs` / `stats` 改为按 `token_pk`
    精确归集，同一用户的多凭证互不混算。**升级前写入的历史行 `token_pk` 为空，
    无法归属到具体凭证、不计入统计**（前端弹窗已标注，不做数据回填）。
13. **IP 白名单**（`ip_allowlist`：JSON 字符串数组，空 = 不限；条目为单个 IP 或
    CIDR 网段）：认证阶段按 `get_request_ip` 校验来源 IP，未命中即拒绝并留审计
    （告警按凭证 60s 节流，防伪造请求刷日志）；客户端 IP 无法解析时 fail-closed。
    序列化器逐条校验格式（非法条目 400 并指明条目），做去空白/去重。
14. **scope 支持方法前缀**：条目可为 `METHOD /path`（如 `GET /api/system/user`），
    仅该 HTTP 方法放行；纯路径条目沿用旧口径、不限方法，方法名大小写不敏感；
    请求方法未知时方法限定条目不匹配（fail-closed）。**本条修订决策 7** 中
    「否决方法级 scope」的判断：机器集成除「只调某几个接口」外还有「只读同一组接口」
    的诉求，方法前缀以最小代价覆盖，且不改变既有条目语义。
15. **销项**：决策 11 的「近似口径」与「后果 · 演进」中登记的两项（创建时 IP 允许
    清单、token 精确审计标识）均已落地；`token_pk` 刻意取 UUID 字段而非外键，规避
    「凭证清理删除记录 → 审计断链」的问题。

## 后果

- 正面：第三方集成不再共享真人密码；单个凭证可独立吊销/过期；归属与审计清晰。
- 负面/边界：PAT 权限 = 用户权限，若给集成账号授予过多菜单权限，PAT 亦随之扩大
  （建议为集成场景建专用最小权限账号）；`changes` diff 白名单等既有审计口径不变。
- 演进：scope（路径 + 方法维度）、IP 白名单、token 精确审计标识均已落地
  （决策 7、12~14）；剩余演进项仅「PAT 数据权限收窄」（与所属用户一致，登记边界）。
