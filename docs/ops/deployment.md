# 部署与运维手册

> 本文档沉淀常用部署方式与运维要点（T6.2 本地化，外站 https://docs.dvcloud.xin/ 降级为补充资料）。
> 常见故障的「现象 → 定位 → 处置」速查见 [runbook.md](runbook.md)。
> 适用于 xadmin-server 4.2.5+（含队列拆分与健康检查增强）。

> **本文为概览页**：本地开发与常见排查见下；
> 容器部署 / 备份恢复 / 容量 / 可观测 / 国产化见 [deployment-docker.md](deployment-docker.md)，
> 升级与回滚见 [deployment-upgrade.md](deployment-upgrade.md)（存量库判定另见 [upgrade-stock.md](upgrade-stock.md)），
> 安全响应头（CSP）见 [deployment-csp.md](deployment-csp.md)，配置速查表见 [config-reference.md](config-reference.md)。

## 1. 本地开发

### 1.1 环境准备

```shell
# 依赖安装（推荐 uv：以 uv.lock 为唯一安装依据，秒级重建；框架内核 xadmin-common
# 为工作区成员，随之以 editable 安装，改 common/ 源码即时生效）
uv sync --all-groups

# 无 uv 环境（pip 路径，安装 uv export 产物；用途见 README「依赖管理」）
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
# pip 产物不含内核（路径依赖，导出时以 --no-emit-workspace 剔除），需再装一次、
# 或把内核源码目录挂到 PYTHONPATH：
pip install --no-deps -e ./packages/xadmin-common
# 等价写法：export PYTHONPATH="$PWD/packages/xadmin-common${PYTHONPATH:+:$PYTHONPATH}"
```

依赖服务：PostgreSQL（或 SQLite）+ Redis。本地快速起 Redis：

```shell
docker run -d --name xadmin-redis -p 6379:6379 redis:8.10.2
```

### 1.2 配置与初始化

```shell
cp config_example.yml config.yml   # 按需修改（sqlite 本地开发：DB_ENGINE: sqlite3）
python manage.py migrate           # uv 环境加前缀 `uv run`（或先 source .venv/bin/activate）
python ops/init_data.py          # 初始数据 + 超管账号（幂等，可重复执行；升级后建议执行一次）
```

`SECRET_KEY` 取值规则（缺失时）：无任何配置文件（回落 `config_example.yml`）或 `DEBUG=true` 时自动生成并持久化到
`data/.secret_key`（仅限开发/首次体验）；显式配置 `config.yml` 且 DEBUG 关闭时缺失则拒绝启动（生产必须显式配置；
如需强制自动生成可设 `SECRET_KEY_AUTO_GENERATE=true`）。

- 超管初始密码：命令行 `--admin-password` > 环境变量 `XADMIN_ADMIN_PASSWORD` > 随机生成（**仅在初始化输出中打印一次
  **，首次登录后立即修改）。历史版本的默认密码 `xAdminPwd!` 已移除（安装器现会生成随机密码并回写 `config.txt`），
  升级不影响已存在的账号；
- `init_data` 常用参数：`--with-demo`（追加演示数据）、`--skip-ip-db`（离线/内网跳过 IP 库下载）、
  `--admin-password`（显式指定初始密码）；
- **首次体验审批流程前先配置组织架构**：内置的请假/采购/用章等流程首节点按「申请人部门负责人」
  解析审批人，超管默认无部门——直接发起会被 fail-closed 拒绝（提示会给出引导）。在
  「组织管理 → 部门管理」为申请人配置所属部门与部门负责人后即可正常流转。

> 配置项（键 / 环境变量 / 默认值 / 必填 / 生效方式）的完整速查见 [§9 配置速查表](#9-配置速查表)；
> 本地非 Docker 开发的数据库连法见 `config_example.yml` 数据库段注释块。

### 1.3 启动服务

```shell
python manage.py start all         # web(gunicorn+flower) + task(default/heavy worker + beat)
python manage.py start web         # 仅 web
python manage.py start task        # 仅任务（worker + beat，一个进程组）
python manage.py status            # 查看服务状态
python manage.py stop              # 停止
```

单服务粒度（容器编排推荐）：

```shell
python manage.py start gunicorn        # API 服务
python manage.py start flower          # 任务监控（/api/flower/）
python manage.py start celery_default  # 默认队列 worker（轻量任务）
python manage.py start celery_heavy    # heavy 队列 worker（导入/导出/批量任务）
python manage.py start beat            # 定时任务调度
```

### 1.4 AI 助手知识库（可选）

「集成管理 → AI 助手」是基于仓库文档的问答（ADR-023，回答带引用出处，不触生产数据）。
启用前需要两步：

```shell
python manage.py sync_ai_knowledge   # ① 同步知识库（幂等，秒级，可重复执行）
```

② 在「集成管理 → AI 配置」填写 OpenAI 兼容的 `base_url` / `api_key` / `model`
（DeepSeek、Qwen、Kimi、vLLM、Ollama 等均可）并打开开关；助手页状态区会显示已入库知识块数量。

- **文档来源（双来源，ADR-033）**：
  - 仓库文档：`docs/**/*.md` + 根目录 `README.md` / `CONTRIBUTING.md`——把 markdown 放进 `docs/`
    （或挂载卷覆盖该目录）后重跑 ① 即可（按内容 hash 增量更新，删除的文档同步移除）；
  - 上传文档：管理端「集成管理 → 知识库」页自助上传（选择本地 .md 读取或直接粘贴文本，同名覆盖更新），
    与仓库文档并存参与检索，可预览全文/分块、启停（停用即退出检索）、删除；
- **同步时机**：仓库文档变更后重跑 ①（或管理页「同步仓库文档」按钮）；未同步时助手页会提示知识库为空，问答无召回。
- **向量索引（pgvector，ADR-074）**：向量检索依赖 PG 的 `vector` 扩展（内置 compose 镜像已带，见 §3；
  外置 PG 需自行安装）。`build_embeddings` 每次构建成功后自动尝试 HNSW 索引定型，一般无需手工干预；
  存量部署升级后可手工执行一次确认状态：

  ```shell
  python manage.py build_ai_vector_index   # 语料 ≥1000 块且维度稳定时定型 vector(N) + HNSW；混存窗口反向定型；<1000 行 no-op
  ```

### 1.5 演示数据（可选，开发 / 演示环境）

一键加载可交互的演示数据（组织、审批、请假、表单、通知公告、聊天室、知识库、文件、
Webhook、开放平台应用等），让各功能页面开箱有内容；卸载命令对称可清：

```shell
python manage.py seed_demo_all               # 一键加载全部（幂等，可重复执行）
python manage.py seed_demo_all --reset       # 先彻底清理再加载
python manage.py seed_demo_clean             # 一键卸载（清理演示数据并回滚对内置种子的改写）
```

| 分项命令            | 内容                                                              |
|-----------------|-----------------------------------------------------------------|
| `seed_demo_org`    | 示例组织（研发部/财务部）+ 预置角色（菜单/字段/数据四层权限）+ 场景模板（报销流程、入职登记表） |
| `seed_demo_flows`  | 审批实例（待办/通过/驳回）+ 轻量审批单五态 + 表单提交                  |
| `seed_demo_leave`  | 请假业务闭环（通过/驳回/待审/草稿）                                     |
| `seed_demo_content` | 通知公告、聊天室历史消息、知识库文档、文件中心示例文件、审批委托、Webhook 订阅与投递审计、开放平台应用 |
| `seed_demo_users`  | 批量演示用户（`--count` 控制数量，撑起数据集的趋势与分布）                  |
| `seed_demo_admin`  | 对外体验账号 `admin`（演示模式角色，破坏性写操作已摘除）                     |
| `seed_demo_extras` | 岗位与成员分配、内置标签打标、AI 助手会话、导出中心记录                       |

说明：

- **对外体验账号** `admin` / `admin123`（`seed_demo_admin`，README 线上演示口径）：
  非超管、挂「演示模式」角色并额外补齐有演示数据的业务页；角色菜单集合里已摘除
  删数据 / 批量删 / 回收站清除 / 导入覆盖 / 密码重置 / 角色与权限定义写入 / 部门授权 /
  任务调度写 / 机器凭证端点等破坏性权限点，访客可最大化体验但不影响演示环境；
  用户级 MFA 方式白名单已收窄为空集（**禁止绑定 OTP/Passkey 等任何 MFA**）——防止访客
  绑定后触发策略强制二次验证锁死公共登录；
  同名真实超管存在时命令整体跳过（不改写密码与权限）；
- **可登录演示账号**（审批链路相关账号必须可登录，否则演示在途单无人能处理/撤回，
  还会因「在途实例存在时流程节点不可编辑」锁死演示流程）：
  - `demo_staff` / `demo_lead` / `demo_fin`（seed_demo_org 业务演示，`--password` 可改）；
  - `demo_flow_lily` / `demo_flow_chen`（seed_demo_flows 审批演示的申请人/审批人）；
  - 初始密码均为 `Demo@2026!`；
- 批量演示用户（`demo_0001` 起，seed_demo_users）为不可登录账号（unusable password），
  仅作数据集趋势/分布的数据填充，不参与审批链；
- **演示账号自愈任务**（`demo_account_selfheal_job`，每日 04:17）：演示账号存在时自动
  恢复发布态——重置密码、重挂演示角色与菜单裁剪、重申 MFA 白名单，并清除该账号的
  登录/MFA 锁定（防恶意访客改密码或故意输错锁死公共演示登录）；账号被人工停用或
  移入回收站视为有意下线，任务跳过；非演示环境无该账号，任务空转零成本；
- 演示账号**不会**进入正式初始化种子（`load_init_json`）；
- 演示委托（seed_demo_content）的委托双方均为演示账号：委托语义是「待办归属替换」
  （节点解析到委托人时任务整体转给代理人），**禁止把超管作为委托人**——否则超管在所有
  流程的待办都会被转走，表现为「待我审批」恒为空；
- 各命令全部幂等（固定标识 / 固定主键），可重复执行；`--clean-only` 只清理不生成
  （`seed_demo_clean` 的编排入口）；
- `seed_demo_clean` 会回滚对内置种子的改写（流程节点审批人、演示部门负责人、演示版本快照），
  内置定义类数据（loadjson 的示例流程/表单/数据集/看板等）不在卸载范围；
- 内置流程的 leader 节点（请假/采购/用章等首节点）按申请人部门的负责人解析审批人：
  申请人无部门 / 部门无负责人 / 负责人即申请人本人时发起会被 fail-closed 拒绝，
  按发起失败的提示为申请人配置部门与负责人即可。


## 5. 常见问题排查

| 现象                        | 原因与处理                                                                                                                                        |
|---------------------------|----------------------------------------------------------------------------------------------------------------------------------------------|
| 登录页提示"当前服务器不允许登录"         | 多为登录接口限流（默认 `login: 50/h`，GET/POST 共享额度）；检查是否被自动化/共享出口打满，可在 `config.yml` 的 `DEFAULT_THROTTLE_RATES` 调整                                       |
| `db_status: false` 但数据库正常 | 确认 `config.yml` 数据库连接项；4.2.5 起健康检查不再依赖 Monitor 表                                                                                             |
| 导入/导出无响应                  | 检查 heavy worker 是否在线（`celery_status`、flower 面板）；无 heavy worker 时任务滞留队列                                                                       |
| flower 无法访问               | flower 随 web 容器启动（`start web`），认证取 `CELERY_FLOWER_AUTH` 配置；未配置认证时仅允许绑定 127.0.0.1，绑定其他地址启动会被拒绝（见 [security-review.md](../security-review.md)） |
| 服务启动即退出                   | `SECRET_KEY` 未设置（非 DEBUG 强制校验）；查看 `data/logs/`                                                                                               |

更多场景（登录锁定、WebSocket 不通、导入导出积压、权限不生效、磁盘占满、备份恢复、migrate 卡住、CVE
响应等）见 [runbook.md](runbook.md)。

