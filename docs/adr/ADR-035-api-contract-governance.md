# ADR-035：API 契约治理（不做 URL 版本化 + 契约测试兜底 + 路由命名约定）

- 状态：已接受（决策：**不做 URL 版本化（no-go）**；以既有契约测试族兜底；新路由 basename 统一 kebab-case，存量不改名）
- 日期：2026-09-14
- 关联：代码审查报告 2026-09-14（A3 API versioning、A5 basename 混用）；
  `docs/schema/*.schema.json`（契约唯一真源）、`xadmin-client/contract/schema`（镜像）、
  `xadmin-client/scripts/check-contract-sync.mjs`、`tests/unit/common/test_contract_schemas.py`、
  `tests/unit/common/test_metadata_schema.py`、`common/core/utils.py::auto_register_app_url`

## 背景

1. 全仓无 URL 版本前缀（无 `/api/v1`），也无 `DEFAULT_VERSION`：响应壳（`code/detail/data`）、
   列表元数据（`search-columns` / `search-fields`）、WS 帧结构一旦发生破坏性变更，URL 层没有退路
   （审查报告建议：以 ADR 明确「不做 + 契约测试兜底」或预留前缀）；
2. 路由 `basename` 三种风格混用：kebab（`chat-room` / `dynamic-form` / `security-*`）、
   snake（`data_dict` / `login_log` / `personal_access_token` / `periodic_task` …）、
   Pascal（`SearchUser` / `SearchDept` / `SearchMenu` / `SearchRole`）；
   审查报告建议「约定 kebab 渐进统一」。

## 决策

### 1. URL 版本化：不做（no-go）

| 维度 | 事实 | 结论 |
|---|---|---|
| 消费者构成 | 前端（`xadmin-client`）与服务端同工作区、同迭代发布；无「无法同步升级」的第三方 REST 消费者 | 版本化的核心收益（多版本共存过渡期）在本项目不成立 |
| 现有纵深 | 开放平台 `ApiApplication`（client-credentials）与出站 webhook 的兼容性由自身契约保障（scope / 回调 HMAC / 事件目录），不依赖 URL 版本 | 引入 `/api/v1` 只增加双向维护与权限点登记成本 |
| 变更控制 | 破坏性变更靠「同批改前端 + 回归/契约测试」控制，历史上未发生失控 | 收益不足 |

**重开条件**（任一满足即重新评估）：

1. 出现无法随版本同步升级的第三方 REST 消费者（如对外分发 SDK）；
2. 需要灰度期「v1 / v2 响应契约共存」；
3. 开放平台从「应用发卡机」演进为完整 OpenAPI 产品面（对外稳定 API 承诺）。

### 2. 契约测试兜底（既有机制，本 ADR 登记为正式保障）

- 契约唯一真源：`xadmin-server/docs/schema/`（`search-columns` / `search-fields` / `api-response` /
  `routes-payload` / `ws-frame` 五份）；
- 前端镜像 `xadmin-client/contract/schema/` 由 `pnpm check:contract` 校验语义一致（忽略缩进/键序），
  漂移即失败；
- 服务端守护：`tests/unit/common/test_contract_schemas.py`（WS 帧与 schema 引用一致性）、
  `test_metadata_schema.py`（列表元数据契约）；
- 纪律：**破坏性变更（响应壳字段、元数据字段、WS 帧、路由 path）必须同批更新
  服务端 schema 源 + 前端镜像 + 相应守护测试**，三者缺一视为未完成。

### 3. 路由命名约定

- **URL path**：`kebab-case`、无尾斜杠（`SimpleRouter(False)`，与 system / notifications / mfa / chat 同口径）；
  权限点 `Menu.path` 正则按实际 URL 登记（权限链按请求路径匹配菜单 path 正则，不依赖 basename）；
- **basename**：
  - 新路由一律 kebab-case（`generate_crud` 模板已按此口径生成）；
  - **存量 snake / Pascal basename 不做一次性全局改名**，事实核对：
    - 权限链不依赖 basename（改名不会动权限码）；
    - 全仓 `reverse()` 仅用于 captcha 图片/音频（`system:captcha-image` 等，来自 `path(name=...)`），与 basename 无关；
    - basename 唯一外部可见处是监控 label（`common/core/middleware.py` → `record_http_request(view_name)` →
      Prometheus `view` 维度）：改名造成历史时间序列断档，且无功能收益；
    - 结论：纯 churn；存量在后续迭代中就近收敛（触及哪个文件顺带统一哪个），不做批量重命名。
- `auto_register_app_url`（`common/core/utils.py`）在导入期扩展 `PERMISSION_SHOW_PREFIX` /
  `PERMISSION_DATA_AUTH_APPS`：保持现状（仅对 `XADMIN_APPS` 中新注册的 app **追加**，不覆盖既有值），
  副作用范围与登记要求已在函数注释中说明。

## 后果

- API 契约的可观测性集中在 schema 文件与契约测试，而非 URL 版本号；
- 新增路由的命名口径以本 ADR + `generate_crud` 模板为准，评审时按此检查；
- 若触发任一重开条件，按「预留前缀」方案评估（`/api/v1` + `DEFAULT_VERSION`，与前端同批切换）。
