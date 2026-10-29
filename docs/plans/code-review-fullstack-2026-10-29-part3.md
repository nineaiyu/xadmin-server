# 全栈功能审查 part3：续审与后端补读

> 主篇：[code-review-fullstack-2026-10-29.md](code-review-fullstack-2026-10-29.md)（总览 / 主登记簿 / 门禁 / 批次 0–16）。
> 本文件承接 2026-10-29 的继续审查：对主篇标记为「部分」的后端 app 做补读，并覆盖
> `common` 内核剩余、`integrations` SDK 与前端共享层。状态与本文件结论共同构成审查全貌。

## 1. 续审范围与阅读记录

| 批次 | 范围 | 读取的文件 / 模块 | 完成项 | 新增问题 |
|---|---|---|---|---|
| 17 | common 内核剩余 + integrations SDK | `common/core/config/{base,conf_security,conf_upload,user_conf}.py`、`cache/{storage,redis,channel}.py`、`storage/{backend,utils}.py`、`utils/{media,sanitize,request,outbound,verify_code}.py`、`core/{models,fields,fields_related,mask,oplog_recorder,middleware,filter,permission_meta,throttle}.py`、`data_scope/*`、`auth/*`、`response/exception.py`、`settings_contract.py`、`contracts.py`、`metrics.py`、`tasks.py`、`drf/parsers/base.py`、`fields/image.py`、`modules/gate.py`；`integrations/sdk/im/*`、`sms/{endpoint,alibaba}.py`、`ai/{chat,async_chat,embeddings}.py` | 配置加密/缓存/存储、软删除与回收站、批量动作、字段与关联字段、导入导出流、脱敏与日志、出站守卫、IM/SMS/AI SDK | P1×1（XLSX 公式注入）、P2 候选 3 条 |
| 18 | identity 剩余 | `scim/{auth,views,resources,resources_group,guards}.py`、`ldap/{auth,client}.py`、`views/user/token.py`、`serializers/token.py`、`services/{token_issue,open_platform,open_oauth}.py`、`views/open/{open,open_oauth}.py`、`utils/{pat_scope,api_grant,account_risk,login_policy,session,account_expiry,login_alert}.py`、`views/admin/{passkey,account_risk}.py`、`views/user/authorized_app.py` | SCIM 用户/组同步、LDAP bind、PAT/应用凭证、OAuth 授权码与刷新轮换、账户风险处置、会话生命周期 | P1×1（LDAP StartTLS 顺序）、P2 候选 4 条 |
| 19 | ai + approval 剩余 | `ai/services/knowledge/{ai_knowledge,doc_extract,ai_embeddings,ai_retrieval}.py`、`ai/services/mcp/{mcp_client,ai_mcp_actions}.py`、`ai/views/{mcp,actions,knowledge}.py`、`ai/services/actions/{ai_actions,ai_api_actions,ai_action_audit,ai_action_target}.py`、`ai/services/assistant/{ai_usage,ai_guard,ai_mask}.py`；`approval/views/{approval_instance_actions,approval_instance_batch,approval_instance_comments}.py`、`approval/services/approval_flow/extra_actions.py` | 知识库分块/向量/检索、MCP 客户端与动作目录、AI 动作审计与审批、审批实例动作/批量/评论 | P1×1（AI/MCP 审计记录口令）、P2 候选 3 条 |
| 20 | system/task/audit/file 剩余 | `system/search.py`、`task/services/{_import,_export}.py`、`task/services/webhook/delivery.py`、`task/views/admin/{record_base,export,import_}.py`、`file/views/admin/file.py`、`file/utils/{upload_store,preview/office,cleanup}.py`、`audit/utils/log_archive.py`、`audit/serializers/log.py` | 全局搜索双权限门、异步导入导出、下载中心归属、上传/预览/清理、审计归档水位 | P2 候选 2 条 |
| 21 | dataset/notifications/settings/captcha 剩余 | `dataset/views/form_data.py`、`dataset/models/dform.py`、`dataset/utils/{dform_formula_parser,report_design,screen_layout}.py`、`notifications/tasks.py`、`notifications/models/notification.py`、`settings/views/{email,sms}.py`、`settings/utils/test_connection.py`、`captcha/{stats,telemetry}.py` | 表单数据权限/导出、公式与画布校验、通知扇出、SMTP/SMS 测试连接、验证码观测隐私口径 | 无新增（2 条既有候选复核仍成立） |
| 22 | 前端共享基础设施 | `utils/http/{index,accessToken,refreshToken,errorStrategies,pendingApproval,redirect}.ts`、`utils/{auth,aes,download,markdown,sanitize,websocket}.ts`、`store/modules/{user,notice}.ts`、`router/{index,publicRoutes}.ts`、`router/utils/{auth,async-routes}.ts`、`api/{base,types}.ts`、`components/{MarkdownView,AiMarkdown}` | 请求/刷新/401/412/429 策略、Cookie 与登录态、权限快照、Markdown/富文本净化、WS 重连、下载文件名 | 无新增 P0/P1；前端共享层静态面未发现越权/注入路径 |
| 23 | 收尾 | 本文件、主篇统计、索引、自检 | 新增发现与候选登记、文档门禁 | — |

### 1.1 后端 app 覆盖更新

- **已补读并收口核心**：`mfa`、`message`、`notifications`、`captcha`（协议/票据/风险/处置/服务/题型/统计/遥测）、`dataset`（数据集/仪表盘/报表/大屏/动态表单/公式/画布）、`approval`（引擎/动作/委托/规则/请假/批量/评论）、`ai`（NL/动作/MCP/知识库/向量/用量/护栏）、`common`（配置/缓存/存储/ORM 基类/序列化器/权限/数据权限/认证/出站/日志/契约）。
- **仍为部分（非核心文件未逐行读）**：`ai` 的档案/观测视图余量、`audit` 的归档命令细节、`file` 的分片上传余量、`identity` 的 SCIM 同步/部分序列化器、`system` 的部分管理视图、`task` 的任务中心/周期余量；这些不影响本文件已登记的 P1 判断，但不等同于「后端 100% 文件已读」。`demo`/`demo_seed`/`devtools`/`integrations` 仍按非业务面处理。
- **前端**：页面 84/84 已在 part2 收口；本批补读共享层（请求、认证、路由、WS、Markdown/富文本、权限快照），未逐行读布局与全部通用组件。

## 2. 续审新增发现登记簿

### 2.1 P1

| 编号 | 级别 | 标题 | 证据 | 影响 | 根治 / 验证 |
|---|---|---|---|---|---|
| LDAP-P1-01 | P1 | LDAP StartTLS 在 bind 之后执行，目录口令先明文发送 | `xadmin-server/identity/ldap/client.py:130-143`：`Connection(..., auto_bind=True)` 先完成明文 bind，随后才 `conn.start_tls()`；`user_connection`（:146-152）同样先以用户 DN+口令 bind 再 StartTLS。ldap3 的 `auto_bind=True` 等价 `AUTO_BIND_NO_TLS`（`.venv/.../ldap3/core/connection.py:382-401`）；正确形态是 `AUTO_BIND_TLS_BEFORE_BIND` 或 `auto_bind=False` → `start_tls()` → `bind()`。 | `LDAP_START_TLS=true` 时：服务账号 bind 口令与用户口令在 TLS 建立前已走网络；同时「bind 后 StartTLS」在要求先加密的目录上会失败，LDAP 登录整体退化。影响所有启用 StartTLS 的部署。 | 把 TLS 前移到 bind 之前（两种连接同一修改），并用 fake connection 断言调用顺序 `start_tls` → `bind`；补 `LDAP_START_TLS=true` 登录集成用例。 |
| AI-P1-01 | P1 | AI 动作 / MCP 调用审计把口令等敏感参数原样落库 | `xadmin-server/ai/services/actions/ai_action_audit.py:19-36` 将 `params` 原样写入 `OperationLog.changes`；`identity/ai_declarations.py:16-34` 的 `user.create` 参数含 `password`；`ai/views/actions.py:350-362` 执行成功后以 `clean`（含口令）调用审计。MCP 侧 `ai/services/mcp/mcp_client.py:459-498` 同样把 `arguments` 原样落库。`audit/services/recorder.py` 只做序列化/截断，不做字段脱敏；`audit/utils/mask.py:171-211` 仅处理带 `model_label` 的 `{field:{old,new}}` 形态，对 AI 审计的 `params` 不生效。 | 任何能查看操作日志（含导出）的人可读到 AI 创建用户的初始口令；外接 MCP 工具参数中的 token/密钥同理。属于凭证落库泄露，不是仅日志噪音。 | AI/MCP 审计写入前统一过 `desensitize_payload`（至少 `password/token/secret/api_key`），并补单测断言 `changes` 不含口令原文；`audit.services.record` 增加 `sensitive=True` 的调用口径或集中脱敏。 |
| EXPORT-P1-01 | P1 | 同步/异步 XLSX 导出默认路径存在公式注入 | `xadmin-server/packages/xadmin-common/common/core/modelset/import_export/streaming.py:76-96`：`sheet.append(cells)` 直接把字符串写入 write-only 工作簿，未设置单元格类型；项目内 openpyxl 实测 `Workbook(write_only=True)` + `append(["=1+1"])` 保存后 `data_type='f'`（公式）。对照 `common/drf/renders/excel.py:41-44` 的旧渲染器显式 `cell.data_type="s"`，CSV 路径 `common/utils/csv_safe.py:23-30` 也做了前缀转义——默认 `export-data` 走流式路径（`import_export/export_actions.py:36-58`），异步导出的视图重放同样落到该路径。 | 用户可控单元格（用户名、文件名、公告标题、聊天文本等）以 `=` 开头时被 Excel/WPS 当公式执行；结合 DDE/外链可造成打开者本机命令执行或数据外带。默认导出路径受影响，且与两条已加固路径不一致。 | 流式写单元格前把字符串前缀 `= + - @`（或至少 `=`）按 CSV 同口径转义/前置单引号，或显式设置只写单元格 `data_type="s"`；补 xlsx 公式注入用例（读取导出文件断言 `data_type` 非 `f`）。 |

### 2.2 P2 候选（续审，未编号）

| 候选 | 根因键 | 证据 | 影响 / 建议 |
|---|---|---|---|
| OAuth 刷新令牌并发轮换可双发 | OAUTH-REFRESH-RACE | `identity/services/open_oauth.py:255-278`：两个并发请求可同时读到 `is_revoked=False` 的行，`_revoke_refresh_row`（:210-217）无条件更新，二者都签发新双令牌；无 `select_for_update` / 条件 UPDATE 判定。 | 窃取 refresh token 的攻击者可与正常客户端并发换发，获得并行长效会话；建议 `select_for_update` + 条件 `update(is_revoked=False)`，并补并发用例。 |
| SCIM 建号口令绕过密码策略 | SCIM-PASSWORD-POLICY | `identity/scim/resources.py:226-242`：`payload["password"]` 直接 `set_password`，未走 `check_password_rules` / 泄露库 / 历史密码；对照注册与重置链路。 | 有 SCIM 凭证的 IdP 可创建弱口令/已泄露口令账号；建议与本地建号同口径校验，或只接受不可用密码并强制联邦登录。 |
| 账号风险处置可停用/踢超管 | ACCOUNT-RISK-SUPERUSER-UNGUARDED | `identity/utils/account_risk.py:296-336` 的 `disable` / `force_logout` 直接操作 `risk.user`；`identity/views/admin/user.py:155-159` 对超管有 `_assert_target_mutable`，但风险处置入口无同等保护。 | 被授予 `handle:SystemAccountRisk` 的非超管可停用超管并踢线；建议复用同一 guard（或明确超级管理员豁免策略）并补用例。 |
| 审批评论 @ 提及不按实例可见域收敛 | APPROVAL-MENTION-SCOPE | `approval/views/approval_instance_comments.py:36-61` 按用户名查任意启用用户，`96-104` 直接发 `mentioned` 通知；未校验被提及者是否可见该实例。 | 有实例评论权限的用户可通过 @ 外泄审批标题/摘要给无关用户；建议提及候选限定实例参与人/可见域，通知正文最小化。 |
| 审计归档水位不校验归档文件完整性 | ARCHIVE-WATERMARK-NOVERIFY | `audit/utils/log_archive.py:187-188` 的 `archived_months` 只读 manifest；`archive_month`（:125-127）仅判断 gz/manifest 存在即 skip；`archive_watermark`（:288-292）据此推进删除水位。`verify_archive`（:351-374）存在但不在水位链路调用。 | gz 丢失/损坏而 manifest 仍在时，清理会删除未真正归档的日志；建议水位判定调用 `verify_archive`，失败即停在水位月初。 |
| 关联字段非法 UUID 可打成 500 | RELATED-PK-VALIDATION-500 | `packages/xadmin-common/common/core/fields_related.py:252-283` 的 `to_internal_value` 只捕获 `(TypeError, ValueError)`；UUID 主键的 `queryset.get(pk="bad")` 抛 `django.core.exceptions.ValidationError`（实测 `Menu._meta.pk` 过滤报 “不是有效的UUID”），不被捕获。 | 任一 UUID 关联字段的写入/联想请求可被非法输入打成 500；建议捕获 `DjangoValidationError` 并按 `incorrect_type` 400。 |
| suggestions 的 pks 参数未做 UUID 归一 | SUGGEST-PKS-UUID-500 | `packages/xadmin-common/common/core/modelset/suggest.py:121-123` 把查询参数原样 `filter(pk__in=pks[:50])`；UUID 模型非法值在过滤构建期抛 `ValidationError`（实测 `Menu.objects.filter(pk__in=["bad"])`）。 | 已登录用户可令联想接口 500；建议复用 `normalize_pks` / 捕获 `ValidationError` 返回 400。 |
| 异步导入在对象存储后端读取本地 `.path` | ASYNC-IMPORT-S3-PATH | `task/services/_import.py:145-165` 用 `record.source_file.filepath.path` 读取行 JSON；`common/storage/backend.py` 的 S3 委托无本地 `path`。 | `FILE_STORAGE_BACKEND=s3` 时异步导入任务直接失败；建议走 `storage_local_path` / `storage_open` 适配层，补 S3 配置下的导入用例。 |
| MCP SSE 响应无总量上限 | MCP-SSE-UNBOUNDED | `ai/services/mcp/mcp_client.py:237-254` 的 `_read_sse` 逐行读取直到匹配 id，未使用 `_read_limited` 的 `MAX_BODY_BYTES`；JSON 分支有上限（:228-235）。 | 被配置的 MCP 服务可流式返回超量数据拖垮工作进程；建议 SSE 也累计字节并在超限时抛 `McpClientError`。 |
| 知识库 DOCX 解压后体积无上限 | AI-DOCX-ZIP-BOMB | `ai/services/knowledge/doc_extract.py:47-86` 只限制压缩前 2MB，`python-docx` 解压 XML 无展开上限。 | 高压缩比 DOCX 可在解析时占用大量内存/CPU（管理端上传面）；建议限制解压后 XML 总量或改用受限解析器，并补坏包用例。 |

## 3. 复核与证据

- **运行时探针**：`openpyxl` write-only `append` 的公式单元格行为（项目 venv 实测 `data_type='f'`）；
  UUID 字段非法值过滤在 `Menu` 模型上实测抛 `django.core.exceptions.ValidationError`；
  `AESCipherV2` 坏 base64 异常链路沿用主篇候选证据。
- **静态复核**：本批所有 P1 均有源码行号 + 推断链；未把「管理员可配置的内网/回环放行」
  「外部 IdP 不可达」等配置面误报为漏洞。既有 P0/P1（`SEC-P0-01`、`DATA-P1-01`、
  `AUTH-P1-01`）结论不变。
- **未验证**：LDAP 真实目录、SCIM IdP、OAuth 并发、对象存储、LibreOffice、外部 MCP、
  XLSX 在 Excel/WPS 的实际公式执行、前端真实浏览器交互，均为 C 级运行期事项；
  需在修复后的真实栈验证。

## 4. 结论

续审未推翻主篇结论；新增 3 条 P1 与 10 条 P2 候选，主篇已登记 2 条 P1 与 14 条候选。
合计：**P0 1 / P1 5 / P2 候选 24**（P2 仍未编号，按根因合并后再决定是否升级）。
本文件只做审查记录，未修改源码、配置、种子、迁移或测试；未执行 git commit。
