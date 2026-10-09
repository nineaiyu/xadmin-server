# ADR-078：ASGI 中间件链异步化（杠杆 B）

> **日期**：2026-10-01
> **状态**：已交付
> **关联**：[ASGI 同步段容量立项（2026.10）](../plans/ASGI同步段容量立项-2026.10.md) 杠杆 B（触发制，本次按用户决策提前触发）；[ADR-006](ADR-006-db-connection-pool.md)（DB 连接池，本链路的容量前提）；[ADR-074](ADR-074-knowledge-pgvector.md) 无关
> **代码路径**：`server/middleware.py`、`common/core/middleware.py`、`common/local.py`
> **背景**：容量立项实测单 worker 吞吐封顶 ≈195 rps，请求线程时间 21.6% 花在 sync→async 交接等待；中间件链 18 项中 7 项自定义中间件为纯同步类（`async_capable` 未声明），每请求在同步段与事件循环之间多次交接。

## 现状（事实基础）

链上 18 项（`server/settings/apps.py:62-86`）中 12 项已 async-capable（Django 内置 8 + corsheaders/csp + 自定义 ApiLogging/Metrics）。需改造 7 项（2026-10-01 实测均为纯同步 `__call__`）：

| 中间件 | 热路径 IO | 生产挂载 | 改造判定 |
|---|---|---|---|
| `RequestMiddleware` | 无（uuid + thread-local 写） | 是 | **async 化**（最大收益点：链首，其同步化把整个内层链拖入线程） |
| `ModuleGateMiddleware` | 无（启动期编译正则，请求相纯内存） | 是 | **async 化** |
| `RefererCheckMiddleware` | 无（header 判断） | 默认关 | **async 化**（零成本对齐） |
| `CSPModeMiddleware` | SysConfig 读×2（L1 内存 30s → Redis → DB） | 是 | **async 化**，配置读经 `sync_to_async` 包裹 |
| `StartMiddleware` / `EndMiddleware` | 无 | 仅 DEBUG_DEV | **保持 sync 并显式声明边界**（生产 MiddlewareNotUsed，收益为零） |
| `SQLCountMiddleware` | `connection.queries`（同步调试连接） | 仅 DEBUG | **保持 sync 并显式声明边界** |

顺序依赖（不可调整）：`RequestMiddleware` 必须最先（`request_uuid`/`current_request` 被全仓消费）；`CSPModeMiddleware` 必须排在 `csp.middleware.CSPMiddleware` 之前（响应相自内向外改写）；`Start`/`End` 成对（`_e_time_*` 属性）。

## 决策

- **D1：四个生产链上中间件改造为双模（`sync_capable=True` + `async_capable=True`）**。沿用 Django `MiddlewareMixin` 的官方混合形态：`__init__` 按 `iscoroutinefunction(get_response)` 判定 `async_mode` 并 `markcoroutinefunction(self)`；sync 链走 `__call__`，async 链走 `__acall__`。Django handler 按 `middleware_is_async = middleware_can_async` 逐层适配（`django/core/handlers/base.py:42-71`），async 链上四个中间件全部留在事件循环，消除中间件段的线程交接。
- **D2：`common/local.py` 的请求上下文存储从线程本地改为上下文本地**。`Local(thread_critical=True)` → `Local()`（contextvars 存储）。依据：asgiref `SyncToAsync` 把当前 context 复制进同步线程（`asgiref/sync.py` `contextvars.copy_context()`），async 中间件在事件循环任务里 `set_current_request` 后，同步视图线程（`thread_sensitive=True` 同线程执行）与流式渲染线程均可读到；新起 OS 线程 context 为空，Celery 任务/后台线程的隔离语义与改造前一致。这是 D1 的**前置条件**：不改此存储，async 中间件写入的 `current_request` 对同步视图不可见（26+ 消费方：serializers 字段权限、日志 formatter、信号、response.requestId 等）。
- **D3：DEBUG-only 三项（Start/End/SQLCount）保持同步并显式声明 `sync_capable = True`**。生产环境 `MiddlewareNotUsed`，改造零收益；显式声明把「有意保持同步」写成代码而非默认值（立项文档 §6.1 允许的第二种口径）。
- **D4：`ApiLoggingMiddleware` 与 `MetricsMiddleware` 不在本次改造面**（立项文档 7 项口径）。ApiLogging 已 async-capable（`__acall__` 整体落线程），其热路径 DB 写（每写请求 1 条 OperationLog INSERT）与 JWT 重复认证的深度异步化**另册登记**（见「边界」）。

## 备选与不选

- **全链 native async（视图/DRF 也异步化）**：收益上限更高，但 DRF 同步视图面巨大，不在中间件杠杆范围（立项文档 §6.2 已界定）。不选。
- **仅声明 `async_capable=True` 不写 `__acall__`**：handler 会把实例包进 `sync_to_async`，仍在线程执行，零收益且语义误导。不选。
- **保持 7 项全同步、只等扩 worker**：杠杆 A（8 worker）已达标，但 4-worker 低配档膝点 ~780 rps 的中间件段开销仍在，本次消除属于同一立项的既定杠杆。选 D1。

## 验收

1. 既有 `tests/unit/server/test_middleware.py` 全绿（sync 路径行为不变）；
2. 新增 async 路径守护：`__acall__` 行为与 `__call__` 等价（X-Request-Id / current_request 上下文可见性 / 404 裁剪 / Referer 403 / CSP 三模式），`current_request` 经 `sync_to_async` 在同步线程可读（contextvars 传播实证）；
3. 后端全量 `pytest -n auto` 绿 + `mypy` 0 错；E2E 全量 fresh 绿（真 ASGI 链回归）。

## 边界（登记）

- **不动**：`ApiLoggingMiddleware` 的深度异步化（process_view 的 OperationLog INSERT、`build_operation_log_info` 的 JWT 重复认证）——登记为后续独立项，触发条件：操作日志写放大成为慢请求主因。
- **不动**：中间件顺序；`ModuleTrimWebsocketMiddleware`（WS 侧本就原生 async）。
- **语义微变（接受）**：`thread_critical=True → False` 后，同一线程内先 set 后跨 `sync_to_async` 再读的场景从「不可见」变「可见」（修复而非破坏）；「跨裸 `threading.Thread` 传 request」本就不可见，维持不可见。

## 交付记录

- 2026-10-01：D1–D3 落地，`server/middleware.py`（Request/ModuleGate/RefererCheck 双模化，Start/End/SQLCount 显式 sync 声明）、`common/core/middleware.py`（CSPMode 双模化 + 配置读 `sync_to_async` 包裹）、`common/local.py`（contextvars 存储）。新增 async 路径守护测试；后端全量门禁与 E2E fresh 全绿（见 NEXT-DEV-PLAN.md 执行记录四）。
- 2026-10-09：收口复核——链上 19 项实测 `async_capable` 盘点：生产默认挂载 15 项全部具备
  async 能力（Request/ModuleGate/CSPMode 双模 + 内置/第三方原生），默认关 / DEBUG-only 4 项
  维持显式同步边界（RefererCheck 双模默认关；Start/End/SQLCount 生产 MiddlewareNotUsed）；
  新增 `TestMiddlewareChainAsyncCapability` 2 例守护（生产项须声明 async + 显式同步白名单须
  保持 DEBUG-only），防新增同步中间件回退。
