# ADR-096：AI 动作声明按应用下沉与自动发现

- 状态：已交付
- 日期：2026-10-19
- 关联：[ADR-038](ADR-038-ai-actions.md)（受限动作：白名单 + 用户确认 + 权限双门）、
  [ADR-048](ADR-048-ai-unified-tool-layer.md)（统一工具层：注册表 / MCP / function calling 同源）、
  [ADR-045](ADR-045-modular-trimmable-architecture.md)（功能模块裁剪）、
  `ai/services/actions/ai_registry.py`、`ai/services/actions/ai_action_types.py`

## 背景

ADR-038/048 把 AI 能力收口为「注册表 → 工具目录 → 执行收口」的统一链路，但注册表与
声明全部住在 ai 应用内（`ai/services/actions/ai_registry.py` + `ai_builtin_actions.py`）：

- 业务能力的所有权在各自应用，声明却由 ai 应用维护——新增/调整动作要改 ai 应用，
  二开插件更是无法在不改宿主源码的前提下接入；
- 三个内置动作（请假 / 动态表单 / 首页统计）在 ai 侧重写了业务校验与写入，业务规则
  改动会静默漂移；首页统计还把六个 system 端点路径硬编码在 ai 应用里；
- 代码生成器其实已经产出 `<app>/ai_declarations.py`，`doctor` 也已扫描该文件，
  但注册仍要求手工 import 到 ai 应用——「生成即接入」只完成了一半。

## 决策

### D1 声明下沉到所属应用

每个应用可选提供根目录 `ai_declarations.py`，以 `AI_ACTIONS`（兼容
`AI_READ_ACTIONS` / `AI_WRITE_ACTIONS` 两个桶）声明动作，值为 `ActionSpec` 或
`ApiActionSpec`。声明文件是**纯数据**：不查库、模块级不 import 业务模型；需要动态
信息时用回调（如动态表单的 `catalog` 提供可选表单与字段）。

动作归属按 URL 前缀/业务模型判定：身份 23、平台 18、任务 14、审批 12、分析/表单 11、
通知 7、审计 4、文件 4、AI 5、系统设置 5，合计 105（102 声明式 + 3 内置）。除原有
写动作外，本轮补全了各域的只读能力（岗位 / 登录策略 / 账号巡检 / 开放平台 / 任务中心 /
审批流实例与定义 / 报表与大屏 / 表单与提交记录 / 通知模板 / 安全策略读取）。

代码生成器同步对齐：`generate_crud` 产出的骨架使用 `AI_ACTIONS` 约定（原
`AI_READ_ACTIONS` 仍被兼容读取），并在 `--with-module` 时自动为动作标注 `modules`，
生成物的 AI 能力随模块裁剪一起收敛。同一应用生成多个模型时按 dict 内生成块合并
（重复生成幂等、`--force` 只替换本模型块），不会出现第二个 `AI_ACTIONS` 覆盖前一个。

### D2 自动发现，注册表仍唯一

`ai/services/actions/ai_registry.py::action_specs()` 遍历 `apps.get_app_configs()` 导入
`{app}.ai_declarations` 并合并：

- 无声明文件即跳过；文件存在但内部 import 失败必须冒泡（声明坏了不能静默）；
- key 全局唯一，重复 fail-fast；声明字典的 key 必须与 `spec.key` 一致；
- 结果按 key 排序（多环境目录与 prompt 稳定）；
- 进程级缓存，改声明需重启（与 `config.py::URLPATTERNS` / `modules.py` 同口径）；
- **不在 `AppConfig.ready()` 构建**——app registry 与数据库可能未就绪。

`ACTION_SPECS` 改为模块级 `__getattr__` 惰性取数，历史导入面（测试、巡检、契约）
零改动；`ai.contracts.api_action_specs()` 继续作为框架层（`common.contracts`）的
唯一契约出口（`ai.services` 保留兼容转发）。

### D3 机制与声明分层

`ActionSpec` 与 `user_can_visit` / `module_allows` 抽到 `ai/services/actions/ai_action_types.py`
（只依赖 common 与标准库），避免「声明 import 注册表、注册表 import 声明」的循环。
`ai/services/actions/ai_actions.py` 只保留机制：目录构建、草稿解析、权限双门、执行收口、审计。

### D4 模块裁剪对齐

内部 dispatch 直接调用视图函数，**绕过 ASGI 的 `ModuleGateMiddleware`**；而
`user_can_visit` 对超管直接放行。声明新增可选 `modules` 字段（可多个，全部启用才
可见），在目录构建、草稿校验、执行收口三处统一判定。分析/表单、审批、审批流、
运维、Webhook、AI 自身动作已标注所属模块。

### D5 内置动作回归业务应用

- `leave.submit` → `approval/ai_declarations.py`（复用 `validate_leave_payload` / `submit_leave`）；
- `dform.submit` → `dataset/ai_declarations.py`（复用 `validate_submission_data` / 模型写入）；
- `dashboard.overview` → `system/ai_declarations.py`（端点清单与聚合就近维护）。

删除 `ai/utils/ai_builtin_actions.py` 及四个集中声明文件（移入工作区回收站）。

### D6 明确不做：按路由自动生成动作

路由没有参数 schema、审批语义与可用性判断，全量注册会把数百个端点暴露给模型并
撑爆 prompt；白名单红线不变。「扫描」只用于发现**声明**，新端点是否 AI 化仍由
`manage.py ai_tool_audit` + triage 清单驱动。

## 影响

- 新增能力 = 在所属应用加一条声明，零改动 ai 应用；二开插件随 `XADMIN_APPS` 安装自动生效，
  模块裁剪只拦路由、不卸载 app，声明仍参与发现但被 `modules` 判定拦下。
- 三处消费面（助手工具目录 / MCP `tools/list` / 原生 function calling）仍同源，
  前端与协议零改动；无新增权限点，无需灌库。
- `doctor` 改为校验发现结果（声明式 path + 内置动作权限点路径），不再单独扫文件；
  `ai_tool_audit` 的声明面改为合并注册表。

## 验证

- 发现结果 105 个动作、102 声明式 + 3 内置、key 排序稳定；
- 守护测试：key 唯一/格式、path 可解析、声明方法与路由一致、权限点种子存在、
  参数声明合法、工具目录与声明同源，新增重复 key / key 不一致 / 模块停用三个负向用例；
- 后端全量 pytest + ruff/行数/跨应用/缓存键/迁移漂移/文档门禁；
- 相关 AI 集成用例（草稿、执行、审批穿透、MCP、流式）全绿。
