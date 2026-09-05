# 缓存策略审计（T3.5，2026-09-05）

> 三套服务端缓存 + 一组前端存储的键规范、TTL、写入点与失效路径集中登记。
> 新增缓存必须在本文档登记；禁止未登记的缓存键（评审项）。

## 一、总览

| 缓存 | 载体 | 机制 | 典型键形态 | TTL |
|------|------|------|-----------|-----|
| ① 数据缓存 | Django cache（redis） | `MagicCacheData.make_cache` 装饰器 | `magic_cache_data_{函数名}_{key}` | 按装饰参数 |
| ② 响应缓存 | Django cache | `cache_response` 装饰器（视图方法） | `magic_cache_response_{View}_{method}_{user_pk}[_queryhash]` | 按装饰参数 |
| ③ 配置缓存 | Django cache | `UserSystemConfigCache`（storage 封装，带写锁合并写） | `{px}_{key}` / `user_{pk}_{key}` | 600s |
| ④ 部门树缓存 | Django cache | `DeptInfo.recursion_dept_info` 内部 | `dept_recursion_{is_parent}_{dept_id}` | `DEPT_TREE_CACHE_TTL` |

## 二、① 数据缓存（MagicCacheData）

| 缓存内容 | 位置 | key | TTL | 失效 |
|----------|------|-----|-----|------|
| API 权限映射 | `common/core/permission.py::get_user_permission` | `{user_pk}_{method}` | 24h | 信号失效（见 §五）+ 登出 |
| 字段权限集合 | `common/core/permission.py::get_user_field_queryset` | `{user_pk}_{menu_pk}` | 10s | TTL 短，靠过期收敛 |

规范：`make_cache(timeout, key_func)` 的 `key_func` 必须包含**所有**影响结果的输入
维度（user pk、method、菜单等）；fail-closed 原则——权限类缓存读取异常按 403 处理
（PERF-01），不得吞异常后缓存空值。

## 三、② 响应缓存（cache_response）

| 缓存内容 | 位置 | TTL | 说明 |
|----------|------|-----|------|
| 用户路由菜单 | `system/views/routes.py::UserRoutesAPIView` | 24h | 与权限映射同步失效 |
| 面板统计卡片 ×N | `system/views/dashboard.py` | 60s | 短 TTL 吞吐保护，允许分钟级延迟 |
| 导出数据 | — | `request.no_cache = True` 强制绕过 | 导出必须实时 |

规范：响应缓存只用于「读多写少 + 按用户隔离」的 GET；写路径接口禁止使用；
`export-data` / 导入类接口必须绕过缓存（现行为已固化于 modelset 的
`paginate_queryset`/`export_data`）。

## 四、③ 配置缓存（SysConfig / 用户个性化）

- `SystemConfig`（站点级）：`{px}_{key}`；变更经 `SystemConfig` 信号
  `invalid_config_cache_handler` 精确失效。
- `UserPersonalConfig`（用户级，如表格列宽/主题）：`user_{pk}_{key}`；
  用户变更信号失效。
- `UserSystemConfigCache.del_many` 批量失效用户侧键（`common/cache/storage.py`）。

## 五、失效链路（唯一入口：`system/signal_handler.py`）

| 触发源 | 信号 | 失效目标 |
|--------|------|----------|
| Menu 变更 | post_save / pre_delete | 权限映射 + 路由响应缓存（全量相关用户） |
| SystemConfig 变更 | post_save / pre_delete | 配置缓存 |
| UserRole / DeptInfo / UserInfo 变更 | post_save / pre_delete | 相关用户权限与响应缓存（batch_invalid_cache） |
| UserRole.menu / UserInfo.roles / DeptInfo.roles | m2m_changed | 同上（覆盖 ORM 直改 M2M） |
| 用户登出 / 踢出 | user_logged_out / invalid_user_cache_signal | 该用户权限缓存 |

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
