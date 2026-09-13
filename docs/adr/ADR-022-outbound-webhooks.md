# ADR-022：出站 Webhook（事件订阅 + HMAC 签名 + 退避重试 + 投递审计）

- 状态：已接受
- 日期：2026-09-13
- 关联：年度开发计划 2026.10-2027.09 §四 W7（G6）；ADR-020/021（数据分析域）；
  既有信号源：登录链路（login_success/login_failed）、审批服务
  （system/utils/approval.py）、敏感操作告警（maybe_alert_sensitive_operation）、
  备份失败告警（notify_backup_failure）

## 背景

通知渠道（notifications）只面向"人"（邮件/站内信/IM）；对外系统集成的标准
能力是出站 Webhook：第三方按事件类型订阅，平台 POST 签名 JSON 到订阅方 URL，
失败自动重试并可审计。

## 决策

### 1. 事件目录 = 代码注册表 + `emit_webhook_event` 唯一发射口

- 目录常量 `EVENT_CATALOG`（key → 中文名，一期 8 个事件）：
  `user.login_succeeded / user.login_failed`、`approval.submitted / approved /
  rejected / cancelled`、`security.sensitive_operation`、`system.backup_failure`；
- 发射口 `emit_webhook_event(event, payload)`：查 active 订阅 → 建
  `WebhookDelivery`（pending）→ 派发投递任务；**全程吞异常**——Webhook 任何
  故障都不允许影响宿主动作（登录/审批等）；payload 统一携带
  `{event, occurred_at, data}` 外壳，data 内不放敏感字段（调用方负责裁剪）；
- 接线点收口在既有服务/工具函数层（login_success/login_failed、
  create_approval/approve_request/reject_request/cancel_request、
  maybe_alert_sensitive_operation、notify_backup_failure），不侵入视图。

### 2. 订阅与签名

- `WebhookSubscription`：`name`(唯一) / `url`（**必须 https**；例外放行
  loopback http://127.0.0.1|localhost，供联调与测试）/ `secret`（per-row，
  沿用 Setting 的 signer **值级加密**落库、write_only 不回传）/ `events`
  （JSON 事件 key 列表，写入校验必须在目录内）/ `is_active` / 描述与最近
  失败摘要；
- **签名**（GitHub 风格）：请求头
  `X-Xadmin-Signature: sha256=<HMAC-SHA256(secret, "{timestamp}.{raw_body}")>`、
  `X-Xadmin-Event`、`X-Xadmin-Delivery`、`X-Xadmin-Timestamp`——timestamp 参与
  签名防重放，接收方 5 分钟窗口校验；
- 投递超时 10s；响应 2xx 即成功。

### 3. 重试与审计

- `WebhookDelivery`：subscription FK / event / request 摘要 / status
  （pending/success/failed/exhausted）/ attempt / response_code / response_body
  （截断 500 字）/ duration；
- 投递任务 `bind=True`：失败按 `countdown = min(60 × 2^attempt, 3600)` 指数
  退避重派（上限 5 次），耗尽 → `exhausted` + **站内信告警超管**
  （WebhookFailedMessage，附订阅名/事件/最后错误）；
- 订阅 `test` 动作：发送 `ping` 测试事件走真实投递管线（配置自检）；
- 投递 `retry` 动作：对 exhausted 的投递重置重派（审计页操作）。

### 4. API 与前端

- `/api/system/webhooks/subscriptions`（CRUD + `events` 目录 + `test`）；
  `/api/system/webhooks/deliveries`（审计列表，按 status/event/subscription
  过滤 + `retry`）；
- 前端两页挂新目录「集成管理」：订阅管理页（列表 + 弹窗 + 测试按钮）与
  投递审计页（表格 + 状态/事件过滤 + 重试按钮）；secret 永不回显。

## 后果

- **明示不做**：事件 payload 的 schema 治理与版本化（一期扁平 JSON）、
  订阅方管理后台自动注册、消息队列扇出（直发即可，量级不匹配 MQ）、
  幂等重投键（delivery pk 即幂等键，接收方按 X-Xadmin-Delivery 去重由
  接收方负责）；
- **失败域隔离**：emit / 投递 / 重试 / 告警全链路不外抛；订阅被禁用后存量
  pending 投递继续完成；
- **安全**：secret 值级加密 + write_only；URL https 强制（loopback 白名单）；
  签名防篡改/重放；事件 payload 不含密码等敏感字段（接线点各自裁剪）。

## 测试与验收

- 单元：目录校验、签名函数（timestamp+body 的 HMAC 稳定性）、调度命中；
- 集成（进程内本地 HTTP 接收端）：成功投递（验签通过）、非 2xx → 指数退避
  重试序列、耗尽 → exhausted + 告警消息、订阅过滤（未订阅事件不投递）、
  login/approval 事件接线（触发登录/审批动作产生投递）、URL 白名单
  （非 https 非 loopback 拒绝）；
- E2E 主链路：建订阅（指向不可达地址）→ test 动作 → 投递审计页出现 FAILED
  记录（UI 全链路）；签名/成功路径由集成测试覆盖；
- 门禁：pytest / ruff / i18n po；前端 typecheck / eslint / locale-keys；
  全量 E2E 回归。
