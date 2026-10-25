# 缓存键与 JWT 审计（N5 安全自查三期）

> 产出日期：2026-09-11。扫描工具：`scripts/check_cache_keys.py --strict`（可挂 CI，违规退出码 1）。
> 缓存机制与失效链路的完整说明见 [cache.md](architecture/cache.md)；本文是**键空间登记表 + 审计规则**。

## 一、缓存键命名规则（检查清单）

| 规则 | 要求 | 说明 |
|---|---|---|
| R1 命名空间前缀 | 手写键必须形如 `<namespace>_<维度>` | 禁止裸键（`"1"`、`"data"`）；失效时可按前缀批量定位 |
| R2 用户维度显式 | 按用户隔离的键必须包含 `user.pk`（或经过属主收口的维度） | 防跨用户读写；键空间必须随用户规模有界 |
| R3 键空间冲突 | 不同模块的字面前缀不得重复 | 共用前缀会让「全量失效」互相误伤 |
| R4 TTL 必须显式 | 每个键都有明确 TTL 或明确的失效信号 | 禁止无 TTL 且无失效路径的键 |
| R5 失效点登记 | 写路径必须有对应的失效调用（信号驱动） | 见 `system/signal_handler.py` 的 `batch_invalid_cache` |

## 二、键空间登记表（2026-09-11 实测扫描）

### 装饰器缓存（自动前缀，键空间由框架保证）

| 前缀 | 载体 | 键构成 | TTL | 失效 |
|---|---|---|---|---|
| `magic_cache_data_` | `MagicCacheData.make_cache` | `<user.pk>_<method>` | 10s（字段权限）/ 24h（接口权限） | 信号驱动（Menu/UserRole/DeptInfo/UserInfo/SystemConfig/登出） |
| `magic_cache_response_` | `@cache_response` | `<ViewSet>_<action>_<user.pk>`（`get_cache_key`/`get_stats_cache_key`） | 10s ~ 24h | `cache_response.invalid_cache`（modelset 写路径收口）+ `?no_cache=1` 旁路 |

### 手写键（扫描输出 + 审计补登）

| 键 | 位置 | TTL | 维度 | 失效 |
|---|---|---|---|---|
| `approval_pending_count_{user.pk}` | approval/services/approval/queries.py | 10s | 用户 | `invalidate_pending_count_cache()`（审批状态变化全量删） |
| `approval_flow_pending_count_{user.pk}` | approval/services/approval_flow/engine_events.py | 10s | 用户 | `_invalidate_pending_count()`（节点推进/任务处理：全量或指定用户删） |
| `approval_flow_remind_{task.pk}` | approval/services/approval_flow/periodic.py | 24h | 单节点任务 | TTL 到期（同一任务每日最多提醒一次的超时占位） |
| `{DICT_CACHE_PREFIX}{code}` | system/utils/platform/dict.py | 常量 TTL | 字典编码（全局） | 字典写路径 `cache.delete` |
| `{MASK_CACHE_PREFIX}{model_label}` | audit/utils/mask.py | 常量 TTL | 模型（全局） | 脱敏规则写路径 + roles m2m 信号 |
| `tag_choice_options_{limit}` | system/services/tags.py | 60s | 全局（标签定义） | Tag 写路径信号（`signal_handler.py`）+ 视图写路径三处 + TTL 兜底 |
| `magic_cache_response_UploadFileViewSet_stats_{user_pk}` | file/views/admin/file.py | 10s | 用户 | TTL 到期（统计口径可容忍） |
| `data_permission_grants_{version}_{user_pk}_{dept_pk}_{menu_pk}` | packages/xadmin-common/common/core/filter.py | 300s | 用户 × 部门 × 菜单 | 全局版本号自增（数据权限/部门/授权关系变更，见 signal_handler）；**观察项**：键空间随「用户 × 菜单」增长，维持现状 + `/metrics` 的 `xadmin_authz_grants_cache_keys` gauge 监控；超预期时的收敛方案 = 键去 `menu_pk`（需评估菜单上下文语义）或版本键分段淘汰 |
| `auth_temp_token_{sha256(token)[:32]}` | packages/xadmin-common/common/utils/token.py | 调用方指定（默认 60s） | 令牌摘要（**不落原文**） | 一次性消费（`success_once`）/ TTL；升级前以原文为键的存量令牌在读取时迁移到摘要键 |
| `auth_verify_code_{target}` | packages/xadmin-common/common/utils/verify_code.py | `VERIFY_CODE_TTL`（缺省兜底 300s） | 目标（手机 / 邮箱） | 校验成功清理；连续失败达上限作废 |
| `auth_verify_code_send_at_{target}` | packages/xadmin-common/common/utils/verify_code.py | `VERIFY_CODE_LIMIT`（缺省兜底 60s） | 目标 | TTL（发送间隔闸门，校验失败不清） |
| `auth_verify_code_{target}_failed` | packages/xadmin-common/common/utils/verify_code.py | `VERIFY_CODE_TTL` | 目标 | 校验成功清理；连续失败达上限作废（在线爆破计数） |
| 进度/锁类（`import_progress`、`preview` 锁） | task/utils/*、file/utils/* | 短 TTL | 单记录 | 任务结束即删 |

### 手写键（键常量声明形态，2026-10-25 补登）

> 这类键由模块级常量拼接（`cache.set(REPORT_CACHE_KEY, ...)`），调用点正则看不到
> 字面量；`check_cache_keys.py` 的「键常量」段单独列出供本表核对。

| 键 | 位置 | TTL | 维度 | 失效 |
|---|---|---|---|---|
| `ai_daily_report_latest` | ai/services/daily_report.py | 48h | 全局（站点级摘要） | 每日任务覆盖写 + TTL |
| `ai_daily_report_metrics` | ai/services/daily_report.py | 120s | 全局（首页指标） | 每日任务回填 + TTL（首页高频读取免重复聚合） |
| `dashboard_data_push_{pk}` / `dashboard_data_push_claim_{pk}` | dataset/ws_dashboard.py | 2h / 10s | 单仪表盘 | 每轮覆盖写 / 原子占位到期放行 |
| `ai_stream_slots` | ai/services/assistant/ai_stream_slots.py | 1800s（touch 续期） | 全局（并发流式计数） | 释放减一 + TTL 兜底 |
| `ai_action_idem*` | ai/services/assistant/ai_idempotency.py | 600s | 用户 × 意图键 | TTL（与审批令牌口径一致） |
| `ai_repo_sync_lock` / `ai_repo_sync_status` | ai/services/knowledge/sync_progress.py | 1800s / 3600s | 全局（知识库同步） | 终态写入 + 锁释放 + TTL 自愈 |
| `ai_embedding_build_lock` / `ai_embedding_build_status` | ai/services/knowledge/embedding_progress.py | 1800s / 3600s | 全局（向量构建） | 终态写入 + 锁释放 + TTL 自愈 |
| `dataset:designer_meta:v1` | dataset/utils/dataset.py | 60s | 全局（设计器元数据） | 版本号换 key（v1 → v2）+ TTL |
| `online_info_snapshot` / `online_layers_snapshot` | message/ws/utils.py | 5s | 全局（在线快照） | TTL（在线列表 5 秒收敛一次） |
| `api_grant_catalog_v1` | identity/utils/api_grant_catalog.py | 60s | 全局（应用授权目录） | TTL |
| `api_grant_menu_meta_{pk}` / `api_grant_menu_paths_{method}` | identity/utils/api_grant.py | 60s | 菜单 | TTL |
| `account_risk_scan_result_{task_id}` / `account_risk_scan_error_{task_id}` | identity/utils/account_risk_scan.py | 3600s | 单次扫描任务 | TTL |
| `passkey_challenge_{scene}_{user_pk}` | identity/utils/webauthn.py | 300s | 用户 × 场景 | 一次性消费（校验即删） |
| `oauth_state_{state}` / `oauth_bind_state_{state}` / `oauth_nonce_{state}` | identity/utils/oauth.py | 300s | 授权会话 | 一次性消费 |
| `oauth_authorize_code_{digest}` | identity/services/open_oauth.py | 300s | 授权码摘要（不落原文） | 一次性消费 |
| `notify_unread_summary_{user_pk}` | notifications/views/user_site_msg.py | 30s | 用户 | 标记已读删除 + TTL |
| `message_template_overrides` | notifications/template_registry.py | 60s | 全局（通知模板覆盖） | 模板写路径删除 + TTL |
| `xadmin_doctor_probe` | system/management/commands/doctor.py | 10s | 全局 | 探测后立即删除 |
| `celery_active_worker_probe` | packages/xadmin-common/common/core/modelset/import_export/celery_utils.py | 15s | 全局 | TTL |
| `data_permission_grants_version` | packages/xadmin-common/common/core/filter.py | 无（版本计数器） | 全局 | 授权变更 incr（键本身即失效信号） |
| `xadmin:lock:*` | packages/xadmin-common/common/cache/lock.py | 各调用方指定 | 资源 | 释放 / TTL 自愈 |

### 扫描结论

- **R3 冲突：0**（`--strict` 退出码 0）；
- **R1/R2：通过**（全部手写键带命名空间前缀；按用户隔离的键均含 `user.pk`）；
- **R4/R5：通过**（全部键有 TTL 或信号失效；失效点 14 处已登记，集中在 `signal_handler.py` 与 modelset 写路径）。

## 三、JWT 审计口径

| 项 | 现状 | 结论 |
|---|---|---|
| Token 类型 | simplejwt 双 Token（access/refresh）+ 黑名单 + 轮换（ADR-001 JWT-only） | ✔ |
| 会话绑定 | `UserSession` 登记，sid claim 写入 refresh 并随轮换继承；单会话失效走 `SessionTokenRevokedCache(sid)` | ✔ 键随会话规模有界 |
| 无状态缓存键 | JWT 本身不落服务端缓存；黑名单/吊销键 = `sid`（UUID） | ✔ 无用户级歧义 |
| 失效链路 | 登出/改密/角色变更信号 → 权限缓存与路由缓存精准失效 | ✔ 见 cache.md 失效矩阵 |
| PAT | `OperationLog.auth_type/token_pk` 精确审计（ADR-008），scope 校验内联在 `IsAuthenticated` | ✔ |

## 四、维护约定

1. 新增缓存键先在本表登记（键构成/TTL/维度/失效），再写代码；
2. 新增键后跑 `python scripts/check_cache_keys.py --strict`，R3 冲突必须归零；
3. 键前缀冲突的修法：**换前缀**而不是「顺手删别人的失效」——键空间是模块契约。

## 五、季度复核记录

### 2026-10-25（扫描面扩域 + 键常量形态补登）

- **扫描面**：`SCAN_DIRS` 由 4 个目录扩到 14 个（新增 `ai` / `dataset` / `message` /
  `identity` / `file` / `task` / `audit` / `approval` / `notifications` / `devtools`）——
  此前 `ai` / `dataset` / `message` 的手写键在扫描面之外，`--strict` 结构上发现不了；
- **新增能力**：「键常量」段输出模块级键常量（`REPORT_CACHE_KEY` 这类），调用点用变量名
  的键由此可核对登记表；
- **登记**：补登 20 类此前未登记的键（见 §二 第二张表），含本轮新增的
  `ai_daily_report_metrics` 与 `dashboard_data_push_*`；
- **R3 冲突 0**（`--strict` 退出码 0）。

### 2026-10-09（Q4，与 [indexes.md](architecture/indexes.md) §五 同批）

- **扫描**：`python scripts/check_cache_keys.py --strict` 退出码 0——装饰器缓存 17 处
  （permission 2 / swagger 1 / monitor 6 / dashboard 7 / routes 1）+ 手写键 3 处，**R3 冲突 0**；
- **R2 维度核对**：`{DICT_CACHE_PREFIX}{code}`（全局）、`tag_choice_options_{limit}`（全局，标签定义）、
  `office_converting_{upload.pk}`（单文件维度）——均无跨用户歧义；
- **R4 TTL 核对**：office_converting（`OFFICE_CONVERT_LOCK_TIMEOUT`）、tag_choice_options（60s）、
  dict（`DICT_CACHE_TIMEOUT`）逐键确认；
- **R5 失效接线核对**：tag_choice_options = Tag 信号（`signal_handler.py`）+ 视图写路径三处 +
  TTL 兜底；office_converting = 任务 finally 删除 + 投递失败释放分支；
- **反向核对**：§二 登记的全部键在代码中仍存在（无过期登记）；
- **登记表补充**：`tag_choice_options_{limit}` 为本轮新补登记（§二）。
