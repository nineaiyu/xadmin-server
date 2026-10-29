# 全栈功能审查 part4：全量补读收口

> 主篇：[code-review-fullstack-2026-10-29.md](code-review-fullstack-2026-10-29.md)。
> 承前：[part2](code-review-fullstack-2026-10-29-part2.md)（页面 84/84）、
> [part3](code-review-fullstack-2026-10-29-part3.md)（批次 17–23）。
> 本文件收口 2026-10-29 的「所有文件」补读：后端 common 内核 / integrations /
> demo / demo_seed / devtools，前端全部剩余运行时源文件；并登记本轮新增的
> P0×1、P1×3 与 P2 候选×24。状态与本文件结论共同构成审查全貌。
> 整改执行与验证状态见 [整改台账](code-review-remediation-2026-10-29.md)。

## 1. 补读范围与覆盖台账

| 范围 | 文件数 | 行数 | 读取方式 | 结论 |
|---|---:|---:|---|---|
| `packages/xadmin-common/common` | 192 | 24,835 | 逐文件全文（排除 `__pycache__`、迁移空壳按模型结构核对） | 覆盖配置/缓存/存储/ORM 基类/序列化器/权限/数据权限/认证/出站/日志/契约/模块裁剪/任务/服务监督/管理命令 |
| `integrations` | 15 | 1,433 | 逐文件全文 | IM / SMS / AI SDK；出站地址均为固定供应商域名；发现流式重试连接未关闭候选 |
| `demo` | 15 | 888 | 逐文件全文（含迁移） | 示例业务闭环（审批回写/回收站/导出）；无新增业务 P0/P1 |
| `demo_seed` | 16 | 3,399 | 逐文件全文 | 演示数据命令；发现 `seed_demo_users --reset` 清理面过宽候选 |
| `devtools` | 26 | 3,186 | 逐文件全文 | 代码生成器；发现批处理字段覆盖串用、生成字段含 `deleted_at` 候选 |
| 前端 `src/**` 非测试源文件 | 1,573 | — | 逐文件全文/分批并行（排除 489 个测试文件、样式与 1.8MB 生成 `src/api/types/openapi.d.ts`） | 84 个页面入口沿用 part2 结论；本轮补读 api/router/store/hooks/utils/layout/components/views 子目录共 1,413 个运行时文件，余下为类型声明、barrel、静态图标数据与纯展示壳，已核对无逻辑承载 |

**范围口径（明确声明）**：测试文件（后端 `tests/`、前端 `__tests__`/`*.spec.*`）、
迁移生成物、构建产物、样式文件、生成的 OpenAPI 类型文件按「验证/生成产物」排除；
这与主篇「后端业务代码逐文件阅读、迁移与测试不作为审查源」的口径一致。运行时
源码本轮已补到「非测试文件全覆盖」，不再保留上一轮「非核心文件未读」的尾巴。

**前端重点面确认**：`v-html`/`innerHTML` 消费点仅 ReIcon SVG、MarkdownView、
AiMarkdown、通知 Markdown 预览与 ArtifactPreview；除 ReIcon 外均经
`utils/sanitize.ts` 或 `escapeHtml`。发现的新增 XSS 面见 §2 `ICON-P1-01`。
请求路径级契约由 `node scripts/check-api-routes.mjs` 复核通过（958 条去重路径，
缺失 0），未发现路径/方法级漂移。

## 2. 新增 P0 / P1 登记簿

### 2.1 P0

| 编号 | 级别 | 标题 | 证据 | 影响 | 根治 / 验证 |
|---|---|---|---|---|---|
| CHAT-P0-01 | P0 | 聊天附件按客户端 MIME 内联返回，可构造存储型 XSS | `xadmin-server/message/chat/attachments.py:66-82` 的 `attachment_kind` 先按 `guess_upload_category(filename, mime_type)` 判类；`file/utils/upload_category.py:123-140` 在 **MIME 为 `text/html` 但文件名以 `.mp4` 结尾** 时仍返回 `video`（扩展名兜底）。`attachments.py:177-184` 对 video/audio 以 `content_type=(upload.mime_type ...)` **inline** 返回，只加 `X-Content-Type-Options: nosniff`；`nosniff` 只禁止嗅探，不阻止已声明为 `text/html` 的文档执行脚本。`file/utils/upload_store.py:181,194` 把客户端 `file_obj.content_type` 原样落库；上传扩展名黑名单不拦截 `.mp4`。CSP 默认 `report-only`（`server/conf/settings_defaults.py:208`），不阻断脚本。 | 任意能发聊天附件的账号（演示角色已有 `upload:ChatMessage`）上传「HTML 内容 + `.mp4` 文件名 + `Content-Type: text/html`」，按 video 消息发送；任一有权限打开该附件的成员访问 `/api/chat/message/{pk}/file` 时，脚本在应用同源执行。前端令牌存于 JS 可读 Cookie（`xadmin-client/src/utils/auth.ts:13-35`，非 HttpOnly），可被直接读取并接管会话。 | 附件响应不使用客户端 MIME：音/视频按服务端检测的媒体类型固定白名单（如 `video/mp4`/`audio/mpeg`）下发，未知一律 `attachment`；上传侧对「扩展名与 MIME/魔数不一致」拒绝或强制归为 file。补用例：`.mp4` + `text/html` 上传后取件响应必须 `Content-Disposition: attachment` 或 `Content-Type` 不在可执行族；并在 E2E 中打开附件断言不执行脚本。 |

### 2.2 P1

| 编号 | 级别 | 标题 | 证据 | 影响 | 根治 / 验证 |
|---|---|---|---|---|---|
| ICON-P1-01 | P1 | 菜单 SVG 图标未净化，存储型 XSS 可打到超管会话 | `xadmin-client/src/components/ReIcon/src/hooks.ts:30-49`：字符串以 `<svg` 开头即 `innerHTML: cleanedSvg`，清理仅删除 `width`/`height` 属性，无 DOMPurify/白名单。数据源 `system/models/menu.py:19` 的 `icon` 为普通 `CharField(255)`，`system/serializers/menu.py:22-28` 的 `MenuMetaSerializer` 未声明 `validate_icon`；消费点 `layout/components/lay-sidebar/NavVertical.vue:224`、`components/SidebarBreadCrumb.vue:167` 等把 `meta.icon` 直接交给 `useRenderIcon`。 | 持有 `create:SystemMenu`/`partialUpdate:SystemMenu` 的角色可在图标字段写入 `<svg onload=...>`；该菜单渲染到任何用户（含超管）的侧边栏时脚本在同源执行。配合 JS 可读令牌 Cookie，可完成从「菜单管理员」到「超管会话」的越权。即使只有超管能改菜单，也构成「一次写入、多人触发」的持久化攻击面。 | 前端内联 SVG 先过 `sanitizeHtml`（标签/属性白名单，禁 `on*`、`script`、`foreignObject`、外链 `href`），或仅接受 iconify 名称；后端 `MenuMetaSerializer.validate_icon` 校验形态（iconify 名/受控 SVG 白名单），并补序列化器用例。补一条组件测试：`useRenderIcon('<svg onload=alert(1)>')` 渲染后 DOM 不含 `onload`。 |
| OAUTH-P1-01 | P1 | OAuth/OIDC 出站守卫无条件放行环回地址 | `xadmin-server/identity/utils/oauth.py:342-355` 的 `_pinned_request` 固定传 `allow_loopback=True`，而 Webhook/AI/MCP 使用 `loopback_allowed()`（仅 `DEBUG` 或 `WEBHOOK_ALLOW_LOOPBACK`）；同文件 `:177-189` 的配置校验调用 `validate_outbound_config_url`，该函数默认 `allow_loopback=True`（`common/utils/outbound.py:396-400`）。`settings/views/oauth.py:116` 的 OIDC discovery 测试直接用请求提交的 provider 触发 `discovery_metadata`。 | 具备设置/OAuth provider 编辑权限的管理员（含被委派的二开角色）可让服务端访问 `http://127.0.0.1:<port>` 等环回目标，探测/读取本机管理端口（Redis/内部 HTTP 服务等），形成认证后的 SSRF；与全仓「出站默认拒绝环回」约定不一致，安全基线随模块漂移。 | `_pinned_request` 与配置校验统一改用 `loopback_allowed()`，仅显式白名单放行；`discovery_metadata` 在发送前再做一次归属校验。补参数化用例：默认配置下 `http://127.0.0.1:6379` 被 `OutboundBlocked` 拒绝，`WEBHOOK_ALLOW_LOOPBACK=true` 才放行。 |
| SETTING-P1-01 | P1 | 保存密钥后「测试连接」把掩码当真实密钥，测试必失败 | `xadmin-client/src/views/settings/components/settings-shell/SettingItem.vue:222-228` 把 PATCH 响应整体并入 `formData`；后端 `settings/views/settings.py:165-168` 对加密字段回填 `MASK="••••••"`。`:238-249` 的「测试连接」直接提交 `addOrEditData.value.formData`；`settings/utils/test_connection.py:37-40` 对 write_only 密钥「提交非空即覆盖」，掩码是非空字符串，于是用掩码建连。 | 邮件/IM/短信/Webhook/LDAP 设置页「先保存新密钥、再点测试连接」必定失败；用户看到的是「连接失败」而非「密钥已保存」，容易反复改配置。保存本身不落掩码（后端写入侧丢弃），但测试结果误导，属于设置页核心操作链路阻断。 | 合并保存响应时剔除 write_only/MASK 字段（或仅用于展示占位、不进入测试 payload）；测试请求只带用户本次实际输入的密钥，空值表示沿用已存值。补组件用例：保存响应含 MASK 后点击测试，请求体不含 MASK；后端补测试连接用例：掩码值按空处理。 |

## 3. P2 候选池（本轮新增 24 条）

> 候选不等于登记；按根因合并后再决定升级。每条均给静态证据与最小修复方向。

### 3.1 后端 / 架构

| 候选 | 根因键 | 证据 | 影响 / 建议 |
|---|---|---|---|
| 批量/联想/导入入口未归一主键类型 | PK-NORMALIZE-MISSING | `common/core/modelset/batch.py:51-57`（rank 直接 `pk__in=pks`）、`impact.py:51`、`import_export/actions.py:101-103`、`modelset/suggest.py:121-123` 均未复用 `normalize_pks`；UUID 模型非法输入在过滤构建期抛 `ValidationError`。 | 已登录用户可用非法主键把排序/影响面/联想/同步更新打成 500；建议统一走 `normalize_pks` 或捕获 `DjangoValidationError` 返 400。 |
| 文件配额只看活跃行，软删文件仍占存储 | UPLOAD-QUOTA-SOFTDEL | `file/utils/upload_store.py:116-154` 用 `UploadFile.objects`（默认管理器排除软删）统计配额；`common/core/models.py:243-247` 软删除只置 `deleted_at`，物理文件保留到回收站彻底清除。 | 用户可「上传 → 删除 → 再上传」循环，界面配额始终不涨而磁盘持续增长；建议配额按 `all_objects` 或物理文件占用统计，并在回收站 purge 时释放。 |
| 数据集执行对未配置字段权限的用户放开全字段 | DATASET-FIELD-FAIL-OPEN | `dataset/utils/dataset.py:394-428`：角色没有任何该模型字段权限时返回 `None`，执行/聚合不裁剪；而同仓 CRUD 序列化器对同场景 fail-closed（零字段）。 | 有 `execute:DataDataset` 的普通用户可经数据集读取 CRUD 页面被字段权限隐藏的列；建议数据集侧也 fail-closed，或提供显式「数据集字段授权」并在元数据提示。 |
| `dataset.aggregate` 声明的指标/时间粒度与后端能力不一致 | DATASET-AI-METRIC-MISMATCH | `dataset/ai_declarations.py:145-176` 广告 `max/min` 与 `date_trunc year`；后端聚合实现只接受 `count/sum/avg` 与 `day/month`（`dataset/utils/dataset.py` 聚合分支）。 | AI 助手/外部调用按声明生成必然失败的查询；建议声明与实现同源，补闭包测试。 |
| 他人重跑报表导出，记录归属仍是原作者 | RERUN-REPORT-OWNER | `task/utils/task_center_unified.py` 的 `_rerun_report` 用 `_precreate_record(report)`，`creator=report.creator`；操作者只体现在执行上下文。 | 重跑者看不到自己的任务记录，原作者看到非本人发起的任务；建议 `creator=当前操作者`，保留 `source_creator` 审计字段。 |
| 邀请无邮箱用户后账号不可登录但提示成功 | INVITE-NO-EMAIL-LOCKOUT | `identity/utils/user_invite.py:98-124` 无论用户是否有邮箱都置 `invite_status=PENDING` 并设不可用密码；管理端收到「邀请已发送」。 | 无邮箱用户既收不到邀请也无法用密码登录，形成静默锁号；建议无邮箱时拒绝邀请并提示先补邮箱，或改为「待激活」可撤销状态。 |
| OTP 密钥明文落库 | OTP-SECRET-PLAINTEXT | `identity/models/user.py:75-79`：`otp_secret_key` 为普通 `CharField(64)`，注释「无需加密存储」。 | 数据库只读泄露即可批量生成 TOTP；建议复用字段级 `signer`（v3）加密存储，读取侧兼容旧明文并迁移。 |
| search-fields 关联下拉先全量求值再截断 | SEARCH-FIELDS-ALL-CHOICES | `common/core/modelset/metadata.py:203-211`：先 `list(widget.choices)` 全量求值，之后才按 `SEARCH_CHOICES_MAX_COUNT` 截断。 | 大表关联字段的元数据请求会一次性装载全表选项，内存/DB 压力与表规模线性增长；建议在 `ModelChoiceIterator` 层限制 queryset 或改用远程搜索默认。 |
| 演示用户 `--reset` 清理范围大于命令自身 | DEMO-USERS-RESET-SCOPE | `demo_seed/management/commands/seed_demo_users.py:55-57` 的 `--reset` 用 `username__startswith="demo_"` 删除；同文件 `:38-47` 的 `--clean-only` 只删 `^demo_[0-9]+$`，注释明确「不触碰 demo_flow_* 等命令专用账号」。 | 单独执行 `seed_demo_users --reset` 会删掉其他 seed 命令创建的演示用户，后续演示链路断链；建议 reset 复用 `_remove_batch_users` 的精确正则。 |
| 代码生成器批量 ZIP 把单模型字段覆盖套到所有模型 | CODEGEN-BATCH-OVERRIDE-MIX | `devtools/services/codegen/gui.py:287-310`：批量分支 `single = {**payload, "model": str(raw)}`，未移除 `payload["fields"]`；`_prepare` 仍调用 `apply_field_overrides`。文档声称批量忽略字段级覆盖。 | 批量生成时首个模型的字段名/字典绑定会作用于其余模型，未知字段直接 `CodegenError`，同名但语义不同的字段被错误改写；建议批量分支显式剔除 `fields/include_fields/exclude_fields`。 |
| 生成的序列化器把 `deleted_at` 当可写字段 | CODEGEN-DELETED-AT-WRITABLE | `devtools/management/commands/_generate_crud/field_plan.py:39-48` 只排除主键与 `AUDIT_FIELDS`（creator/modifier/dept_belong），软删模型的 `deleted_at` 进入 `serializer_fields`；DRF 对可空 DateTimeField 默认可写。 | 生成物允许客户端 PATCH `deleted_at`，可绕过删除审批/回收站语义隐藏或复活数据；建议生成器把 `deleted_at` 加入审计只读集，或在序列化器显式 `read_only`。 |
| 导出 ZIP 加密口令固定为用户名 | EXPORT-ZIP-USERNAME-PASSWORD | `common/drf/renders/base.py:313-337`：`secret_key = request.user.username`，注释「默认密码是用户名，后期可配置」。当前仅 `identity/views/admin/user.py:123` 注释示例，无生产视图开启 `export_as_zip`。 | 一旦启用，任何知道用户名的人可解密导出包；建议改为个人加密口令/一次性随机口令，并在响应中安全下发。 |
| 异步流式聊天 5xx 重试丢弃未关闭的流响应 | ASYNC-CHAT-STREAM-LEAK | `integrations/sdk/ai/async_chat.py:110-135,149-182`：`_send_with_retry` 对 5xx 直接 `continue`，已打开的 `_OwnedStream` 未 `aclose_all`；同步 `chat.py:167-199` 同样不关闭 `stream=True` 响应。 | 供应商持续 5xx 时可累积未关闭连接/文件描述符；建议重试前关闭 response，或用 `async with` 管理每次尝试。 |
| multipart 点分键递归无深度上限 | AXIOX-FORM-RECURSION | `common/drf/parsers/axios_form_data.py:67-100`：`format_data` 按 `.` 递归拆分键，请求体键可含上千个点。 | 认证用户可构造深键触发 `RecursionError`（500），消耗 worker；建议限制键深度（如 ≤10）或改迭代解析。 |

### 3.2 前端

| 候选 | 根因键 | 证据 | 影响 / 建议 |
|---|---|---|---|
| 站点配置保存不判业务码，失败仍提示成功 | SITE-CONFIG-FALSE-SUCCESS | `src/store/modules/siteConfig.ts:248-258` 只 `.then` 即提示成功并 `captureSyncedSnapshot`，未判 `res.code`；后端非超管改站点级键返回 HTTP 200 + `code=1001`（`system/views/user/configs.py:144-158`）。 | 普通用户改主题/布局被拒却显示成功，刷新即丢；建议 `isSuccess(res)` 后再提示/写快照，失败保留 dirty。 |
| 我的视图保存失败后按钮永久 loading | SAVED-VIEWS-LOADING-HANG | `src/components/RePlusPage/src/components/SavedViews.vue:142-172`：`sureBtnLoading: true`，`beforeSure` 在 `getPayload()` 为空或 `!isSuccess(res)` 时直接 `return`，不调用 `done()`/`closeLoading()`；`src/components/overlay/useOverlayFooter.ts:93-112` 只在 `done/closeLoading` 复位。 | 名称为空或后端报错后保存按钮永久 spinner，用户改对也无法重试；建议所有提前返回分支调用 `closeLoading()`。 |
| 音/视频消息重发丢失附件类型 | CHAT-RESEND-ATTACHMENT-LOSS | `src/views/chat/hooks/useChatSend.ts:88-103` 仅对 `image|file` 附带 `message_type/file_pk`；`src/api/chat/index.ts:59-67` 的附件类型含 `video|audio`。 | 视频/音频发送失败后重发只发文件名文本，附件不可恢复；建议按附件类型集合判断并复用 `item.message_type`。 |
| 切换会话时上翻历史串房 | CHAT-HISTORY-RACE | `src/hooks/useHistoryPagination.ts:69-95` 的 `loadMore` 在 `await` 后无 `historySeq` 复核；`loadHistory`（:43-67）有守卫。`useChatLifecycle.ts` 切会话时清空并重载。 | A 房上翻慢响应会前插到 B 房消息区，后续 `before_id` 也取错；建议响应后比对序号丢弃过期页。 |
| 文件上传中即 emit `{pk: undefined}` | UPLOAD-FILES-UNDEFINED-PK | `src/components/RePlusPage/src/components/UploadFiles.vue:133-148` 对全量 `fileList` 映射 `{pk:item.pk}`，上传中/失败项无 pk；进度变化触发 deep watch。 | 保存时载荷含空对象/undefined，可能清空已选附件或报校验错；建议过滤 `pk != null` 后再 emit。 |
| SafeFrame 默认 sandbox 同时开 scripts 与 same-origin | SAFE-FRAME-SANDBOX | `src/components/SafeFrame/index.vue:22-29` 默认 `allow-scripts allow-same-origin allow-forms allow-popups`；`src/views/system/file/components/PreviewDrawer.vue:120-129` 用 `blob:` 预览；`src/views/analysis/screen/components/PaneMediaView.vue:64-75` 同类。 | 同源文档可逃逸沙箱；当前预览产物多为 PDF，但一旦预览类型误判/扩展为 HTML 即成 XSS；建议默认不含 `allow-same-origin`，可信场景显式开启。 |
| 报表保存期间可继续编辑，成功后被旧快照覆盖 | REPORT-SAVE-EDIT-LOSS | `src/views/analysis/report/utils/useReportPersistence.ts:59-76` 保存前取 `payload`，成功后 `deps.design.value = payload`；`designer.vue:269-278` 侧栏仅按 `!preview && !readonly && !datasetMissing` 显示，未锁 `saving`。 | 保存 RTT 内的新编辑被静默回滚且 `dirty=false`，用户丢失改动；建议照搬大屏 `editSeq` 守卫或保存期禁用编辑。 |
| WS 4403 后心跳仍触发即时重连 | WEBSOCKET-4403-RECONNECT | `src/utils/websocket.ts:246-252` 对 4401/4403 直接 `return`，未清 `timer`/置 `socketOpen=false`；`:320-332` 的 `send` 在 CLOSED 时调用 `connect()`，下一次心跳（默认 10s）即重连。 | 权限被拒后仍每心跳重连一次，服务端持续拒绝日志，违背注释「停止退避循环」；建议该分支 `close()` 后再返回。 |
| AI 动作确认卡明文展示 password 参数 | AI-CARD-PARAM-PLAINTEXT | `src/components/AiActionCard/index.vue:40-55,66-98` 对 `draft.params` 全量渲染，`password` 仅映射为标签；执行前的确认卡会显示明文口令，草稿随消息落库。 | 口令进入聊天记录/多人可见房间；建议确认卡对 `password/token/secret` 掩码，仅允许重新输入。 |
| 通知非公告类型不净化 HTML | NOTICE-HTML-SANITIZE-BRANCH | `src/store/modules/notice.ts:136-160` 仅在 `notice_type.value===0` 且内容像 HTML 时 `sanitizeHtml`；其他类型保留原始 `message` 却设置 `dangerouslyUseHTMLString: true`。 | 服务端当前已净化，客户端第二道防线缺失；建议所有 `dangerouslyUseHTMLString` 路径统一净化。 |
| 仪表盘新增卡片在非安全上下文崩溃 | DASHBOARD-RANDOMUUID | `src/views/dashboard/utils/useCardDialog.ts:26-37` 直接 `crypto.randomUUID()`；`src/utils/auth.ts:32-53` 明确支持 HTTP 内网（`isSecureContext=false`）。 | HTTP 部署下点「添加卡片」抛 TypeError；建议与 report/screen 一致使用 `globalThis.crypto?.randomUUID?.()` 并回退时间戳/UUID 库。 |
| 批量 ZIP 导出同名卡片条目互相覆盖 | SCREEN-EXPORT-NAME-COLLISION | `src/views/analysis/screen/utils/screenExportFiles.ts:54-70` 以卡片名生成 ZIP 条目，未去重；`src/utils/imageExport.ts:224` 注释要求调用方去重。 | 解压后同名图片互相覆盖，导出缺图；建议按序号/ID 补后缀。 |
| 报表轮询离开页面不取消 | REPORT-RUN-POLLING-LEAK | `src/views/analysis/report/utils/reportRunPolling.ts:12-47`、`hook.tsx:63-69` 固定 15×2s 轮询，无 `onUnmounted` 取消；长任务固定超时。 | 离开页面仍请求并弹 toast，长任务误报失败；建议用 `AbortController`/序号取消，并按后端状态延长。 |

## 4. 根因归并

1. **服务端 MIME/扩展名双口径**（CHAT-P0-01）：附件「种类」按扩展名兜底、「响应类型」按客户端 MIME，
   两者可被同一请求分别操纵；同类面还包括媒体 `/media/` 与对象存储代理。根治应统一到服务端魔数检测。
2. **出站守卫按模块各写一遍**（OAUTH-P1-01）：Webhook/AI/MCP 用 `loopback_allowed()`，OAuth 固定
   `True`；与主篇 AUTH-P1-01「登录入口各写一遍」同构，属于「安全策略未收口到唯一入口」。
3. **前端展示层信任后端字符串**（ICON-P1-01、通知 HTML、AI 参数卡）：`icon`/富文本/动作参数在
   前端直接进入 `innerHTML` 或 HTML 通知；服务端缺少字段形态校验时，前端就是最后一道缺口。
4. **保存链路「成功」判定不统一**（SETTING-P1-01、SITE-CONFIG-FALSE-SUCCESS、SAVED-VIEWS-LOADING-HANG）：
   有的判 `isSuccess`，有的只看 Promise resolve；加载态/成功态/错误态没有统一收口，导致假成功与悬挂。
5. **生成器与运行期契约分叉**（CODEGEN-BATCH-OVERRIDE-MIX、CODEGEN-DELETED-AT-WRITABLE）：
   生成器是二开入口，生成物必须默认满足权限、审计、软删约定；当前把 `deleted_at` 与字段覆盖当作普通字段。

## 5. 证据与未验证

- **A 级静态证据**：§2 的 1 个 P0 与 3 个 P1 均有源码行号 + 调用链；`CHAT-P0-01` 的关键前提
  （`.mp4` + `text/html` 被判 video、响应按 `upload.mime_type` inline）已逐行复核。
- **B 级交叉证据**：前端 `check-api-routes` 通过；后端 `ruff` / 门禁与 `makemigrations --check`
  沿用主篇 §7 结论（本轮未改动源码，无新增漂移）。
- **C 级未验证**：真实浏览器中的附件脚本执行、菜单图标注入、设置页测试连接、OAuth discovery
  环回、演示数据命令在真实库的副作用，均需在修复后的真实栈验证；本轮为只读审查，未启动
  docker / 浏览器 / 外部服务。
- **未纳入**：测试文件、迁移生成物、构建产物、样式与 `openapi.d.ts` 生成文件；测试缺口在
  各条修复建议中单列。

## 6. 统计更新

| 项 | 主篇 | part3 | part4 | 合计 |
|---|---:|---:|---:|---:|
| P0 | 1 | 0 | 1 | **2** |
| P1 | 2 | 3 | 3 | **8** |
| P2 候选 | 14 | 10 | 24 | **48** |

- P0：`SEC-P0-01`、`CHAT-P0-01`。
- P1：`DATA-P1-01`、`AUTH-P1-01`、`LDAP-P1-01`、`AI-P1-01`、`EXPORT-P1-01`、
  `ICON-P1-01`、`OAUTH-P1-01`、`SETTING-P1-01`。
- 建议整改顺序：先 `CHAT-P0-01`（存储型 XSS，影响所有聊天成员）→ `ICON-P1-01`
  （跨用户持久化 XSS）→ `OAUTH-P1-01`（出站基线）→ `SETTING-P1-01`（设置页核心链路）；
  P2 候选按 §4 根因分批，不逐条单独立项。
