# 异常处理与错误码规范

> 生成日期：2026-09-04（半年规划 T1.2 交付物）
> 关联任务：TD-09（部分异常 `str(exc)` 直出前端，敏感信息泄露风险）

## 一、审计结论（2026-09-04 全仓 `str(exc)` 扫描）

| 位置 | 场景 | 决策 | 说明 |
|------|------|------|------|
| `common/core/exception.py`（未处理异常分支） | 全站兜底异常处理器 | **已修复** | 未预期异常不再直出 `str(exc)`，改为通用文案；完整堆栈仍由 `unexpected_exception_logger` 记录 |
| `common/core/exception.py`（list detail 分支） | DRF 校验错误 detail 为列表 | **已修复** | 保留列表结构透传，不再 `str(exc)` 扁平化（避免 ErrorDetail repr 泄露/乱码） |
| `system/views/auth/verify_code.py` check 配置接口 | 用户侧（登录/注册/重置发码） | **已修复** | 业务校验改抛 `ValidateError`（APIException 子类）按原文案透传；非预期异常返回通用文案并记录日志 |
| `system/utils/auth.py` 登录失败提示 | 用户侧登录限流提示 | 保留 | `error=str(e)` 内容为登录校验的业务文案（如"用户名或密码错误"），非内部信息 |
| `settings/views/email.py` 测试邮件接口 | 超管诊断接口 | 保留 + 已有日志 | SMTP 报错是诊断能力的一部分，接口仅超管可达 |
| `settings/views/sms.py` 测试短信接口 | 超管诊断接口 | 保留 + **补日志** | 同上，新增 `logger.warning` |
| `common/api/common.py` healthz 探测 | 运维健康检查 | 保留 | 返回给 healthz 状态字段，含探测错误便于定位 |
| `common/drf/parsers/*`（axios_form_data/excel/base） | 上传解析错误 | 保留 | ParseError 文案来自第一方解析代码，面向用户上传场景 |
| `common/base/magic.py` / `common/decorators.py` / `common/core/config.py` / `settings/models.py` | 后台任务/缓存/日志路径 | 保留 | 不直接进客户端响应 |
| `system/views/auth/verify_code.py` ValueError 分支 | 用户侧发码失败 | 保留 + **补日志** | `SendAndVerifyCodeUtil` 抛出的 ValueError 为业务校验文案 |

**脱敏原则**（后续新代码遵循）：

1. 未预期异常一律返回通用文案，详情只进日志（`logger.exception`）；
2. 需要透传给用户的业务错误统一继承 `APIException`（system 内用 `ValidateError`），在视图中显式按类型捕获；
3. 仅超管可访问的诊断接口允许返回上游错误原文，但必须留日志；
4. DRF `ValidationError` 的结构化 detail（list/dict）原样透传，不做字符串扁平化。

## 二、错误码表

| code | 含义 | 触发位置 |
|------|------|----------|
| 0 / 200 | 成功 | ApiResponse 默认 |
| 500 | 服务器内部错误（详情不入响应） | `common/core/exception.py` 兜底分支 |
| 998 | 数据被其他数据引用，无法删除（ProtectedError） | `common/core/exception.py` |
| 999 | 请求过于频繁（Throttled） | `common/core/exception.py` |
| 40001 | access token 失效或过期（前端触发无感刷新） | `common/core/exception.py` |
| 40002 | refresh token 失效或过期（前端跳登录页） | `common/core/exception.py` |
| 1001 | 业务校验失败（发码配置校验、权限不足等） | verify_code、各业务视图 |
| 1002 | 发送失败/参数处理失败（邮件、发码等动作） | verify_code、email 测试 |
| 1004 | 请求数据异常 | verify_code |

> 约定：新增错误码必须先在本表登记；`40001/40002` 为前端约定的协议码，不可挪作他用。
