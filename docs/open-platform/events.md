# 出站 Webhook 事件契约

> 本文档由 `scripts/gen_event_docs.py` 从 `system/utils/webhook.py` 的 `EVENT_CATALOG` 自动生成，
> 请勿手工编辑；一致性由守护测试 `tests/unit/system/test_webhook_contract.py` 在 CI 保证，
> 本地/发布前可用 `python scripts/gen_event_docs.py --check` 复核。

## payload 外壳

所有事件使用统一外壳（`data` 为事件数据，字段契约见下表）：

```json
{ "event": "<事件 key>", "schema_version": 1, "occurred_at": "2026-09-15T10:30:00+08:00", "data": { } }
```

- 签名头：`X-Xadmin-Signature`（`sha256=HMAC(secret, "{timestamp}.{raw_body}")`）、
  `X-Xadmin-Timestamp`、`X-Xadmin-Event`、`X-Xadmin-Delivery`（投递主键，幂等键）；
- 投递重试：最多 5 次，退避 `min(60 × 2^(attempt-1), 3600)` 秒；耗尽后订阅行记录
  `last_failure` 并站内信告警，可在「投递审计」页手动重试；
- 版本策略：事件 key 不带版本后缀；破坏性变更新增 `xxx.v2` 事件（旧 key 至少保留一个发布窗口），
  `schema_version` 随契约表递增；新增可选字段不改版本。

## 事件列表

### `user.login_succeeded`

- 版本：1
- 说明：Login succeeded

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `username` | string | 是 | Username |
| `ip` | string | 是 | Client IP address |

### `user.login_failed`

- 版本：1
- 说明：Login failed

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `username` | string | 是 | Username |
| `ip` | string | 是 | Client IP address |

### `approval.submitted`

- 版本：1
- 说明：Approval submitted

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `approval_id` | string | 是 | Approval id |
| `module` | string | 是 | Business module |
| `path` | string | 否 | Detail path |
| `status` | string | 是 | Approval status |
| `creator` | string | 是 | Creator username |

### `approval.approved`

- 版本：1
- 说明：Approval approved

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `approval_id` | string | 是 | Approval id |
| `module` | string | 是 | Business module |
| `path` | string | 否 | Detail path |
| `status` | string | 是 | Approval status |
| `creator` | string | 是 | Creator username |

### `approval.rejected`

- 版本：1
- 说明：Approval rejected

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `approval_id` | string | 是 | Approval id |
| `module` | string | 是 | Business module |
| `path` | string | 否 | Detail path |
| `status` | string | 是 | Approval status |
| `creator` | string | 是 | Creator username |

### `approval.cancelled`

- 版本：1
- 说明：Approval cancelled

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `approval_id` | string | 是 | Approval id |
| `module` | string | 是 | Business module |
| `path` | string | 否 | Detail path |
| `status` | string | 是 | Approval status |
| `creator` | string | 是 | Creator username |

### `security.sensitive_operation`

- 版本：1
- 说明：Sensitive operation alert

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `module` | string | 否 | Business module |
| `path` | string | 否 | URL path |
| `method` | string | 否 | HTTP method |
| `ipaddress` | string | 否 | Client IP address |

### `system.backup_failure`

- 版本：1
- 说明：Backup failure

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `source` | string | 否 | Backup source |
| `event` | string | 否 | Failure summary |
| `host` | string | 否 | Host name |

### `flow.submitted`

- 版本：1
- 说明：Flow application submitted

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `instance_no` | string | 是 | Instance number |
| `title` | string | 是 | Instance title |
| `flow_name` | string | 是 | Flow name |
| `status` | string | 是 | Instance status |
| `creator` | string | 是 | Creator username |
| `current_node` | string | 否 | Current node |
| `reason` | string | 否 | Finish reason |

### `flow.approved`

- 版本：1
- 说明：Flow application approved

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `instance_no` | string | 是 | Instance number |
| `title` | string | 是 | Instance title |
| `flow_name` | string | 是 | Flow name |
| `status` | string | 是 | Instance status |
| `creator` | string | 是 | Creator username |
| `current_node` | string | 否 | Current node |
| `reason` | string | 否 | Finish reason |

### `flow.rejected`

- 版本：1
- 说明：Flow application rejected

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `instance_no` | string | 是 | Instance number |
| `title` | string | 是 | Instance title |
| `flow_name` | string | 是 | Flow name |
| `status` | string | 是 | Instance status |
| `creator` | string | 是 | Creator username |
| `current_node` | string | 否 | Current node |
| `reason` | string | 否 | Finish reason |

### `flow.cancelled`

- 版本：1
- 说明：Flow application cancelled

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `instance_no` | string | 是 | Instance number |
| `title` | string | 是 | Instance title |
| `flow_name` | string | 是 | Flow name |
| `status` | string | 是 | Instance status |
| `creator` | string | 是 | Creator username |
| `current_node` | string | 否 | Current node |
| `reason` | string | 否 | Finish reason |

### `api_quota.warning`

- 版本：1
- 说明：API application quota warning

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `application` | string | 是 | Application name |
| `client_id` | string | 是 | Client id |
| `used` | integer | 是 | Used requests today |
| `quota` | integer | 是 | Daily quota |

### `webhook.ping`

- 版本：1
- 说明：Webhook ping

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `subscription` | string | 是 | Subscription name |
