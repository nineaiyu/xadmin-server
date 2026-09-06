# 架构总览

> 本文是 [XADMIN_FRAMEWORK_ANALYSIS.md](../../XADMIN_FRAMEWORK_ANALYSIS.md)（1289 行深度分析，2026-09-04 撰写）的**精炼导航版**（T6.1，2026-09-06）。
> 深度细节、跨语言重写建议请看完整版；两文冲突时以本文（较新）与代码为准。
> 关联：[permission.md](permission.md)（三层权限）、[mfa.md](mfa.md)（MFA/敏感操作二次验证）、[cache.md](cache.md)（缓存策略）、[indexes.md](indexes.md)（索引评审）、[../exception-handling.md](../exception-handling.md)（错误码）、[../schema/](../schema/)（元数据契约）。

## 一、技术栈与系统形态

| 维度 | 后端 xadmin-server | 前端 xadmin-client |
|------|--------------------|--------------------|
| 语言/框架 | Python 3.13 / Django 5.2 + DRF 3.16 | TypeScript / Vue 3 + Vite(rolldown) |
| 认证 | SimpleJWT 双 Token + 黑名单 | Cookie 承载 Token，Axios 无感刷新 |
| 实时通信 | Django Channels + Redis（Daphne） | 原生 WebSocket 封装 |
| 任务 | Celery + django-celery-beat/results（default/heavy 双队列） | — |
| 数据 | PostgreSQL / MySQL / SQLite + Redis 缓存 | — |
| 部署 | Gunicorn/Uvicorn + Docker（base 镜像自动构建） | nginx 静态托管 |

## 二、后端分层

```
URL 层        server/urls.py → 各 app/urls.py（SimpleRouter / NoDetailRouter + 插件自动注册）
ViewSet 层    BaseModelSet（common/core/modelset/ 包，T2.1 拆分为 10 模块）
              + Action Mixin（CRUD/批量/元数据/导入导出/上传/缓存）
Serializer 层 BaseModelSerializer（字段权限裁剪 + 动态字段）
Model 层      DbUuidModel / DbBaseModel / DbAuditModel（UUID 主键 + 审计字段 + 文件自动清理）
Filter 层     BaseFilterSet + BaseDataPermissionFilter（数据权限过滤）
Core 层       permission / response / pagination / exception / auth / throttle / middleware / config
```

跨 app 引用约束：业务层一律走 `<app>.services` 契约层，`scripts/check_cross_app_imports.py` 静态门禁接入 CI（T2.2，坏味道 ≤4 处且均为规划允许保留项）。

**新增一个完整 CRUD 模块的后端成本**（模型 + 序列化器 + 筛选器之外仅需）：

```python
class BookViewSet(BaseModelSet, ImportExportDataAction):
    queryset = Book.objects.all()
    serializer_class = BookSerializer
    filterset_class = BookFilter
    ordering_fields = ["created_time"]
```

## 三、元数据驱动（框架灵魂）

前后端契约的核心是两个元数据接口 + 统一渲染：

1. `GET /api/<app>/<model>/search-columns` — 字段/表格列/表单元数据；
2. `GET /api/<app>/<model>/search-fields` — 搜索表单元数据；
3. 响应结构以 JSON Schema 固化于 [docs/schema/](../schema/)，服务端契约测试 + 前端类型生成双端护航（T2.3）；
4. 性能优化（T3.2）：列表接口支持 `?with_meta=1` 内联元数据，前端 RePlusPage 首开 3 请求合并为 1。

前端 `BaseApi`（`src/api/base.ts`）封装全部端点（list/create/retrieve/update/destroy/batch-destroy/choices/columns/fields/import/export），`RePlusPage` 按 `input_type` 自动渲染表格列、搜索表单、编辑表单（注册表模式，`registry.ts` + `renderers/`）。新增页面 = 一行 API 实例化 + 一行组件引用。

`input_type` 完整映射表见完整版分析文档 §6.4。

## 四、三层权限（概要，详见 permission.md）

| 层 | 控制对象 | 生效位置 | 配置模型 |
|----|----------|----------|----------|
| 菜单/API 权限 | 页面可达性 + 接口调用（method+path 正则匹配） | `common/core/permission.py` `IsAuthenticated` | Menu(目录/菜单/按钮) ←→ UserRole / DeptInfo |
| 数据权限 | 数据行可见范围（14 种规则，AND/OR 组合，可绑菜单） | `common/core/filter.py` `get_filter_queryset()` | DataPermission.rules(JSON) |
| 字段权限 | 序列化字段可见性（角色×菜单维度） | `common/core/serializers.py` `get_allow_fields()` | FieldPermission ←→ ModelLabelField |

权限编码约定：`{action}:{ViewSetName}`（如 `create:UserViewSet`）；前端 `v-auth` 指令 / `hasAuth()` / `getDefaultAuths()` 消费。缓存失效由信号驱动（见 cache.md），变更即时生效。

## 五、关键子系统速览

| 子系统 | 入口 | 要点 |
|--------|------|------|
| 认证 | `common/core/auth.py` | access+refresh 双 Token、登出黑名单（Redis）、AES 加密登录、验证码前置 |
| 统一响应 | `common/core/response.py` | `{code, detail, requestId, timestamp, data}`，code 表见 exception-handling.md；错误码登记制 |
| 异常处理 | `common/core/exception.py` | 全局兜底脱敏（未预期异常返回通用文案），JWT 40001/40002 协议码 |
| 缓存 | `common/cache/` + `common/base/magic.py` | 四套缓存键规范/TTL/失效矩阵见 [cache.md](cache.md)；MagicCacheData 函数级 + cache_response 视图级 |
| 信号失效 | `system/signal_handler.py` | Menu/UserRole/DeptInfo/UserInfo/SystemConfig/登出 变更即失效权限与路由缓存，含 m2m_changed 挂钩 |
| 通知 | `notifications/` | `@register_message` 显式注册 + `BACKEND_MSG_RENDERERS` 集中注册（T2.4）；站内信/邮件/短信后端；新增后端 1 文件 + 1 行 |
| WebSocket | `message/base.py` | 自定义 `{action, data, mid}` 协议，补类型约束后保持（ADR-003）；ping/userinfo/push_message/chat_message/task_log |
| Celery | `common/celery/` | `@register_as_period_task` 声明式定时任务；default/heavy 队列分离；批量导入分片异步、无 Worker 自动降级同步 |
| 导入导出 | `common/drf/parsers|renders` | CSV(编码探测)/Excel(下拉验证/列宽/样式)/ZIP(AES 加密)；AxiosMultiPartParser 反解 dot-notation（TD-19 登记，更换 HTTP 库需评估） |
| 限流 | `common/core/throttle.py` | login/register/reset_password/upload/download 分类限流（login 50/h 等 6 类） |
| 中间件链 | `server/settings/base.py` | Request-Id 注入、操作日志（动词方法无兜底缺陷已修复 TD-24）、Referer 校验（可选开关）、SQL 统计 |
| 配置系统 | `server/conf.py` + `common/core/config.py` | 三层合并：config.yml/env → 默认值；数据库态 SysConfig/UserConfig 支持模板引用；SECRET_KEY 生产拒启校验 |
| 上传 | `common/core/modelset/upload.py` | 扩展名白名单（png/jpeg/jpg/gif）+ 大小上限；安全复核见 [../security-review.md](../security-review.md) |
| 任务监控 | Flower（`CELERY_FLOWER_AUTH`） | basic-auth 配置化（T5.3 收尾）：未配置认证仅允许绑定 127.0.0.1 |

## 六、前端核心结构

```
Views（RePlusPage 声明式页面） → Components（RePlusPage/RePlusSearch/ReAuth/ReDialog）
→ Store（user/permission/app/multiTags/settings/epTheme，Pinia 单源，T4.2 全覆盖测试）
→ API 层（BaseApi / ViewBaseApi） → HTTP 层（PureHttp：Token 无感刷新、请求排队、multipart 自动切换）
→ Router（动态路由 import.meta.glob + 权限编码映射 permissionAuths）
```

要点：
- **Token 无感刷新**：请求拦截器检测过期 → refresh_token 刷新 → 排队请求批量重放；401 区分 40001/40002；
- **路由权限**：登录后 `/api/system/routes` 返回路由树 + auths 列表，前端动态注册；
- **巨型组件已拆分**（T2.5）：lay-tag/lay-setting/system user hook 均为组装层 + 子组件/composable；
- **构建**（T3.4）：vite advancedChunks 四组分包（vue-core/element-plus/plus-pro/echarts），主 chunk gzip 440KB。

## 七、前后端协作时序（登录 + 首屏）

```
1 GET  /api/system/login/basic          登录方式配置（captcha/encrypted 开关）
2 GET  /api/system/auth/captcha         图片验证码
3 GET  /api/system/auth/token           临时 Token（AES 加密密钥）
4 POST /api/system/login/basic          登录 → access/refresh + lifetime
5 GET  /api/system/routes               动态路由 + 权限编码（列表接口可 with_meta=1 内联元数据）
6 GET  /api/system/userinfo             用户信息
7 WS   /ws/message/{group}/{username}   实时推送通道建立
```

## 八、工程门禁与安全基线

- CI：server pytest（覆盖率门禁 75%）+ ruff + 跨 app import 门禁 + 元数据契约测试；client typecheck/eslint(no-explicit-any error) + vitest 覆盖率阈值 + Playwright E2E（扩展中）；
- 发布：前后端版本一致性校验 → 单架构构建 → trivy 镜像扫描（HIGH/CRITICAL 阻断）→ 多架构推送 → SBOM 附 release（T5.5）；
- 安全基线：CORS/ALLOWED_HOSTS 配置化、SECRET_KEY 拒启、JWT 轮换+黑名单、六类限流、pip-audit 周检、compose 密码 .env 注入；自查档案见 [security-review.md](../security-review.md)。

## 九、架构决策与技术债索引

| ADR | 决策 |
|-----|------|
| [ADR-001](../adr/ADR-001-csrf-jwt-only.md) | CSRF 中间件不启用（JWT-only 架构），以限流/Referer 开关纵深防御 |
| [ADR-002](../adr/ADR-002-demo-app.md) | demo app 保留但默认不启用（XADMIN_APPS 不含） |
| [ADR-003](../adr/ADR-003-websocket-protocol.md) | WebSocket 保持自定义协议，补 Schema 与类型约束 |
| [ADR-004](../adr/ADR-004-django-60-upgrade.md) | 停留 Django 5.2 LTS，2027-01 窗口评估 6.2 LTS |

技术债台账（TD-01 ~ TD-24）与任务看板统一维护于工作区 `docs/半年技术规划-2026.09-2027.02.md`（不在仓库内，个人规划文档）。

## 十、新人上手路径

1. 环境搭建：根 [README](../../README.md) 快速启动 / [ops/deployment.md](../ops/deployment.md)（Docker 与生产部署）；
2. 通读本总览 + [permission.md](permission.md)；
3. 第一个功能：参考 demo app（Book）——后端 model/serializer/filter/viewset 四件套 + 菜单初始化，前端 `new BaseApi("/api/demo/book")` + `<RePlusPage :api="bookApi" />`；
4. 修改核心框架前先读 [cache.md](cache.md) 与对应模块的测试地图，跑通本地 pytest。
