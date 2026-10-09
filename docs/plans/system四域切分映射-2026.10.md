# system app 四域切分映射清单（Phase C：identity / file / audit / task）

> 对应任务台账《CODE-REVIEW-TASKS/04-system-app域拆分与服务层下沉.md》Phase C（T04-09 / T04-10 / T04-11 / T04-12）。
> 总原则：**行为零变化**——接口路径（含 `/api/system/` 前缀与 `system:` 命名空间视图名）、权限点、celery 任务名、
> 菜单种子、消息类型（message_type=类名）全部不变；物理表名按 ADR-080 口径随域改名（`identity_*` 等，
> 元数据级 RENAME，无数据重写），历史迁移一律不改（ADR-058/080 口径）。
> 上游：任务 03（架构解耦）、Phase A（utils 五域归包）、Phase B（服务下沉）均已完成；
> 迁移落在 ADR-084 的 18 迁移链之上，当前链上的存量库可线性升级。

## 〇、迁移形态（清库重建口径，全量迁移重建）

本批次为大版本升级（ADR-058/084 清库重建前提的延续）：**业务 app 历史迁移文件全部删除，
按当前模型重新生成全新初始迁移**，不做存量库线性升级；表名按 ADR-080 终态口径直接落到
各域默认命名（identity_* / system_* 留存），无锚定、无改名、无数据平移。

各 app 迁移形态：

1. `identity/0001_initial`：16 个身份域模型**真实建表**（含 trgm GinIndex），deps 仅 auth；
   operations 首位 `RunPython(_ensure_trgm_extension)`——本迁移成为全链最前端（system.0001 经
   swappable 依赖它），pg_trgm 扩展必须先行（vendor 守护 + 失败告警不阻断，同 system.0004 旧口径）。
2. `identity/0002_deptinfo_rules_userinfo_rules_userrole_menu`：`UserInfo.rules` / `DeptInfo.rules` /
   `UserRole.menu` 三个引用 system（DataPermission/Menu）的 M2M **推迟补加**（deps = identity.0001 +
   system.0001）——identity.0001 的状态渲染与 DDL 不依赖 system，避免 `identity.0001 ↔ system.0001`
   图环（system.0001 的用户 FK 经 AUTH_USER_MODEL 依赖 identity.0001）。
3. trgm 索引不再单列迁移：identity 侧 4 条 userinfo 索引随 `identity/0001` 的模型 Meta GinIndex
   直接建列（扩展已在其首操作就绪）；system 侧 1 条（uploadfile）与 approval 侧 4 条同样随各自
   `0001_initial` 的模型 Meta 落地，快照漂移由 tests/unit/system/test_search_indexes.py 守护。
4. `system/0001_initial`：留存 platform 域模型真实建表，deps = contenttypes/django_celery_beat/
   identity（creator FK 经 `settings.AUTH_USER_MODEL` 自动指向 identity.UserInfo）。
5. 其余 app（approval/ai/dataset/settings/mfa/notifications/message/demo/captcha/common）：
   各自重建 `0001_initial`（demo/message/notifications 另有各自域内演进的 0002），deps 经
   swappable/显式 FK 自动收敛到 identity → system 单向链。ai/0001 保留
   `create_vector_extension` RunPython（首位），embedding_vector 向量列随模型
   VectorField 正常建列。

删除面：原 system.0001~0004、approval/ai/dataset/settings/mfa/notifications/message/demo/captcha/common
全部历史迁移（18 个文件）与涟漪 AlterField；content_type / 权限点由 contenttypes post_migrate
按最终注册表生成，无 stale 内容类型；loadjson 种子经 load_init_json 按新 fixture 头加载。

## 一、模型迁移映射（26 个模型 + 1 抽象基类）

| 目标 app | 迁入模型（原 system/models/ 文件） | 表名终态 |
|---|---|---|
| identity | UserInfo(user)、UserRole(role)、DeptInfo + DeptManagerAssignment(department)、Post(post)、UserOAuthBinding(oauth)、LdapUserBinding(ldap)、PasswordHistory(password)、AccountRisk + LoginAccessPolicy + UserPasskey(security.py)、UserSession(session)、PersonalAccessToken + ApiApplication + ApiApplicationGrant + OAuthRefreshToken(token) | identity_*（16 张 + 3 张 auto m2m） |
| file | UploadFile + UploadSession + UploadSessionPart(upload)、FileAccessLog(security.py 拆出) | file_* |
| audit | OperationLog + UserLoginLog(log)、DataMaskRule(mask) | audit_* |
| task | TaskExecution + CeleryTaskRecordModel(task，abstract 随迁)、ExportRecord(export)、ImportRecord + ImportTemplate(import_)、WebhookSubscription + WebhookDelivery(webhook) | task_* |
| system 留存 | SystemConfig + UserPersonalConfig(config)、DataDict(dict)、ModelLabelField(field)、Menu + MenuMeta(menu)、DataPermission + FieldPermission + ModeTypeAbstract(permission/abstract)、SavedListView(saved_view)、Tag + TaggedItem(tag)、SystemModule(module) | system_*（不变） |

裁决说明：

- **Post 随 identity**：岗位属组织结构（user/dept/role 同族），任务台账 identity 清单"dept"语义涵盖岗位；
  views/search/post、views/admin/post 同步随迁。
- **FileAccessLog 随 file 而非 identity**：文件域自有审计面（Phase A 映射同口径），FK 指向 UploadFile；
  所在 security.py 按模型拆文件。
- **DataPermission/FieldPermission 留 system**：与 Menu（M2M/FK）强耦合的权限框架面，四域清单均未列；
  `permission_preview/`、`permission_sync/`、`rule_meta.py` 三个工具随之留 system（移入 utils/platform）。
- **LoginTypeChoices 上提 identity**：UserSession.login_type 复用 UserLoginLog.LoginTypeChoices 会造成
  identity → audit 模型级反向依赖；枚举语义属登录域，上提后 audit.UserLoginLog 反向引用 identity（方向健康）。

### 1.1 AUTH_USER_MODEL

`server/settings/base.py`：`AUTH_USER_MODEL = "identity.UserInfo"`。
`AUTHENTICATION_BACKENDS` 中 `system.ldap.auth.LdapBindBackend` → `identity.ldap.auth.LdapBindBackend`。

### 1.2 字符串注册表改写清单（代码 + 种子 + 存量数据）

| 注册表 | 位置 | 改写 |
|---|---|---|
| TAGGABLE_MODELS | system/models/tag.py | `system.userinfo`/`system.uploadfile` → `identity.userinfo`/`file.uploadfile` |
| AUDIT_DIFF_MODELS 默认值 | server/conf/settings_defaults.py | `system.UserInfo` → `identity.UserInfo` |
| API_LOG_IGNORE | server/conf/config.py | `system.OperationLog` → `audit.OperationLog` |
| impact 注册表键 | utils/audit/impact.py（随迁 audit） | `system.userrole`/`system.deptinfo` → `identity.*` |
| ModelLabelField 节点 name | 存量数据 + loadjson/modellabelfield.json（2007 处） | 按 §一 映射改写 |
| DataPermission.rules[].table | 存量数据 + loadjson/datapermission.json | 同上 |
| DataMaskRule.model | 存量数据 + loadjson/datamaskrule.json | 同上 |
| ImportTemplate.target_model | 存量数据（示例种子含 system.userinfo） | 同上 |
| loadjson fixture 头 `"model": "system.<x>"` | userinfo/userrole/deptinfo/datamaskrule/datapermission/modellabelfield/*.json | 随域改写 |
| search trgm 表名快照 | system/search_indexes.py + 各新 app 对应模块 | `system_userinfo`/`system_uploadfile` → 新表名 |
| PERMISSION_DATA_AUTH_APPS | server/settings/custom.py | 追加 identity/file/audit/task |
| check_file_length SCAN_DIRS | scripts/check_file_length.py | 加四个新 app |
| 表名守护 SPLIT_APPS | tests/unit/server/test_table_domain_alignment.py | 加四个新 app |
| e2e 种子任务名/模型串 | scripts/e2e_seed*.py、scripts/sql_sampling.py | 按映射改写 |

## 二、views / serializers / urls 映射

新 app 的 `urls.py` 显式 `app_name`（= app 名），由 `server/urls.py` **独立前缀挂载**：
`^api/identity/`、`^api/file/`、`^api/audit/`、`^api/task/`。切分当时沿用的 ADR-057 D1.2
「system 同前缀挂载、路径零变化」口径已于 2026-10-09 按 ADR-059 同原则升级——URL 前缀
与 app 边界对齐，权限点 / menu.json / 前端 API 层同步平移（见 ADR-085，存量库用
`manage.py migrate_api_prefixes` 平移）。

各域注册串口径：identity 保持语义原名（user/dept/role/...）；file 保留注册串 `file`
（`/api/file/file`，与 `/api/approval/approvals` 同类轻微冗余，避免二次语义改名）；
audit 保持 `logs/operation`、`mask-rules`、`user/log`；task 拍平原 `tasks/` 注册层
（`/api/task/periodic`、`/api/task/executions` 等）。

| app | views 迁入（原 system/views/） | urls 注册项（原 system/urls.py） |
|---|---|---|
| identity | auth/ 全部 11 文件；admin/{user,dept,post,role,login_policy,passkey,online,account_risk}；user/{userinfo,token,directory}；open/ 全部 2 文件；search/{user,role,dept,post} | login/basic、login/code、login/mfa/*、register、auth/captcha、auth/token、auth/verify、auth/reset、auth/invite/*、auth/oauth/*、logout、impersonate/exit、refresh、rules/password、userinfo、user、dept、posts、role、online、account-risks、login-policies、passkeys、directory、search/user|role|dept|post、personal-access-tokens、api-applications、open/token、open/oauth/* |
| file | admin/{file,file_chunk,file_access} | file（挂 `/api/file/` 前缀） |
| audit | admin/{operationlog,loginlog,mask}、user/login_log | logs/operation、logs/login、mask-rules、user/log |
| task | task/ 全部 4 文件；admin/{export,import_,record_base} | exports、imports、import-templates、periodic|crontab|executions|interval、unified、webhooks/*（均挂 `/api/task/` 前缀） |
| system 留存 | admin/{config,dict,menu,modelfield,credential,codegen,saved_view,permission}；platform/ 全部；search/{global_search,menu}；user/{routes,configs} | dashboard、monitor、search/menu、menu、permission、field、dict、saved-views、codegen、config/system、credentials、modules、config/user、tags、routes、configs、global-search |

serializers：identity（user/userinfo/role/department/post/oauth/token/directory）；file（upload）；
audit（log 中的 OperationLog/LoginLog/UserLoginLog 三个 + mask；UserSessionSerializer 随 UserSession 归 identity）；
task（task/export/import_/webhook）；system 留存（config/dict/menu/route/permission/saved_view/tag/fields）。

SCIM：`identity/scim/` → `identity/scim/`，`server/urls.py` 挂载改 `include("identity.scim.urls")`，
namespace 仍 `scim`；LDAP：`identity/ldap/` → `identity/ldap/`。

## 三、services 契约面重组

- `packages/xadmin-common/common/contracts.py` `_CONTRACT_PROVIDERS` 按域改挂：
  - identity.services：UserInfo、UserRole、DeptInfo、PersonalAccessToken、get_active_superuser_queryset、
    publish_api_quota_warning、apply_grant_fields、apply_grant_row_scope、application_of_request、
    enforce_application_grant、resolve_request_menu_pk
  - file.services：UploadFile
  - audit.services：OperationLog、apply_mask、get_mask_rules、record_original_channel_access、
    maybe_alert_sensitive_operation
  - task.services：emit_webhook_event
  - system.services 留存：SystemConfig、UserPersonalConfig、Menu、FieldPermission、DataPermission、
    ModelLabelField、ModeTypeAbstract、sync_model_field、scan_permission_gaps
- `system/services/__init__.py` 对应瘦身；identity/file/audit/task 各建 `services.py`
  （PEP 562 惰性导出，与 Phase B 后形态一致）。
- 各 app 内消费点改指新门面（message/notifications/dataset/ai/approval/mfa 的 `system.services` import 面
  按 §三 映射改写）；管理命令文件在 check_cross_app_imports ALLOWLIST 内，直连新 app models 合法。

## 四、celery 任务与周期注册

**任务名零变化的实现方式：任务函数壳全部留在 `system/tasks/__init__.py`（任务名 = `__module__` + 函数名
的仓内约定不变，beat 注册表 / TaskExecution / 路由 / import_string 引用零感知），实现体随域下沉**：

| 任务名（不变） | 原实现体 | 新实现体位置 |
|---|---|---|
| auto_clean_operation_job | utils/task/ctasks.auto_clean_operation_log | audit/utils（log_archive 联动） |
| auto_clean_black_token_job | ctasks.auto_clean_black_token | identity/utils |
| account_expiry_job / auto_expire_user_session_job / auto_clean_user_session_job / auto_clean_pat_job / scan_account_risk_job / demo_account_selfheal_job | utils/identity/*、models/token | identity/utils |
| auto_clean_tmp_file_job / auto_clean_upload_file_job / auto_clean_preview_cache_job / auto_clean_upload_sessions_job / convert_office_preview_task / auto_clean_file_access_log_job | utils/file/*、utils/task/ctasks 文件函数 | file/utils |
| auto_clean_task_execution_job / auto_clean_export_record_job / auto_clean_import_record_job | tasks/__init__ 本体 | task/utils |
| async_export_data_task / async_import_data_task | tasks/_export.py、_import.py | task/tasks_impl（导出/导入执行体） |
| dataset.analysis_tasks.dispatch_scheduled_reports、ldap 同步、webhook 投递（re-import 注册行） | dataset/、identity/ldap/、system/webhook_tasks.py | import 路径随域更新，注册行保留 |

`system/webhook_tasks.py` → `task/webhook_tasks.py`：`deliver_webhook` 显式 `name="system.webhook_tasks.deliver_webhook"`
钉住（模块路径变化但注册名/投递名零变化）。

## 五、notification 平铺模块与 signal 归位

| 原平铺模块 | 内容 | 去向 |
|---|---|---|
| system/notifications.py | DifferentCityLogin / AbnormalLogin / ResetPasswordSuccess / LdapSync / ApiQuotaWarning 五消息类 | identity/notifications.py |
| 同文件 SensitiveOperationMessage | 敏感操作告警 | audit/notifications.py（连 notifications_alert.py 的 maybe_alert_sensitive_operation） |
| 同文件 ApprovalRequestMessage + notifications_approval_flow.py ApprovalFlowMessage | 审批通知（评审 P1"寄居 system"一并归位，任务 05 上游收益） | approval/notifications.py |
| system/notifications.py WebhookFailedMessage | Webhook 投递告警 | task/notifications.py |
| system/signal.py invalid_user_cache_signal | 用户缓存失效信号 | identity/signal.py（settings 侧发送点随改） |
| system/signal.py approval_instance_finished | 审批完成信号 | 留 system/signal.py（approval 域治理属任务 05，本次不动） |
| system/signal_handler.py | 17 个 receiver 按模型域拆：UserRole/DeptInfo/UserInfo/invalid_user_cache/api_grant → identity；DataMaskRule(+roles m2m) → audit；TaskExecution pre_delete → task；Menu/MenuMeta/SystemConfig/UserPersonalConfig/DataDict/Tag/DataPermission(+m2m)/审批回写留 system | identity/audit/task/system 各自 signal_handler.py，apps.ready() 导入 |
| system/signal_task_execution.py | celery 信号 → TaskExecution 记账 | task/（task.apps.ready 导入） |
| system/ws.py TaskLogNotify + routing.py ws/tasks/log | 任务日志 WS | task/ws.py + task/routing.py（collect_app_ws_urls 自动收集） |
| system/ws_monitor.py + routing.py ws/system/monitor | 监控 WS | 留 system |

消息类型（message_type = 类名）与 category 串不变，订阅行/模板行零迁移。

## 六、utils / services / 其他模块随域清单

- identity：utils/identity/* 15 文件 + api_grant 族；services/{auth_login,open_oauth,token_issue}.py；
  builtin.py 的 BUILTIN_ROLE_CODES 常量随 identity（同步装配逻辑 sync_builtin_roles 留 system，经
  identity.services 取常量——Menu/FieldPermission/ModelLabelField/Tag 均为 system 留存模型）。
- file：utils/file/* 6 文件 + preview 包；services/file.py。
- audit：utils/audit/*（impact/mask/log_archive）。
- task：utils/task/* 10 文件；tasks/{_export,_import}.py。
- system/utils/platform/：codegen/dict/menu/modelfield/modelset/module_impact/monitor/credential/seed/tags
  + permission_preview/permission_sync/rule_meta（自 utils/identity 移入）。
- system/search.py：providers 模型面改函数级惰性（file/audit/identity 模型经 services）。
- system/admin.py：注册清单按域拆到各 app admin.py。

## 七、执行顺序与验证口径

顺序：**identity → file → audit → task**（identity 最重先趟平方法），每域独立回归、独立提交：
全量 pytest（xdist 失败项以串行口径复核）+ `manage.py check` + 六项静态门禁 + 路由快照 diff 仅允许
视图模块路径前缀平移（`system.` → `<域>.`，pattern+name 不变；django admin 模型注册随 app 归属
平移，排除在外）+ `makemigrations --check` 干净 + 全新库（DB_DATABASE 指向空库）migrate 一次通过。

### 完成态（2026-10-05）

**identity（T04-09）✅**：16 模型 + views/serializers/ldap/scim/services/notifications/urls 独立成 app；
AUTH_USER_MODEL 切换；迁移清库重建为 identity.0001（trgm 扩展先行 + Meta GinIndex）+ 0002（跨域 M2M
推迟补加破环）；tests/labels 全量改写；路由审计 175 处视图前缀平移零违例。

**file（T04-10）✅**：UploadFile/UploadSession/UploadSessionPart/FileAccessLog 独立成 app（file_* 表）；
views/{file,file_chunk,file_access} + serializers/upload + serializers/file_access_log + utils/file 全树
+ services/{file_impl,cleanup} 迁入；`file/urls.py` 同前缀挂载注册 "file"；system.tasks 五个文件清理
任务壳留位（任务名不变），实现体落 `file/services/cleanup.py` 与 `file/utils/*`；消费方
（message/attachments、notifications/serializers/message、dataset/{analysis_tasks,dform_fields}、
system/search、system/tasks/{_export,_import}）统一改经 `file.services` 契约缝；label/表名串
（system.UploadFile/uploadfile/system_uploadfile、TAGGABLE_MODELS、loadjson、AutoCleanFileMixin、
input_types、_generate_crud、import_actions.get_model）随域改写；FK 字面引用（message/notifications/
demo/export/import_）改 `file.UploadFile`；迁移重建 file.0001 deps=identity；路由审计 18 处视图
前缀平移零违例。

**audit（T04-11）✅**：OperationLog/UserLoginLog/DataMaskRule 独立成 app（audit_* 表）；
views/{operationlog,loginlog,mask,user/login_log} + serializers/{log,mask} + utils/audit 全树迁入；
`audit/urls.py` 同前缀挂载（logs/operation、logs/login、mask-rules、user/log）；契约面：
common.contracts 八项提供方改挂 audit.services（OperationLog、maybe_alert_sensitive_operation、
apply_mask 族、impact 族），identity/message 等消费方同步改缝；SensitiveOperationMessage 迁
audit/notifications.py（连 notifications_alert 节流实现），DataMaskRule 失效 receiver 迁
audit/signal_handler.py（apps.ready 注册）；auto_clean_operation_log 实现体落 audit/services/cleanup.py
（任务壳留 system.tasks）；log_archive 命令随域；API_LOG_IGNORE 的模型键、loadjson 种子
（datamaskrule/operationlog/userloginlog 子树）、ai_mask get_model、dashboard 的
LoginLogSerializer 惰性引用随域改写；迁移重建 audit.0001（deps=identity）+ system.0001 重生成；
路由审计 25 处视图前缀平移零违例。另：migrate 期消息订阅注册表补挂 identity/audit 通知模块——
修复 identity 域拆分后五类消息在全新库上不建订阅行的隐性回归。

**task（T04-12）✅**：TaskExecution/ExportRecord/ImportRecord/ImportTemplate（+ CeleryTaskRecordModel
抽象基类）+ WebhookSubscription/WebhookDelivery 独立成 app（task_* 表）；views/{task,task_center,
task_periodic,webhook,admin/{export,import_,record_base}} + serializers/{task,export,import_,webhook} +
utils/task 全树迁入（views/task/* 拍平为 task/views/*）；`task/urls.py` 同前缀挂载 11 个 basename
（exports / imports / import-templates / tasks/{periodic,crontab,executions,interval,unified} /
webhooks/{subscriptions,deliveries}）；ws.py + routing.py 随域（collect_app_ws_urls 自动收集，
system/routing.py 留存 monitor 通道）；signal_task_execution.py 与 TaskExecution 日志清理 receiver 迁
task/（apps.ready 注册）；契约面：task.services 门面（模型 + DisplayRelatedField + emit_webhook_event /
deliver_webhook / EVENT_CATALOG / URL 签名工具 + update_progress + run_async_export/import + 清理实现体），
common.contracts 的 emit_webhook_event 提供方改挂 task.services；消费方（identity/{auth_login,open,token}、
approval/{notify,engine_events,serializers}、ai/serializers、dataset/analysis_tasks、
system/{serializers/{dict,tag},platform/{metrics,monitor_events,credential_rotate},tasks 壳,
management/seed_demo_*}、scripts/{gen_event_docs,sql_sampling}）统一改缝。任务名零变化：
system.tasks 周期任务壳留位，_export/_import 实现体落 task/services/{_export,_import}.py 与 cleanup.py，
deliver_webhook 显式 name="system.webhook_tasks.deliver_webhook" 钉住；import_actions/export_actions 的
get_model 改 ("task", Model)，task_center_unified 的 import_string("system.tasks.*") 任务名串保持；
MODEL_CREDENTIAL_FIELDS 的 WebhookSubscription.secret 改 task 挂载（顺修 identity 轮漏网的
ApiApplication → identity）；WebhookFailedMessage 迁 task/notifications.py，ApprovalRequestMessage +
notifications_approval_flow.py 归位 approval/notifications.py（评审 P1 寄居问题一并解决），migrate 期
消息注册表补挂 approval/task 通知模块；loadjson modellabelfield 种子 12 行 system.* → task.*；
迁移重建 task.0001（deps=file/identity + django_celery_beat）+ system.0001 重生成；路由审计 66 处
视图平移零违例（21 处严格前缀 + 45 处 views 拍平），WS 通道归属测试收集源补 task/routing。

**口径修正记录**（相对上文的计划表述）：
- `packages/xadmin-common/common/contracts.py` 无 file.services 提供方——common 侧对 UploadFile 的消费全部是
  label 串比较（AutoCleanFileMixin / input_types / serializers.py），不产生 import 缝，无需登记；
- trgm 索引未单列迁移，随各域 0001 的模型 Meta 落地（identity.0001 首操作仅保证扩展先行）；
- identity.0002 即为跨域 M2M 推迟补加迁移（三段式 0001/0002/0003 的规划合并为两段式）；
- MODEL_CREDENTIAL_FIELDS 的 app_label 属行为面（轮换入口按其 get_model 解析），identity 轮
  漏改的 ApiApplication（已迁 identity/models/token.py）在 task 轮顺修回 identity。
