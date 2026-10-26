# 2026-10 全栈功能审查 · 分册：设置域（8 页）

> 主册：[code-review-2026-10.md](code-review-2026-10.md)。证据等级：A=代码直接确证，B=跨文件静态推断，C=待运行时验证。
> 设置域统一形态：前端表单页（非列表协议），后端 `BaseSettingViewSet`（NoDetailModelSet）家族，24 个 ViewSet 全部继承同一契约。

## 0. 域级公共机制（BaseSettingViewSet，本轮重点核对）

- **保存契约**：仅显式提交键落库（「改了什么存什么」）、write_only 密文空值=不修改、响应=未提交键当前值+变更键新值合并视图、`change_fields` 显式回写、序列化器 `post_save()` 联动失效钩子（`settings/views/settings.py:129-156`）。A。
- **?category 取值域校验**：mapper 外的 category 显式 400（防拼错静默落新分区读不到，:50-61）；元数据缓存键并入 category（:63-65）。
- **同 path 多权限码的字段面隔离（写入侧）**：`get_permission_field_groups` + `enforce_field_permission_groups`——同端点多码（如 basic/watermark）时，按当前方法取 `get_user_permission_codes`，未持有对应码却提交组内字段 → 逐字段 400（:91-116）。**这是主册 §3.1 P1 的写入侧缓解落地证据（A）**；读取侧仍为并集 OR 语义（`get_user_field_queryset_for_menus`），与 AGENTS.md §八 口径一致。
- 密文落库走 parse_serializer_data 的 write_only 识别（:118-127）；密钥值不回显。

## 1. 基础设置（/settings/basic）

- **标识**：settings · core_config · `basic/index.vue`（99 行）· 后端 `settings/views/basic.py BasicSettingViewSet`（与水印共享 GET/PATCH path 的双码端点）。
- **后端实现**：`get_permission_field_groups` 显式分组 `partialUpdate:SettingWatermark → watermark 字段组`（`basic.py:23-34`）——共享端点字段面隔离的参考实现。
- **闭环校验**：权限 ✅（双码确定性消解 + 字段面隔离）；元数据 ✅（category 进缓存键）；契约 ✅；数据 ✅。
- **问题清单**：无。
- 附：隐藏静态路由 `/system/setting/index`（remaining.ts:105）复用 settings/basic 页（S7，兼容旧入口）。

## 2. 安全设置（/settings/security/index）

- **标识**：settings · core_config · `security/index.vue`（151 行，多分组表单）· 后端 `settings/views/security.py` 11 个分类 ViewSet（密码规则/登录限制/登录/注册/找回/绑定邮箱/绑定手机/验证码/图形验证码/MFA/监控，各一个 category）。
- **后端实现**：薄继承 + `get_readonly_values` 附加「实际生效状态」诊断（`security.py:44-48`，与登录页能力清单同口径——配置了但未生效的面板可解释）。
- **闭环校验**：权限 ✅（每分类独立权限点，全局对账通过）；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。
- **质量速记**：历史 P0（bind-phone 配置键错写）修复后已有 `ops/fix-bind-phone-setting-keys-2026-10.md` 核查记录；本页键名与序列化器契约已对齐（B）。

## 3. 短信设置（/settings/sms）

- **标识**：settings · core_config · `sms/index.vue`（106 行，多渠道）· 后端 `settings/views/sms.py SmsSettingViewSet + SmsConfigViewSet`（category mapper：alibaba 等渠道面）。
- **后端实现**：`category_whitelist` 显式声明「mapper 只覆盖部分渠道」形态（settings.py:42-44 注释）；backends 动作前端显式权限门控（`sms/index.vue:44`）。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅（密文键不回显）。
- **问题清单**：无。

## 4. 消息设置（/settings/message）

- **标识**：settings · core_config · `message/index.vue`（126 行）· 后端 `notifications/views/notifications.py SystemMsgSubscriptionViewSet / UserMsgSubscriptionViewSet`（订阅模型，非 Setting）。
- **后端实现**：系统级与用户级订阅分开（User 级 queryset 收口本人，`notifications.py:143-144`）；不可选后端由 `only_user_selectable` 控制（:30）。
- **闭环校验**：权限 ✅（partialUpdate:SystemMsgSubscription 前端显式判定，`message/index.vue:59`）；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。

## 5. 配置台账（/settings/ledger/index）

- **标识**：settings · core_config · 薄壳 24 行 · 元数据驱动 · 后端 Setting 全表台账视图（`settings/views/settings.py SettingViewSet:184`）。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。

## 6. LDAP 目录同步（/settings/ldap）

- **标识**：settings · ldap(标配) · `ldap/index.vue`（34 行）· 后端 `settings/views/ldap.py LdapServerSettingViewSet`。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅（bind 密码 write_only）。
- **问题清单**：无。
- **质量速记**：模块注释明确「模块开关控制页面/接口可达，是否真启用由 LDAP_AUTH_ENABLED 决定」（catalog.py 注释，B）——双开关语义已在文档声明。

## 7. 开放平台设置（/settings/open-platform）

- **标识**：settings · open_platform(可选) · `open-platform/index.vue`（67 行，OAuth 提供方 + SCIM 两个分区）· 后端 `settings/views/oauth.py OAuthProviderSettingViewSet`（SystemConfigSettingViewSet 子类）+ `scim.py ScimSettingViewSet`。
- **前端实现**：SCIM 分区按 `retrieve:ScimSetting` 门控（`index.vue:14`）。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅（client_secret 密文）。
- **问题清单**：无。

## 8. 登录策略（/settings/login-policy/index）

- **标识**：identity · core_auth · `login-policy/index.vue`（32 行 + ReEmpty 权限态）· 元数据驱动 · 后端 `identity/views/admin/login_policy.py LoginAccessPolicyViewSet`（BaseModelSet + preview 动作）。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。
- **质量速记**：C：策略评估（时段/IP/数量限制）在认证链路的实际拦截顺序属运行时行为，本批只审了 CRUD/预览面。

## 批次 4 小结

- **发现数**：P0=0 / P1=0 / P2=0 / E=0。
- **正向**：BaseSettingViewSet 的保存契约（显式提交键 + 密文空值跳过 + change_fields + post_save 钩子）与共享端点字段面隔离（写入侧）质量高；「实际生效状态」诊断直击「配置了没生效」的运维痛点。
- **已读文件**（后端全文/核心段 + 前端红旗扫描）：`settings/views/settings.py`（37-156）、`settings/views/basic.py`（分组段）、`settings/views/security.py`（30-60）、`settings/views/{sms,ldap,oauth,scim,system_config}.py`（类清单）、`notifications/views/notifications.py`（订阅段）；前端 8 页红旗 grep（0 违例）+ security/sms/message/open-platform/login-policy 门控行。
