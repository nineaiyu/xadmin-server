# 新手陷阱清单（dev-pitfalls）

> 二次开发高频坑集中登记：每条 = **现象 → 原因 → 正确做法**。
> 这些坑的共同特征：**不报错、但功能静默失效**——所以先查这里，再读源码。
> 配套：[guide/first-module-30min.md](guide/first-module-30min.md)（快速上手）、`xadmin-client/e2e/README.md`（测试陷阱表）。

## 一、后端：元数据与权限

### 1. 列表有数据，但单元格全空 / 新增字段不显示

- **现象**：接口返回正常、表格有行，但某些列为空；或模型新增字段后前端完全看不到。
- **原因**：前端列/表单**完全由后端元数据生成**（`search-columns` / `search-fields`），
  序列化器没声明就不会下发。
- **做法**：字段进接口 → `Meta.fields`；进列表默认列 → `Meta.table_fields`；
  字段权限树缺失 → `python manage.py sync_model_field`。改完重启进程。

### 2. 非超管 403 / 整个页面不渲染

- **现象**：超管一切正常，普通角色 403 或页面空白（按钮全部消失）。
- **原因**：权限码 `动作:组件名`（如 `list:CustomerViewSet`）未入库或未授权；
  前端 `hasAuth("list:CustomerViewSet")` 为假时页面不渲染。
- **做法**：`python manage.py doctor` 查缺口 → `sync_menu_permissions` 补齐 → 角色管理里授权。
  新增端点（自定义 `@action`）同样需要权限点，PUT 方法需**手工登记**（生成器不产出 PUT）。

### 3. 新 app 接口 404

- **原因**：app 未注册，或注册了但没有 `your_app/config.py::URLPATTERNS`（此时启动日志有一条
  「缺少 `your_app/config.py` 的 URLPATTERNS，该应用路由未注入」告警——注册后模型才在
  `INSTALLED_APPS` 内，所以「先注册、后由 `generate_crud` 生成 config.py」是正常顺序）。
- **做法**：`config.yml` 的 `XADMIN_APPS: [your_app]`；`python manage.py generate_crud <app>.<Model>`
  会写出 `config.py`（已存在则需 `--force`）；
  改配置后**重启进程**（挂载代码不热加载：`docker compose restart server celery-worker celery-heavy celery-beat`）。

### 4. 数据权限"消失"：手写 `Model.objects.filter(...)` 绕过数据权限

- **红线**：所有列表/详情查询必须走 `filter_queryset` / `get_filter_queryset`
  （`BaseModelSet` 已内置）；直接 `.filter()` 会同时绕过数据权限与部分审计。
- **跨 app 调用**：必须走 `<app>.services`（CI 有静态门禁）。

### 5. DRF 3.16+ 的"可空外键被误判必填"

- **现象**：模型 `UniqueConstraint(condition=...)` 含可空 FK 时，创建请求要求该 FK 必填。
- **原因**：DRF 会为带条件唯一约束生成 `UniqueTogetherValidator`。
- **做法**：序列化器覆写 `get_unique_together_validators()` 返回 `[]`，在 `validate()` 显式查重，
  DB 层用部分唯一索引兜底（项目有守护测试范式可抄）。

### 6. 改了后端代码，接口行为没变

- **原因**：容器是源码挂载 + 进程常驻，**不热加载**；且 `xadmin-celery-*` 是独立进程
  （导出/报表等任务跑在 worker 里，只重启 web 不够）。
- **做法**：`docker compose restart server celery-worker celery-heavy celery-beat`。

## 二、后端：配置与启动

### 7. 首启报 `No config file found` / `SECRET_KEY is required`

- **现状**：无 `config.yml` 时自动回落 `config_example.yml`（开发兜底）并自动生成 `SECRET_KEY`
  到 `data/.secret_key`；**显式配置了 config.yml 但没填 SECRET_KEY 且 DEBUG=false** 才会拒绝启动。
- **做法**：按报错提示三选一（config.yml / 环境变量 / `SECRET_KEY_AUTO_GENERATE=true`）。
  生产务必显式配置（多实例一致 + 已加密数据可解）。

### 8. 中文界面变英文 / 新加的文案不翻译

- **原因**：`locale/*/LC_MESSAGES/django.mo` 未编译（`.mo` 不入库，CI/新环境天然缺失）。
- **做法**：`python manage.py compilemessages`（升级后跑 `python manage.py post_upgrade` 一并处理）。
- **注意**：断言文案的测试要写 zh/en 双语，否则 CI（无 .mo）会挂。

### 9. 改配置不生效

- **原因**：配置有三级（`config.yml` → 环境变量 → 代码默认值）+ 数据库态 `SysConfig`（热更新）。
  运行期改管理页设置走 DB（需缓存失效，一般自动）；改 `config.yml` 必须重启。
- **做法**：`python manage.py expire_caches config_*` 失效缓存；`DEBUG`/端口类改动重启进程。

### 10. 环境异常时先跑 `doctor`

- 一条命令覆盖：密钥来源 / DB / Redis / 语言包 / 权限点缺口 / 模块裁剪 / 前端契约镜像，
  每项附修复命令；有失败项返回非零退出码（可进 CI）。

## 三、前端：页面与渲染

### 11. 找不到 `v-auth` 指令

- **事实**：本项目**没有** `v-auth`。用 `hasAuth("动作:组件名")`（`src/router/utils/auth.ts`）、
  `<Auth value="...">` 组件、或 `getDefaultAuths(...)` 一次算出 RePlusPage 的 `auth` 对象。
- **组件名**：取自 `defineOptions({ name })`，与后端权限码里 `:` 后半段**一字不差**。

### 12. 自定义渲染器/组件不生效

- **原因**：`registerSearchRenderer` / `registerFormRenderer` / `registerDetailRenderer` /
  `registerApiSearchComponents` 是**模块级全局注册**，必须早于页面首次渲染（页面入口顶部调用）。
- **做法**：放进页面模块顶层；DEV 环境注册晚会有 `console.warn`。

### 13. 新增 Element Plus 组件报 `Failed to resolve component`

- **做法**：在 `src/plugins/elementPlus.ts` 手动登记（组件 + `style/css`），有单测比对清单。

### 14. 图标不显示

- **事实**：图标**全离线**，只有随包注册的集可用（`ep` / `ri` / `fa-solid`，见
  `ReIcon/src/iconRegistry.ts` 的 `SET_LOADERS`）；菜单里的图标名来自后端元数据。
- **做法**：用集内图标，或往 `SET_LOADERS` / `offlineIcon.ts` 里补。

### 15. `RePlusPage` 内部深拷贝爆栈

- **做法**：只有 `lodash-es` 的 `cloneDeep` 能处理挂了 renderer 的 columns
  （`@pureadmin/utils` 的 cloneDeep 会因循环引用爆栈）。

### 16. 切路由后请求"莫名失败"

- **原因**：路由切换会**取消来源页面在途请求**（`utils/http/routeCancel.ts`）。
- **做法**：跨页面存活的请求加 `skipRouteCancel` 豁免（boot 级请求已有豁免）。

### 17. 样式互相覆盖 / 暗色不对

- **事实**：`main.ts` 的样式引入顺序（reset → index → tailwind → element-plus → plus-pro）是**契约**，勿调换；
  弹层类全局样式要写非 scoped（teleport 到 body，`scoped` 的 `:deep` 命不中），
  并用 `.el-popover.el-popper.xxx` 这种更高特异性选择器压过 EP 默认样式。

### 18. 启动失败：`platform-config.json` 缺失

- **原因**：`main.ts` 必须成功拉取 `public/platform-config.json` 才挂载应用。
- **做法**：不要删该文件；标题/布局/多标签缓存等开关在这里而不是 `.env`。

### 19. 列表分页/排序不符合预期

- **事实**：RePlusPage 默认 `pageSize=15`、`ordering=-created_time`；树形列表一次性拉全量。
- **做法**：页面级用 `pagination` / `ordering` 覆盖；写 E2E 断言时注意默认排序（见 e2e/README）。

## 四、联调与测试

### 20. 改了后端，E2E 结果不可信

- **原因**：Playwright `reuseExistingServer` 会复用旧后端进程。
- **做法**：后端有改动一律 `pnpm test:e2e:fresh`（先杀旧进程再重拉 + 重新种子）。

### 21. WebKit 偶发失败 ≠ 回归

- **做法**：先隔离单跑该 spec；通过即负载瞬态。真回归的特征是**双浏览器同点失败**
  或隔离复跑仍失败（e2e/README 有完整判据与历史清单）。

### 22. 测试断言写死中文

- **原因**：CI 无 `.mo`（gitignored），文案回退英文。
- **做法**：locale 相关断言用 gettext 同源取值或 zh/en 双语断言。

### 23. 契约漂移（元数据字段改动）

- **做法**：先改后端 `docs/schema` → 同步前端 `contract/schema` → `pnpm gen:metadata-types`；
  CI 的 `check:contract` 会拦截，`doctor` 本地也能发现。

### 24. http 访问时「登录没反应」，一直停在登录页

- **原因**：生产构建（`pnpm build`）的认证 Cookie 带 `Secure`（`src/utils/auth.ts` 的
  `import.meta.env.PROD` 分支）。浏览器只在 `localhost` / `127.0.0.1` 视 http 为可信——
  用 **IP 或域名走 http** 时 Cookie 被拒收，token 存不下 → 接口 401 → 回跳登录页。
- **做法**：改用 https 访问（自签证书在自动化里用 `ignoreHTTPSErrors`；手动测试需信任证书），
  或在非生产构建下调试（DEV/未设 PROD 时 Cookie 不带 Secure）。
  细节与实测记录见 [deployment.md §8](ops/deployment.md)。
