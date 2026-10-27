# 缓存策略审计（T3.5，2026-09-05）

> 四套服务端缓存 + 一组前端存储的键规范、TTL、写入点与失效路径集中登记。
> 新增缓存必须在本文档登记；禁止未登记的缓存键（评审项）。

## 一、总览

| 缓存      | 载体                  | 机制                                         | 典型键形态                                                        | TTL                   |
|---------|---------------------|--------------------------------------------|--------------------------------------------------------------|-----------------------|
| ① 数据缓存  | Django cache（redis） | `MagicCacheData.make_cache` 装饰器            | `magic_cache_data_{函数名}_{key}`                               | 按装饰参数                 |
| ② 响应缓存  | Django cache        | `cache_response` 装饰器（视图方法）                 | `magic_cache_response_{View}_{method}_{user_pk}[_queryhash]` | 按装饰参数                 |
| ③ 配置缓存  | Django cache        | `UserSystemConfigCache`（storage 封装，带写锁合并写） | `{px}_{key}` / `user_{pk}_{key}`                             | 600s                  |
| ④ 部门树缓存 | Django cache        | `DeptInfo.recursion_dept_info` 内部          | `dept_recursion_{is_parent}_{dept_id}`                       | `DEPT_TREE_CACHE_TTL` |

## 二、① 数据缓存（MagicCacheData）

| 缓存内容     | 位置                                                   | key                   | TTL | 失效             |
|----------|------------------------------------------------------|-----------------------|-----|----------------|
| API 权限映射 | `packages/xadmin-common/common/core/permission.py::get_user_permission`     | `{user_pk}_{method}`  | 24h | 信号失效（见 §五）+ 登出 |
| 字段权限集合   | `packages/xadmin-common/common/core/permission.py::get_user_field_queryset` | `{user_pk}_{menu_pk}` | 10s | TTL 短，靠过期收敛    |

规范：`make_cache(timeout, key_func)` 的 `key_func` 必须包含**所有**影响结果的输入
维度（user pk、method、菜单等）；fail-closed 原则——权限类缓存读取异常按 403 处理
（PERF-01），不得吞异常后缓存空值。

## 三、② 响应缓存（cache_response）

| 缓存内容      | 位置                                          | TTL                            | 说明                 |
|-----------|---------------------------------------------|--------------------------------|--------------------|
| 用户路由菜单    | `system/views/user/routes.py::UserRoutesAPIView` | 24h                            | 与权限映射同步失效          |
| 面板统计卡片 ×N | `system/views/platform/dashboard.py`                 | 60s                            | 短 TTL 吞吐保护，允许分钟级延迟 |
| 导出数据      | —                                           | `request.no_cache = True` 强制绕过 | 导出必须实时             |

规范：响应缓存只用于「读多写少 + 按用户隔离」的 GET；写路径接口禁止使用；
`export-data` / 导入类接口必须绕过缓存（现行为已固化于 modelset 的
`paginate_queryset`/`export_data`）。

## 四、③ 配置缓存（SysConfig / 用户个性化）

- `SystemConfig`（站点级）：`{px}_{key}`；变更经 `SystemConfig` 信号
  `invalid_config_cache_handler` 精确失效。
- `UserPersonalConfig`（用户级，如表格列宽/主题）：`user_{pk}_{key}`；
  用户变更信号失效。
- `UserSystemConfigCache.del_many` 批量失效用户侧键（`packages/xadmin-common/common/cache/storage.py`）。

## 五、失效链路（唯一入口：`system/signal_handler.py`）

| 触发源                                             | 信号                                          | 失效目标                             |
|-------------------------------------------------|---------------------------------------------|----------------------------------|
| Menu 变更                                         | post_save / pre_delete                      | 权限映射 + 路由响应缓存（全量相关用户）            |
| SystemConfig 变更                                 | post_save / pre_delete                      | 配置缓存                             |
| UserRole / DeptInfo / UserInfo 变更               | post_save / pre_delete                      | 相关用户权限与响应缓存（batch_invalid_cache） |
| UserRole.menu / UserInfo.roles / DeptInfo.roles | m2m_changed                                 | 同上（覆盖 ORM 直改 M2M）                |
| 用户登出 / 踢出                                       | user_logged_out / invalid_user_cache_signal | 该用户权限缓存                          |

红线：**任何绕过 ORM 的批量写路径**（raw SQL、bulk_update 绕过信号、外部脚本直改库）
必须手动调用 `MagicCacheData.invalid_caches([...])` / `cache_response.invalid_caches`，
或补挂信号——否则权限变更最长 24h 不生效。

## 六、调试与测试地图

- 日志关键字：`magic_cache_data_`（数据缓存键）、`invalid_cache_data cache_key:`
  （失效轨迹）、`invalid_response_cache cache_key:`。
- 旁路验证：`cache_response.invalid_cache(key)` / `MagicCacheData.invalid_caches([...])`。
- 测试：`tests/unit/common/test_magic_cache_data.py`（机制）、
  `tests/unit/system/test_signal_handler.py`（失效链路 + m2m_changed）、
  `tests/unit/system/test_routes_view.py`（路由缓存失效）、
  `tests/unit/common/test_dept_tree_cache.py`（部门树缓存）。

## 七、已知取舍

- 权限映射 24h TTL 是性能取舍，正确性完全依赖信号失效（含 M2M 直改挂钩）。
- 响应缓存与数据缓存键前缀不同（`magic_cache_response_` / `magic_cache_data_`），
  批量失效分别走 `invalid_caches` 的两个入口，勿混用。
- 消息未读等高频计数不走缓存，直接聚合查询（PERF-04 索引覆盖），避免缓存一致性问题。

## 八、缓存命中率指标（2026-10-09）

`packages/xadmin-common/common/metrics.py::record_cache_request` 记录两套内核缓存的读取结果，
Counter `xadmin_cache_requests_total{cache, result}`：

- `cache` 标签用**缓存名**（MagicCacheResponse 取 `{View}_{method}`，MagicCacheData 取被装饰函数名），
  **不含缓存键**——键带用户/参数维度，基数不可控；
- `result` 为 `hit` / `miss`。MagicCacheResponse 在 `_serve_cached` 记 hit（含锁内二次命中）、
  在进入 `_execute_view` 前记 miss（`?no_cache=1` 主动旁路不计）；MagicCacheData 在 `is_valid`
  判定处记 hit/miss。接线性指标旁路：依赖缺失或记录失败一律 no-op，不影响缓存主流程。

整体命中率 PromQL：

```promql
sum(rate(xadmin_cache_requests_total{result="hit"}[5m]))
  / sum(rate(xadmin_cache_requests_total[5m]))
```

按缓存名分组：

```promql
sum by (cache) (rate(xadmin_cache_requests_total{result="hit"}[5m]))
  / sum by (cache) (rate(xadmin_cache_requests_total[5m]))
```

守护测试：`tests/unit/common/test_cache_metrics.py`。指标清单登记见
[../ops/observability.md](../ops/observability.md) §三。

## 九、TTL 抖动与降级回源复核（2026-10-09，结论制）

**复核口径**：针对「缓存同批到期/失效是否造成回源尖峰」逐项评估——①主要同批风险点（权限信号失效、
授权池 grants 版本号 `incr`）与 TTL 无关；②窗口到期的并发击穿已被三层单飞/占位锁吸收
（MagicCacheData 占位锁、MagicCacheResponse 单飞锁、元数据载荷单飞锁）；③ TTL 写入时刻天然分散
（逐请求 `c_time`），不存在集中到期窗口。

**结论：不引入 TTL 抖动。** 维持现有确定性 TTL——便于推理、与失效链路一致，且抖动对上述风险点无收益。

**降级回源现状**（安全语义不擅自改）：

| 缓存 | 读取异常 | 处置 |
|------|----------|------|
| 权限类（`MagicCacheData`，permission.py 消费） | 异常 | **fail-closed**（403），不得改 |
| 元数据载荷（`modelset/metadata_cache.py::cached_payload`） | 缓存/锁不可用 | 降级回源直建（已交付） |
| 授权池（`common/core/filter.py::_grants_cache_version`） | 版本读取异常 | 返回 None → 跳过缓存直查（已交付） |
| 菜单元信息（`common/core/api_grant.py`） | 读取异常 | 降级回源（已交付） |
| 响应缓存（`MagicCacheResponse`，只读 GET） | 读缓存/锁故障 | 目前直接抛错；**判定为「可降级回源」**，属行为变更，本次不改 |

**登记项（独立最小改动）**：`MagicCacheResponse` 读/锁/写三处 `try → 直渲`（读缓存是优化、
不是正确性要求），配套 +1 守护测试；待后续窗口单独实施，避免与本轮指标接入混提。
另登记观察项：授权池版本切换瞬间无锁保护，若监控（`xadmin_cache_requests_total` 回源率）见尖峰再评估。


## 十、统一人机验证挑战缓存（2026-10-27）

挑战 / 尝试计数 / 一次性 ticket 均为 2 分钟级短命数据，走 Django cache（Redis）。
统一协议下图片题型也只写挑战缓存，**不再有 DB `CaptchaStore`**。挑战 / 尝试计数 /
未消费计数的键前缀集中登记在 `captcha/cache.py`；签名票据与 verify 加签的键前缀登记在
`captcha/tickets.py`，均纳入 `scripts/check_cache_keys.py --strict`。

| 键 | TTL | 说明 |
|----|-----|------|
| `captcha_challenge_{challenge_id}` | `CAPTCHA_CHALLENGE_TTL`（默认 120s） | 题型 + scene + 服务端答案（JSON），答案永不下发客户端 |
| `captcha_attempt_{challenge_id}` | 与挑战同 TTL | 已尝试次数；超 `CAPTCHA_MAX_ATTEMPTS` 即删挑战 |
| `captcha_nonce_{n}` | 票据 TTL + 60s | 结构化签名票据的一次性 nonce（`captcha/tickets.py`）；消费走 `atomic_get_delete` |
| `captcha_seen_{nonce}` | 300s | verify 请求签名的 nonce 防重放登记（`cache.add`，重复即拒） |
| `captcha_ticket_inflight_{ip}` | 300s | 同 IP 未消费 ticket 计数，上限 3（防囤票重放） |
| `captcha_stats_{hours}` | 60s | 观测大盘聚合缓存（`captcha/stats.py`，键为窗口小时数；缓存故障直接现算，fail-open） |
| `captcha_freq_ip_{ip_hash}` / `captcha_freq_fp_{fp}` | 60s | 挑战频率信号（同 IP >10/分、同指纹并发 >3） |
| `captcha_phase_challenge_{ip_hash}` / `captcha_phase_verify_{ip_hash}` | 600s | 挑战/校验计数，派生 `challenge_abandon`（挑战远多于校验 = 枚举/爬题） |
| `captcha_type_mix_{ip_hash}` | 600s | 同 IP 题型分布，派生 `type_fixation`（>90% 单一题型） |
| `captcha_audio_{ip_hash}` | 600s | 音频请求计数，派生 `audio_abuse` |
| `captcha_ticket_rate_{ip_hash}` | 600s | ticket 消费计数，派生 `ticket_velocity` |
| `captcha_spread_ip_{ip_hash}` / `captcha_spread_fp_{fp}` | 600s | Redis Set：同 IP 指纹数 / 同指纹 IP 数，派生扩散规则 |
| `captcha_hist_{fp}` | 7d | 指纹历史信誉（成功 ≥3 且无失败 → `hist_good` 负分） |
| `captcha_acct_fail_{account_hash}` | 900s | 账号近期失败计数（`acct_fail` 信号） |
| `captcha_type_recent_{fp/ip}` | 60s | 题型轮换防重复（同指纹/IP 短时间内不重复同型题） |
| `captcha_pow_budget_{ip_hash}` | 3600s | 单 IP 每小时 PoW 验签预算（防算力放大器） |
| `captcha_dim_fail_{dim}_{value}` | 3600s | 三维度失败阶梯计数（支撑「20 次/1h」档） |
| `captcha_cooldown_{dim}_{value}` | 900s / 3600s | 冷却期（第二/第三档），冷却内一律同形失败 |
| `captcha_alert_total_{window}` / `_deny_{window}` | 告警窗口 | 窗口内决策/拦截量与拦截率 |
| `captcha_alert_sent_{kind}` | 告警窗口 | 告警去重（同窗口只发一次） |

失效语义：Redis 不可用时 challenge / verify / ticket 消费一律 **fail-closed**
（拒绝），不降级为放行；挑战通过后立即删除挑战键，ticket 消费即删除（nonce
一次性取回），不做主动失效。
