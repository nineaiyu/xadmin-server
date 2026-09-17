#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批：协议常量与节流窗口。"""

# 前端重发请求时携带令牌的请求头；query 参数 approval_id 作为兜底
APPROVAL_HEADER = "X-Approval-Id"
APPROVAL_QUERY_PARAM = "approval_id"
# 拦截响应的协议标识（前端 http 层按 type 分流，业务码 1002 表示待审批）
APPROVAL_RESPONSE_TYPE = "approval_required"
APPROVAL_PENDING_CODE = 1002

# 重复提交节流窗口（秒），仿 maybe_alert_sensitive_operation 的 cache.add 原子占位
APPROVAL_NOTIFY_THROTTLE_SECONDS = 60

# 请求体快照上限（字节）：超过则不留快照，审批通过后仍需申请人手动重放
APPROVAL_PAYLOAD_MAX_SIZE = 64 * 1024

# 待办计数短缓存（秒）：顶栏角标/页签角标高频轮询，10s 内的多次读取共用一次聚合
APPROVAL_PENDING_COUNT_CACHE_SECONDS = 10
# 提醒占位保留期（秒）：同一单只提醒一次（占位仅在同一单被处理后自然过期）
APPROVAL_REMIND_CACHE_SECONDS = 60 * 60 * 24 * 30
# 审批统计默认回看窗口（天）
APPROVAL_STATS_WINDOW_DAYS = 30
