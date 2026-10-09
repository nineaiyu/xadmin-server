# OWASP ASVS 对照表（L1/L2 轻量映射）

> 目的：把本项目现有安全现状映射为 **OWASP ASVS** 对照表，用于「一页回答客户安全问卷」；
> **只登记不强制整改**——差距项按「自动化可下沉 / 接受项 / 触发条件」三类归属（见 §三），不因合规而新增控制。
>
> **版本与范围**：采用 **ASVS 4.0.3** 的章节结构（V1–V14）；只评估 **L1/L2**，**L3 不评估**
> （硬件安全模块、密码模块认证、强不可否认等 L3 要求不在范围内）。ASVS 5.0 将章节重排为 V1–V17，
> 控制面可按本表条目平移，不改变各条现状判定。
>
> **状态口径**：满足 / 部分满足 / 不适用 / 差距。
> **证据口径**：引用本项目内的实现与守护（文档章节 / ADR 编号 / 测试路径 / 代码模块）。
>
> **关联**：安全接受项与重开条件汇总见 [security-review.md](security-review.md) 八期登记；
> 触发制项另见 [plans/触发制任务清单-长期.md](plans/触发制任务清单-长期.md) §三；
> 每条的残余面在「备注」列指向 §三 归属。

## 一、状态图例

| 状态 | 含义 |
|---|---|
| 满足 | 控制已实现，且有守护（守护测试 / CI 门禁 / 同源漂移校验） |
| 部分满足 | 主控制已实现，存在**明确残余面**（残余部分计入 §三 差距清单） |
| 不适用 | 该条要求不适用于本架构 / 技术栈（附一句理由，避免被读成「未评估」） |
| 差距 | 控制尚缺，按 §三 归属之一登记 |

## 二、ASVS 对照表

### V1 架构、设计与威胁建模

| 条目 | 要求摘要 | 状态 | 证据（实现与守护） | 备注 |
|---|---|---|---|---|
| V1.1 | 安全开发生命周期 | 部分满足 | ADR 机制（`docs/adr/README.md` 索引，篇数由 `scripts/check_doc_facts.py` 计数守护）、CI 门禁统一登记（`docs/ci-gates.md`）、提交与回归纪律红线（`docs/plans/README.md`） | 无正式威胁建模流程 → §三.②接受项 |
| V1.2 | 认证架构 | 满足 | JWT-only（ADR-001）；认证 backend 链显式（`server/settings/libs.py` `AUTHENTICATION_BACKENDS`）+ LDAP/OAuth/OIDC/Passkey 多通道（`packages/xadmin-common/common/core/auth.py`） | |
| V1.3 | 会话管理架构 | 满足 | JWT 双 Token 轮换 + 黑名单 + `sid` 会话失效（ADR 见 `docs/security-review.md` 三期 JWT 专项）；会话存储 `SESSION_ENGINE=cached_db`（`server/settings/libs.py`） | |
| V1.4 | 访问控制架构 | 满足 | 权限点 + 菜单 + 数据/字段权限三层，默认拒绝（`docs/architecture/permission.md`、`packages/xadmin-common/common/core/permission.py`） | |
| V1.5 | 输入/输出架构 | 满足 | DRF 序列化器 + 元数据协议；受控查询字段/lookup 白名单 fail-closed（`docs/architecture/metadata-protocol.md`） | |
| V1.6 | 密码学架构 | 满足 | 传输加密 AES v2（ADR-011）；字段级加密 signer v3（`packages/xadmin-common/common/core/credentials.py`）；密码哈希 argon2id（`server/settings/libs.py`） | |
| V1.7 | 错误、日志与审计架构 | 满足 | 统一异常处理（`docs/exception-handling.md`、`packages/xadmin-common/common/core/exception.py`）、操作日志 + 运行日志脱敏（ADR-072） | |
| V1.8 | 数据保护与隐私架构 | 满足 | 数据权限 / 字段权限 / 脱敏规则（ADR-009，`packages/xadmin-common/common/core/mask.py`） | |
| V1.9 | 通信安全架构 | 部分满足 | HTTPS/HSTS/Secure Cookie 开关（`server/settings/security_https.py`） | 默认 HTTP 直连可用 → §三.②接受项 |
| V1.10 | 恶意软件架构 | 满足 | 依赖审计（`.github/workflows/security.yml` pip-audit）+ 镜像扫描（`docs/ops/deployment-upgrade.md` §6.3 trivy/SBOM）；CELERY 仅 JSON 序列化（`server/settings/libs.py`） | |
| V1.11 | 业务逻辑架构 | 满足 | 审批引擎 + 敏感操作 412 审批协议（ADR-012/026/040）；AI 受限动作权限双门（ADR-038/048/049） | |
| V1.12 | 文件上传架构 | 满足 | 扩展名白名单 + magic bytes 双重校验（`packages/xadmin-common/common/core/modelset/upload.py`）、上传扩展名策略 fail-closed + 文件访问审计 | |
| V1.14 | 配置架构 | 满足 | 声明式配置 + 转发守护（`server/settings/setting.py`、`server/conf/settings_defaults.py`）、生产 SECRET_KEY 缺失拒启（`server/settings/base.py`） | |

### V2 认证

| 条目 | 要求摘要 | 状态 | 证据（实现与守护） | 备注 |
|---|---|---|---|---|
| V2.1 | 口令安全（强度/长度/弱口令） | 满足 | Django `AUTH_PASSWORD_VALIDATORS`（`server/settings/base.py`）+ 平台规则（`settings/utils/password.py`，含内置离线弱口令库 `settings/data/leak_passwords.txt`、历史密码、有效期）；守护 `tests/unit/settings/test_password_rules.py`、`tests/unit/system/test_password_security.py` | |
| V2.2 | 通用认证器安全 / 反自动化 | 满足 | 六类限流 + 登录 50/h（`server/settings/libs.py`、`packages/xadmin-common/common/core/throttle.py`）；账号/ IP 锁定（`tests/integration` 锁定用例）；登录策略 `LoginAccessPolicy`（优先级/时段/网段/并发会话上限） | |
| V2.3 | 认证器生命周期 | 满足 | 密码重置 / 强制改密 / 账号有效期 / 邀请开户（ADR-071 系列登记）；MFA 绑定与解绑（`identity/views/auth/mfa.py`） | |
| V2.4 | 凭证存储 | 满足 | argon2id 首位 + PBKDF2 回退渐进重哈希；守护 `tests/unit/system/test_password_hashers.py` | |
| V2.5 | 凭证恢复 | 满足 | 重置链路不区分账号存在性（S-5 收口）+ `ResetPasswordThrottle`；守护 `tests/integration/system/test_password_reset_api.py` | |
| V2.6 | 查找式密钥验证器（用户枚举面） | 部分满足 | 登录/验证码发送链路防枚举（`docs/security-review.md` S-5） | 注册链路保留「账号已占用」明确提示 → §三.②接受项 |
| V2.8 | 一次性验证器（OTP / MFA） | 满足 | MFA 后端与一次性状态缓存（`server/settings/base.py` `mfa_*_key`）；Passkey/WebAuthn 作为 MFA（`mfa/backends/passkey.py`、`identity/views/admin/passkey.py`） | |
| V2.10 | 服务认证 | 满足 | PAT（ADR-008）、开放平台 client-credentials（ADR-030）、SCIM 独立令牌（S1）、OIDC（ADR-053） | |

### V3 会话管理

| 条目 | 要求摘要 | 状态 | 证据（实现与守护） | 备注 |
|---|---|---|---|---|
| V3.1 | 基础会话管理 | 满足 | 无状态 JWT + 服务端 `UserSession` 登记与在线口径（会话级 `sid` 失效） | |
| V3.2 | 会话绑定 | 部分满足 | 临时令牌绑客户端指纹（UA/Accept/XFF，`common/utils/token.py` 口径）；access/refresh 分离 | 无设备绑定强校验 → 计入 §三.③ |
| V3.3 | 登出与超时 | 满足 | 登出吊销 access + refresh 拉黑；强制下线（用户级 / 会话级）（`docs/security-review.md` 三期 4–6 项）；守护 `tests/integration` 强制下线用例 | |
| V3.4 | Cookie 型会话属性 | 部分满足 | `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` 随 HTTPS 开关（`server/settings/security_https.py`）；SameSite=Lax | HTTP 直连无 Secure → §三.②接受项 |
| V3.5 | 令牌型会话 | 满足 | 算法固定 HS256（不接受 header 算法）、类型限制 `AUTH_TOKEN_CLASSES`、轮换 + 黑名单（`server/settings/libs.py`） | |
| V3.7 | 会话管理攻击防护 | 满足 | 登录锁定 / 异地登录检测 / 令牌失效时间戳（`docs/security-review.md` 三期） | |

### V4 访问控制

| 条目 | 要求摘要 | 状态 | 证据（实现与守护） | 备注 |
|---|---|---|---|---|
| V4.1 | 通用访问控制设计（默认拒绝/最小权限） | 满足 | `DEFAULT_PERMISSION_CLASSES=IsAuthenticated`（`server/settings/libs.py`）+ 权限点 + 数据权限编译器 fail-closed（`docs/architecture/permission.md`） | |
| V4.2 | 操作级访问控制 | 满足 | 权限码 `动作:组件名`，方法级绑定；越权矩阵 29 例入 CI（`tests/integration/system/test_privilege_escalation_matrix.py`） | |
| V4.3 | 字段级 / 其他访问控制 | 满足 | 字段权限白名单同时约束读（响应裁剪）与写（字段忽略）（`docs/architecture/field-permission.md`）；应用级四级授权（ADR-039） | |
| V4.4 | 防 IDOR（对象级） | 满足 | 数据权限编译 + `OwnerUserFilter` 水平隔离；越权矩阵断言数据库零副作用 | |

### V5 校验、净化与编码

| 条目 | 要求摘要 | 状态 | 证据（实现与守护） | 备注 |
|---|---|---|---|---|
| V5.1 | 输入校验 | 满足 | DRF 序列化器 + 元数据驱动校验；动态表单写入/提交双侧校验（ADR-025/064） | |
| V5.2 | 净化与沙箱（XSS） | 满足 | 富文本统一 `sanitizeHtml`（DOMPurify，S-4 收口）；消息模板沙箱渲染 | |
| V5.3 | 输出编码与注入防护（SQLi 等） | 满足 | ORM 参数化 + 受控 lookup 白名单（禁跨关系 `a__b__`）+ JSON 路径列 fail-closed（ADR-069/071） | |
| V5.4 | 内存 / 字符串 / 非托管代码 | 不适用 | Python 托管运行时，无手动内存管理面 | |
| V5.5 | 反序列化防护 | 满足 | 仅 JSON / FormData 解析；Celery 仅接受 JSON，无 pickle（`server/settings/libs.py`） | |

### V6 存储密码学

| 条目 | 要求摘要 | 状态 | 证据（实现与守护） | 备注 |
|---|---|---|---|---|
| V6.1 | 数据分类与静态加密 | 部分满足 | 密钥类字段级加密（signer v3，HKDF/AES-GCM）；凭据注册表（`packages/xadmin-common/common/core/credentials.py`） | 数据库无透明加密（依赖部署层磁盘加密）→ §三.②接受项 |
| V6.2 | 算法 | 满足 | argon2id、AES-256-GCM、HMAC-SHA256（Webhook 签名）、HS256（JWT）；守护 `tests/unit/common/test_aes_cipher_v2.py`、`tests/unit/common/test_signer_v3.py` | |
| V6.3 | 随机值 | 满足 | 前端 WebCrypto / 服务端 `secrets`；AES v2 salt/iv 由 CSPRNG 生成（ADR-011） | |
| V6.4 | 密钥管理 | 满足 | 凭据注册表 + 轮换命令 + 只读凭据页；`SENSITIVE_SETTING_KEYS` 写侧加密、读侧不回传明文 | |

### V7 错误处理与日志

| 条目 | 要求摘要 | 状态 | 证据（实现与守护） | 备注 |
|---|---|---|---|---|
| V7.1 | 日志内容（不含敏感） | 满足 | 递归按敏感键掩码 + 脱敏先于截断，操作日志 `body`/`response_result`、DEBUG 正文、慢请求日志同口径（ADR-072）；守护 `tests/unit/common/test_logging_mask_filter.py` | |
| V7.2 | 日志处理（集中/审计） | 满足 | 操作日志 + 登录日志 + 审计日志冷归档（`audit/utils/log_archive.py`）；`tests/integration/system/test_log_archive.py` | |
| V7.3 | 日志保护（完整性） | 部分满足 | 冷归档随包 sha256 + 行数清单校验（`--verify`） | 无 WORM / 在线防篡改 → §三.②接受项 |
| V7.4 | 错误处理（不泄露内部信息） | 满足 | 统一异常处理器归一化错误码 + 文案脱敏（`docs/exception-handling.md`） | |

### V8 数据保护

| 条目 | 要求摘要 | 状态 | 证据（实现与守护） | 备注 |
|---|---|---|---|---|
| V8.1 | 通用数据保护 | 满足 | 数据权限 / 字段权限 / 脱敏规则（ADR-009）；敏感读接口审计埋点 | |
| V8.2 | 客户端数据保护 | 部分满足 | 敏感页面水印（ADR-029）；无 BFF | token 存 JS 可读 Cookie（S-6）→ §三.②接受项 |
| V8.3 | 敏感私有数据（传输/回传） | 部分满足 | 敏感凭证写侧 `write_only`、读侧不回传明文（凭据只读页）；传输 AES v2 | HTTP 直连时传输加密降级 → §三.②接受项 |

### V9 通信安全

| 条目 | 要求摘要 | 状态 | 证据（实现与守护） | 备注 |
|---|---|---|---|---|
| V9.1 | 客户端通信（TLS） | 部分满足 | HSTS 一年 + 子域/preload 开关（`server/settings/security_https.py`）；生产推荐 HTTPS | 非 HTTPS 直连可用 → §三.②接受项 |
| V9.2 | 服务端通信（出站 SSRF） | 满足 | 统一出站守卫（`packages/xadmin-common/common/utils/outbound.py`）：协议白名单 + 元数据/link-local/保留地址恒拒绝 + 固定解析连接（DNS rebinding）；S-2 收口；守护 `tests/unit/common/test_outbound_guard.py` | 私网/环回按场景放行（AI base_url）→ §三.②接受项 |

### V10 恶意代码

| 条目 | 要求摘要 | 状态 | 证据（实现与守护） | 备注 |
|---|---|---|---|---|
| V10.1 | 代码完整性 | 部分满足 | 依赖版本锁定（`uv.lock` + `tests/unit/test_dependency_manifest.py`） | 无制品签名/校验链 → §三.③触发条件 |
| V10.2 | 恶意代码搜索 | 满足 | pip-audit（PR + 每周定时）、pnpm audit（client）、trivy 镜像扫描 HIGH/CRITICAL 阻断、SBOM（`docs/ops/deployment-upgrade.md` §6.3） | |
| V10.3 | 部署应用完整性 | 满足 | CSP 强制（页面层）限制第三方脚本；无第三方 iframe 脚本注入面 | |

### V11 业务逻辑

| 条目 | 要求摘要 | 状态 | 证据（实现与守护） | 备注 |
|---|---|---|---|---|
| V11.1 | 业务逻辑安全（顺序/限额/幂等/防自动化） | 满足 | 审批动作实例行锁 + 终态 CAS（ADR-049）；敏感操作 412 一次性令牌重放（ADR-026）；消息 `client_msg_id` 幂等；限流 + 配额 | |
| V11.1 | AI 业务逻辑边界 | 部分满足 | 声明式动作注册表 + 权限双门 + 高危动作强制审批（ADR-048/049）；提示注入告警（不阻断）+ 输出脱敏 | 注入仅告警不阻断 → §三.②接受项 |

### V12 文件与资源

| 条目 | 要求摘要 | 状态 | 证据（实现与守护） | 备注 |
|---|---|---|---|---|
| V12.1 | 文件上传 | 满足 | 扩展名白名单 + magic bytes + 大小上限（`packages/xadmin-common/common/core/modelset/upload.py`）；上传扩展名策略 fail-closed | |
| V12.2 | 文件完整性 | 部分满足 | 上传大小/类型校验；文件访问审计（`file/utils/file_audit.py`） | 无上传内容深度扫描/杀毒 → §三.③触发条件 |
| V12.3 | 文件执行 | 满足 | 上传目录非可执行、静态服务不解析脚本 | |
| V12.4 | 文件存储 | 满足 | `/media/` 直链移除，统一应用鉴权 + X-Accel 内转（S-3）；可插拔存储 local/S3 | |
| V12.5 | 文件下载 | 满足 | 受鉴权 download / preview 端点 + 文件访问审计（`tests/integration/system/test_file_audit.py`） | |
| V12.6 | SSRF | 满足 | 同上 V9.2 统一出站守卫（Webhook 发送侧固定解析连接） | |

### V13 API 与服务

| 条目 | 要求摘要 | 状态 | 证据（实现与守护） | 备注 |
|---|---|---|---|---|
| V13.1 | 通用 Web 服务安全 | 满足 | OpenAPI schema（`manage.py spectacular`）+ 契约治理（ADR-035，`docs/schema/` 唯一真源）；认证作用域统一 | URL 版本化 no-go 已登记（ADR-035） |
| V13.2 | RESTful Web 服务（校验 / 配额 / CORS） | 满足 | 方法级权限点；序列化器校验 + 元数据；CORS 白名单（`CORS_ALLOW_ALL_ORIGINS` 凭证组合拒启，`server/settings/libs.py`）；限流 + 导出并发上限；请求体协议 FormData v1（ADR-007） | |
| V13.3 | SOAP Web 服务 | 不适用 | 无 SOAP 面 | |
| V13.4 | GraphQL | 不适用 | 无 GraphQL 面 | |

### V14 配置

| 条目 | 要求摘要 | 状态 | 证据（实现与守护） | 备注 |
|---|---|---|---|---|
| V14.1 | 构建与部署 | 满足 | 镜像构建流水线（`build-base-image.yml`/`build-image.yml`） + trivy + SBOM；镜像 issue | SBOM 生成未前移 CI → §三.① |
| V14.2 | 依赖 | 部分满足 | pip-audit / pnpm audit 双 0；停维依赖台账按季度复核 | Django 6.0.8 EOL 期停留（S-6）→ §三.②接受项 |
| V14.3 | 非预期安全披露 | 满足 | `DEBUG` 生产受控；django-silk 仅 DEBUG/DEBUG_DEV 且要求登录 + 员工权限（`server/settings/base.py`）；Sentry `send_default_pii=False`（`server/monitoring.py`） | |
| V14.4 | HTTP 安全响应头 | 部分满足 | CSP（页面层 nginx 强制 + Django 侧 report-only，三处同源漂移守护 `tests/unit/common/test_csp.py`）；HSTS / Secure Cookie（`server/settings/security_https.py`）；X-Frame-Options / X-Content-Type-Options Django 默认 | Django 侧 CSP 默认 report-only → §三.③触发条件 |
| V14.5 | HTTP 请求头校验 | 满足 | Referer 校验中间件（`REFERER_CHECK_ENABLED`）；`TRUSTED_PROXY_IPS` 防 XFF 伪造（`server/settings/base.py`）；CSRF 中间件启用（ADR-001 修订） | |

## 三、差距项清单与归属

> 口径：本清单只覆盖 §二 中状态为「部分满足 / 差距」的条目；**本轮只登记，不实施新整改**。
> 「接受项」写入 [security-review.md](security-review.md) 八期登记的既有台账形态（含理由与重开条件）。

### ① 自动化可下沉（可做成的 CI 检查 / 守护测试方向）

| 差距 | 对应条目 | 可下沉形态 |
|---|---|---|
| 安全响应头无统一回归守护 | V14.4 | 新增守护测试断言关键响应头矩阵（CSP 已有 `tests/unit/common/test_csp.py`；补 X-Frame-Options / X-Content-Type-Options / HSTS 开关面，参照 `tests/unit/common/test_settings_https.py`） |
| 本对照表「满足」项引用路径部分未受校验 | V1.7 / V7 全章 | 活跃文档路径校验（`scripts/check_doc_paths.py`）当前只覆盖 `packages/` `server/` `system/` 等前缀；将 `tests/` 前缀纳入，防止对照表证据链漂移 |
| 停维依赖台账为人工季度复核 | V14.2 | 守护测试断言「新增直连依赖必须登记停维台账」，把季度人工核对降为增量守护 |
| SBOM 生成仅在发布时 | V14.1 | 将 CycloneDX SBOM 生成前移到 CI 并归档（现随 release 生成，见 `docs/ops/deployment-upgrade.md` §6.3） |
| 上传扩展名策略与被引用测试未对账 | V12.1 | 策略词表 ↔ 消费点（校验代码 / 单测）双向对账守护，形态参照 `docs/ci-gates.md` 既有对账门禁 |

### ② 接受项（已接受残余风险；理由 + 重开条件登记见 security-review.md 八期）

| 差距 | 对应条目 | 接受理由（摘要） | 重开条件（摘要） |
|---|---|---|---|
| 无正式威胁建模 / SSDLC 流程 | V1.1 | 单人维护规模，ADR + CI 门禁 + 纪律红线承担主要 SDLC 面 | 团队扩容或客户要求正式威胁建模 |
| HTTPS 非强制、HTTP 直连 Cookie 无 Secure | V1.9 / V3.4 / V9.1 | 保留内网 HTTP 直连可用性（既有 S-6 项） | 全站强制 HTTPS 后启用 HSTS + Secure |
| JWT 存 JS 可读 Cookie | V3.5 / V8.2 | 无感刷新链路依赖 Cookie 读 token（既有 S-6 项） | 出现绕过 CSP 的 XSS 面或改 BFF |
| 注册链路保留账号占用提示 | V2.6 | 注册必须告知占用（既有 S-6 项） | 注册改邀请制 |
| Django 6.0.8 EOL 期停留 | V14.2 | beat 声明阻断升级（既有 S-6 / 六期 S-1） | `django-celery-beat` 声明放宽支持 6.1 |
| 数据库无透明静态加密（仅字段级） | V6.1 | 依赖部署层磁盘/卷加密，字段级已覆盖密钥类 | 合规要求 DB 透明加密或多租户 |
| 审计日志无 WORM / 在线防篡改 | V7.3 | 冷归档 sha256 校验已覆盖离线完整性 | 合规要求日志不可否认或在线防篡改 |
| AI 提示注入仅告警不阻断 | V11.1 | 阻断会伤害问答可用性；引用块包裹 + 输出脱敏已落地 | 出现注入导致的真实越权或数据外泄 |
| 出站私网/环回按场景放行 | V9.2 | 内网自建推理与本地联调需要（S-2 已处置） | 部署形态改变导致私网目标不再可信 |

### ③ 触发条件（条件未命中前不立项、不做预备工作）

| 差距 | 对应条目 | 触发条件 |
|---|---|---|
| 无外部渗透测试 / 第三方安全评估 | V1.1 | 客户合规要求，或正式对外发布前 |
| Django 侧 CSP 默认 report-only | V14.4 | django-csp 对 swagger 的 enforce 兼容性完成一轮观察（CI 含 api-docs 用例全绿），或出现绕过 nginx 直连 Django 的 HTML 面（同 [plans/触发制任务清单-长期.md](plans/触发制任务清单-长期.md) §三.3） |
| 无制品签名 / 校验链 | V10.1 | 分发二进制制品或引入第三方制品源 |
| 无上传内容深度扫描 / 杀毒 | V12.2 | 开放办公文档/压缩包等更广文件类型上传 |
| 会话无设备绑定强校验 | V3.2 | 出现定向会话劫持或合规要求设备绑定 |
