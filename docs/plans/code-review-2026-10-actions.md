# 2026-10 全栈功能审查 · 行动建议与未验证事项

> 主册：[code-review-2026-10.md](code-review-2026-10.md)。本分册汇总全部行动项与未验证事项，可直接并入 `DEV-TASKS.md` 候选（按其现有格式追加）。

## 一、行动建议

| # | 优先级 | 编号 | 建议 | 涉及文件 | 工作量 | 验证方式 | 回归风险 | 依赖 |
|---|---|---|---|---|---|---|---|---|
| 1 | P2 | audit-P2-01 | 删除死权限点 `retrieve:SystemUserLoginLog`：登录日志 ViewSet 无 detail retrieve 路由（仅 list/export/`{pk}/logout`），前端亦未消费该点；授予无效果 | `audit/seeds/menu.json`（删除对应 menu_type=2 条目及 menumeta 对应项） | S | `python manage.py sync_menu_permissions --dry-run`（缺口 0）→ `load_init_json --check`（0 error）→ 复灌种子；菜单管理页 `permission-audit` 应少 1 条 orphan | 低：无消费面；删除前用 `permission-audit` 复核其确在 orphan 名单 | 需先 `load_init_json` 复灌验证 |
| 2 | P1 | 全局-P1-01 | 共享端点登记纪律固化：现存 4 组同 (path,method) 多码已由 `SHARED_PERMISSION_PRIORITY` 确定性消解；建议把「新增共享端点必须登记优先级或改解析」固化进 PR checklist（AGENTS.md 已有文字，缺流程挂点） | `packages/xadmin-common/common/core/permission.py`（无代码改动）+ 工作区 PR 模板/AGENTS | S | 守护测试 `tests/integration/test_shared_permission_endpoints.py`（暂存中） | 无 | 无 |
| 3 | E | analysis-E-01 | 仪表盘卡片联动重拉期间加轻量 loading 指示（推送帧丢弃重拉时视觉静止） | `xadmin-client/src/views/dashboard/components/ChartCard.vue`（fetchData 分支 :345-351） | S | 视觉基线（visual E2E） | 低 | 建议随 Phase 2 前端专项一起处理 |

**说明**：本轮 71 页审查未发现 P0 与功能性 P1 缺陷；上表 3 项外无需立行动项。

## 二、未验证事项（全部 C 级，静态审查边界）

| # | 事项 | 建议验证方式 |
|---|---|---|
| 1 | 登录防枚举延时/统一文案口径（auth-flows 文档声明 vs 实现） | 登录链路集成测试 + 错误响应比对 |
| 2 | WS 通道（screen/dashboard/chat/monitor）鉴权、断线重连、模块裁剪后 4404 语义 | E2E（websocket spec）+ 手工断网演练 |
| 3 | 审批实例多级链完整流转矩阵（加签/转办/回退 × 终态组合） | 引擎单测矩阵补全（现有 test_sensitive_approval_mounts 覆盖挂载面） |
| 4 | MCP server URL 写入侧是否复用 `validate_outbound_config_url` | 读 `ai/views/mcp_client.py` 写入路径 + 一条私网 URL 拒绝用例 |
| 5 | 暂存改动（server 42 文件 / client 14 文件）是否通过全量门禁 | `uv run pytest -n auto` + `pnpm test:run` + 各 check:* |
| 6 | 交付链路（installer/web/docs、干净机安装、升级实测） | 见索引行「2026-10 交付链路审查」（另行安排，不在本轮） |
| 7 | Phase 2 前端专项（横切 A–F + 逐页 UI + 量化指标） | 按 CODE-REVIEW-PROMPT §6 在后续会话执行（先读本套 Phase 1 文档） |

## 三、会话交接（按 PROMPT §9 格式）

```
Phase / 已完成批次 / 下一批起点 / 文档路径 / 未验证事项 / 需要用户决策的问题
Phase 1 全栈功能审查 / 批次 0-8 全部完成（71/71 页，覆盖率 100%）/
  下一批起点：Phase 2 前端专项审查第一轮横切 A（布局体系）/
  文档路径：xadmin-server/docs/plans/code-review-2026-10.md + 9 个分册 + 本册 /
  未验证事项：本册二节 7 项 / 需要用户决策：无（P0=0；3 个行动项均为 S 工作量）
```

## 四、整改进度（2026-10-26）

| # | 编号 | 状态 | 证据 |
|---|---|---|---|
| 1 | audit-P2-01 | ✅ 已完成 | 种子删除权限点/菜单元数据/3 条字段白名单/2 条角色声明；新增 `audit/migrations/0005_remove_dead_loginlog_retrieve_permission.py` 清理存量库；种子基线 728 菜单 / 652 权限点 / 250 白名单 / admin 283·test 87；开发库 `dead_exists=False`，`sync_menu_permissions --dry-run` 缺口 0 |
| 2 | 全局-P1-01 | ✅ 已完成 | `common/core/permission_meta.py::shared_permissions/shared_permission` 声明注册表；`select_shared_permission` 注册表优先、兜底表兼容；设置 basic / 操作日志 / 任务执行 4 组已迁移；`test_core_permission` 双向守护（种子↔声明↔兜底表）；AGENTS 口径更新 |
| 3 | analysis-E-01 | ✅ 已完成 | `ChartCard.vue` 联动重拉 `refreshingCount` 轻量指示（`data-testid=chart-card-refreshing`）+ `dashboard.refreshing` 双语文案；不闪整卡骨架 |

> 验证：后端全量 `7112 passed / 2 skipped`；前端 vitest 2824 例；两侧门禁全绿；`pnpm audit --prod` 0 漏洞。
