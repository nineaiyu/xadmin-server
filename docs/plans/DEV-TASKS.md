# xadmin 开发任务总清单（2026-10 起）

> 生成：2026-10-09。由工作区既有开发文档收敛重写——`NEXT-DEV-PLAN.md`（执行记录一~二十二）、
> `CODE-REVIEW-TASKS/`（13 份任务台账 + 对账总表）、两份代码审查报告、`PHASE-C-HANDOFF.md`。
> **上述文档的结论已全部收敛进本清单与仓库内两份台账**（见下方权威源）；原始归档件已随
> 2026-10-14 工作区文档整理清理，追溯历史细节以仓库内台账 / ADR / git 提交为准。
>
> 本清单是**工作区级唯一待办入口**（2026-10-14 由工作区根迁入本目录，纳入版本保存）；
> 同目录两份长期台账是触发制 / 时间窗的权威源，本文摘其要点，两处不一致时以它们为准再回写本文：
>
> - `触发制任务清单-长期.md` —— 触发条件 / 红线重开 / 供应链 / 时间窗 / 滚动债；
> - `下阶段长期计划-2026.10.md` —— 月度窗口排期 / 贯穿专项 / 候选池。
>
> **纪律**（沿用项目规约）：触发条件未命中不立项、不占窗口、不做预备工作；每项独立立项、
> 独立 commit；结构 / 接口变更先 ADR；新功能 100% 带测试；改动后端后跑 `pnpm test:e2e:fresh`；
> 只有用户明确说提交才 commit；改动完成后保留工作区待人工检查（桌面级检查项 = 门禁全绿 + 编号纪律自查）。

---

## 〇、状态快照（2026-10-09，2026-10-14 追加 5.0 发布批次）

- **2026-10-14 5.0.0 发布批次（改动留工作区，待用户指示提交）**：迁移链收敛为初始迁移
  （14 app → `0001_initial` + 7 个 `0002_initial`，以数据库目录元数据比对证明结构等价：126 表 /
  1376 列 / 787 索引 / 585 约束全等）+ 新增 `manage.py upgrade_ledger`（4.2.5 链账本认领，
  `upgrade_check` 增 `legacy-chain-reconcilable` 判定，安装器升级链路已串联）；
  种子重生成（权限点缺口 0 / 模型字段树 +157 / 清菜单软删历史行 / 删含口令哈希的孤儿种子）；
  演示模式完善（`init_data --with-demo` 打印演示账号，安装器 `WITH_DEMO_DATA=1`）；
  文档体系整理（平台 `CHANGELOG.md`、`docs/ops/upgrade-5.0.md`、ADR-093、索引与版本事实同步、
  路径口径 `utils/`→`ops/` 修正、五仓发行说明补「升级注意」分组）；AGENTS.md 全面重写
  （工作区 + 四仓）；本地清库重灌 + 全量测试与 E2E 见
  [RELEASE-5.0.0.md](RELEASE-5.0.0.md)。**安装器真实缺陷修复**：`utils/init_data.py`（该路径在
  仓库中不存在，安装到初始化步骤必然失败）→ `ops/init_data.py`。
  **发布前复核批（同日晚些时候）**：五仓改动冻结后全量重跑——后端 pytest exit=0 ×2、mypy
  864 文件 0 错（修复 `migration_ledger.py` 一处失效 ignore）、ruff / 脚本 / 文档七门禁与
  前端 vitest 2152 + 全部门禁 + 文档站构建 + 安装器 `bash -n` 全绿；全量 E2E 4 路并行
  690 passed / 2 failed / 1 flaky，3 个负载敏感用例逐个隔离复跑全绿（非回归）。
  顺带收口：`xadmin-web` 补齐 AGENTS.md（五仓规范齐全）、后端 README 补 5.0 版本横幅、
  工作区根文档整理（已交付一次性工作文档归档 `_delete/workspace-working-docs-20261014/`，
  失效的历史归档引用已在 README / 本文修正）。
  **对标叙事与台账编号清理批（同日晚些时候）**：两仓源码 / 样式 / 组件 / 测试 / 脚本 / CI /
  种子注释中的「对标归属说明」与「任务台账编号」全部去除（保留语义与数值口径；
  越权矩阵用例编号与测试夹具值按原样保留），`xadmin-client/docs` 两篇同步改写，
  `docs/plans` 对标任务书移入工作区回收站并在 plans/README 登记去向；
  清理后复跑：后端 pytest exit=0 + ruff + 文档 6 门禁 + makemigrations 全绿、
  前端 vitest 313 文件 2152 例 + typecheck + lint + 6 项 check 全绿。

- **2026-10-14 E2E 档缓存替身能力补齐（`ttl`，用户点名顺手修复）**：`FakeRedisCache` 补 `ttl`（对齐 django-redis 口径：键不存在 0 / 无过期 None / 其余剩余秒）——此前缺失时，验证码重复发送的限流闸门 `CodeSendOverRate(cache.ttl(limit_key))` 在入参求值处抛 AttributeError（未预期 500 + Traceback，与既有 `iter_keys` 同类缺口，E2E 跑批日志中被淹没）。守护：单测 `test_fake_redis_cache.py` 新增 2 例（ttl 语义 + 限流链路可读文案，先红后绿）；E2E 新增 `verify-code-resend.e2e.ts`（专属邮箱 `e2e_approver@example.com`，与 reset-password 的 xadmin@dvcloud.xin 隔离限流窗口；幂等断言「限流响应含可读秒数且非未预期异常文案」，`--repeat-each=2` 双路径实测 2 passed）。
- **已知边界（未修，待决策）**：验证码限流异常 `CodeSendOverRate` / `CodeSendTooFrequently` 未覆写 `status_code`，HTTP 语义为 500（前端可读到 detail 文案，但 daphne 日志记 "Internal Server Error"）；若改 400/429 需同步核对前端 `errorStrategies` 与契约文档。

## 〇-历史

> 2026-10-13 及更早的历史执行记录（2026-09 审查整改收口、2026-10 各批交付与推送明细）
> 已归档至 [archive/工作区开发任务执行记录-2026.10.md](archive/工作区开发任务执行记录-2026.10.md)。

## 一、近期待办（2026-10 ~ 2026-11）

| # | 事项 | 验收 / 触发口径 |
|---|---|---|
| 1 | ~~工作区改动复核入库~~ | **已完成（2026-10-09）**：server `35d8e0dc` / `45c3c8a8` + installer `3348910` 入库并推送；门禁全量复验通过 |
| 2 | ~~F6 容器化测试验收终判~~ | **已完成（2026-10-09）：通过（达标）**——时长 ✅ + 9 个红 run 全部分诊收敛（零真库基建故障）；字面「连续 2 周绿」余窗顺延至 10-21 观察；处置方案（回退 sqlite）不适用 |
| 3 | T5 Renovate 验收 | 下次发布窗口合 main 后 `gh workflow run renovate.yml` 补跑（renovate.yml 仅在 dev，非默认分支 dispatch 404） |
| 4 | T6 SLO 校准 | 数据成熟（满 3 个月）即校准回填 `docs/metrics.md` |

> 2026-11 窗口两项主项（存量库升级通道、HNSW 向量定型实测）已于 2026-10-09 提前完成，无需再排。

---

## 二、月度窗口排期（滚动，1 主项 / 窗口）

| 窗口 | 主项 | 说明 / 验收口径 |
|---|---|---|
| 2026-12 | 依赖窗口 | D2 TS 7.1 重评 / D3 Node 26 转正 / O9-6 停维依赖季度复核；独立分支 + 全量门禁 |
| 2027-01 | mypy 严格度提升试点 | **✅ 内核 2026-10-09 完成**（9 flag 子集；**不可写 `strict = true`**——mypy 2.x per-module strict 会全局生效；守护测试 `tests/unit/test_mypy_strict_pilot.py`）；**✅ 业务 app 全量收口（2026-10-09）**：第一批 7 个（captcha/demo/devtools/integrations/mfa/server/settings，722 处）+ 第二批 11 个（audit/file/demo_seed/notifications/task/message/dataset/approval/ai/system/identity，4092 处）存量告警清零入列；全仓 mypy **860 文件 0 错**（无未推广面，`PROMOTED_APPS` 与配置块双向校验） |
| 2027-01 | 基线清偿批次一 | **✅ 2026-10-09 提前完成**：新增 `scripts/check_function_length.py`（AST ≥100 行即失败，基线只减不增；CI + PR 模板 + 6 例自测）；函数净减 7 → 2（余 113/111 登记基线） |
| 2027-02 | 二开实战验证 | **✅ 2026-10-09 提前完成**：示例分发包 `examples/plugins/xadmin-demo-plugin`（entry point + `ready()` 双路径、路由 / WS / 周期任务 / 自有迁移 / 模块声明）+ 教程 `docs/guide/plugin-development.md`；真机 `pip install` 与 `XADMIN_APPS` 注册双进程实证 + 停用三层拦截实证，11 例守卫测试 |
| 2027-03 | 备份恢复季度演练 + 半年复盘 | 按既有演练脚本口径执行，回填 `docs/ops/backup-drill-*`；对账本计划半年执行度 |

> 排期原则：按需插槽制，连续两个窗口未启动的主项降级回候选池并说明原因。

---

## 三、触发制任务（⏸ 条件未到不立项、不占窗口、不做预备工作）

1. **TG-1 JSONField 慢查询逐一物化索引**（唯一开放中的开发类触发项）
   - 触发条件：生产出现真实慢查询且定位到具体 JSON 字段查询路径；
   - 2026-10-09 复核：全量盘点（模型层 64 字段 / 11 个查询点）+ 20 万 / 200 万行真实 PG 实测 ——
     筛选链路 `filter_data @>` GIN 命中正常；dataset 键提取不可静态索引；webhook / `extra` 属受控小表低频面；
     **零新增索引**，补 `filter_data` GIN 守护 5 例。触发条件不变，维持待命。

2. **红线（🚫 明确不做，命中重开条件才重评）**

| 项 | 重开条件 | 重开后第一动作 |
|---|---|---|
| 多租户 | SaaS 商业化诉求 | 重走 ADR（数据隔离读 / 写 / 巡检三面已有） |
| 独立移动 APP / uni-app | 原生能力 / 商店分发 / 移动占比 >40% | 先实测移动端数据再立项 |
| 工作流 BPMN 化 | 复杂编排诉求 | 评估自研引擎扩展 vs 引擎替换 |
| 在线建表（运行期 DDL） | 无既定条件（明确不投） | 需产品层重新决策 |
| AI 成本计价 | 结算诉求 / 云支出限额 / 分发场景任一 | 预研已固化：定价表 → 记录级成本快照 → 汇总展示三层 |
| SAML / CAS SSO | 企业客户需求 | 走开放平台 OAuth2 既有扩展点评估 |
| AI 多供应商原生适配 | 真实客户需求 | 先核实 OpenAI 兼容层覆盖缺口 |
| common 独立包化（RePlusPage 前端半边） | 多个前端仓复用组件库的真实需求 | 后端半边已交付（uv 工作区 + settings 契约 + 发布渠道）；前端触发复核未命中维持待命，命中后走 pnpm workspace 源码包路径 |
| pgbouncer / `fields` 端点内继续优化 | 无（连接池债已根治、无单点热点） | 不重开；容量问题走 worker 档位 |

3. **供应链与安全登记（周期复核）**
   - **O9-6 停维依赖季度台账**：`unicodecsv` / `django-ranged-response` / `user-agents` / `ldap3` / `pilkit`
     逐包复核（`pyexcel` 已核实仍活跃移出清单）；处置路径与重开条件见 `security-review.md` 七期 S-2；
   - **O9-7 wangEditor 替换触发三条件**：安全公告无上游修复 / 协同编辑等产品需求 / Vue 主版本致适配层不可用；
   - **O8-7 CSP Django 侧 enforce 重开条件**：swagger enforce 兼容性完成一轮观察，或出现绕过 nginx 直连 Django 的 HTML 面；
   - **其他已接受安全项**：JWT 存 JS 可读 Cookie、HTTP 直连无 Secure、注册账号占用提示、Django 6.0.8 EOL 监控等，
     理由与重开条件见 `security-review.md` S-6 台账（季度窗口随 S-6 一并复核）。

---

## 四、时间窗（🕐 到点才动，到期未触发先复核窗口判断）

| 编号 | 组件 | 当前 → 目标 | 触发 / 阻断物 |
|---|---|---|---|
| D1 | Django | 6.0.8 → 6.2 LTS | 唯一阻断：`django-celery-beat 2.9.0` 声明 `Django<6.1`；声明放宽或 6.2 全家桶覆盖即动 |
| D2 | TypeScript | 6.0.3 → 7.1 后重评 | 预计 2026-12（受 vue-tsc 阻断）；准备项已交付（`typecheck:tsc` / `typecheck:vue` 拆分） |
| D3 | Node | 24 → 26 LTS | 2026-Q4 转正后（晋升一般在 10 月下旬）；`.nvmrc` / `Dockerfile-base` / `engines` 三处同步 |
| D4 | Vue | 3.5 → 3.6 stable | stable 发布后；Vapor opt-in，约 0.5 人日 |
| D5 | 跟踪项 | Celery 6 / Python 3.15 | 2027-12 / 3.15.1+；触发即评估 |

---

## 五、贯穿专项与滚动债

**贯穿专项（随改动面持续，非触发制）**

1. **测试隔离治理**：`transaction=True` 用例的 DDL 不随 TRUNCATE 回滚（schema 跨用例泄漏）——
   为涉及 DDL 的用例建立 schema 重置约定；排查剩余 module 作用域提交型 fixture
   （原待核 `tests/unit/common/test_media_serve.py` 已闭环：fixture 均函数作用域、无跨用例状态，非治理对象）。
   **2026-10-09 收口三例**（同家族：进程级状态 / 共享目录跨用例残留）：①用例内定义模型会注册进 app
   registry（`test_auto_clean_file.py::PlainFileModel`）→ 同 worker 后续 `upgrade_check` 体检按注册表
   列举期望表时误报缺表 → finally 内 `apps.all_models.pop` + `clear_cache()`；②存储缓存清理用例按绝对
   计数断言（`test_storage_backend.py::test_clean_storage_cache`）→ 同 worker MEDIA_ROOT 跨运行复用带出
   历史遗留文件 → 改断言语义（目标文件被清 + 至少 1 个），不再断言精确计数；③`SwitchableStorage` 委托
   缓存指纹纳入 `MEDIA_ROOT` / `MEDIA_URL`（原指纹只含 SysConfig 键，同 worker 内改 MEDIA_ROOT 的用例
   落盘到旧目录 → 新增 `delegate_fingerprint` 作 `_current()` 重建判据，守护 2 例）。
   单例红登记：`test_oauth_flavors::TestCallbackIntegration::test_auto_create_and_unique_binding`
   回调 1006 非确定性（隔离复跑 30 passed），按「真容器 xdist 计时 / 状态敏感抖动」家族登记，
   不做 skip；频次上升再定位缓存键 / TTL 竞争。
   2026-10-09 mypy 推广轮新登记两例（同家族，均非本轮改动引入）——两例均已于当日收口：
   ①`tests/integration/plugins/test_plugin_demo.py::TestModuleDeclaration::test_release_preset_report_and_snippet_include_plugin`
   在 `pytest tests/integration -n auto` 下确定性失败（`MODULE_ENABLE 中存在未知模块：demo_plugin`）——**2026-10-09 已收口**：
   根因 = `plugin_installed_as_app` fixture 注入插件 AppConfig 后未清模块清单 lru 缓存（`discovered_modules` / `all_module_specs` / `module_index`），
   该用例又无 `module_config` 顺带清理 → 命中同 worker 其他测试构建的陈旧清单；修法 = fixture 注入后与还原后各 `reset_module_state()`
   （`plugin_importable` module teardown 同步补一次）；复现 = 最小组合「先构建无插件缓存再跑该用例」（复现文件已移入 `_delete/`），
   修复后 `tests/integration -n auto` 全量绿；
   ②`tests/unit/common/test_auto_clean_file.py::TestOwnFileCleanup::test_soft_delete_keeps_file_hard_delete_removes`
   与 `tests/unit/system/test_upload_preview.py` 同批运行时失败（隔离 / 两两组合全绿）——**2026-10-09 已收口**：
   根因是 `SwitchableStorage` 委托缓存指纹（`config_fingerprint`）不含 `MEDIA_ROOT`，`media_root` fixture 改根目录后
   委托实例不重建、后续用例落盘到旧目录；修复 = 新增 `delegate_fingerprint`（SysConfig 键 + `MEDIA_ROOT` / `MEDIA_URL`）
   作 `_current()` 重建判据（运行期改 MEDIA_ROOT 亦生效），守护 2 例；`tests/unit -n auto` 全量与集成批绿。
2. **会话级锁统一封装**：`pg_advisory_lock` 全仓仅 `ai_vector_ddl` 一处（savepoint 口径已修复）；
   新增锁必须走统一 helper + 守护用例，禁止裸 `finally` 解锁。
3. **滚动债（只减不增，改到哪清到哪）**：

| 项 | 当前基线 | 守护 |
|---|---|---|
| 宽泛异常吞噬（`except Exception`） | 余量只减不增（存量已治理 36+ 处） | 随改动面清理 + code review |
| 前端 `as unknown as` | **0 处（2026-10-11 清零）** | `scripts/check-as-unknown.mjs` |
| 前端超长 hook | **基线清零（2026-10-11）**：≥240 行 0 个、≥120 行 0 个（四个超长 hook 已按域拆分） | 行数门禁 |
| 后端超长函数 | **基线清零（2026-10-11）**：`scripts/check_function_length.py`（AST 计行，≥100 行即失败、基线只减不增）；两存量项（113 / 111）已按产物族 / 风险族拆分达标，超阈值函数 0 个 | `scripts/check_function_length.py` |
| 后端巨型文件（>500 行） | 存量基线 0 项 | `scripts/check_file_length.py` |

---

## 六、候选池（启动前需产品优先级确认）

| 项 | 说明 / 前置条件 |
|---|---|
| 适老 / 可读性轻方案 | 用户级字号设置或浏览器缩放引导（不动全局样式；全局主题方案已否决） |
| print.ts 前端打印 | 零依赖，待有打印需求时引入 |
| 二开脚手架复评 | 2026-10-09 现状更新：示例分发包 + 教程章节已交付（2027-02 窗口），复评条件收窄为「出现多人协作诉求」 |
| 移动端填报 / 审批 | 触发条件：移动端访问占比超阈值（15%~20% 区间届时取值）或出现外勤 / 巡检 / 车间审批真实诉求；命中后最小切片 = 审批待办 + 一张 dform 表单提交的 H5 渲染 PoC |
| ~~大屏画布（ADR-086 ~ ADR-090）~~ **五期均已交付（2026-10-12）** | 栅格画布之外新增「自由像素布局」模式（`layout_mode` + `free_layout` 双载荷、允许重叠、`ReResize` 承接八向缩放 + 参考线吸附、投屏按设计画幅等比缩放）；二期补编辑效率四项（zoom-to-fit 缩放编辑 + `Ctrl+滚轮` 指针锚点、多选与对齐分布、复制粘贴跨屏剪贴板、内置模板 + 我的模板）。三期补「团队模板（服务端 `ScreenTemplate`） + 模板 JSON 导入 + 旋转/入场动画/富文本 + 元素级网格吸附 + zoom 键盘档位 + 多选批量改属性 + 手柄屏幕尺寸恒定」。四期（[ADR-089](../adr/ADR-089-screen-canvas-boundary-followups.md)）收回动画时序（时长/延迟/循环）与「多元素批量对齐网格」，其余条目登记为触发制。五期（[ADR-090](../adr/ADR-090-screen-canvas-advanced-4.md)）交付进阶四项：富文本扩展（链接/列表/图片/表格 + 选区工具条）、旋转 OBB 几何（框选按旋转矩形 / 对齐与吸附按视觉盒）、视频与自定义 HTML 窗格、模板分组 + 版本回滚 + 跨设备云端剪贴板。落地记录见 `docs/adr/ADR-086-screen-free-canvas.md` ~ `ADR-090-screen-canvas-advanced-4.md` §落地记录；E2E 覆盖 `screen-designer` / `screen-free-canvas`（一期）/ `screen-free-canvas-2`（二期）/ `screen-canvas-toolkit`（三期）/ `screen-canvas-advanced`（五期：旋转几何 / 版本回滚 / 云端剪贴板） |

> 2026-10-09 完成 / 核销两项：**ASGI 异步中间件链迁移**已于 2026-10-02 交付
> （ADR-078，`33b941e1`：4 项双模化 + 3 项显式同步边界；本日收口复核 + 链能力守护 2 例）；
> **缓存与索引季度复核**已完成（`check_cache_keys.py --strict` 退出码 0；`indexes.md` 全量
> 对齐 64 项；`cache-keys-audit.md` 登记表复核，见各文档 §五）。

---

## 七、历史审查遗留登记（维持评估，防重复立项）

> 来源：两轮代码审查的处置结论，原台账（对账总表 / 处置表）已归档。
> 以下条目**维持现行口径不改**；如需重新评估，先读这里的理由，避免重复立项。

**维持不改 11 条**

| ID | 条目 | 维持理由 |
|---|---|---|
| R12-4 | 模块页 dirty 深比较 | 实为 computed（Vue 已缓存），报告「每次访问 stringify」证据不成立 |
| R13-6 | codegen `payloadSignature` 全量 stringify | watch 源形态带 600ms 防抖，字段表规模下开销可接受 |
| R19-2 | force-logout 的 pk 语义反直觉 | 语义可用，已有注释口径 |
| R22-2 | 登录日志 online 列对 HTTP 登录恒 "/" | 后端口径即如此，渲染已是合理降级 |
| R30-4 | Setting destroy API 面开放（UI 全遮蔽） | post_delete 热更闭环后删除已安全，保留管理 API 面 |
| R1.2-6 | 渠道 / 集成等默认关闭项 | 信息项非缺陷（运维知悉即可） |
| R3.2-2 | file / audit 模型层守护方法未下沉 service | 已随四域拆分归位域内，内聚合理 |
| R3.4-2 | 消息类文件组织散落残余 | 注册机制已统一（register_message），剩余为文件组织美观项 |
| R4.2-3 | 运行环境门槛（Python / Node 版本） | 技术栈门槛非缺陷，报告未列整改项 |
| R62-4 | 权限码命名漂移（`list:UserNotice` vs basename） | 改种子触碰存量角色授权，风险大于收益；门禁对账兜住 |
| R63-1 | 首页缓存键含用户维度 | 正确口径（数据权限过滤后聚合），注释自证 |

**证据过时（无需处置）6 条**：R08-3（field 导入导出不对称前提部分失实）、R19-3（空 pk 守卫早已在位）、
R22-1（时间过滤实为 range）、R54-4（metric 导出 skipped 口径前提从未成立）、R55-6（metric 双渲染条件已互斥）、
R57-5（linkage 剔除早有提示）。

**维持评估 4 条**（重审报告结论）

| 条目 | 维持口径 |
|---|---|
| screen command 无 owner 守卫 | 维持 shared 协作语义（command 已是管理员级权限点），docstring 明确 |
| message 设置页跨 settings / notifications 两域 | 维持聚合页（渠道 + 订阅 + 模板是「消息域」统一入口） |
| 同 app 双审批引擎并存 | 维持（`approval/README.md` 决策树为唯一裁决入口） |
| `system/utils/platform` 工具 vs 服务边界 | 命名分区已登记 + 代表模块已迁 services；全量重构登记滚动 |

**另行立项 1 条**：前端模块环完整破除（`api/base → utils/http → utils/auth → store → api/auth`）——
130 环已全豁免并接入 `check-module-cycles.mjs` 门禁；完整破除触碰全站导入图，待单独立项。

---

## 八、权威文档地图（长期有效）

| 主题 | 位置 |
|---|---|
| 长期方向 / 优化清单 / 审查结论 | 本目录 `长期开发计划-2026Q4起.md`（度量驱动审查 + 外部案例对标 + 分期与风险） |
| 触发制 / 红线 / 时间窗 / 滚动债 | `docs/plans/触发制任务清单-长期.md` |
| 月度窗口 / 贯穿专项 / 候选池 | `docs/plans/下阶段长期计划-2026.10.md` |
| 决策记录（ADR 全集 + 索引） | `docs/adr/`（最新 ADR-092；`adr/README.md` 为索引） |
| 安全接受项 / 供应链台账 | `docs/security-review.md` |
| 发布 checklist / 运维 | `docs/ops/`（`release-checklist.md` / `deployment.md` / `upgrade-stock.md` / `log-archive.md` / `kernel-release.md`） |
| 指标与性能基线 | `docs/metrics.md`、`loadtest/baseline.json`、`xadmin-client/e2e/perf-baseline.json` |
| 架构 / 权限 / 契约 / 内核包 | `docs/architecture/`（`kernel-package.md` / `component-handbook.md` / `permission.md` / 框架开发遵循准则.md） |
| 二开入口 | `docs/guide/`（`first-module-30min.md` / `recipes.md` / `secondary-dev-quickref.md` / `menu-maintenance.md`） |
| 前端约定与门禁 | `xadmin-client/CONTRIBUTING.md` / `AGENTS.md` / `e2e/README.md` |

---

## 附：工作区文档整理记录

**2026-10-14（5.0 发版窗口）**：工作区根文档两轮整理——①已交付的一次性工作文档（vben 对标系列
任务书、对标参考截图等）移入工作区回收站 `_delete/workspace-working-docs-20261014/`（可恢复）；
②**工作区级台账入库**：本清单、`长期开发计划-2026Q4起.md`、`RELEASE-5.0.0.md` 由工作区根迁入
本目录 `docs/plans/`（纳入版本保存），`README.md`（工作区总览）与 `AGENTS.md`（AI 协作规范）
作为工具常驻约定文件留在工作区根；工具目录中的散落内容（`.codeartsdoer/temp/accept` 备份、
`.workbuddy/memory` 旧记忆、空 `.trae` / `.arts`）同批移入 `_delete/`（可恢复）。

**2026-10-09**：`NEXT-DEV-PLAN.md`（执行记录一~二十二）、`CODE-REVIEW-TASKS/`（13 份台账）、
两份代码审查报告、`PHASE-C-HANDOFF.md` 等历史台账的结论已收敛进本清单与仓库内两份台账；
原文归档件其后已清理，追溯文献编号（`T01-xx`~`T10-xx` / `O1-xx`~`O12-x` / `R60-x` / `TG-x`）
对应的上下文，以仓库内 plans 台账、ADR 与 git 提交记录为准。
