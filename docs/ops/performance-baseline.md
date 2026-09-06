# 性能基线测定流程（T3.1）

> 关联：半年规划 P3/T3.1；基线登记处 [docs/metrics.md](../../../docs/metrics.md)（工作区）；
> 缓存体系见 `docs/architecture/cache.md`，索引现状见 `docs/architecture/indexes.md`。
> 状态：**开发侧准备完成（2026-09-06）**——silk 接入 + k6 脚本 + 本流程文档已就绪，
> 实测待具备运行环境（压测专用 DB/Redis + k6）后按本文执行，结果回填 metrics.md。

## 一、目的与分工

建立六个关键接口的 P50/P95 基线，让性能可度量、可回归（对应风险 R5「无性能基线」）。两个工具分工明确：

| 工具 | 用途 | 何时用 |
|------|------|--------|
| k6（`loadtest/k6/`） | 压测定基线：RPS、客户端观测的 P50/P95、错误率 | 基线测定与回归对比，**必须关闭 silk** |
| django-silk | 单接口剖析：SQL 逐条耗时、N+1、Python profiling | 基线劣化归因、优化前定位热点，低并发下使用 |

**核心原则：silk 有侵入开销（每请求记录 + 落库），k6 基线测定时必须关闭**，否则数据被污染；
先 k6 测出「哪里慢」，再开 silk 看「为什么慢」。

## 二、六个关键接口与脚本

规划 T3.1 指定的关键接口 → k6 脚本映射（目标模块默认用户管理，可用 `LIST_PATH` 切换）：

| # | 接口 | 方法与路径 | 脚本 | 默认档位 | 前置条件 |
|---|------|-----------|------|---------|---------|
| 1 | 登录 | POST `/api/system/login/basic` | `01-login.js` | 5 VU / 30s | 压测环境关闭登录三开关 + 放开 login 限流（见 §三） |
| 2 | 菜单/路由 | GET `/api/system/routes` | `02-routes.js` | 20 VU / 1m | 普通登录态即可（白名单路由） |
| 3 | 列表页 | GET `/api/system/user?page=1&size=20` | `03-list.js` | 20 VU / 1m | 种子数据（`seed_users.py`） |
| 4 | 元数据 | GET `search-columns` / `search-fields` / `?with_meta=1` | `04-metadata.js` | 20 VU / 1m | 同上；with_meta 组用于验证 T3.2 内联优化收益 |
| 5 | 导出 | GET `/api/system/user/export-data?type=xlsx` | `05-export.js` | 5 VU / 1m | 种子数据 + `EXPORT_FILTER` 绑定导出范围 |
| 6 | 导入 | POST `/api/system/user/import-data?action=update&task=false` | `06-import.js` | 5 VU / 1m | 种子数据（update 模式）或短时 create 模式 |

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
3. **启动服务**：`python manage.py runserver` 仅适合冒烟；正式测定用
   `python manage.py services gunicorn`（或 compose 拉起），并记录 worker 数/机器规格——
   这些是基线的环境元数据，换环境后基线不可比。

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

| 字段 | 口径 |
|------|------|
| 数值 | 三轮中位数；P50/P95 为主，异常轮次（有 throttled/5xx）整轮作废 |
| 环境 | 机器规格 / worker 数 / DB 引擎与版本 / 种子规模 / 压测日期 |
| 档位 | 各脚本默认档位（§二表），改动过需注明 |
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
