# 全栈审查整改执行台账（2026-10-29）

> 依据：[主篇](code-review-fullstack-2026-10-29.md)、[part3](code-review-fullstack-2026-10-29-part3.md)、
> [part4](code-review-fullstack-2026-10-29-part4.md)。
> 本文件是整改**状态唯一源**；代码是否落地以 `git status` 与测试结果为准。未提交 git。

## 1. 执行范围与原则

- 执行范围：全部 P0、P1、全部 P2 候选（含 OTP 密钥加密迁移、ZIP 导出随机口令、
  委托「全部流程」语义收口）。
- 仅保留一条**产品口径**（非缺陷）：报表定义全员共享只读；已在 §3.2 登记依据与升级条件。
  数据集字段权限已按「共享 strict / 个人 inherit」落库并配套巡检命令。
- 修复优先根因：登录统一收口、附件服务端媒体类型、出站守卫同源、软删除计数口径、
  前端保存/加载态统一判定。
- 后端改动后按工作区规范重启容器验证；本轮已补 E2E `smoke:fresh`（独立 E2E 库 +
  daphne + Vite），运行期清单见 §5。

## 2. P0 / P1 整改结果

| 编号 | 级别 | 修复摘要 | 主要落点 | 验证 | 状态 |
|---|---|---|---|---|---|
| SEC-P0-01 | P0 | 用户搜索序列化器移除 `password` 输出 | `identity/views/search/user.py` | `tests/unit/identity/test_search_user_serializer.py` | ✅ |
| CHAT-P0-01 | P0 | 音视频附件不再信任客户端 MIME；扩展名/声明类型白名单不通过即附件下载 | `message/chat/attachments.py` | `tests/integration/message/test_chat_attachment.py::TestAttachmentMimeHardening` | ✅ |
| DATA-P1-01 | P1 | 角色/部门/岗位/数据权限关联计数带 `deleted_at` 过滤 | `identity/serializers/{role,department,post}.py`、`system/views/admin/permission.py` | `tests/unit/system/test_dept_user_count.py::test_soft_deleted_user_not_counted` | ✅ |
| AUTH-P1-01 | P1 | `complete_login` 统一检查登录开关/访问策略；OAuth、MFA、注册、验证码登录全部收口 | `identity/services/auth_login.py`、`identity/views/auth/{oauth,mfa,register,login}.py`、`common/swagger/views.py` | `tests/unit/identity/test_auth_login_gate.py` + 既有登录/MFA/API docs 用例 | ✅ |
| LDAP-P1-01 | P1 | `auto_bind=False` → StartTLS → bind（服务账号与用户连接同改） | `identity/ldap/client.py` | `tests/unit/identity/test_ldap_client_tls.py` | ✅ |
| AI-P1-01 | P1 | AI 动作参数、MCP arguments 落审计前统一 `desensitize_payload` | `ai/services/actions/ai_action_audit.py`、`ai/services/mcp/mcp_client.py` | `tests/unit/ai/test_ai_action_audit_mask.py` | ✅ |
| EXPORT-P1-01 | P1 | 流式 XLSX 用 `WriteOnlyCell.data_type="s"` 写文本，阻断公式注入 | `common/core/modelset/import_export/streaming.py` | `tests/unit/common/test_export_streaming.py::test_formula_like_text_is_written_as_text` | ✅ |
| ICON-P1-01 | P1 | 内联 SVG 过 DOMPurify SVG 白名单；菜单 icon 服务端拒绝事件属性/script/foreignObject | `xadmin-client/src/utils/sanitize.ts`、`ReIcon/src/hooks.ts`、`system/serializers/menu.py` | `useRenderIcon.spec.ts`、`test_menu_frame_url_guard.py::TestMenuIconGuard` | ✅ |
| OAUTH-P1-01 | P1 | OAuth 出站环回改走 `loopback_allowed()`（与 Webhook/AI/MCP 同源） | `identity/utils/oauth.py` | `tests/unit/system/test_oauth_outbound.py` | ✅ |
| SETTING-P1-01 | P1 | 测试连接剔除响应回填的密钥掩码，不再用 `••••••` 建连 | `xadmin-client/src/views/settings/.../SettingItem.vue`、`utils/secretMask.ts` | `secretMask.spec.ts` | ✅ |

## 3. P2 整改结果

### 3.1 已修复

| 根因键 | 修复摘要 | 主要落点 | 验证 |
|---|---|---|---|
| PK-NORMALIZE-MISSING | rank/impact/suggestions/同步导入统一 `normalize_pks`；关联字段捕获 Django ValidationError | `modelset/{batch,impact,suggest}.py`、`import_export/actions.py`、`fields_related.py` | 既有导出/导入/联想用例 + 定向回归 |
| RELATED-PK-VALIDATION-500 | 关联字段非法 UUID 按 `incorrect_type` 400 | `common/core/fields_related.py` | 同上 |
| SUGGEST-PKS-UUID-500 | 联想 pks 归一后过滤 | `modelset/suggest.py` | 定向回归 |
| UPLOAD-QUOTA-SOFTDEL | 配额/数量按 `all_objects` 统计，堵住软删循环 | `file/utils/upload_store.py` | `test_upload_enhance.py::test_soft_deleted_files_still_count_toward_quota` |
| DATASET-AI-METRIC-MISMATCH | AI 声明收敛为 count/sum/avg 与 day/month | `dataset/ai_declarations.py` | 元数据/契约测试 |
| RERUN-REPORT-OWNER | 重跑记录归属操作者，保留报表创建者语义 | `dataset/analysis_tasks.py`、`task/utils/task_center_unified.py` | 任务中心用例 |
| INVITE-NO-EMAIL-LOCKOUT | 无邮箱/无邮件渠道直接拒绝邀请，不再制造不可登录账号 | `identity/utils/user_invite.py`、`identity/services/user_admin.py` | `test_user_invite.py::test_invite_without_email_rejected_without_lockout` |
| SEARCH-FIELDS-ALL-CHOICES | 先截断 ModelChoiceField queryset 再求值 choices | `modelset/metadata.py` | 元数据测试 |
| DEMO-USERS-RESET-SCOPE | `--reset` 只删 `demo_数字`，不再误伤 `demo_flow_*` | `demo_seed/.../seed_demo_users.py` | 命令静态核对 |
| CODEGEN-BATCH-OVERRIDE-MIX | 批量 ZIP 剔除 `fields/include/exclude` | `devtools/services/codegen/gui.py` | devtools 单测 |
| CODEGEN-DELETED-AT-WRITABLE | `deleted_at` 纳入生成器审计只读集 | `_generate_crud/constants.py` | devtools 单测 |
| ASYNC-CHAT-STREAM-LEAK | 重试前关闭流响应（同步/异步/embeddings） | `integrations/sdk/ai/*` | AI SDK 用例 |
| AXIOX-FORM-RECURSION | multipart 点分键深度上限 20 | `common/drf/parsers/axios_form_data.py` | 解析器用例 |
| POST-MEMBER-DATASCOPE | 岗位成员读取/增删按用户数据权限收敛 | `identity/views/admin/post.py` | 岗位/数据权限用例 |
| DEPT-AUTH-SILENT-DROP | 部门授权越权角色/规则显式 400 | `identity/serializers/department.py` | 部门用例 |
| ROLE-CREATE-FIELDS | `fields` 改可选并默认空 dict | `identity/serializers/role.py` | 角色用例 |
| FE-200-BIZCODE | 权限试算按 `isSuccess` 判定 | `views/system/user/components/preview/PreviewTrial.vue` | 前端单测/类型 |
| FE-TYPE-DRIFT | 用户树 pk 类型改为 `string | number` | `views/system/user/components/UserTree.vue` | `pnpm typecheck` |
| FE-SHOW-NUMBER | Webhook 重试 `show` 返回布尔、`index` 独立 | `views/integration/delivery/utils/hook.tsx` | 类型/单测 |
| SYSMSG-MISSING-ROW | 系统消息订阅缺失行自愈补建 | `notifications/views/notifications.py` | 通知用例 |
| AUTH-AES-DECRYPT-500 | 统一受控解密助手，坏密文/空明文 400 | `identity/utils/auth/captcha.py` + 各登录/注册/重置/发码入口 | `test_auth_login_gate.py` |
| REGISTER-CHANNEL-RECHECK | 注册提交侧复核邮箱/短信通道开关 | `identity/views/auth/register.py` | 注册用例 |
| OAUTH-REFRESH-RACE | `select_for_update` + 条件撤销，单方轮换成功 | `identity/services/open_oauth.py` | OAuth 用例 |
| SCIM-PASSWORD-POLICY | SCIM 建号口令走强度/泄露库校验 | `identity/scim/resources.py` | SCIM 用例 |
| ACCOUNT-RISK-SUPERUSER-UNGUARDED | 风险处置禁止停用/踢超管 | `identity/utils/account_risk.py` | 风险用例 |
| APPROVAL-MENTION-SCOPE | @ 提及按实例可见域收敛 | `approval/views/approval_instance_comments.py` | `test_approval_collaboration.py` |
| ARCHIVE-WATERMARK-NOVERIFY | 归档跳过/水位推进前校验 sha256/行数 | `audit/utils/log_archive.py`、`log_archive` 命令 | `test_log_archive.py` 冷归档演练 |
| ASYNC-IMPORT-S3-PATH | 异步导入走 `storage_open`，不再读本地 `.path` | `task/services/_import.py` | 导入用例 |
| MCP-SSE-UNBOUNDED | SSE 累计字节上限 | `ai/services/mcp/mcp_client.py` | MCP 用例 |
| AI-DOCX-ZIP-BOMB | DOCX 解压总量上限 20MB | `ai/services/knowledge/doc_extract.py` | 文档解析用例 |
| SITE-CONFIG-FALSE-SUCCESS | 站点配置保存/重置判业务码，失败不写快照 | `store/modules/siteConfig.ts` | 前端单测/类型 |
| SAVED-VIEWS-LOADING-HANG | 保存校验/失败分支复位 `closeLoading` | `SavedViews.vue` | savedView 单测 |
| CHAT-RESEND-ATTACHMENT-LOSS | video/audio 重发携带附件类型 | `views/chat/hooks/useChatSend.ts` | 前端单测/类型 |
| CHAT-HISTORY-RACE | 上翻历史响应按 `historySeq` 丢弃过期页 | `hooks/useHistoryPagination.ts` | 类型/单测 |
| UPLOAD-FILES-UNDEFINED-PK | 未完成上传项不进入提交载荷 | `UploadFiles.vue` | 类型/单测 |
| SAFE-FRAME-SANDBOX | 默认 sandbox 不含 `allow-same-origin`；大屏媒体同改 | `SafeFrame/index.vue`、`PaneMediaView.vue` | SafeFrame 单测 |
| REPORT-SAVE-EDIT-LOSS | 保存成功仅在本页未再编辑时回写快照 | `useReportPersistence.ts` | 前端单测/类型 |
| WEBSOCKET-4403-RECONNECT | 4401/4403 关闭心跳/退避并置 socketOpen=false | `utils/websocket.ts` | websocket 单测 |
| AI-CARD-PARAM-PLAINTEXT | 确认卡对 password/token/secret 掩码 | `components/AiActionCard/index.vue` | 类型/单测 |
| NOTICE-HTML-SANITIZE-BRANCH | 所有 HTML 通知统一 DOMPurify 净化 | `store/modules/notice.ts` | 前端单测/类型 |
| DASHBOARD-RANDOMUUID | 非安全上下文回退随机 id | `views/dashboard/utils/useCardDialog.ts` | 类型 |
| SCREEN-EXPORT-NAME-COLLISION | ZIP 同名卡片补序号 | `screenExportFiles.ts` | 类型 |
| OTP-SECRET-PLAINTEXT | OTP 密钥改 `EncryptedCharField`（v3 透明加解密，存量明文兼容）；新增迁移 | `common/fields/char.py`、`identity/models/user.py`、`identity/migrations/0004...` | `test_otp_secret_encryption.py`、MFA 用例 |
| EXPORT-ZIP-USERNAME-PASSWORD | ZIP 导出改一次性随机口令并经 `X-Export-Zip-Password` 响应头下发 | `common/drf/renders/base.py` | 渲染器静态/编译验证 |
| DICT-USAGE-NX3 | 同一次序列化复用 `dict_usage`，3 指标不再各查 3 次 Redis | `system/serializers/dict.py` | 字典用例 |
| DELEGATION-ALLFLOWS-UNREACHABLE | 空 `flow_codes` = 全部流程，新建/编辑不再拒绝（与模型及前端标签一致） | `approval/serializers/approval_delegation.py` | `test_approval_delegation.py::test_create_all_flows_allowed` |
| REPORT-RUN-POLLING-LEAK | 报表运行轮询支持 AbortSignal，页面卸载取消并停止 toast | `reportRunPolling.ts`、`utils/hook.tsx` | 前端类型/单测 |
| DATASET-FIELD-FAIL-OPEN | 新增 `Dataset.field_permission_mode`（inherit/strict）；新建/切换共享默认 strict，个人/存量共享保持 inherit；strict 无字段配置返回空；提供 `dataset_field_permission_audit` 巡检 | `dataset/models/dataset.py`、`dataset/serializers/dataset.py`、`dataset/utils/{dataset,dataset_query}.py`、`dataset/management/commands/dataset_field_permission_audit.py` | `test_build_queryset_field_permission.py`、`test_dataset_api.py` |

### 3.2 保留 / 待决策

| 根因键 | 结论 | 后续动作 |
|---|---|---|
| REPORT-NO-VISIBILITY | **产品口径确认保留**：报表定义全员共享只读，数据边界由数据集行级/字段权限承担（模型 docstring 已登记） | 如引入个人报表，再补 visibility 与列表/详情收敛 |
| DIRECTORY-CUSTOM-LIST | 展示型页面自建列表属可接受范式，非缺陷 | 后续加批量动作时回归 RePlusPage |
| MENU-VIEW-IMPORT | `get_view_permissions` 仅接受 URL 清单中存在的 view 字符串，未形成任意模块导入 | 无（复核排除误报） |

### 3.3 用户实测反馈修复（2026-10-29 晚）

| 根因键 | 修复摘要 | 主要落点 | 验证 |
|---|---|---|---|
| DASHBOARD-THEME-OVERRIDE | 快照导出误用 `@vueuse/core` 的 `useDark`，挂载即按系统偏好改写 `html.dark`，进入仪表盘整站变黑；改用 `@pureadmin/utils` 的只读 `useDark` | `views/dashboard/utils/useDashboardSnapshot.ts` | 新增 `e2e/dashboard.e2e.ts`「进入仪表盘不改变站点明暗主题」（相反系统偏好做诱饵） |
| SETTINGS-GUARD-RAW-KEY | 设置页守卫把 `localeName/title` 拼进命名空间，弹窗回显 `setting:settingLdap:unsaved` 等原始 key；统一为 `settingCommon` 并补齐 zh/en 词条 | `views/settings/components/settings-shell/SettingItem.vue`、`locales/{zh-CN,en}.yaml` | 新增 `e2e/ldap.e2e.ts`「未保存离开提示使用站点文案」；`pnpm check:i18n` |
| SETTINGS-AUTOFILL-DIRTY | 浏览器把站点登录凭据自动填进设置页「文本 + 密码」组合（实测 LDAP 绑定 DN/密码被填），dirty 判定误报；设置表单文本输入加 `autocomplete=off`、密码输入加 `autocomplete=new-password`，dirty 比较改用 `isEqual`（键序不敏感） | `views/settings/components/settings-shell/SettingItem.vue` | `settingItemEffective.spec.ts` 防填充用例；`e2e/ldap.e2e.ts` 属性断言 + 「未修改离开不弹确认」 |

## 4. 验证记录

- 后端定向：新增 P0/P1/P2 用例全部通过；覆盖搜索序列化、聊天附件、LDAP TLS、OAuth 出站/刷新、
  登录收口、邀请、关系计数、流式导出、上传配额、AI 审计、归档演练、审批提及等。
- 后端全量：`uv run pytest -q -n auto` **exit 0**（全绿；含 OTP 加密迁移与新增用例）。
  期间 `test_screen_data.py::test_stale_throttle_key_allows_push` 曾在并行高负载下偶发一次，
  单测隔离与整文件复跑均通过，最终全量重跑 exit 0，判定为时序敏感用例而非本次改动回归。
- 前端全量：`pnpm test:run` **491 个测试文件 / 3,082 项通过**；`pnpm typecheck` 通过；
  `pnpm lint:eslint`、`pnpm lint:prettier`、`pnpm lint:stylelint` 均通过。
- 后端静态：`ruff format --check .`（1958 files）与 `ruff check .` 通过；
  `makemigrations --check --dry-run` 输出 `No changes detected`（DB 主机不可达时仍完成模型态检查）。
- 门禁：`check_cross_app_imports`、`check_file_length`、`check_function_length`、
  `check_cache_keys --strict`、`check_ledger_ids`、`check_menu_titles`、`check_tutorial_mirror`、
  `check_doc_facts`、`check_doc_index`、`check_doc_paths`、`check_doc_size`、`check_adr_status`、
  `check_docs_site_nav` 全部通过。
- E2E：**已执行**。`node scripts/e2e-run.mjs smoke:fresh` 9/9 通过（独立 E2E 库重置 +
  daphne + Vite）；定向 `chat/config-persist/oauth/approval-delegation` 12/12 通过（chromium）。
  覆盖登录、权限菜单、聊天文本/图片/文件/私聊/群聊、配置持久化、OAuth 与审批委托入口。
  `analysis + dashboard` 组合跑 6/8：2 个时序敏感用例（大屏远程控制、卡片联动）在组合负载下
  失败，隔离复跑各自 1/1 通过；数据集 strict/inherit 与执行/聚合/过滤用例、前端构建均通过。
- E2E 实测反馈回归：`dashboard.e2e.ts + ldap.e2e.ts` 全量 **10/10 通过**（chromium，
  `E2E_FRONT_PORT=8849` 独立前端，未打断本机 dev server）；覆盖「暗色系统偏好下进入仪表盘不改站点主题」、
  「设置页未保存离开弹窗为中文文案、无原始 i18n key」、「未修改离开不弹确认」与绑定字段
  `autocomplete` 防凭据自动填充。两条 spec 的 ESLint 与 `pnpm typecheck` 同步通过。

## 5. 未验证 / 运行期事项

- 已由 E2E 覆盖：登录/权限菜单（smoke 9 项）、聊天文本/图片/文件/私聊/群聊、配置持久化、
  OAuth 入口、审批委托入口（定向 12 项）。
- 仍需专项/手工：菜单 SVG 恶意图标（构造性安全用例）、聊天 HTML 伪装附件（安全用例）、
  SafeFrame HTML 预览、设置页真实 SMTP/LDAP/IM 测试连接、WebSocket 4403 重连。
- SMTP/LDAP/IM/OAuth discovery 等外部服务未接真实端点；本轮验证为离线桩/单元/E2E 桩。
- 容器运行验证：已启动本地开发栈（`docker-compose up -d`，源码 bind mount 启动即加载新代码），
  `identity` 迁移 0004 与 `dataset` 迁移 0004 已应用，health 四项全绿（celery 首次探测后恢复 true），
  `manage.py doctor` 结果 11 通过 / 0 警告 / 0 失败；`dataset_field_permission_audit` 通过。
- 数据集共享字段权限：新建/切换共享默认 strict、个人/存量 inherit；前端生产构建通过，
  OpenAPI 契约镜像与生成类型已同步。
- 后端运行时代码改动后仍建议按迭代习惯滚动重启：`docker restart xadmin-server xadmin-celery-worker
  xadmin-celery-heavy`（改 Celery 任务/配置时再加 `xadmin-celery-beat`）。
