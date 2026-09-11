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
| `approval_pending_count_{user.pk}` | system/utils/approval.py | 10s | 用户 | `invalidate_pending_count_cache()`（审批状态变化全量删） |
| `approval_flow_pending_count_{user.pk}` | system/utils/approval_flow.py | 10s | 用户 | `_invalidate_pending_count()`（节点推进/任务处理：全量或指定用户删） |
| `approval_flow_remind_{task.pk}` | system/utils/approval_flow.py | 24h | 单节点任务 | TTL 到期（同一任务每日最多提醒一次的超时占位） |
| `{DICT_CACHE_PREFIX}{code}` | system/utils/dict.py | 常量 TTL | 字典编码（全局） | 字典写路径 `cache.delete` |
| `{MASK_CACHE_PREFIX}{model_label}` | system/utils/mask.py | 常量 TTL | 模型（全局） | 脱敏规则写路径 + roles m2m 信号 |
| `magic_cache_response_UploadFileViewSet_stats_{user_pk}` | system/views/admin/file.py | 10s | 用户 | TTL 到期（统计口径可容忍） |
| 进度/锁类（`import_progress`、`preview` 锁） | system/utils/* | 短 TTL | 单记录 | 任务结束即删 |

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
