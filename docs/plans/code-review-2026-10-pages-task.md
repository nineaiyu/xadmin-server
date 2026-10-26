# 2026-10 全栈功能审查 · 分册：任务域（定时任务/Webhook/任务执行 6 页）

> 主册：[code-review-2026-10.md](code-review-2026-10.md)。证据等级：A=代码直接确证，B=跨文件静态推断，C=待运行时验证。

## 1. 任务执行记录（/system/celery/logs/index）

- **标识**：task · ops(标配) · 薄壳 35 行 · 元数据驱动 · 后端 `task/views/task.py` TaskExecutionViewSet（list 与 `log:SystemTask` 同 path 多码共享端点，主册 §3.1）。
- **链路**：`taskExecutionApi` → TaskExecutionViewSet → `TaskExecution`；任务日志增量读取（`task/utils/task_log.py` read_task_log_chunk）。
- **闭环校验**：权限 ✅（同 path 多码 `list:SystemTaskExecution` | `log:SystemTask` 已入显式优先级表）；元数据 ✅；契约 ✅；数据 ✅（C：日志文件读取的路径收敛在 utils 层，未逐行验证）。
- **问题清单**：无。

## 2. 任务列表（/system/celery/task/index）

- **标识**：task · ops(标配) · 薄壳 29 行 · 元数据驱动 · 后端 `task/views/task_periodic.py PeriodicTaskViewSet`（BaseModelSet + run/batch-run/batch-enable/batch-disable/clone/registered 动作）。
- **后端实现**：`run`/`batch-run` 立即投递 Celery；`registered` 返回 worker 注册任务清单；clone 复制任务（`task_periodic.py:282-374`）。任务执行动作的权限点 `execute:SystemTask`（GET 例外共享组之外的部分）与页面按钮对应。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅（C：run 的幂等/并发语义待运行时验证）。
- **问题清单**：无。

## 3. Crontab 任务（/system/celery/crontab/index）

- **标识**：task · ops(标配) · 薄壳 27 行 · 元数据驱动 · 后端 `CrontabScheduleViewSet`（ScheduleDeleteGuardMixin）。
- **后端实现**：删除护栏——被 PeriodicTask 引用的 schedule 拒删并提供 `references` 查询（`task_periodic.py:64-140`，防「删调度致任务失效」的隐性破坏）；`preview` 动作预览表达式。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅（引用护栏）。
- **问题清单**：无。

## 4. 周期任务（/system/celery/interval/index）

- **标识**：task · ops(标配) · 薄壳 20 行 · 元数据驱动 · 后端 `IntervalScheduleViewSet`（同护栏；get_queryset 有定制段 :195-218）。
- **闭环校验**：同上 ✅。
- **问题清单**：无。

## 5. Webhook 订阅（/integration/subscription/index）

- **标识**：task · webhook(可选) · 薄壳 11 行 · 元数据驱动 · 后端 `task/views/webhook.py WebhookSubscriptionViewSet`（BaseModelSet + events/test/rotate-secret）。
- **后端实现**：
  - **secret 值级加密落库，明文仅轮换响应返回一次**（`rotate_secret` :74-84，`secrets.token_urlsafe(32)`）；
  - `test` 走真实投递管线做自检（不广播其他订阅者，:87-98）；
  - **SSRF 防链（本轮确认的重点横向项）**：写入侧 `validate_outbound_config_url`（https 强制 + DEBUG 下 loopback http 联调例外 + IP 字面量私网/环回/link-local 拒绝 + `OUTBOUND_ALLOWED_HOSTS` 白名单放行内网接收端）；投递侧 `pinned_request` 固定解析结果连接——**DNS rebinding 防护**（`task/services/webhook/delivery.py:10-12,57-75` + `packages/xadmin-common/common/utils/outbound.py`）。证据等级 A（链路完整，运行时行为 C）。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅（secret 不可回读）。
- **问题清单**：无。
- **质量速记**：安全 ✅（SSRF 三层：协议/字面量/发送侧钉扎）。

## 6. Webhook 投递（/integration/delivery/index）

- **标识**：task · webhook(可选) · 薄壳 11 行 · 元数据驱动 · 后端 `WebhookDeliveryViewSet`（只读审计 + retry）。
- **后端实现**：
  - **审计不可删**：不注册 destroy/batch-destroy 路由（模块注释 :109-113）；
  - `retry` 状态机收口（仅 exhausted/failed）+ **行锁内复核状态 + 代际递增**——队列残留旧倒计时任务到期后因代际不匹配静默失效（:122-144，防并发重派与陈旧任务复活，A）；
  - select_related(subscription)。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅（状态机 + 并发防护）。
- **问题清单**：无。

## 批次 3b 小结

- **发现数**：P0=0 / P1=0 / P2=0 / E=0。
- **正向**：Webhook SSRF 防链（协议白名单 → 私网字面量拒绝 → 发送侧 DNS 钉扎）与 secret 生命周期（加密落库/一次性回显/轮换）为全站安全标杆之一；投递 retry 的行锁+代际设计覆盖并发与陈旧任务两类竞态。
- **已读文件**（前端 6 薄壳 wc + 红旗 grep 0；后端全文/结构段）：`celery/{logs,task,crontab,interval}/index.vue`、`integration/{subscription,delivery}/index.vue`；`task/views/{task_periodic.py(结构段),webhook.py}`、`task/views/task.py`、`task/views/task_center.py`（结构段）、`task/services/webhook/delivery.py`（安全段）。
