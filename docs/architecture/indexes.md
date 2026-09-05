# 数据库索引评审记录（T3.3，2026-09-05）

> 方法：遍历各 app 模型现有索引 → 对照 filterset / 高频查询路径（列表默认排序、
> 权限中间件每请求查询、定时清理任务）逐表评审 → 代表性查询以
> `EXPLAIN QUERY PLAN` 断言索引命中（tests/unit/system/test_index_usage.py）。

## 一、现状清单（含 PERF 批次成果）

| 表 | 索引 | 服务的查询 |
|----|------|-----------|
| OperationLog | `idx_oplog_created(created_time)`、`idx_oplog_module_created(module, created_time)`、`idx_oplog_request_uuid` | 日志列表默认排序、按模块过滤、X-Request-Id 全链路追踪 |
| UserLoginLog | `idx_loginlog_created(created_time)` | 登录日志列表默认排序 |
| UploadFile | `idx_uploadfile_tmp_created(is_tmp, created_time)`、`idx_uploadfile_md5sum` | 列表过滤 + 每日清理任务（PERF-13）、秒传去重 |
| MessageUserRead | `(owner, unread)` 复合 | 站内信未读数/列表（PERF-04，冗余单列索引已删） |
| UserInfo | username unique、phone/email db_index | 登录、检索 |
| UserMsgSubscription | unique(user, message_type) | 订阅读写 |
| SystemMsgSubscription | message_type unique | 通知发布 |
| 各 FK | Django 默认 db_index | 关联过滤/JOIN |

## 二、评审结论：不再新增索引的项（含理由）

| 候选 | 结论 | 理由 |
|------|------|------|
| OperationLog.status_code | 不加 | 布尔语义过滤（错误/正常）低基数；默认排序已走 created_time 索引，过滤在索引覆盖的行集内进行 |
| OperationLog/LoginLog 的 ipaddress/system/path/agent `icontains` | 不加 | 前缀通配无法命中 B-tree 索引；低频管理页查询，加索引无收益 |
| UserLoginLog.status / login_type | 不加 | 低基数；列表按 created_time 排序已覆盖 |
| FieldPermission(menu, role) 复合 | 不加 | 表规模 = 角色×菜单（小）；role/menu 单 FK 自动索引已覆盖每请求权限查询（另有 10s 缓存） |
| ModelLabelField.name/parent | 不加 | 字段元数据树，行数极小 |
| DataPermission(userinfo/rules M2M) | 不加 | 规则表行数小；M2M 中间表 Django 自动建唯一约束索引 |

## 三、回归保护

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
