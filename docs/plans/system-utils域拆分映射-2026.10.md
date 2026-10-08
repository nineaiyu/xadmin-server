# system app utils/views 域归位映射清单（Phase A）

> 对应任务台账《CODE-REVIEW-TASKS/04-system-app域拆分与服务层下沉.md》Phase A（T04-01 / T04-02 / T04-03）。
> 总原则：**行为零变化**——接口路径、权限码、celery 任务名、菜单种子全部不变；仅移动模块与更新 import 面。
> 子包按 identity / file / audit / task / platform 五域划分，与 Phase C 四域切分（+平台通用）对齐，
> 为后续每域独立成 app 预置目录形态。

## 一、utils 目标子包结构（T04-01）

| 目标子包 | 域语义 | 纳入内容 |
|---|---|---|
| `identity/utils/` | 身份/认证/账号/会话/数据权限 | 16 个平铺文件 + `permission_preview/`、`permission_sync/` 两个既有包整体迁入 |
| `system/utils/file/` | 文件上传/预览/存储/文件访问审计 | 5 个平铺文件 + `preview/` 既有包整体迁入 |
| `system/utils/audit/` | 审计（脱敏/影响面/日志归档） | 3 个平铺文件 |
| `system/utils/task/` | 任务中心/导入导出/webhook/celery 清理实现体 | 10 个平铺文件 |
| `system/utils/platform/` | 平台通用（字典/菜单/标签/代码生成/监控/凭据/种子等） | 17 个平铺文件 |

### 1.1 utils 51 个平铺文件映射

| 原路径 `system/utils/` | 新路径 | 域 | 归属依据（模块自述职责） |
|---|---|---|---|
| account_expiry.py | identity/account_expiry.py | identity | 账号有效期与到期处置（登录拦截/自动停用） |
| account_risk.py | identity/account_risk.py | identity | 账号安全风险巡检与处置 |
| api_grant.py | identity/api_grant.py | identity | 应用级资源授权（权限体系第四层，仅 PAT 凭证） |
| api_grant_catalog.py | identity/api_grant_catalog.py | identity | 应用授权目录与写入校验（自 api_grant 拆出） |
| auth.py | identity/auth.py | identity | 登录校验（验证码/临时令牌/登录日志/异地登录） |
| codegen_fields.py | platform/codegen_fields.py | platform | 代码生成器字段级自定义（开发工具） |
| codegen_gui.py | platform/codegen_gui.py | platform | 代码生成器 GUI 适配层（开发工具） |
| credential.py | platform/credential.py | platform | 凭据治理（系统配置密钥/模型字段加密，平台安全基建） |
| credential_rotate.py | platform/credential_rotate.py | platform | 凭据轮换动作（自 credential 拆出） |
| ctasks.py | task/ctasks.py | task | celery 清理任务的实现体（system/tasks 的调用面） |
| dept_managers.py | identity/dept_managers.py | identity | 部门管理员任命装配 |
| dict.py | platform/dict.py | platform | 数据字典消费端工具（跨 app 读项） |
| file_audit.py | file/file_audit.py | file | 文件访问审计与上传安全策略（file 域自有审计面） |
| impact.py | audit/impact.py | audit | 影响面预检与引用保护（Phase C audit 域清单成员） |
| impersonation.py | identity/impersonation.py | identity | 用户模拟（JWT claim 承载） |
| import_progress.py | task/import_progress.py | task | 异步导入运行期进度上报（导入导出链路） |
| import_report.py | task/import_report.py | task | 导入失败行错误报告（导入导出链路） |
| log_archive.py | audit/log_archive.py | audit | 审计日志冷归档与冷热分层（Phase C audit 域清单成员） |
| login_alert.py | identity/login_alert.py | identity | 新设备/新 IP/新城市登录提醒 |
| login_policy.py | identity/login_policy.py | identity | 登录访问策略判定 |
| mask.py | audit/mask.py | audit | 字段级数据脱敏（Phase C audit 域清单成员） |
| menu.py | platform/menu.py | platform | 菜单/权限点推导（跨 app 权限点治理） |
| metrics.py | platform/metrics.py | platform | 监控面板指标采集（监控运维面） |
| modelfield.py | platform/modelfield.py | platform | 模型字段标签同步（元数据治理） |
| modelset.py | platform/modelset.py | platform | modelset 动作工具（ViewSet 共用动作） |
| module_impact.py | platform/module_impact.py | platform | 功能裁剪预演（模块化架构） |
| monitor_events.py | platform/monitor_events.py | platform | 监控告警记录与事件查询（监控运维面） |
| monitor_export.py | platform/monitor_export.py | platform | 监控报表导出渲染（监控运维面） |
| monitor_history.py | platform/monitor_history.py | platform | 监控历史趋势聚合（监控运维面） |
| oauth.py | identity/oauth.py | identity | OAuth2/OIDC provider 配置与交换（Phase C identity 域） |
| oauth_flavors.py | identity/oauth_flavors.py | identity | 企业 IM 扫码登录 flavor 适配器 |
| oidc.py | identity/oidc.py | identity | 标准 OIDC（discovery/验签/claims 映射） |
| pat_scope.py | identity/pat_scope.py | identity | PAT scope 选项（PAT 属 identity 域） |
| record_stats.py | task/record_stats.py | task | 异步记录（导出/导入/任务）统计口径 |
| rule_meta.py | identity/rule_meta.py | identity | 数据权限规则可读文案（与 permission_preview 配套） |
| seed.py | platform/seed.py | platform | 种子装配预处理（模块裁剪/冲突预检） |
| session.py | identity/session.py | identity | 会话管理（登记/强制下线/过期清理） |
| storage_migrate.py | file/storage_migrate.py | file | 文件存储搬迁（本地 ↔ 对象存储） |
| tags.py | platform/tags.py | platform | 通用标签中心（跨资源白名单打标） |
| task_center.py | task/task_center.py | task | 任务中心：统一任务视图 + 协作式取消 |
| task_center_unified.py | task/task_center_unified.py | task | 任务中心三源合并聚合（自 task_center 拆出） |
| task_log.py | task/task_log.py | task | 任务执行日志增量读取 |
| task_progress.py | task/task_progress.py | task | 任务进度统一更新助手 |
| task_whitelist.py | task/task_whitelist.py | task | 可手动执行任务白名单 |
| upload_category.py | file/upload_category.py | file | 上传文件自动分类 |
| upload_chunk.py | file/upload_chunk.py | file | 分片上传/断点续传协议内核 |
| upload_store.py | file/upload_store.py | file | 上传落库内核（安全校验/去重/存储） |
| user_invite.py | identity/user_invite.py | identity | 邀请开户（令牌 + 邮件 + 激活） |
| user_options.py | identity/user_options.py | identity | 用户候选数据源（选人控件） |
| webauthn.py | identity/webauthn.py | identity | WebAuthn/Passkey 服务端校验 |
| webhook.py | task/webhook.py | task | 出站 Webhook（Phase C task 域清单成员） |

### 1.2 utils 既有 3 个子包整体迁入

| 原路径 | 新路径 | 域 |
|---|---|---|
| system/utils/permission_preview/ | identity/utils/permission_preview/ | identity（数据权限预览） |
| system/utils/permission_sync/ | identity/utils/permission_sync/ | identity（权限点同步） |
| system/utils/preview/ | system/utils/file/preview/ | file（文件预览） |

包内文件不拆不动，`__init__.py` 再导出面原样保留——包内相对 import 不变，
包内指向 `system.utils.rule_meta` 等的绝对 import 随映射改写。

### 1.3 归类争议的裁决说明

- **file_audit → file（非 audit）**：它是文件域的自有审计面（上传/下载/预览/删除留痕）+ 上传
  安全策略，消费方是 upload_store/upload_chunk 与文件中心视图；Phase C audit 域清单
  （操作日志/登录日志/脱敏/impact/log_archive）未列它。
- **ctasks → task**：它是 `system/tasks` celery 任务函数的实现体，唯一存在理由是承载任务逻辑；
  虽混有审计日志清理与文件清理，Phase C task 域成 app 时随任务调度面走。
- **api_grant(_catalog) → identity（非 platform）**：权限体系第四层（模型×动作×字段×行收敛），
  `docs/architecture/permission.md` 将其列为权限模型成员；依附 PAT（identity 域）。
- **credential(_rotate) → platform**：凭据加密治理是平台安全基建（SystemConfig/模型密钥字段），
  不属于任何业务域。
- **（2026-10-08 更新）credential(_rotate) 已下沉 `system/services/`**：判据从「是否属业务域」
  细化为「是否含写副作用」——凭据轮换/重加密落库 + 审计属服务型，随服务层下沉迁至
  `system/services/credential.py` / `system/services/credential_rotate.py`；platform 目录
  分区口径与逐文件清单见 `system/utils/platform/README.md`。
- **（2026-10-08 更新）platform 服务型模块已全部下沉 `system/services/`**：按
  `system/utils/platform/README.md` 的判据（写库 / 事务 / 文件落盘 / 外部副作用），
  `modelfield.py` / `modelset.py` / `seed.py` / `tags.py` / `permission_sync/` 随服务层
  下沉迁至 `system/services/` 同名路径；只读查询型（dict / codegen_gui / monitor_* /
  module_impact / permission_preview）与纯工具型（menu / codegen_fields /
  monitor_export / rule_meta）留在 `system/utils/platform/`。
- **seed / module_impact / modelfield / menu / dict / tags → platform**：装配与元数据治理，
  服务全平台。

## 二、views 顶层 13 个平铺文件归位（T04-03）

既有子包约定：admin/（管理面）/ auth/（认证面）/ search/（全局搜索）/ user/（"我的"面）。
新增 task/（任务域）、open/（开放平台）、platform/（平台页面）三个子包，与 utils 五域口径对齐。

| 原路径 | 新路径 | 子包 | 归属依据 |
|---|---|---|---|
| views/configs.py | views/user/configs.py | user | 个人配置消费端（UserPersonalConfig） |
| views/dashboard.py | views/platform/dashboard.py | platform | 工作台跨域统计（用户/日志趋势） |
| views/directory.py | views/user/directory.py | user | 通讯录浏览（用户面只读名录） |
| views/modules.py | views/platform/modules.py | platform | 功能模块裁剪管理（平台装配） |
| views/monitor.py | views/platform/monitor.py | platform | 系统监控面板（运维面） |
| views/open.py | views/open/open.py | open | 开放平台应用与凭证 |
| views/open_oauth.py | views/open/open_oauth.py | open | OAuth 2.0 授权码端点 |
| views/routes.py | views/user/routes.py | user | 前端动态路由下发（用户面） |
| views/tag.py | views/platform/tag.py | platform | 通用标签中心（跨资源） |
| views/task.py | views/task/task.py | task | 定时任务管理 |
| views/task_periodic.py | views/task/task_periodic.py | task | 周期任务/Cron/间隔计划（自 task.py 拆出） |
| views/task_center.py | views/task/task_center.py | task | 任务中心聚合视图 |
| views/webhook.py | views/task/webhook.py | task | Webhook 订阅与投递（Phase C task 域） |

文件名一律不动（`views/open/open.py` 略冗余，但"仅移动不改行为"优先于命名洁癖；
Phase B 下沉服务层、Phase C 域切分时自然会再调整）。

## 三、行为零变化的保证点

1. **接口路径**：urls.py 的注册路径全部不变，仅 import 来源模块路径更新；菜单种子
   （loadjson/menu.json）存的是 API path 与组件名，不含模块路径，不受影响。
2. **权限码**：`get_view_permissions` 从 URL registry 运行期推导（视图类 + action），
   权限点 code 只依赖路由注册与动作名，不依赖模块路径；既有权限点种子零变化。
3. **celery 任务名**：任务函数本体全部留在 `system/tasks/__init__.py`（任务名 =
   `__module__` + 函数名的仓内约定），utils 迁移不触碰任务注册面。
   注意：`scripts/e2e_seed_scenes.py` 以字符串登记了 `"system.utils.ctasks.auto_clean_tmp_file"`
   演示周期任务名，随映射同步改写为新路径（E2E 演示数据，非生产契约）。
4. **导出/导入重放链**：`ExportRecord`/`ImportRecord` 在**提交时**写入
   `{view.__module__}.{view.__class__.__name__}` 供任务 `import_string` 重放——仅提交后
   数秒内的在途任务受部署窗口影响；仓库内唯一启用该链路的视图是
   `views/admin/codegen.py`（本次不动）。13 个平铺 views 均不挂导入导出动作。
5. **`system/services.py` 契约面**：对外函数名与签名零变化，仅函数体内
   `from system.utils.X import ...` 惰性导入随映射改写；跨 app 消费方零感知。

## 四、迁移执行清单（T04-02 / T04-03）

1. `git mv` 全部文件到目标子包（含 3 个既有子包整体移动）；
2. 新增 5 个 utils 域包 + 3 个 views 域包的 `__init__.py`；
3. 全仓 import 改写（含 `from system.utils import X` 包成员形态、
   `monkeypatch.setattr("system.utils.X...")` 字符串形态、
   `import_module("system.utils.task_center_unified")` 动态形态、`apps.py` 相对形态）；
4. 文档路径引用同步（component-handbook / oauth-login / permission / overview /
   模块化与功能裁剪 / recipes / exception-handling / observability / cache-keys-audit）；
5. `scripts/gen_event_docs.py` 源路径更新并重生成 `docs/open-platform/events.md`；
6. 回归：全量 pytest（串行）+ 六项静态门禁 + `manage.py check` + 路由快照对比。

## 五、验证口径

- `pytest` 全量通过（基线：5076 collected；xdist 下 MFA 集成/锁看门狗 8 例有
  既有资源竞争抖动，串行全绿——以串行口径验收）；
- `manage.py check` 无异常；
- 路由快照：迁移前后 `manage.py show_urls`（或 url 注册 dump）diff 为空；
- 门禁：check_cross_app_imports / check_file_length / check_doc_paths /
  check_doc_facts / check_doc_index / check_tutorial_mirror 全绿；
- `git status` 确认 utils 顶层与 views 顶层除 `__init__.py` 外无残留平铺文件。
