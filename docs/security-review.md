# 安全自查清单（归档）

> 背景：半年规划 P5/T5.3「安全自查二期」。本文档归档每轮安全自查的范围、结论与遗留项，
> 后续自查在本文追加新章节，不另立文档。
> 关联：ADR-001（CSRF/JWT-only 决策）、docs/exception-handling.md（错误脱敏）、
> docs/architecture/permission.md（三层权限）。

## 二期自查（2026-09-06）

范围：Flower 认证收尾、X-Frame-Options、Referer 校验、上传类型校验复核。
越权矩阵测试（水平/垂直越权用例集入 CI）的开发侧完成于同日（见下节），实跑验证留待测试窗口。

### 1. Flower 任务监控认证 ✅ 本次收尾

| 项 | 内容 |
|----|------|
| 风险 | 历史版本 `CELERY_FLOWER_AUTH` 默认值硬编码弱口令（`flower:flower123.` / `flower:flower` 双兜底），部署方不改即带弱口令暴露监控面板 |
| 处置 | ① `server/conf.py` 默认值改为空串；② `common/management/commands/services/hands.py` 移除 `or 'flower:flower'` 兜底；③ `services/flower.py` 启动守卫：未配置认证时仅允许绑定 `127.0.0.1`/`localhost`，绑定其他地址直接 `sys.exit(11)` 拒绝启动，未配置认证时不再向 flower 传空的 `--basic-auth=` 参数；④ `config_example.yml` 补充配置示例与说明 |
| 验收 | 全仓 grep 无 `flower123`/`flower:flower` 硬编码残留；生产部署必须显式配置 `CELERY_FLOWER_AUTH` 才能对外暴露监控面板 |
| 面板访问链路 | 管理台经 `common/celery/flower.py` 代理访问，代理侧自动携带所配置的 basic-auth，前端无需感知 |

### 2. X-Frame-Options ✅ 无需变更

- `XFrameOptionsMiddleware` 已启用（`server/settings/base.py` 中间件链），未显式设置 `X_FRAME_OPTIONS`，取 Django 默认 `SAMEORIGIN`，管理台页面不可被第三方 iframe 嵌套。
- 既有豁免均为有意保留：`common/swagger/views.py`（API 文档页）、`common/celery/flower.py`（Flower 代理页）需要以 iframe 内嵌进管理台，属功能必需。
- 结论：默认防线有效，豁免面最小化，记录即可。

### 3. Referer 校验 ✅ 默认关闭属合理决策

- `REFERER_CHECK_ENABLED`（`server/conf.py` settings 段，实现在 `server/middleware.py`）默认 `False`。
- JWT-only 架构（ADR-001）下 API 不依赖 Cookie 凭证，CSRF/Referer 伪造面远小于 Cookie 会话架构；开启开关可作为纵深防御选项。
- 结论：维持默认关闭；面向纯浏览器 Cookie 场景的部署可在 config.yml 打开。

### 4. 上传类型校验 ✅ 白名单已有，记录一个低风险项

- `common/core/modelset/upload.py`：扩展名白名单 `FILE_UPLOAD_TYPE = ["png", "jpeg", "jpg", "gif"]` + 大小上限（`FILE_UPLOAD_SIZE`，可按站点配置 `PICTURE_UPLOAD_SIZE`），不符合即拒绝（code=1002/1003）。
- 低风险记录：未校验文件 magic bytes / 实际内容类型，理论上可在白名单扩展名内伪装内容。上传目录非可执行目录、Django 静态服务不解析脚本，可利用面很小。
- 处置：不阻塞发版；后续如引入更广泛文件类型（办公文档/压缩包）上传，必须同步引入 python-magic 内容校验。

### 5. 既有基线复核确认（未发现回退）

- SECRET_KEY 生产拒启校验、ALLOWED_HOSTS/CORS 配置化（conf.py 默认收紧，`config_example.yml` 有注释示例）。
- JWT 双 Token 轮换 + 黑名单、六类接口限流（login 50/h 等）、登录锁定与异地登录检测（`SECURITY_*` 配置段）。
- compose 无 privileged、PG/Redis 密码经 `.env` 注入。

## 越权矩阵用例集（2026-09-06，T5.3 开发侧交付）

`tests/integration/system/test_privilege_escalation_matrix.py`：水平/垂直越权 HTTP 集成测试 19 例
（矩阵编号 M01-M17，随 pytest 全量进 CI，`test.yml` 无需改动）。

设计要点：

- **载体选择**：数据权限以 demo.Book（owner 字段 `admin`）为载体，垂直越权以 system.user 管理端接口为目标；
  菜单授权遵循生产种子惯例（列表路由 `$` 精确锚定、详情路由 `(?P<pk>[^/.]+)$` 正则，见 `loadjson/menu.json`）；
- **矩阵分层**：认证边界（匿名 401 / 伪造 JWT 401+40001）→ 接口权限（无菜单 403、方法越权 403、
  列表授权不隐含详情授权、白名单路由方法面、角色/菜单停用即时吊销）→ 自提权（关联字段 roles 经数据
  权限过滤，无法给自己授予不可见角色，400 且零副作用）→ 数据权限（列表水平隔离、读/改/删他人数据一律
  400 且零副作用、未授权默认拒绝、菜单作用域授权不跨菜单泄漏）→ 字段权限（白名单同时约束读侧响应裁剪
  与写侧字段忽略）；
- **缓存纪律**：权限结果按用户+方法缓存（MagicCacheData 24h），每条用例在首个请求前完成全部授权布置，
  用例间由 conftest `_clean_cache` 隔离；
- **验收口径**：T5.3 规划要求「越权用例 ≥10 条入 CI」，实际交付 19 条；断言同时覆盖 HTTP 状态、业务码与
  数据库零副作用（被拒操作不留痕）。

## 依赖升级窗口一期（2026-09-06，P5/T5.1 前置执行）

pip-audit（2.10.1，OSV/PyPI 数据库）对生产依赖实测：**45 个已知漏洞 / 5 个包**，全部有修复版本，已一次性清零：

| 包 | 升级 | 修复的漏洞 |
|----|------|-----------|
| django | 5.2.9 → **5.2.17** | PYSEC-2026-42~55/197~201/2090~2092/2448~2449/3717 等 20+ 项（LTS 补丁系列内升级） |
| djangorestframework | 3.16.1 → **3.17.2** | CVE-2026-73228 / CVE-2026-73229 |
| daphne | 4.2.1 → **4.2.3** | PYSEC-2026-213 / PYSEC-2026-214 |
| pyzipper | 0.3.6 → **0.4.0** | PYSEC-2026-3044 |
| requests | 2.32.5 → **2.33.0** | PYSEC-2026-2275 |
| openpyxl | 3.2.0b1 → **3.1.5** | TD-18 收尾：PyPI 上 3.2 系列仅有 beta，回退至最新稳定版 |

复测：`pip-audit` **0 漏洞**；`manage.py check` 无告警；`manage.py spectacular` 正常导出（200 paths）。未跑 pytest（本轮按「忽略测试」约定），合入前 CI 门禁兜底。

client 侧（pnpm audit 11.25，2026-09-06 实测）：**40 漏洞（20 high / 16 moderate / 4 low），全部位于传递依赖**（构建链 rollup/esbuild/postcss/picomatch/nanoid 等 + vue3-ts-jsoneditor 引入的 devalue/svelte/preact/fast-uri/form-data/lodash-es）。直接依赖整体较新（落后多为 dev 工具 minor），未动 lockfile——修复需升级 `vue3-ts-jsoneditor` 3.3→3.4.1 并刷新构建链，留待升级窗口在独立分支 + 全量门禁验证。另登记：`crypto-js` 已被上游标记 Deprecated，窗口期评估替换（WebCrypto 原生 API 或 aes-js）。

## 遗留项

| 项 | 归属 | 说明 |
|----|------|------|
| 越权矩阵测试（水平/垂直越权用例 ≥10 条入 CI） | ✅ 开发完成 | 2026-09-06 交付 19 例（M01-M17）入 `tests/integration/`，实跑验证待测试窗口，见上节 |
| 上传 magic bytes 校验 | 按需 | 仅在扩展非图片类型上传时升级为必做 |
| client pnpm audit 高危清零（40 → 0） | P5/T5.1 升级窗口 | vue3-ts-jsoneditor 3.4.1 + 构建链刷新，独立分支 + 全量门禁；crypto-js 弃用替换评估 |
| server pip-audit | ✅ 已清零 | 2026-09-06，见上节 |
