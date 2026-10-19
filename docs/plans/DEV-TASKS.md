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

> 2026-10-17 两条执行记录（「4.2.5 → 5.0.1 升级演练与升级通道修复」「清库重建 + 回灌全面测试与操作手册」）
> 已归档到 [archive/工作区开发任务执行记录-2026.10.md](archive/工作区开发任务执行记录-2026.10.md)。

- **2026-10-19 AI 动作声明按应用下沉（已提交 server `3fb264ae`/`ee383140`/`3ad0b54d`、client `eda26830`）**：
  各应用 `ai_declarations.py` 自动发现合并，动作 60 → 105（102 声明式 + 3 内置）；内置动作回归
  所属应用；`modules` 对齐模块裁剪；生成器改 `AI_ACTIONS`、`--with-module` 标注、多模型分块合并。
  验证：pytest/ruff/mypy/行数/跨 app/文档全绿；AI E2E 双浏览器 36 passed；容器重启后巡检
  105 动作 / 候选 0 / 缺口 0。

- **2026-10-18 种子分层工具链对齐（留工作区待提交）**：三处收口——`sync_menu_permissions
  --update-seed` 按归属回写各层（免人工 `seed_split`，字段树不再整表塞回平台层）；生成器与
  分层工具统一 `menu.json`/`menumeta.json`（旧 `menu_<model>.json` 吸收清空）；
  `dump_init_json --split-apps --apps <新应用>` 自动初始化 `manifest.json` + `doctor` 新增
  「内置种子」自检；示例层落地（`dataset/seeds/sample/` 六个文件、`approval/seeds/` 流程定义、
  `audit/seeds/` 脱敏规则），`init_data` 显式 `--with-sample` 保持新装观感。补齐两份 AGENTS/
  二开文档站 3 页/架构运维 5 处口径，修复两处只读平台层的空转测试。验证：后端全量（含示例层
  校验 `--check` 0 error）+ 静态门禁全绿；前端 typecheck/vitest 2262/i18n/contract/
  menu-permissions 全绿；全量 E2E（6 分片隔离库）724 passed/8 failed/16.1min（失败为既有
  flake），顺带修「场景种子产物发现」与「启动静默窗口吞保存」两处；清库 `init_data` 新装
  实测 samples 全在（数据集 2/看板 1/大屏 1/报表 2/表单 2/提交 4）、默认复灌不删示例且幂等。

- **2026-10-18 种子分层收口批（server 5 提交 + client 2 提交，已推送 dev）**：`c82452f9`
  「种子按 app 分层」的衍生问题四处收口。①**读取侧 N+1**（`ade554e3`）：`permission_sync`
  两处 + `dump_init_json` 改批量预取，`pytest.ini` 加 `faulthandler_timeout=600`。②**写入侧
  批量收敛（根治卡死，`0d386d2f`）**：profile 实证单次 `load_init_json` 9.14s 里
  `fill_field_permissions` 的逐组合 `get_or_create` + `.set()` 占 1257 次 / 2.83s；改为按
  「字段集」分组——一次 `filter(role__in, menu__in)` 查全部已存在组合、m2m 按组「一条 DELETE
  + 一条多值 INSERT」、只对缺口逐个 create（`FieldPermission` 的 id 由 save 路径拼装，
  `bulk_create` 拿不到主键）。**实测**：5 个种子测试文件合跑从「分钟级卡死需手动 kill」→
  **27.6s / 68 例全过**；全量 pytest **1 分 45 秒 exit 0**（旧基线 5 分 23 秒）。守护 = 幂等
  路径查询数不随组合数线性增长（与读取侧护栏对称）。③**升级链路 mypy 债务**（`079f9c13`）：
  升级批次提交时未跑 mypy、在已入列严格清单的 `system` / `ops` 留 59 处告警，12 个文件清零，
  全仓回到 896 文件 0 错；顺带修 `legacy_import` 变量同名复用，并让 `test_kernel_release`
  兼容 renovate 锁版的 sha+注释形态（PR #121 上 3 例假红）。④**E2E 场景种子路径断裂**
  （`31abb97d`）：`seed_demo_book_scene` 仍读旧路径而静默 skip → demo/book 路由 404、两条 spec
  双浏览器全挂；改为 glob 发现 + 按文件名排序装载。client 侧同批修 `preferenceFieldSync.spec`
  种子发现（`97dcee53`）与 `viewTransition.spec.ts` 的 `as unknown as`（`f2d17321`）。
  **教训**：①读侧与写侧 N+1 是两个独立放大源，只修一半不解决合跑卡死；②性能回归要用 profile
  定位而非凭推断；③client E2E 的 `server-ref` 默认 dev，server 改动会以「client spec 挂」现形。

- **2026-10-17 「清库重建 + 回灌」官方升级路径全面测试 + 操作手册（改动留工作区待提交）**：全文见
  [archive/工作区开发任务执行记录-2026.10.md](archive/工作区开发任务执行记录-2026.10.md)。虚拟机用 4.2.5 库跑完整
  路径九项验收全通过；补齐三处搬运缺口（个人配置实体、公告附件关联 `notice.file`、模型自带文件字段），
  「单实体失败只跳过该实体」与「附件缺失跳过不建坏记录」固化为行为 + 测试；新增操作手册
  `docs/ops/upgrade-4x-to-5x.md`，守护测试 18 例（`test_legacy_transfer.py`）。


- **2026-10-16 5.0 发布收口与发布后验证（R1 收尾 / R3 / R4，已提交推送）**：①**发布**：五仓 `v5.0.0` 就位，server/client 镜像构建+推送成功（tag 各一次强推更新，不再删建）；②**发布说明**：Release 原仅 3 条 PR（release-drafter 只汇总 PR，直接推主干的 655/1120 个提交未纳入）→ 按提交类型分组补齐并**修根因**（新增 `scripts/sync_release_notes.py` / `sync-release-notes.mjs` + 发布流水线调用，后续自动完整）；③**发布后验证 6 项全过**：`upgrade_check`=up-to-date（88 迁移/0 待应用/101 表无缺）、`sync_menu_permissions --dry-run` 缺口 0、health 四项 true、认证冒烟 POST 200、操作日志改动真实链路（完整单条 INSERT，并为旧逻辑遗留的 1 条孤儿行提供实证）、`post_upgrade` 幂等；④**发布后验证发现并修复的真实缺陷**：`load_init_json` 重灌种子会覆盖运行期补齐的字段白名单 → 非内置角色（演示角色「示例-主管/示例-员工」在审批接口）缺口 8 个 = 演示账号列表空白 → 收尾新增 `fill_field_permissions` 自愈（只补缺口、不改写人工裁剪；容器实测重灌后归零且幂等）+ 守护测试并入 `TestSeedOrderingFillsBuiltinRoles`；同时回退了误入提交的 `--update-seed` 种子回写（4700 余行，运行时模块权限点不应进种子）；⑤**R3**：逻辑层由 `test_upgrade_check.py`（含真实库 legacy 4x → reconcilable）守护 + 本地栈验证；端到端 4.2.5→5.0 演练登记待执行（需旧实例）；⑥**R4**：文档站本地构建通过（无死链）、5.0 升级要点与实现核对一致、`docs-build.yml` 历史全绿；⑦**衍生修复**：client 多架构构建改 `--platform=$BUILDPLATFORM`（QEMU 下 vite/rolldown 卡死 50 分钟）、构建前显式 `pnpm run prebuild`（图标/动画子集为 gitignore 生成物）、server setuptools 升级改绝对路径（PATH 指向 venv）、trivy 报告逐条定性（apt 构建期升级 / autobahn 示例私钥 / pip vendored msgpack+urllib3 豁免）、pyjwt 升 2.15.1、twisted 无修复漏洞豁免；⑧**建立本地验证闭环**：本地构建 base+运行镜像并用 trivy（ECR 漏洞库源）扫描，两镜像均 exit 0；本地 pip-audit 复验通过。

- **2026-10-16 TG-8 低风险子项收口（R2，两仓改动留工作区，待提交）**：D-6 操作日志取消占位行（单条 INSERT）、D-5 确认交互三范式成文；详见
  [archive/工作区开发任务执行记录-2026.10.md](archive/工作区开发任务执行记录-2026.10.md)。

- **2026-10-14 前端入口权限审计 + 内置角色只读授权补齐（改动留工作区，待提交）**：起因=用户追问
  「其它权限是不是都要检查」。审计结论：框架默认按钮由页面级权限位控制，自定义按钮绝大多数已用
  `hasAuth`（未声明 `show` 的按钮不渲染，不构成 403 面）——真缺口 3 处：MCP 工具抽屉「同步 / 调用」
  （`sync` / `call:AiMcpServers`）、审批实例「申请详情」（`retrieve:SystemApprovalInstance`）、
  知识库「预览」（`retrieve:AiKnowledge`），均已按权限点收敛入口。后端按「只读与取用」口径补齐
  内置演示角色授权：演示模式 +17、管理员 +23（岗位联想 / 预览 ×4 / 回收站列表与恢复 ×4 / 全局搜索 /
  模型字段 / 设置读取 / 消息订阅 / 消息模板列表；破坏类与物理清除维持不给），种子 `userrole.json`
  与库同步、用户权限缓存已失效。新增通用守护 `test_builtin_role_readonly_coverage.py`（角色拥有页面
  ⇒ 其下只读与取用类权限点必须授权；白名单 URL 与「复用端点」豁免清单显式登记），覆盖未来新增
  只读接口的授权漂移。

- **2026-10-14 文件中心非超管 403 修复（改动留工作区，待提交）**：两类「页面能开、单接口 403」，
  ①**字段白名单与菜单模型脱节**——在线用户页数据源由登录日志换成用户会话后，管理员 / test / 演示模式
  三角色的白名单仍指向旧模型（运行时按「模型标签 + 字段名」匹配，等价空白名单 → 表头无列、字段全 null）；
  修复=审计判定识别「白名单与权限点模型无交集」+ 绑定校正新增**陈旧模型收缩**（`plan_binding_fixes`
  跳过运行时模块权限点，命令改为「先收缩绑定再补齐」）+ 内置角色字段树固定 ROLE 口径 + `post_upgrade`
  报告字段白名单缺口；种子 `fieldpermission.json` / `menu.json` 已回写并加守护（种子白名单必须覆盖
  权限点模型）。②**页内接口权限点未授权给角色**——文件页新增接口（统计 / 访问记录 / 下载 / 预览 /
  回收站 / 分块）登记权限点后未同步授权（管理员 / 演示模式只有 list），非超管打开即 403；修复=
  `userrole.json` 补齐两角色 file 接口权限点（管理员全量；演示模式只读与取用口径，剔除写操作与物理清除）、
  前端行操作按钮与统计请求按权限点判定（未授权隐藏 / 不请求，`stats` 与 `retrieve` 同权）、守护测试
  `test_file_menu_seed.py`（页面 → 页内权限点 → 角色授权三层）+ E2E `file-center` 新增非超管零 403
  用例（超管用例看不到该类缺口，E2E 种子新增 `seed_file_center_scene`）。③**访问记录 / 回收站输出空对象**：
  `accessLogs` / `recycleList` 权限点无模型绑定（绑定只随 CRUD 动作自动补）→ 角色页无法配置其字段白名单、
  非超管拿到 `{}` 行；修复=按「接口实际输出模型」显式绑定（access-logs → `file.fileaccesslog`、回收站 →
  `file.uploadfile`）+ 补两角色白名单（种子缺口归零，`OUTPUT_MODEL_OVERRIDES` 登记到种子守护）。
  **遗留登记**：其余页面同类授权缺口 43 处（导入导出链 / 回收站 / 预览 / 变更历史 / 强制下线 / 模拟登录等，
  多为前端有 `hasAuth` 守卫的按需能力）未在本批改动，新增 action 是否同步给内置角色按需评估。
  复盘见 `docs/dev-pitfalls.md` §一.1/§一.2。

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
- **限流 HTTP 语义收口（2026-10-14 已完成）**：验证码限流异常 `CodeSendOverRate` / `CodeSendTooFrequently` 覆写 `status_code = 429`——客户端错误不再伪装成 500（daphne 不再记 "Internal Server Error"）；前端无需改动（`errorStrategies` 的 429 策略自动接管：非 GET 走去重提示、文案含剩余秒数）；守护=单测 `test_verify_code_exceptions.py`（含全局异常处理器映射）+ 集成用例「同目标重复发码 → 429 + 剩余秒数」+ E2E `verify-code-resend.e2e.ts` 增加状态码断言；映射已登记 `docs/exception-handling.md`。

- **2026-10-14 演示管理员权限完善 + 全面安全排查（改动留工作区，待提交）**：①**「页面不显示字段」根因修复**——
  字段权限为 fail-closed 白名单制，`seed_demo_admin` 此前只授菜单不建白名单，演示角色 54 个走序列化器的权限点
  零字段（数据集 / 报表 / 审批 / 我的填报 / 知识库 / 岗位等整页空白）；新增 `apply.fill_field_permissions`
  （按「已授权权限点 × 绑定模型 ROLE 树全字段」补缺口，已有非空白名单不覆盖）+ `sync_menu_permissions
  --fix-field-permissions`（含 `--update-seed` 回写种子），`seed_demo_admin` 自动补齐、新增页面（9 → 26 个）
  继承同款写权限裁剪、`--clean-only` 对称回滚，`identity.builtin` 内置角色同步兜底；演示账号字段白名单
  43 → 123 条、全库审计缺口 253 → 0、种子 `fieldpermission.json` 171 → 248 条；②顺带修复报表页对非超管 500
  （`_visible_queryset` 按 Report 不存在的 `visibility` 字段过滤 → 改按模型能力分支）；③演示数据：新增
  「示例-表单提交趋势」报表（聚合 / 周频，覆盖第二个数据集）；④**安全排查**（`security-review.md` 九期）：
  修复 9 项（接口文档端点匿名可枚举 / xlsx 与 CSV 公式注入 / 出站守卫重定向绕过 / 导出与验证码错误回显内部异常 /
  白名单死条目 / OAuth 绑定端点收敛 / PAT scope 上限）、登记 11 项（含 LDAP-SMTP 出站守卫、defusedxml 等）；
  ⑤测试：新增 10 个文件 / 用例集（全量 5995 collected 绿、mypy / ruff / 文档门禁 / 迁移漂移全绿），
  E2E 双浏览器冒烟 9 + 相关面（webhook / data-permission / analysis / api-app）18 全过。

## 〇-历史

> 2026-10-13 及更早的历史执行记录（2026-09 审查整改收口、2026-10 各批交付与推送明细）
> 已归档至 [archive/工作区开发任务执行记录-2026.10.md](archive/工作区开发任务执行记录-2026.10.md)。

## 一、近期待办（2026-10 ~ 2026-11）

| # | 事项 | 验收 / 触发口径 |
|---|---|---|
| 1 | ~~工作区改动复核入库~~ | **已完成（2026-10-09）**：server `35d8e0dc` / `45c3c8a8` + installer `3348910` 入库并推送；门禁全量复验通过 |
| 2 | ~~F6 容器化测试验收终判~~ | **已完成（2026-10-09）：通过（达标）**——时长 ✅ + 9 个红 run 全部分诊收敛（零真库基建故障）；字面「连续 2 周绿」余窗顺延至 10-21 观察；处置方案（回退 sqlite）不适用 |
| 3 | ~~T5 Renovate 验收~~ | **已完成（2026-10-16）**：修 `renovatebot/github-action@v41` → `v46.3.8`（该 action 无 major tag，此前 workflow 从未成功运行过），两仓 `gh workflow run renovate.yml` 均 success，且已实际开出 PR（`renovate/pypi-pyjwt-vulnerability`） |
| 4 | T6 SLO 校准 | 数据成熟（满 3 个月）即校准回填 `docs/metrics.md` |
| 5 | ~~5.0 端到端升级演练（4.2.5 实例 → 5.0）~~ | **已完成（2026-10-17）**：虚拟机以 4.2.5 库跑九项验收全通过（导出 → 清库重建 → 回灌 → 计数/关联/附件/旧密码/幂等 → `upgrade_check`=up-to-date + health 四项 true）。**结论 = 切分前旧库原地升级不可行**，当时路径为清库重建 + 回灌（手册 `docs/ops/upgrade-4x-to-5x.md`）；方向已由 #8 改写。**同轮修复升级通道三处缺陷**（installer：镜像 tag 带 `v` → `manifest unknown`、备份开关缺 `${VAR:-}` 缺省、基线门禁误判）+ PG 大版本数据目录迁移。 |
| 6 | ~~文档站死链与口径守护补强~~ | **已完成（2026-10-17）**：新增 `scripts/check_docs_site_links.py`（锚点级 + 跨仓路径引用校验，进 docs CI），抓出并修复 8 处锚点死链；升级门槛串纳入事实门禁（+3 条）。 |
| 7 | ~~回灌链路端到端集成测试~~ | **已取消（2026-10-17，用户决策）**：回灌路径对二开损失面过大（旧迁移引用须人工改写、表文件不搬、审批/表单/数据集/日志/任务/PAT 不在搬运面内），转 #8；回灌保留为逃生舱。 |
| 8 | **真·原地升级立项与分批实施** | **已立项（2026-10-17）**：决策 [ADR-094](../adr/ADR-094-inplace-upgrade-for-legacy-libraries.md) + 方案 [原地升级立项与方案-2026.10.md](原地升级立项与方案-2026.10.md)。**第一~三批已交付**：旧结构样本生成器 + 结构与引用与账本一键对齐（`legacy_align`）+ 差异报告 + 体检新判定 `legacy-inplace-capable` + 安装器接线（`--probe` 三态）+ 二开 app 迁移引用改写工具；`v4.1.3`/`v4.2.3`/`v4.2.5` 三代样本原地对齐全绿（实测数据见方案 §八）。**第四批（实机演练完成）**：真实 4.2.5 库升 5.0.1 跑通、数据零丢失（行数+哈希比对）；修复「容器无 git → common_monitor 未映射」与「有菜单无列（字段白名单 fail-closed）」两个真问题（后者改 post_upgrade 自动补齐+清缓存）。 |
| 9 | ~~应用级种子拆分（五批次）~~ | **已交付（2026-10-17）**：种子按 app 落 `<app>/seeds/`（平台层保留 `loadjson/`），12 个 manifest 自声明依赖与归属；`load_init_json --list/--check/--apps` + `seed_split` + `dump_init_json --split-apps`；角色授权改声明式 grants 并集（破除 loaddata m2m 覆盖清零）；决策 [ADR-095](../adr/ADR-095-per-app-seed-layering.md)，方案与执行记录见 [应用级种子拆分方案-2026.10.md](应用级种子拆分方案-2026.10.md) / [执行计划](应用级种子拆分执行计划-2026.10.md) |
| 10 | ~~前端框架一致性收敛（表单字段元数据化 + 弹窗全屏按钮体验缺口）~~ | **已完成（2026-10-18）**：调查、迁移与验证证据见 [方案](前端框架一致性调查与收敛方案-2026.10.md) §九。① ReDialog 全屏按钮默认渲染；② `check-handwritten-forms.mjs` 门禁 + 豁免登记进 CI；③ post / tag / login-policy / subscription / mcp / api-app / screen / approval-rule 八页迁至元数据表单（混合形态用字段渲染器或扩展字段）；④ 选型判定写入 component-handbook §六；⑤ 顺带修 `headerRenderer` DOM 泄漏、`time` 协议、`tag.color` 类型、MCP `pk` 只读、`formProps.rules` 整块覆盖；⑥ 最大化态强制显示还原按钮（`lay-tag/index.vue`，避免进入最大化后无可见出口）。数据集/报表因联动列维持豁免。验证：vitest 2312、后端 103 passed、E2E 双浏览器 60+（含 `tag-bar` 最大化还原 6 例） |

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
| D3 | Node | 24 → 26 LTS | 2026-Q4 转正后（晋升一般在 10 月下旬）；`.nvmrc` / `Dockerfile-base` / `engines` 三处同步。**2026-10-19 复核：未到点**——本机 nvm 最新 v24.20.0、无 v26；等 LTS 公告后动 |
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
