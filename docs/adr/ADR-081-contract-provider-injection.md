# ADR-081：契约出口升级为可注入提供方（二开生态注入制）

- 状态：已交付

> **日期**：2026-10-03
> **关联**：[ADR-079](ADR-079-contract-seam-interface.md) D4（本 ADR 是其预留段的落地）；触发制台账 TG-5；红线表「common 独立包化 / RePlusPage 独立发包」（同一触发）；二开体验与模块化管理评估（发包评估结论见该文档 P2-5）
> **代码路径**：`common/contracts.py`（注册 API + entry-point 装配）、`common/apps.py`（装配挂点）、`tests/unit/common/test_contracts.py`（注入生命周期守护）
> **背景**：TG-5 触发命中（2026-10-03 用户点名启动二开生态预备）。ADR-079 已把框架层消费业务 app 的 31 缝/26 文件收敛为 `common/contracts.py` 单文件（39 契约名白名单 + 2 Protocol + PEP 562 惰性解析），并预留「注入制升级」：同仓同生命周期下注册机制是空转的间接层，故触发前不建。触发后本 ADR 补上这一层——common 其余 25 个消费文件零改动（替换面 = 本模块的声明清单与 Protocol，D4 承诺兑现）。

**对账记录（触发命中流程第一步）**：红线表「common 独立包化」与 TG-5 同一触发——本批完成其后端预备半边（注入面就位，发包立项的前置重构已消除）；**common 物理独立发包仍维持触发制**（重开条件「多仓复用需求」未主张），前端 RePlusPage 发包同（P2-5 结论不变）。候选池无同域在办项。

## 决策

- **D1：注册制注入 API（`register_contract` / `unregister_contract`）**。业务 app 在 `AppConfig.ready()` 对白名单契约名注册实现对象，覆盖默认解析。约束三条例入 API 本身：① **白名单外名字拒绝注入**（`ValueError`）——缝面不因注入扩大，白名单仍是契约面唯一声明处；② **重复注册 fail-fast**（替换须先 `unregister_contract` 显式表达意图，防两个二开包静默互踩）；③ 注册即时清掉该名字的默认解析缓存。同仓内置 app 不注册任何覆盖——本批交付的是「可替换的面」而非第二实现，运行期行为零变化。
- **D2：解析序与缓存语义**。属性解析序 = 注入覆盖 → 白名单默认（PEP 562 惰性解析 + globals 缓存，ADR-079 语义原样）→ 未声明名 `AttributeError`。覆盖值**不写入** globals 缓存（每次访问查注册表，注册/撤销随时生效）；默认值维持首次解析后缓存。**绑定边界（显式登记）**：from-import 消费点在消费方模块 import 期绑定对象，注入须先于其 import 才生效（`common.ready()` 是框架层最晚 ready 的统一装配点，先于 URLConf 与绝大多数业务模块消费绑定）；两处属性访问式消费点（credentials / gate）调用期解析，注册后即时生效。D3 的 `unregister_contract` 回落默认并提供方自测通道。
- **D3：entry-point 装配（外置分发包零 INSTALLED_APPS 接入）**。entry points group `xadmin.contracts`，条目名 = 契约名、条目值 = 提供方对象（`pkg.mod:attr`）；`common/apps.py ready()` 统一加载（位于既有 fail-fast 校验之前、修复命令早退之后——broken 提供方在正常启动路径 fail-fast，migrate/doctor 等修复通道保持可达，与模块裁剪校验同口径）。加载失败 / 白名单外名字一律抛错，不静默降级。需要精确控制注入时序的二开走 INSTALLED_APPS + `ready()` 注册路径（早于 common.ready()）。
- **D4：模型契约 Protocol 全覆盖再议——维持不选**。注入制不改变 ADR-079 的评估事实：模型契约消费形态（`Meta.model` / `isinstance` / Manager/QuerySet）在 django-stubs 下协议声明噪音大、静态收益低，且类对象消费本就无法被接口约束——可注入 ≠ 可接口化，替换模型契约的提供方本质须是同 ORM 面的模型/proxy。Protocol 仍以 SystemConfig / Menu 两处先行；其余契约的接口声明随真实二开替换出现时按其消费面补充（届时该替换本身即立项依据）。本批新增的类型化面 = 注册 API 本身。
- **D5：门禁与守护**。跨 app 门禁（`check_cross_app_imports.py`）**零改动**——CONTRACT_SEAMS 五缝仍是默认提供方解析路径，白名单是唯一合法注入名集合，注入不产生新缝。守护扩展（`tests/unit/common/test_contracts.py`）：注入覆盖与回落往返、白名单外拒绝、重复注册拦截、entry-point 装配（含越界/加载失败 fail-fast）、既有白名单往返恒等与 Protocol 面用例不变。

## 验收

1. `python scripts/check_cross_app_imports.py` 绿（契约缝仍 1 文件 5 缝，白名单漂移校验不变）；
2. `tests/unit/common/test_contracts.py` 新增注入守护全绿（注册覆盖/回落/越界拒绝/重复注册/entry-point 装配与失败路径）；
3. 后端全量 pytest（真实 PG17+Redis8）+ fresh mypy + ruff + 行数门禁绿；同仓零注册 → 全部消费点解析结果与升级前逐一恒等（行为零变化）。

## 边界（登记）

- **不动**：`system.services` 自身惰性导出与契约委托函数机制；25 个消费文件的 import 形态与解析时机（ADR-079 语义原样）。
- **不做**：契约对象面的 Protocol 全覆盖（D4）；依赖注入容器 / IoC 框架（注册表 + dict 查找已覆盖需求，运行期零抽象开销）。
- **语义微变（接受）**：无同仓行为变化。二开注入的生效边界见 D2 绑定边界登记。

## 交付记录

- 2026-10-03：D1–D5 落地——contracts 注册 API + entry-point 装配 + common.ready() 挂点 + 注入守护测试扩展；全量门禁绿（见 NEXT-DEV-PLAN.md 执行记录十三）。
