# ADR-061：岗位（Post）模型

- 日期：2026-09-27
- 状态：**已交付 + 全面接入**（2026-09-28 二期：审批人解析 / 通知维度 / 权限预览 / 通讯录 / 全局搜索 / 个人中心，见「二期：全面接入」节）
- 背景：组织维度（部门）与权限维度（角色）齐备，缺人员维度（岗位）。对标 ruoyi-vue-pro 的 Post：用于人员标识与名录展示；本仓另需明确「岗位不参与权限判定」以免授权模型分叉。

## 决策

### D1 模型定位：人员维度，不参与权限判定

- 权限只经角色授予，岗位用于人员标识、名录展示与审批人解析的补充筛选；
- 软删除（进回收站），名称 / 编码在**未删除数据**内唯一（`UniqueConstraint(condition=deleted_at IS NULL)`），删除后名称编码可复用；
- `dept` 可空：空 = 全组织通用岗（如「安全员」），非空 = 该部门下的岗位。

### D2 关联与计数

- `UserInfo.posts` 多对多（一人可兼多岗，人力场景常见兼岗 / 代岗）；
- 成员数上列表：`PostSerializer.relation_count_fields = {"user_count": Count("post_query")}` +
  视图混入 `RelationCountMixin`（列表/详情/导出一次预聚合，与删除影响面同源）。

### D3 接口：查看与分配分离

- `/api/system/posts`：CRUD（软删除进回收站）+ `batch-update`（仅批量启停用白名单字段）；
- `GET {pk}/members`：查看成员（权限点 `members:SystemPost`）；
- `POST {pk}/assign`：增量分配 `{add, remove}`（权限点 `assign:SystemPost`），幂等；
  **查看与分配拆成两个端点**：EP 的单点权限只能绑定一种 HTTP 方法，合一会导致
  「名片录授权」与「可改成员」不可区分；
- 成员候选走 `GET /api/system/posts/user-options`（框架 `shared_list` 注册表，与父级 list 权限同口径，无需独立权限点）。

### D4 种子与授权

- 13 个权限点由 `sync_menu_permissions --update-seed` 生成（`PARENT_MENU_MAP` 登记 `api/system/posts` → 页面菜单 `SystemPost`），
  权限码形如 `list:SystemPost` / `assign:SystemPost` / `recyclePurge:SystemPost`；
- 页面菜单与全部权限点授予「管理员 / 演示模式」两个内置角色（与部门管理的授权面一致）；
- AI 工具面：`ai/utils/ai_tool_triage.py` 登记 `system/posts` 为 exempt（组织配置面，不 AI 直调），缺口守护保持清零。

### D5 前端

- 页面 `system/post/index`（RePlusPage）+ 弹窗表单（ReDialog + `PostForm.vue`）+ 成员分配弹窗（`PostMembersDialog.vue`）；
- 启停用列渲染为只读标签（关闭默认 `partialUpdate` 后框架开关恒禁用，避免双入口）；
- 删除保留框架默认入口（自带二次确认）。

## 验证

- 集成测试 12 例：CRUD、未删除唯一（重名/重码 400）、软删除释放名称、批量启停用、成员增量分配幂等、
  失效用户不入成员、非法主键可读报错、`members` 为只读端点（POST 405）、`user-options` 仅回三段信息；
- 前端门禁 + E2E（新增岗位 → 远程搜索分配成员 → 复开弹窗断言成员 → 删除清理）；
- 权限/菜单门禁：`test_menu_view_resolvable.py`（component 指向真实视图）与 `test_permission_seed_coverage.py`（缺口清零）通过。

## 二期：全面接入（2026-09-28）

D1 预留的「审批人解析的补充筛选」与名录展示全面落地；**边界不变：岗位仍不承载任何授权语义**，
所有新能力都是「按岗位找人 / 看人 / 展示人」：

- **审批流引擎**：`ApprovalFlowNode.AssigneeType` 新增 `post`（值 = 岗位 code 逗号串），解析口径
  `posts__is_active + deleted_at IS NULL + code__in`、`.distinct()` 防兼岗重复、沿用「剔除申请人 + 委托展开」；
  无候选时的可读提示区分「岗位不存在/停用」与「岗位无在岗成员」；
- **敏感操作规则级次**：`ApprovalRuleLevel.AssigneeType` 同步新增 `post`，`resolve_level_users` 同口径解析，
  fail-closed 语义不变；审批人候选目录 `candidate-options` 增加 `posts` 段（仅启用岗位）；
- **岗位维度预览**：`get_post_preview` + `PostPreviewAction`（`preview:SystemPost` 权限点），
  前端复用 `RePermissionPreview` 三件套（岗位信息 + 成员采样 + 固定说明，无授权段）；
- **通知/公告按岗位**：`notice_type=POST(5)` + `notice_post` M2M，收件范围/未读/计数仅命中
  「启用岗位的在岗用户」；远程搜索候选 `search/post`（`list:SearchPost`，仅启用岗位）；
- **通讯录**（新页面 `SystemDirectory`，只读）：`/api/system/directory` 轻量名录
  （部门树 / 岗位清单两种视角），数据权限随调用者收口，AI 分级登记 exempt；
- **检索与展示**：全局搜索新增岗位分组（小表走 trgm 豁免，见 docs/architecture/indexes.md）；
  个人中心 userinfo 只读回显岗位；服务端 zh 翻译补齐（含 fuzzy 传播的旧文案修正）。

## 边界

- 不做岗位级权限与数据域（不扩权模型）；不做岗位层级（组织层级由部门表达）；
- 成员分配为增量接口，不提供整体替换（避免漏传即清空）；用户侧写入不提供 UI（管理入口统一在岗位页）；
- 审批节点/通知维度里的岗位仅解析「人」的候选与可见面：岗位被删/停用后解析为空，
  审批链路 fail-closed 拒绝发起（可读文案兜底），不产生任何权限放大。
