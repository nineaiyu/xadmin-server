# 2026-10 全栈功能审查 · 分册：系统域 C（审计/日志/脱敏/在线/账号风险 5 页）

> 主册：[code-review-2026-10.md](code-review-2026-10.md)。证据等级：A=代码直接确证，B=跨文件静态推断，C=待运行时验证。

## 1. 操作日志（/system/logs/operation/index）

- **标识**：audit · core_log · 薄壳 44 行 · 元数据驱动 · 后端 `audit/views/admin/operationlog.py OperationLogViewSet`（只读 + 详情 + 导出）。
- **后端实现**：
  - **审计不可灭迹**：不提供删除/批删端点，留存收敛走 `manage.py log_archive`（模块注释 :79-83）；
  - 列表/详情分口径：列表大 JSON 降为有界预览、changes 保留全量供变更历史（:85-93）——防大载荷拖垮列表；
  - Keyset 分页（深翻页不劣化）；受控 lookup 透传试点（字段面 fail-closed，:97-100）；
  - 过滤器丰富：object_pk 行级变更历史回溯、has_changes、error_status、exec_time 区间（:25-54）；
  - `slow-threshold` 为 `shared_list_action`（与父级 list 同口径，主册 §3.1）。
- **闭环校验**：权限 ✅（list:SystemOperationLog 为主码，同 path 多码由 SHARED_PERMISSION_PRIORITY 确定性消解，主册 §3.1）；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。

## 2. 登录日志（/system/logs/login/index）

- **标识**：audit · core_log · 薄壳 40 行 · 元数据驱动 · 后端 `audit/views/admin/loginlog.py LoginLogViewSet`（只读 + 导出 + 强退）。
- **后端实现**：
  - 审计不可灭迹与操作日志同口径（模块注释 :41-45）；Keyset 分页；
  - `logout` 强退动作：**超管会话仅超管可踢**（:60-62）、空 creator/channel 返回可读 400 而非 500（:64-65）、会话已下线给可读提示不算静默成功（:66-70）。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：
  - **audit-P2-01（P2，死配置）**：种子权限点 `retrieve:SystemUserLoginLog`（path `api/audit/logs/login/(?P<pk>[^/.]+)$`）无对应路由——ViewSet 仅 list/export/logout，无 detail retrieve。授予该点无效果（死配置）；前端亦未消费。建议：删除该权限点或补路由。证据：`audit/seeds/menu.json` 权限点条目 + 路由内省（仅 `api/audit/logs/login$`、`api/audit/logs/login/{pk}/logout$`、export）。证据等级 A。
- **质量速记**：安全 ✅（强退提权防护）；可靠 ✅（空值分支）。

## 3. 脱敏规则（/system/mask/index）

- **标识**：audit · datamask(标配) · 薄壳 42 行 · 元数据驱动 · 后端 `audit/views/admin/mask.py DataMaskRuleViewSet` + 实时预览动作。
- **后端实现**：
  - 预览入参归一化：双入参形态、限量 20 条/单条限长 500、截断显式标记（:24-52）；
  - **自定义正则灾难性回溯防护**：`apply_masks_with_timeout` 子进程硬超时，超时报「简化正则」（:110-114）——用户可写正则的 ReDoS 面被收口（A）；
  - 角色视角模拟预览与运行时「规则 roles ∩ 查看者角色」同口径；pk 两侧统一 str 化比较防混型永假（:99-103）；
  - 非法自定义正则显式 `invalid_pattern` 标记（静默回退 + 显式提示，:105-109）。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。
- **质量速记**：安全 ✅（ReDoS 子进程隔离是该页面最大的风险点，已专业处理）。

## 4. 在线用户（/system/online/index）

- **标识**：identity · core_log · 薄壳 24 行 · 元数据驱动 · 后端 `identity/views/admin/online.py UserOnlineViewSet`（ListDeleteModelSet + force-logout / batch-force-logout）。
- **后端实现**：
  - 在线判定统一数据源：WS 会话看 channel 心跳、纯 HTTP 会话看活跃窗口（模块注释 :3-9）；
  - **批量删除强制逐行**（`_needs_rowwise_delete=True`，:90-93）——非逐行的 queryset.delete() 会静默跳过 WS 踢线/令牌失效副作用（在线看似被删、实际未下线），注释明确；
  - **越权防护**：非超管不可踢超管（行级 :67-73、批量 `filter_force_logout_targets` 过滤并回传 failures，:158-159）；
  - HTTP 会话精确踢本会话（sid 失效），不影响该用户其他登录（:82-87）；
  - batch-force-logout 兼容三种 pk 形态（会话/用户/登录日志行）且解析失败不 500（:141-157）；用原始 queryset 不做在线交集（快照语义，注释 :135-137）。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅（副作用与删除语义对齐）。
- **问题清单**：无。
- **质量速记**：这是「删除 ≠ 幂等副作用」问题的教科书处理；工作区暂存改动（test_force_logout_protection.py）即其守护测试。

## 5. 账号风险（/system/account-risk/index）

- **标识**：identity · core_log · `account-risk/index.vue`（88 行，含 stats 汇总卡）· 元数据驱动（只读列表）+ 处置动作 · 后端 `identity/views/admin/account_risk.py AccountRiskViewSet`（OnlyListModelSet + scan/scan-status/handle/batch-handle/stats）。
- **后端实现**：
  - 风险项由巡检任务产出，无手工创建面（模块注释 :4-5）——数据来源单一可信；
  - **task_id 防枚举**：他人任务与不存在任务同口径 404（:69-72）；
  - 处置文案区分「已解除」与「已执行待自动解除」（:97-103）——防误导；
  - 批量处置逐项隔离，success/failures 明细回传（:130-141）；
  - scan 异步投递 + 轮询进度（:44-73）。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅（状态机：PENDING→处置→自动关闭，文案与语义对齐）。
- **问题清单**：无。

## 批次 3a 小结

- **发现数**：P0=0 / P1=0 / P2=1（audit-P2-01 死权限点）/ E=0。
- **正向**：审计不可灭迹口径（两日志 + webhook 投递一致）、强退提权防护双入口收口、ReDoS 子进程隔离。
- **已读文件**（前端 grep 红旗扫描 + 后端全文）：`logs/operation/index.vue`、`logs/login/index.vue`、`mask/index.vue`、`online/index.vue`、`account-risk/index.vue`（薄壳 wc 确认 + 红旗 0）；`audit/views/admin/{operationlog,loginlog,mask}.py`、`identity/views/admin/{online,account_risk}.py`。
