# 数据库索引评审记录（T3.3，2026-09-05）

> 方法：遍历各 app 模型现有索引 → 对照 filterset / 高频查询路径（列表默认排序、
> 权限中间件每请求查询、定时清理任务）逐表评审 → 代表性查询以
> `EXPLAIN QUERY PLAN` 断言索引命中（tests/unit/system/test_index_usage.py）。
> 2026-09-18 增补 §三 全局搜索 pg_trgm 索引（PostgreSQL 专属，SQLite 上不建、由覆盖守护把关）。

## 一、现状清单（2026-10-09 季度复核实拉对齐）

> 对齐方式：`django.apps.get_models()` 实拉自有 app 全部 `Meta.indexes`（**64 项**），与本文逐项对账后
> 按域登记；2026-09-05 初始评审之后的各批次新增索引一并归位（审批性能 / 聊天室 / 文件审计 / 任务中心 /
> 监控告警 / PAT 会话 / AI 用量 / 账号巡检等）。三方 app 自带索引（celery results 8 项）不重复登记；
> 唯一约束 / `db_index` / FK 默认索引由字段声明表达，不在此表。

| 域 / 表 | 索引（字段） | 服务的查询 |
|---|---|---|
| 审计 OperationLog | `idx_oplog_created`(created_time)、`idx_oplog_module_created`(module, created_time)、`idx_oplog_request_uuid`、`idx_oplog_exec_time`(exec_time)、`idx_oplog_module_objectpk`(module, object_pk)、`idx_oplog_path`(path)、`idx_oplog_token_created`(token_pk, created_time) | 日志列表默认排序、按模块过滤、X-Request-Id 全链路追踪、慢请求统计、按对象 / 令牌审计回溯 |
| 审计 UserLoginLog | `idx_loginlog_created`(created_time) | 登录日志列表默认排序 |
| 审计 DataMaskRule | `idx_datamask_model_field`(model, field) | 脱敏规则每请求查找 |
| 文件 UploadFile | `idx_uploadfile_tmp_created`(is_tmp, created_time)、`idx_uploadfile_md5sum`、`idx_uploadfile_creator_created`(creator, created_time)、`idx_uploadfile_filename_trgm`（§三） | 每日清理任务（PERF-13）、秒传去重、我的文件列表、文件名检索 |
| 文件 UploadSession | `idx_upsess_resume`(creator, filename, filesize, status)、`idx_upsess_status_created`(status, created_time) | 断点续传恢复、过期会话清理 |
| 文件 FileAccessLog | `idx_file_log_file_created`(file, created_time)、`idx_file_log_user_action`(user, action) | 文件访问记录（按文件 / 按用户+动作） |
| 身份 UserInfo | 4×trgm（§三）；username unique、phone/email db_index | 登录、全局检索 |
| 身份 UserSession | `idx_session_status_active`(status, last_active) | 在线列表 / 会话活跃度扫描 |
| 身份 AccountRisk | `idx_account_risk_status_level`(status, level)、`idx_account_risk_type_created`(risk_type, created_time) | 账号巡检列表（状态+等级、类型+时间） |
| 身份 ApiApplication | `idx_api_app_client_id`(client_id) | 应用按 client_id 定位 |
| 身份 ApiApplicationGrant | `idx_api_grant_app_active`(application, is_active) | 应用授权查询 |
| 身份 OAuthRefreshToken | `idx_oauth_refresh_app_user`(application, user) | 刷新令牌按应用+用户 |
| 身份 PersonalAccessToken | `idx_pat_creator_created`(creator, created_time)、`idx_pat_expired_at`(expired_at) | PAT 列表、过期扫描 |
| 审批 ApprovalRequest | `idx_approval_status_created`(status, created_time)、`idx_approval_creator_created`(creator, created_time)、3×trgm（§三） | 审批单列表（状态 / 发起人 + 时间）、检索 |
| 审批 ApprovalInstance | `idx_appr_inst_status_created`(status, created_time)、`idx_appr_inst_creator_created`(creator, created_time)、`idx_appr_inst_biz`(biz_type, biz_id) | 流程实例列表、按业务对象定位 |
| 审批 ApprovalInstanceTask | `idx_appr_task_assignee_status`(assignee, status)、`idx_appr_task_inst_order`(instance, node_order) | 待我审批列表、节点顺序 |
| 审批 ApprovalRequestStep | `idx_appr_step_req_status`(request, status) | 步骤按审批单+状态 |
| 审批 ApprovalInstanceComment | `idx_appr_comment_inst_created`(instance, created_time) | 讨论区按实例加载 |
| 审批 ApprovalDelegation | `idx_appr_deleg_delegator`(delegator, is_active)、`idx_appr_deleg_delegate`(delegate, is_active) | 委托生效查询（双向） |
| 审批 Leave | `idx_leave_status_created`(status, created_time)、`idx_leave_creator_start`(creator, start_date)、`idx_leave_reason_trgm`（§三） | 请假列表、检索 |
| 表单 DynamicFormSubmission | `idx_dformsub_filter_gin`(filter_data, JSONB GIN) | JSON 键筛选链路（ADR-075；TG-1 复核守护） |
| 消息 MessageContent | `idx_msg_created`(created_time)、`idx_msg_notice_type`(notice_type) | 站内信列表默认排序、按类型过滤 |
| 消息 MessageUserRead | 复合 (owner, unread) | 未读数 / 列表（PERF-04，冗余单列索引已删） |
| 消息 ChatRoomMember / ChatMessage | `chat_member_user_room_idx`(user, room)、`chat_msg_room_id_idx`(room, -id) | 会话成员定位、房间消息分页 |
| 任务 ExportRecord | `idx_export_status_created`(status, created_time) | 导出任务列表 / 清理 |
| 任务 ImportRecord / ImportTemplate | `idx_import_status_created`(status, created_time)、`idx_import_tpl_model_shared`(model, is_shared) | 导入任务列表、模板共享查询 |
| 任务 WebhookDelivery | `idx_webhook_sub_created`(subscription, created_time) | 投递记录按订阅 |
| 系统 DataDict | `idx_datadict_code_active`(code, is_active) | 字典按编码查询 |
| 系统 TaggedItem | `tagged_item_target_idx`(content_type, object_id) | 通用标签反查 |
| 系统 MonitorAlert | `idx_monitoralert_status_time`(status, last_time)、`idx_monitoralert_item_status`(item, status) | 监控告警列表 / 按指标 |
| AI AiUsageLog / AiChatMessage | `ai_usage_user_time_idx`(creator, -created_time)、`ai_usage_feat_time_idx`(feature, -created_time)、`ai_chat_msg_user_feat_idx`(creator, feature, -id) | 用量统计（用户 / 功能维度）、会话消息 |
| MFA MfaRecoveryCode | `idx_mfarecovery_user_used`(user, used_time) | 恢复码校验 |

> 订阅类（UserMsgSubscription unique(user, message_type)、SystemMsgSubscription message_type unique）
> 与各 FK 默认 `db_index` 维持 2026-09-05 初始口径，不重复列示。

## 二、评审结论：不再新增索引的项（含理由；2026-10-09 复核维持）

| 候选                                                              | 结论 | 理由                                                      |
|-----------------------------------------------------------------|----|---------------------------------------------------------|
| OperationLog.status_code                                        | 不加 | 布尔语义过滤（错误/正常）低基数；默认排序已走 created_time 索引，过滤在索引覆盖的行集内进行   |
| OperationLog/LoginLog 的 ipaddress/system/path/agent `icontains` | 不加 | 低频管理页查询；且 OperationLog 为写热表（每请求落审计），加 GIN 会把维护成本压到写入路径——维持不加（见 §四 豁免） |
| UserLoginLog.status / login_type                                | 不加 | 低基数；列表按 created_time 排序已覆盖                              |
| FieldPermission(menu, role) 复合                                  | 不加 | 表规模 = 角色×菜单（小）；role/menu 单 FK 自动索引已覆盖每请求权限查询（另有 10s 缓存） |
| ModelLabelField.name/parent                                     | 不加 | 字段元数据树，行数极小                                             |
| DataPermission(userinfo/rules M2M)                              | 不加 | 规则表行数小；M2M 中间表 Django 自动建唯一约束索引                         |

## 三、pg_trgm 检索索引（全局搜索，2026-09-18）

全局搜索的 `icontains` 是**前缀通配**（`LIKE '%关键词%'`），B-tree 无法命中；
PostgreSQL 部署下补 pg_trgm GIN 索引加速（语义不变：仍是 icontains，非 PG / 扩展不可用
自动回退顺序扫描）。清单与豁免见 `system/search_indexes.py`；trgm 索引随模型 Meta 按表归属落在
`identity/migrations/0001_initial.py`（identity 侧 4 个）、`system/migrations/0001_initial.py`（system 侧 1 个）与 `approval/migrations/0001_initial.py`（approval 侧 4 个）（pg_trgm 扩展由 identity.0001 首操作先行确保，失败只告警；合并口径见 ADR-084——快照即以现名冻结）。

| 表                     | 索引                                                                                  | 服务的检索字段                     |
|-----------------------|-------------------------------------------------------------------------------------|-----------------------------|
| identity_userinfo     | `idx_userinfo_username_trgm` / `idx_userinfo_nickname_trgm` / `idx_userinfo_email_trgm` / `idx_userinfo_phone_trgm` | 用户分组 username/nickname/email/phone |
| system_uploadfile     | `idx_uploadfile_filename_trgm`                                                      | 文件分组 filename                 |
| approval_approvalrequest | `idx_approvalrequest_path_trgm` / `idx_approval_module_trgm` / `idx_approval_object_pk_trgm` | 审批单分组 path/module/object_pk   |
| approval_leave        | `idx_leave_reason_trgm`                                                             | 请假分组 reason                   |

> 索引名沿用 2026-09-18 首次登记的原名（不含表前缀）；2026-10 迁移合并（ADR-084）后
> approval 侧初始迁移即以现名建表建索引，不再需要改名折算登记。

**豁免**（登记理由，覆盖守护在 `tests/unit/system/test_search_indexes.py`）：
DeptInfo.name/code（小表）；OperationLog.path/module/ipaddress（写热表 + 超管低频检索，维持 §二 结论）；
Tag.name/remark（标签分组，管理配置类小表——百级以内，顺序扫描成本可忽略）；
Post.name/code（岗位分组，管理配置类小表——百级以内，顺序扫描成本可忽略）。

**验证方式**（PG 库上）：`EXPLAIN SELECT id FROM identity_userinfo WHERE username ILIKE '%关键词%';`
应出现 `Bitmap Index Scan on idx_userinfo_username_trgm`；单字符关键词不使用索引（trigram 需 ≥2 字符）。

## 四、回归保护

`tests/unit/system/test_index_usage.py` 以 `EXPLAIN QUERY PLAN` 断言以下查询
命中索引（sqlite 与 pg 均会按此计划走索引）：

1. OperationLog 按 created_time 排序分页 → `idx_oplog_created`
2. OperationLog 按 module 过滤 → `idx_oplog_module_created`
3. UserLoginLog 按 created_time 排序 → `idx_loginlog_created`
4. MessageUserRead 按 (owner, unread) → 复合索引
5. UploadFile 清理任务查询 (is_tmp, created_time) → `idx_uploadfile_tmp_created`
6. UserInfo 按 username 精确查 → unique index

新增索引请同时在本文件登记 + 补 EXPLAIN 断言；回滚方式：删除对应迁移文件
或 `migrate <app> <previous>`。

守护测试全集（2026-10-09 复核核对）：

- `tests/unit/system/test_index_usage.py`：上列 6 项的 EXPLAIN 断言（sqlite 断言计划名 /
  PG 断言索引存在——空表计划文本不与规划器博弈）+ PG 专属 trgm 可用性断言；
- `tests/unit/system/test_search_indexes.py`：§三 trgm 索引清单与豁免清单覆盖守护；
- `tests/unit/dataset/test_dform_filter_index.py`：`filter_data @>` GIN 索引守护 5 例
  （模型 Meta + 迁移快照 + 查询形态绑定，TG-1 复核补）。

## 五、季度复核记录

### 2026-10-09（Q4）

- **实拉对账**：模型层 64 项显式索引（自有 app）逐项归位至 §一；2026-09-05 初版与
  2026-09-18 trgm 增补之后的各批次新增索引（约 46 项）本轮补齐登记——此前本文仅覆盖
  PERF 批次成果与 trgm，存在登记滞后；
- **失效核对**：§一 原登记 8 组索引全部仍在（无过期登记）；§二「不再新增」6 项结论维持
  （理由条目与原评审一致，本轮未发现推翻性证据）；
- **豁免核对**：§三 豁免 4 类（DeptInfo.name/code、OperationLog.path/module/ipaddress、
  Tag.name/remark、Post.name/code）维持；其中 `OperationLog.path` 已有普通 B-tree
  `idx_oplog_path`（非 icontains 检索索引），与 §二「不加 GIN」结论不冲突——GIN 只服务
  icontains，普通等值 / 前缀查询走 B-tree；
- **守护核对**：§四 三份守护测试全绿（25 例），覆盖 = 高频查询计划断言 + trgm 清单 +
  GIN 绑定；
- **缓存键同批复核**：`scripts/check_cache_keys.py --strict` 退出码 0，登记表复核见
  [cache-keys-audit.md](../cache-keys-audit.md) §五。
