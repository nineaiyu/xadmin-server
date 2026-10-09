# ADR-079：common→业务 app 契约缝显式接口化（单缝收敛）

- 状态：已交付

> **日期**：2026-10-02
> **关联**：[NEXT-DEV-PLAN](../../../../NEXT-DEV-PLAN.md) R6（批三结构治理）；`scripts/check_cross_app_imports.py` 契约缝机制（2026-09-26 建立）；二开体验与模块化管理评估（P1/P2 已交付，本 ADR 是其框架层收尾段）
> **代码路径**：`common/contracts.py`（新增）、`scripts/check_cross_app_imports.py`、common 内 25 个契约缝消费文件
> **背景**：跨 app 门禁把框架层（common）对业务 app 的消费收敛到 `<app>.services` 并逐缝登记（实测 26 文件 31 条缝；NEXT-DEV-PLAN R6 行的「30 条缝 / 25 文件」为 oplog_recorder 补登记前的旧口径），但登记只是「显式化」而非「收口」——common 侧 21 处 `from system.services import …` 直接落在业务名字上，耦合面分散在 26 个文件里；其中 credentials / modules.gate 两处是「属性访问式引用 + 迁移期降级」（调用期解析，模型不可用时降级）。R6 立项目的：把属性访问式引用 Protocol 化 / 显式接口化，降低 common 对业务 app 的隐式耦合，为二开生态 / common 独立包化（触发制）预备单点替换面。

## 现状（事实基础）

`scripts/check_cross_app_imports.py` CONTRACT_SEAMS 改造前现状：31 条缝、26 个源文件，全部为 common → 业务 app `*.services`。按消费形态分三类：

| 形态 | 文件数 | 代表 | 特征 |
|---|---|---|---|
| from-import **模型类** | 11 | `config/base.py`（SystemConfig）、`data_scope/values.py`（UserInfo/DeptInfo）、`oplog_recorder.py`（OperationLog） | import 期即触发模型加载（经 services 的 PEP 562 惰性导出）；serializer Meta / isinstance / 签名默认值需要类对象 |
| from-import **契约函数 / 实体** | 13 | `notifications.py`（消息渠道生产面）、`modelset/*`（ensure_impact_confirmed 等） | 函数/常量是 services 模块的普通属性，import 期零模型加载 |
| **属性访问式**（调用期解析） | 2 | `core/credentials.py`（SystemConfig 巡检 + Setting 巡检）、`core/modules/gate.py`（Menu 裁剪） | `import system.services as …` + 调用期取属性 + `except Exception` 迁移期降级；settings 加载期 / 空库不炸 |

按提供方分布：system.services 23 文件 / 24 缝（`hands.py` 与 `credentials.py` 各另有 settings 侧消费）、notifications.services 4 文件、approval / ai / settings.services 各 1 文件。

门禁已有能力：缝隙必须登记（双向漂移校验）+ 函数级惰性 import 作观察项打印。缺口：**26 个文件的分散直连没有被上限约束**——新 common 文件仍可直接 from-import 业务 services，只要补登记即可，「框架层耦合有上限」停留在台账层。

## 决策

- **D1：单缝收敛——`common/contracts.py` 成为框架层消费业务 app 的唯一出口**。契约名清单（`_CONTRACT_PROVIDERS`：名字 → (提供方模块, 原因)，39 项）+ PEP 562 `__getattr__` 惰性解析（解析结果缓存到模块 globals，模块级零业务 import）。common 内其余文件的 import 语句从 `from system.services import X` 改为 `from common.contracts import X`，**用法与解析时机完全不变**（from-import 触发 contracts `__getattr__` 的时机 = 原先触发 services `__getattr__` 的时机）。门禁同步升级：common 内除 `contracts.py` 外出现任何业务 app 模块级 import 即违例（含 `*.services` 形态）；CONTRACT_SEAMS 收敛为 contracts.py 一条登记（5 个服务模块缝）。31 条缝 / 26 文件 → 1 文件 5 缝。
- **D2：显式接口化 = 声明式白名单 + Protocol 消费面（最小面）**。契约接口由三部分构成，全部写在 common 侧：① `_CONTRACT_PROVIDERS` 白名单（每条注原因，未声明名字 `__getattr__` 直接 AttributeError——契约面之外不可达）；② `__all__` 由白名单派生（单一事实源）；③ 对两处**属性访问式**消费面定义 `typing.Protocol`（`SystemConfigContract`：凭据巡检按键批取值；`MenuContract`：模块裁剪的菜单树行 + 权限点类型常量）——「common 需要业务 app 长什么样」第一次以接口形式写在框架层，而非散落在调用点。
- **D3：属性访问式的迁移期降级语义原样保留**。credentials / gate 改为 `import common.contracts as contracts` + 调用期 `contracts.SystemConfig` / `contracts.Menu`，解析失败（迁移期模型不可用）仍由调用方既有 `except Exception` 降级——contracts 的惰性解析不吞异常、不改变失败传播路径。credentials 的 Setting 巡检（原函数级惰性 `from settings.models import Setting` 观察项）一并收编为 `contracts.Setting`（同一模型对象，降级语义不变），common 内业务 import 观察项清零。
- **D4：注入制升级留触发（不在本期）**。二开生态 / common 独立包化触发时，把 contracts.py 升级为可注入提供方（业务 app 在 `AppConfig.ready()` 注册实现或 entry-point 装配），common 其余代码零改动（替换面 = 本模块的声明清单与 Protocol）。触发前不引入注册机制——同仓同生命周期下注入层是空转的间接层。
- **D5：contracts.py 的缝漂移校验改对照白名单声明**。契约出口文件无模块级业务 import（D1 的前提），原「登记缝 ↔ 文件 import 语句」的漂移校验对它失义；改为「CONTRACT_SEAMS 登记的提供方 ↔ `_CONTRACT_PROVIDERS` 白名单声明」双向对照（门禁新增 `CONTRACT_PROVIDER_RE` 解析白名单条目），其余文件维持原校验。

## 备选与不选

- **立即全面依赖注入（注册制 provider）**：本期无第二实现，注册/发现机制只增加启动期复杂度与排障面。不选（D4 触发制）。
- **模型契约 Protocol 全覆盖**（11 个模型类逐一声明 ORM 消费面）：django-stubs 下 Manager/QuerySet 泛型方差使协议声明噪音大、静态收益低，且模型 from-import 的类对象消费（Meta.model / isinstance）本就无从注入。不选；SystemConfig / Menu 两处先行（消费面小、语义清晰），其余随 D4 触发再议。
- **函数级惰性 import（逃生门）一并收口**：观察项机制已在位、存量极少，随改动面自然清理。不单独立项。

## 验收

1. `python scripts/check_cross_app_imports.py` 绿：CONTRACT_SEAMS 仅剩 `common/contracts.py`（4 个服务模块缝），新规则（非 contracts 文件禁业务 import）生效；
2. `tests/unit/scripts/test_gate_scripts.py` 适配新规则并新增单缝规则用例；新增 `tests/unit/common/test_contracts.py`：**白名单 ↔ 真实提供方往返恒等**（每个声明名从 contracts 解析与从 provider 解析是同一对象，防白名单笔误）、未声明名 AttributeError、Protocol 消费面存在性（`Menu.MenuChoices.PERMISSION` 等）；
3. 后端全量 `pytest -n auto`（真实 PG17+Redis8）+ mypy + ruff + 行数门禁绿——所有消费点 import 时序与改造前一致（模型加载时机不前移不后移）；
4. common 内函数级业务 import 观察项清零（credentials 的 Setting 巡检收编为契约消费）。

## 边界（登记）

- **不动**：业务 app 之间（非 common）的 `*.services` 消费口径；`system.services` 自身的惰性导出与契约委托函数机制（contracts 是其上游消费面的收敛，不是替代）。
- **不动**：函数级惰性 import 观察项（逃生门继续可用、继续打印）。
- **语义微变（接受）**：无。全部消费点的名字解析时机、失败降级路径与改造前逐一等价；唯一变化是 import 语句的来源模块。

## 交付记录

- 2026-10-02：D1–D3、D5 落地——`common/contracts.py`（39 契约名白名单 + SystemConfig/Menu Protocol）、common 内 26 个消费文件 import 切换、门禁单缝规则 + CONTRACT_SEAMS 收敛（31 缝 → 1 文件 5 缝）+ 白名单漂移校验、守护测试扩展与新增；后端全量门禁复验绿（见 NEXT-DEV-PLAN.md 执行记录八）。
