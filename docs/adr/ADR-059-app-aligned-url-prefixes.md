# ADR-059：URL 前缀与 app 对齐（approval / ai / dataset 独立挂载）

- 日期：2026-09-27
- 状态：**已交付**（server pytest 全量 EXIT=0 / client vitest 577 + typecheck + 契约 + eslint + build 全绿 / scratch 全新库 doctor 9/9 + 权限点扫描与既有基线一致 / 全量 E2E 通过 / 清库重部署健康验证）
- 背景：ADR-057 拆分时以「权限点路径不变」为硬约束，三域路由经 `path("", include(...))` 空前缀挂回 `system/urls.py`，继续走 `/api/system/...`。用户决策让 URL 与 app 边界对齐（`/api/approval/...`、`/api/ai/...`、`/api/dataset/...`），接受一次性的四类联动改动。

## 决策

### D1 路由与命名空间

- `server/urls.py` 新增三个独立前缀挂载：`^api/approval/`、`^api/ai/`、`^api/dataset/`（namespace 与 app 同名）；`system/urls.py` 移除三个空前缀 include；
- 三 app `urls.py` 显式 `app_name`（视图名由 `system:approval_request-list` 迁为 `approval:approval_request-list` 等；全仓无 `reverse("system:...")` 消费点，零影响）；
- `ai/urls.py` 注册串去掉冗余 `ai/` 前导（`/api/system/ai/assistant` → `/api/ai/assistant`，非 `/api/ai/ai/assistant`）；approval / dataset 注册串不变（`/api/approval/approvals` 等轻微复数冗余保留，避免二次语义改名）。

### D2 联动平移（380 处 server + 395 处 client）

- **权限点种子**：`loadjson/menu.json` 内 `api/system/<域>` 权限点 path 全量平移；`system/utils/permission_sync/constants.py`（MODEL_BINDINGS / 排除清单）同步；
- **模块裁剪**：`common/core/modules/registry.py` 的 ModuleSpec `routes` 正则（`^/api/system/approvals` 等）→ 新前缀；
- **AI 工具层**：`API_ACTION_SPECS` 声明路径、`ai_tool_audit` 豁免前缀（`api/ai/`）、`ai_tool_triage` 资源域键改为整域一条（`approval` / `dataset`，`resource_key` 对非 system 首段取第一段）；
- **全局搜索**：provider `list_url`（`system/search.py`）；**标签中心** visit URL（`system/models/tag.py`）；**审批 ongoing 页签权限** `ONGOING_PERMISSION_PATH`（`approval/views/approval_flow.py`）；**dform 审批回写注册**（`dataset/utils/dform_flow.py`）；
- **路由白名单门**：`PERMISSION_SHOW_PREFIX`（server/settings/custom.py）新增三前缀——缺失会让路由枚举跳过三域（巡检/AI 审计/权限扫描全部失明，首跑即暴露）；
- **客户端**：API 模块文件自 `src/api/system/` 迁至 `src/api/approval|ai|dataset/`（10 文件，66 处 import 站点同步改写），请求路径 395 处平移，E2E spec 同步；
- **契约镜像**：`docs/schema/` 只描述载荷结构不含 URL，零改动（`check:contract` 通过）。

### D3 验证口径

- 权限点扫描基线不变（18 条 DemoBook 存量「未匹配路由」警告，与本重构无关）；
- doctor「代码路由与库内权限点一致」通过；AI 巡检 `stale = []`（声明面与路由面一致）；
- 全新库 scratch 重放 82 迁移 + init + 演示数据，权限点分布 `api/approval` / `api/ai` / `api/dataset` 三前缀、旧前缀 0 行。

## 后果

- URL 自描述：接口前缀即所属 app，二开者按目录/前缀即可定位代码；
- ADR-057 的「路径不变」约束就此解除，此后三域路由演进不再牵动 system 权限点面；
- 存量集成方（若有外部脚本直调旧路径）需按本 ADR 映射表平移（本地部署无外部消费方）。
