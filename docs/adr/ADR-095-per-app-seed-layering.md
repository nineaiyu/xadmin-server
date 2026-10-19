# ADR-095：内置种子按应用分层（`loadjson/` 平台层 + `<app>/seeds/`）

- 日期：2026-10-17
- 状态：**已交付**（批次一~五全部落地；方案见 [plans/应用级种子拆分方案-2026.10.md](../plans/应用级种子拆分方案-2026.10.md)，执行记录见 [执行计划](../plans/应用级种子拆分执行计划-2026.10.md)）

- 背景：内置种子长期是 `loadjson/` 下 21 个「每模型一个文件」的扁平 fixture，
  所有 app 的菜单 / 字段权限树 / 字段白名单 / 全局配置混在共享文件里。后果：
  二开 app 的种子只能往共享文件里塞行、`module remove` 必须改写共享文件、
  灰度回灌某个域只能整库 dump 再挑行。2026-09-17 曾评估 `loadjson` 目录级拆分并
  **否决**（理由：打断二开对 `loadjson/*.json` 的路径引用）——本轮以「保留平台层 +
  双读兼容 + 归属可校验」重开该结论。

## 决策

### D1 按 app 拆，不按模块拆

模块是运行期裁剪概念且**横跨 app**（`chat` 的菜单归 `message`、AI 助手菜单归 `system`），
按模块拆会出现「一行属于两个模块」的死结。模块裁剪继续做**装载时过滤层**，与文件层正交。
`django_celery_beat` 字段树（36 行）与 `*` 公共字段按既定决策归平台层。

### D2 平台层保留 `loadjson/` 原名与「可直接 loaddata」契约

二开脚本里 `loaddata loadjson/menu.json` 的既有写法继续可用（平台层仍是合法 fixture）；
应用层新增 `<app>/seeds/`，由 `manifest.json` 自声明 `depends` / `url_prefixes` /
`config_keys` / `grants`，装载顺序经**依赖拓扑排序**得出，不靠 `os.listdir`。

### D3 归属规则必须可穷尽（否则不拆）

权限点按 `url_prefixes` **最长前缀匹配**；页面按子权限点众数（≥80% 判给该应用，否则留
平台层）；目录与公共字段一律平台层；字段白名单跟随菜单；配置键 / 字典按清单声明。
实测：前缀匹配结果与「解析路由 → 视图模块」的权威口径**逐条一致（0 偏差、0 未覆盖）**，
人工例外 2/61 = 3.3%（低于 5% 止损线）。字典按根分组归属（避免同组拆到两层），
仅跨域共用的 `login_type` 留平台层。

### D4 角色授权改为「声明式 + 装载后并集」（拆分的前置硬障碍）

`loaddata` 对 m2m 是**覆盖式赋值**：同一角色行被两个 app 的文件各装一次会互相清零。
因此 `userrole.json` 的 `menu` 列表清空，授权改由各层 `grants.json` 声明（按
`Menu.name` 引用），装载后统一 `role.menu.add`——幂等、只增不删。
**语义变更**：种子重灌不再撤销运行期额外授予的菜单（原覆盖式会静默撤销）。

### D5 迁移用「写盘前逐行等价校验」，不用双读过渡

`manage.py seed_split` 是纯文件操作（不连库），写盘前比对迁移前后**每一行**
（pk + 全字段），缺行 / 多行 / 内容变化任一即拒绝写盘；未选中的应用原地不动，重复执行幂等。
比「双读装载 + 事后比对」更严，且省掉过渡态。等价性实测：4 个种子角色菜单数
255 / 75 / 29 / 153 逐 role 一致（含 sha256 记录于执行计划）。

### D6 导入导出共用同一份归属口径

`bucket_rows` / `write_buckets`（`system/services/seeding/split.py`）是唯一实现，
`seed_split`（整理文件）与 `dump_init_json --split-apps`（从库导出）都走它——
否则「导出到 A 层、导入从 B 层读」的错位迟早出现。跨仓对账门禁
（`xadmin-client/scripts/check-menu-permissions.mjs`）与 `parity_permissions.py`
同步改为聚合各层。

## 影响

- **正面**：二开 app 的种子自包含（不再改共享文件）；`dump_init_json --apps identity`
  直接产出可装载补丁；`module remove` 改为多层扫描后，停用模块的菜单不会在应用层复活。
- **代价**：新增一层目录约定与 12 个 `manifest.json`；`dump_init_json` 的**不分层模式**
  仍写平台层，之后需跑 `seed_split` 重新分层（命令已输出提示）；客户端对账脚本需聚合多文件。
- **不变量（守护测试）**：`test_seed_plan`（覆盖与层内顺序）、`test_seed_ownership`
  （归属规则边界）、`test_seed_references`（跨层引用）、`test_seed_grants`
  （授权规模与端到端）、`test_dump_split_apps`（按 app 导出幂等）。

### 修订（2026-10-18）：工具链与分层对齐

- **`sync_menu_permissions --update-seed` 改为按归属回写**：复用 `row_owners` +
  `write_layer_model`，直接写 `<app>/seeds/` 与 `loadjson/`，不再「先写平台层、再人工
  `seed_split`」。字段树/字段白名单同样按归属落层（此前会把整表字段树合并回平台层，
  造成同主键跨层两份、只有 `SEED_PK_OVERRIDE` warning 兜底）。
- **生成器与分层工具统一规范文件名**：`generate_crud` 改为写 `seeds/menu.json` /
  `seeds/menumeta.json`（按主键 upsert），不再产出 `menu_<model>.json`；已有的旧文件在
  下次回写/导出时被吸收并清空（`write_layer_model` 的 `merge` / `removed` 语义）。
- **`dump_init_json --split-apps --apps <新应用>` 可初始化清单**：目标 app 已注册但没有
  `seeds/manifest.json` 时，按路由反查 `url_prefixes` 自动生成（推导为空则告警），
  不再要求先手写清单。
- **自检补位**：`doctor` 新增「内置种子」项（清单/归属/引用 + 有 `seeds/` 但未注册的应用）；
  守护测试 `test_seed_layer_writeback`（落层/收敛/幂等）。
- **示例层落地**：数据分析示例（数据集/看板/大屏/报表/表单/提交）迁入
  `dataset/seeds/sample/`；内置流程定义与脱敏规则分别迁入 `approval/seeds/`、`audit/seeds/`
  （**契约**，默认装载）。`init_data`（新装）显式 `--with-sample`，保持「装了就有」；
  `load_init_json` 默认不装示例，`--with-sample` 可补；`--list/--check` 始终展开示例层
  （否则 CI 会静默漏检 `seeds/sample/`）。

## 触发制

- 字典归属已按**根分组**收敛（2026-10-17）：10/11 分组声明到消费方所在应用，
  `login_type` 因 identity（会话/在线）与 audit（登录日志）共用而留平台层。
  剩余触发条件：该组收敛或显式登记例外后，`DICT_UNOWNED` warning 升为 error。
- 若未来出现「同一菜单被两个应用共同持有」的真实需求，需重评 D1（按 app 拆的前提）。
