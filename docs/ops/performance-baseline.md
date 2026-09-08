# 性能基线测定流程（T3.1）

> 关联：半年规划 P3/T3.1；基线登记处 [docs/metrics.md](../../../docs/metrics.md)（工作区）；
> 缓存体系见 `docs/architecture/cache.md`，索引现状见 `docs/architecture/indexes.md`。
> 状态：**开发侧准备完成（2026-09-06）**——silk 接入 + k6 脚本 + 本流程文档已就绪，
> 实测待具备运行环境（压测专用 DB/Redis + k6）后按本文执行，结果回填 metrics.md。

## 一、目的与分工

建立六个关键接口的 P50/P95 基线，让性能可度量、可回归（对应风险 R5「无性能基线」）。两个工具分工明确：

| 工具                 | 用途                                  | 何时用                     |
|--------------------|-------------------------------------|-------------------------|
| k6（`loadtest/k6/`） | 压测定基线：RPS、客户端观测的 P50/P95、错误率        | 基线测定与回归对比，**必须关闭 silk** |
| django-silk        | 单接口剖析：SQL 逐条耗时、N+1、Python profiling | 基线劣化归因、优化前定位热点，低并发下使用   |

**核心原则：silk 有侵入开销（每请求记录 + 落库），k6 基线测定时必须关闭**，否则数据被污染；
先 k6 测出「哪里慢」，再开 silk 看「为什么慢」。

## 二、六个关键接口与脚本

规划 T3.1 指定的关键接口 → k6 脚本映射（目标模块默认用户管理，可用 `LIST_PATH` 切换）：

| # | 接口    | 方法与路径                                                        | 脚本               | 默认档位       | 前置条件                            |
|---|-------|--------------------------------------------------------------|------------------|------------|---------------------------------|
| 1 | 登录    | POST `/api/system/login/basic`                               | `01-login.js`    | 5 VU / 30s | 压测环境关闭登录三开关 + 放开 login 限流（见 §三） |
| 2 | 菜单/路由 | GET `/api/system/routes`                                     | `02-routes.js`   | 20 VU / 1m | 普通登录态即可（白名单路由）                  |
| 3 | 列表页   | GET `/api/system/user?page=1&size=20`                        | `03-list.js`     | 20 VU / 1m | 种子数据（`seed_users.py`）           |
| 4 | 元数据   | GET `search-columns` / `search-fields` / `?with_meta=1`      | `04-metadata.js` | 20 VU / 1m | 同上；with_meta 组用于验证 T3.2 内联优化收益  |
| 5 | 导出    | GET `/api/system/user/export-data?type=xlsx`                 | `05-export.js`   | 5 VU / 1m  | 种子数据 + `EXPORT_FILTER` 绑定导出范围   |
| 6 | 导入    | POST `/api/system/user/import-data?action=update&task=false` | `06-import.js`   | 5 VU / 1m  | 种子数据（update 模式）或短时 create 模式    |

脚本约定：

- 所有参数经环境变量注入：`BASE_URL`（默认 `http://127.0.0.1:8896`）、`USERNAME`/`PASSWORD`、
  `VUS`、`DURATION`、`LIST_PATH`、`LIST_SIZE`、`EXPORT_FILTER`、`IMPORT_MODE`、`IMPORT_ROWS`；
- 结果 JSON 写入 `loadtest/results/`（已 gitignore），按脚本分文件，含按 group 拆分的
  avg/P50/P90/P95/P99/max 与错误率；
- 阈值（`p(95)<2000`、失败率 <1%）是**异常波动护栏而非 SLO**，首轮基线回填后另行评审正式 SLO。

## 三、压测环境准备（一次性）

压测必须使用**专用环境与专用数据库**，不得指向日常开发/生产数据：

1. **config.yml 关键项**（压测专用副本，勿改日常 config.yml）：
    - `SILK_ENABLED` 保持缺省 false（见 §一）；
    - `SECURITY_LOGIN_CAPTCHA_ENABLED: false`、`SECURITY_LOGIN_ENCRYPTED_ENABLED: false`、
      `SECURITY_LOGIN_TEMP_TOKEN_ENABLED: false`——否则脚本无法完成登录；
    - 登录限流放开：`DEFAULT_THROTTLE_RATES: { login: '100000/h' }`（默认 50/h 会在 50 次后
      全部返回 999，`01-login.js` 的 `login_throttled` 指标非零即为命中）；
    - 数据库指向压测专用库（PG/MySQL 均可，但基线多轮对比必须同库同机）。
2. **安装 k6**：`brew install k6`（或参考 [k6 安装文档](https://k6.io/docs/get-started/installation/)）。
3. **启动服务**：`python manage.py runserver` 仅适合冒烟；正式测定用 gunicorn（与生产同参，
   `python manage.py services gunicorn`），并记录 worker 数/机器规格——
   这些是基线的环境元数据，换环境后基线不可比。

### 3.1 可复现压测环境（2026-09-06 首测实际采用）

无需改动日常 config.yml，两步拉起完全隔离的专用环境：

```bash
# ① 一次性专用容器（仅绑 127.0.0.1，与日常开发库/Redis 完全隔离）
docker run -d --name xadmin-loadtest-pg \
  -e POSTGRES_USER=server -e POSTGRES_PASSWORD=loadtest -e POSTGRES_DB=xadmin_loadtest \
  -p 127.0.0.1:55432:5432 registry.cn-beijing.aliyuncs.com/nineaiyu/postgres:17.11 \
  postgres -c max_connections=500
docker run -d --name xadmin-loadtest-redis \
  -p 127.0.0.1:56379:6379 registry.cn-beijing.aliyuncs.com/nineaiyu/redis:7.4.11 \
  redis-server --requirepass loadtest --port 6379

# ② 以压测专用 settings 执行 migrate + 初始化 + 种子（密码仅本地压测环境）
export DJANGO_SETTINGS_MODULE=loadtest.settings_loadtest XADMIN_ADMIN_PASSWORD='<压测密码>'
.venv/bin/python manage.py migrate
.venv/bin/python utils/init_data.py        # 默认超管用户名为 xadmin
.venv/bin/python loadtest/seed_users.py --count 1000

# ③ 生产同参启动被测服务后按 §四 压测；结束后 docker rm -f 两个容器
DJANGO_SETTINGS_MODULE=loadtest.settings_loadtest .venv/bin/gunicorn \
  server.asgi:application -b 127.0.0.1:8896 -k uvicorn.workers.UvicornWorker \
  -w 4 --max-requests 10240 --max-requests-jitter 2048 --graceful-timeout 30
```

`loadtest/settings_loadtest.py` 职责：注入专用 DB/Redis 连接、关闭登录三开关、
放开 anon/user/login 限流、固定 SILK_ENABLED=False、celery broker 指向专用 Redis
独立 db 15（无 worker 时导入导出经探针自动走直接执行分支）。

**⚠️ ASGI 每请求新建 DB 连接（T3.1 首测最重要发现）**：Django 的 ASGIHandler 为每个
请求创建独立线程（ThreadSensitiveContext），线程随请求结束消亡，其 DB 连接随之丢弃
——base.py 虽配置 `CONN_MAX_AGE=600`，但该机制在 ASGI 形态下**无效**，等效于每请求
新建 PG 连接；psycopg2 不支持 Django 5.1+ 的 server 端连接池（仅 psycopg3）。
持续 ~600rps 时临时端口耗尽（macOS 宿主机与 Linux 容器均实测
`Cannot assign requested address`）→ routes 端点 13-27% 请求 500。
首测环境处理：压测容器加 `--sysctl net.ipv4.tcp_tw_reuse=1` + 扩大临时端口段
（`net.ipv4.ip_local_port_range="1024 65535"`）后 0% 失败完成测量（每请求连接开销
保留在基线数据中，符合生产现状）。**根因修复另立技术债**：psycopg3 + `OPTIONS.pool`
连接池或 pgbouncer，并复核 ASGI/WSGI 形态取舍。

## 四、基线测定流程（每次执行）

```bash
cd xadmin-server

# 1. 种子数据：固定规模（首次或数据漂移后执行；会清理并重建 perf_ 前缀用户）
python loadtest/seed_users.py --count 1000

# 2. 预热：短时低压，填充 ORM/权限缓存，避免首轮冷启动污染
cd loadtest/k6 && VUS=2 DURATION=15s k6 run 03-list.js

# 3. 正式压测：六接口顺序执行
BASE_URL=http://127.0.0.1:8896 USERNAME=admin PASSWORD=<压测环境密码> ./run-all.sh

# 4. 重复第 3 步共 3 轮（间隔 1 分钟），单指标取三轮中位数登记
```

> **macOS 注意**：`USERNAME` 是 zsh 魔法参数（固定为本机登录名），命令行内联
> `USERNAME=admin k6 run ...` 传不进 k6 子进程（实测登录被打成系统用户名）。
> macOS 下统一用 `env` 注入：`env BASE_URL=... USERNAME=admin PASSWORD=... ./run-all.sh`。

执行注意：

- **导入基线默认 update 模式**（不增长数据）；如需测 create 链路：
  `IMPORT_MODE=create IMPORT_ROWS=5 DURATION=30s VUS=2 k6 run 06-import.js`，跑完重跑 `seed_users.py` 复位；
- **导出必须绑定 `EXPORT_FILTER`**（如 `EXPORT_FILTER='&username=perf_'`），导出不分页，
  全表导出会让数据规模漂移、多轮结果不可比；
- 压测期间不要同时开 silk / DEBUG_DEV SQL 日志（`02-routes` 等缓存型接口对额外查询极敏感）；
- 登录脚本若 `login_throttled` 计数非零，本轮作废，检查 §三 限流配置。

## 五、silk 剖析流程（归因时使用）

```bash
# 1. 安装 dev 依赖（含 django-silk）
pip install -r requirements-dev.txt

# 2. 压测环境 config.yml 开启：DEBUG/DEBUG_DEV true + SILK_ENABLED: true
python manage.py migrate          # 创建 silk 三张表
# 3. 启动服务，以正常操作/低 VU 复现目标接口流量（建议 VUS≤2，避免剖析落库干扰）
# 4. 访问 http://127.0.0.1:8896/silk（需员工/超级管理员登录）：
#    Requests 页查看单请求 SQL 列表与耗时 → cProfile 火焰定位 Python 热点
# 5. 剖析完立即关回 SILK_ENABLED（剖析 profile 落在 tmp/silk_profiles/，已 gitignore）
```

已知限制：项目开启 `ATOMIC_REQUESTS`，silk 记录与业务同事务；若剖析期间遇事务相关报错，
属工具与该配置的已知张力，仅在本地剖析环境临时关闭 `ATOMIC_REQUESTS` 复现，不进基线数据。

## 六、结果登记口径（回填 metrics.md）

| 字段  | 口径                                         |
|-----|--------------------------------------------|
| 数值  | 三轮中位数；P50/P95 为主，异常轮次（有 throttled/5xx）整轮作废 |
| 环境  | 机器规格 / worker 数 / DB 引擎与版本 / 种子规模 / 压测日期   |
| 档位  | 各脚本默认档位（§二表），改动过需注明                        |
| 观测点 | k6 客户端口径（含网络与本机回环）；服务端 SQL 定位用 silk，不混入基线表 |

metrics.md 的「五、性能基线」占位表逐行回填，形如：

```
| 接口 | RPS | P50 | P95 | 错误率 | 环境 |
|------|-----|-----|-----|--------|------|
| 登录 | ... | ... | ... | 0      | ...  |
```

## 七、回归判定

- 以 metrics.md 登记的基线为参照：某接口 P95 劣化 **>20%** 且 RPS 同向下降 → 需开 silk 归因，
  并在 PR 描述中给出 SQL/火焰证据；
- 涉及 `common/core/`（modelset/filter/serializers/permission）、元数据接口、分页的改动，
  PR 自查项加「是否跑过基线回归」；
- 每阶段结束（双周回顾）若架构有实质变更（如 T2.1 拆分、T3.2 内联），重新测定并覆盖登记，
  旧基线在回填记录中留痕。

## 八、基线快照与自动比对（2026-09-08，N5 性能防退化）

§七 的判定此前靠人工查表，本節把它固化成可执行的门禁：基线数值入快照文件，
k6 跑完由脚本自动比对并给出退出码。

**组成**

| 文件 | 职责 |
|------|------|
| `loadtest/baseline.json` | 基线快照：六用例的 P95 / RPS 基线 + 环境元数据 + 容差（三轮中位数，来源 metrics.md §五） |
| `loadtest/check_baseline.py` | 比对脚本：读 `loadtest/k6/results/*.json` 与快照比对，输出表格/markdown/JSON，劣化非 0 退出 |
| `loadtest/k6/run-all.sh` | `CHECK=1` 时跑完自动调比对（`CHECK_PYTHON` / `CHECK_FORMAT` / `CHECK_ARGS` 可覆盖） |
| `.github/workflows/perf.yml` | 夜间 + 手动触发的 CI 压测档（PG/Redis service + gunicorn + k6 + 比对） |

**判定口径**（快照 `tolerance` 可改，命令行亦可覆盖）

| 项 | 判定 | 默认容差 |
|----|------|---------|
| P95 | 当前 > 基线 × `p95_ratio` | 1.2（劣化 20%） |
| RPS | 当前 < 基线 × `rps_ratio` | 0.8 |
| 错误率 | 当前 > `max_error_rate` | 0.01 |
| Trend 分档 | 04 元数据三变体各自按 P95 同口径判定 | 同 `p95_ratio` |

> k6 v2 移除了 group 子指标，同一脚本内的多接口（如 04 三变体）改由显式 `Trend`
> 分档登记（`meta_columns_duration` / `meta_fields_duration` / `meta_with_meta_duration`），
> 否则混跑时某个变体劣化会被整体 `_all` 拉平而漏报。

**用法**

```bash
cd xadmin-server

# 常规回归（固定环境跑完 k6 后）：默认比对 loadtest/k6/results 与 baseline.json
.venv/bin/python loadtest/check_baseline.py

# 跑完即比对（run-all.sh 内置）
cd loadtest/k6 && env BASE_URL=... USERNAME=admin PASSWORD=xxx CHECK=1 ./run-all.sh

# 输出 markdown 贴 PR / step summary
.venv/bin/python loadtest/check_baseline.py --format md

# CI 断崖档：跳过 RPS（与环境强相关），P95 容差放宽
.venv/bin/python loadtest/check_baseline.py --checks p95,error --tolerance 3.0

# 固定环境重新测定（三轮中位数）后刷新快照
.venv/bin/python loadtest/check_baseline.py --update --update-note "2026-xx-xx 复测，环境：xxx"
```

退出码：`0` 通过 / `1` 存在劣化 / `2` 输入错误（缺基线或结果目录）。

**两档的定位差异（重要）**

- **本机固定环境档**：机器规格、worker 数、PG/Redis 版本、种子规模与快照 `env` 一致时才可比，
  是精确回归（P95 1.2 倍 + RPS 0.8 倍）的唯一可信来源；
- **CI 档（perf.yml）**：GitHub runner 规格与快照环境不同，绝对吞吐天然偏低，
  故只跳过 RPS、P95 容差放宽到 3 倍，用于拦截**数量级**劣化并留档趋势报告，
  **不代表精确回归结论**。首轮 CI 跑完后按实测调整 `tolerance` 入参并在此登记。

**快照刷新纪律**：改动 `common/core/`、元数据接口、索引、连接池、缓存策略后，
在固定环境重跑三轮并 `--update` 刷新快照，同时在 metrics.md §三 回填记录中写明环境与方法。
