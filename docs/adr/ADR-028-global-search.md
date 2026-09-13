# ADR-028：全局搜索（G9，跨实体 + 顶栏统一入口）

- 状态：已接受
- 日期：2026-09-13
- 关联：年度开发计划 2026.10-2027.09 §四 2027-08（G9）；`common/core/permission.py`（URL 权限门 /
  页面权限数据源）、`common/core/filter.py::get_filter_queryset`（数据权限编译器）；
  `system/views/search/`（既有关系字段搜索，输入型组件用，与本篇互不影响）

## 背景

系统内找人/找文件/找审批单分散在各页面的搜索框里，用户需要先知道「它在哪个页面」。
G9 要一个**顶栏统一入口**：一处输入关键词，跨实体（用户/部门/文件/审批单/操作日志）返回分组结果，
点击直达对应页面。

两条铁律先行（与既有权限体系对齐，缺一不可）：

1. **搜索不得成为绕过页面权限的信息通道** —— 用户搜不到自己无权访问的页面里的数据；
2. **搜索结果的行范围必须与各页面列表口径一致** —— 走同一个数据权限编译器，不另造一套。

## 决策

### 1. 提供者注册表（`system/search.py`），逐实体两道门

| 分组 | 模型 | 搜索字段 | 页面权限门（list URL） | 行级收紧 | 跳转 |
|------|------|----------|------------------------|----------|------|
| 用户 | `UserInfo` | username/nickname/email/phone | `api/system/user` | 数据权限编译器 | `/system/user/index` |
| 部门 | `DeptInfo` | name/code | `api/system/dept` | 数据权限编译器 | `/system/dept/index` |
| 文件 | `UploadFile` | filename | `api/system/file` | 数据权限编译器 | `/system/file/index` |
| 审批单 | `ApprovalRequest` | path/module/object_pk | `api/system/approvals` | 数据权限编译器 + **仅申请人/审批人**（非超管） | `/system/approval/index` |
| 操作日志 | `OperationLog` | path/module/ipaddress | —— **仅超管** | —— | `/system/logs/operation/index` |

- **页面权限门**：`SearchProvider.visible_to` 按 list 权限 URL 匹配用户菜单权限码
  （`get_user_permission` + `get_menu_pk`，与 URL 权限中间件同源）；超管直通（与全局 URL 门同口径）；
- **数据权限门**：命中关键词的查询集统一过 `get_filter_queryset`（fail-closed：无适用授权 = 空结果）；
- 每组 LIMIT 5 + `total`，只下发有命中的分组；`scope` 参数可限定单分组（预留精确检索）。

### 2. 接口与权限码

- `GET /api/system/global-search?keyword=&scope=`（`system/views/search/global_search.py`）；
- URL 权限码 `retrieve:SystemGlobalSearch`（种子 `loadjson/menu.json` + `menumeta.json` 已登记，
  uuid5 确定性 pk，挂在系统管理目录下）；无该权限码的普通用户 403；
- 关键词口径：trim 后 1–50 字符，超界返回空分组（不报错）。

### 3. 检索语义与全文检索引擎化的评估出口

- **基线 `icontains`**（全库可移植、中文可用）。关键词为 SQL LIKE 通配符语义（`%`/`_` 是通配符）：
  Django 的 `icontains` 不带 ESCAPE 子句，**手工转义反而破坏匹配**（已在 E2E 库实证：
  `e2e\_user` 命中 0 行）；通配符只会放大检索范围，不构成注入（参数化查询）或越权（权限门在查询集层）；
- **Postgres 全文检索引擎化（zhparser/pg_trgm）登记为部署侧评估出口**：标准 Postgres 17 镜像无中文
  分词扩展、内置 simple 解析器对中文不可用；单实体规模 + 每组 LIMIT 下 icontains 无性能压力
  （perf.yml 基线可复核）。出现「单实体数十万行 / 搜索 P95 超标」时按部署侧扩展能力再评估。

### 4. 前端：挂在既有顶栏搜索弹窗内（不新增入口）

- `lay-search` 的 `SearchModal` 在菜单匹配结果之下追加「全局搜索」分组区：
  关键词 ≥1 字符时随既有 300ms 防抖调用接口，按分组渲染（组名 + total + 命中文本），
  点击整行 `router.push(group.route)`（由目标页面自身搜索能力承接关键词，不在弹窗里做详情预览）；
- 与菜单结果的关系：菜单结果仍走本地过滤 + 拼音匹配 + 键盘导航；全局分区为**点击型**补充
  （键盘导航只作用于菜单结果，弹窗 footer 计数不变），菜单无命中时不再显示「暂无数据」空态；
- 接口失败静默降级为仅菜单结果（搜索是辅助能力，不因后端抖动打断用户）。

### 5. 明示不做

- 不做命中项在弹窗内的详情预览（点击即跳页，避免弹窗内再套各实体的详情协议）；
- 不做搜索历史/收藏的跨端同步（沿用菜单搜索的本地存储口径）；
- 不做全文检索索引表/向量检索（候选池已有 AI RAG 词频检索先例，升级路径见 §3）。

## 测试与验收

- 服务端（`tests/integration/system/test_global_search.py`，12 例）：
  超管各分组命中与跳转字段、scope 限定、空/超长关键词、通配符安全、
  无权限码 403（URL 门）、有权限码但无数据授权 fail-closed（数据权限门）、
  分组页面权限门（`visible_to` 单测）、审批单「申请人/审批人/超管」行级收紧；
- 前端：locale-keys（新增 `search.globalResult` 双语）+ typecheck + eslint/prettier；
- E2E（`e2e/global-search.e2e.ts`）：顶栏入口搜 `e2e_user` → 用户分组可见 → 点击跳转
  `/system/user/index`；无命中关键词不展示分组；
- 门禁：pytest / ruff（check + format）/ 跨 app 导入 / i18n po / 前端 vitest / prettier。
