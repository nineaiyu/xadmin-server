# xadmin-server

xadmin-基于Django+vue3的rbac权限管理系统

前端 [xadmin-client](https://github.com/nineaiyu/xadmin-client)

### 在线预览（线上演示）

[https://xadmin.dvcloud.xin/](https://xadmin.dvcloud.xin/)
账号密码：admin/admin123（线上演示账号；本地初始化的账号为 `xadmin`，见下方快速开始）

## 快速开始

```shell
# 方式一：Docker 一键体验（起后端全栈 + 幂等初始化 + 启动前端；需 Docker 与 Node/pnpm）
bash utils/dev_up.sh              # --with-demo 追加演示数据；--backend-only 仅起后端；--help 查看帮助
```

- 浏览器打开 <http://127.0.0.1:8848>，账号 `xadmin`，初始密码在初始化输出中**仅打印一次**；
- 停止后端：`bash utils/dev_down.sh`；重复执行是幂等的（升级后同样适用）；
- 首次运行会自动构建镜像（数分钟）。

```shell
# 方式二：本机源码启动（Python 3.13+，需自备数据库（PostgreSQL/MySQL/SQLite）与 Redis）
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt   # 依赖清单由 pyproject.toml 导出，见下方「依赖管理」
cp config_example.yml config.yml   # 可跳过：不创建时自动使用内置默认配置并自动生成 SECRET_KEY
python manage.py migrate
python utils/init_data.py          # 幂等；--with-demo / --skip-ip-db / --admin-password
python manage.py start all -d
```

超管初始密码：`--admin-password` 或环境变量 `XADMIN_ADMIN_PASSWORD` 显式指定，未设置时随机生成并仅打印一次。

### 依赖管理（pyproject + uv）

- **事实源**：`pyproject.toml`（运行依赖 + `dev` 组）；`requirements.txt` / `requirements-dev.txt` 是
  `uv export` 的**导出产物**，请勿手工编辑（改依赖 = 改 pyproject 后重新导出）；
- **uv 快路径**（需 [uv](https://docs.astral.sh/uv/) ≥ 0.12，环境重建为秒级）：

```shell
uv sync --all-groups              # 创建/同步 .venv（等价 pip install -r requirements.txt -r requirements-dev.txt）
uv lock                           # 变更 pyproject 依赖后刷新 uv.lock（入库，保证解析可复现）
# 重新导出产物（pyproject.toml 头部注释同口径）：
uv export --no-hashes --no-emit-project --no-group dev --no-annotate -o requirements.txt
uv export --no-hashes --no-emit-project --only-group dev --no-annotate -o requirements-dev.txt
```

- **pip 路径保持不变**（安装器 / Docker / CI 均按既有命令安装产物文件，见 `Dockerfile` 与 `.github/workflows/`）；
- **可选依赖**（对象存储后端，P-4）：声明于 `[project.optional-dependencies].storage`，默认不装，
  未装时文件链路回退本地，启用方式 `uv sync --extra storage` 或 `pip install django-storages boto3`
  （详见 `docs/ops/storage.md`）；
- 三方一致性（pyproject ↔ 产物 ↔ uv.lock）由 `tests/unit/test_dependency_manifest.py` 守护：
  CI 跑纯解析校验（不依赖 uv 与网络），本机存在 uv 时额外校验「产物与导出逐行一致」。

## 开发部署文档

**优先查阅本仓库文档中心：[docs/README.md](docs/README.md)**（环境搭建、架构总览、三层权限、部署运维 runbook、ADR）

- 本地部署 / Docker 部署 / 升级回滚 / 国产化适配：[docs/ops/deployment.md](docs/ops/deployment.md)
- 常见故障排查：[docs/ops/runbook.md](docs/ops/runbook.md)
- 外站在线文档（补充资料）：[https://docs.dvcloud.xin/](https://docs.dvcloud.xin/)

### [Centos 9 Stream 安装部署](https://docs.dvcloud.xin/guide/installation-local.html)

### [Docker 容器化部署](https://docs.dvcloud.xin/guide/installation-docker.html)

# 附录

⚠️ Windows上面无法正常运行celery flower，导致任务监控无法正常使用，请使用Linux环境开发部署

## 启动程序(启动之前必须配置好Redis和数据库)

### A.一键执行命令【不支持windows平台，如果是Windows，请使用 手动执行命令】

```shell
python manage.py start all -d  # -d 参数是后台运行，如果去掉，则前台运行
```

### B.手动执行命令

> 日常运行推荐直接用上面的 A 一键命令；以下为拆解示意，各服务参数（端口 / 地址 / 认证）以
> `common/management/commands/services/` 下的定义为准。

#### 1.api服务

```shell
python manage.py runserver 0.0.0.0:8896
```

#### 2.定时任务

```shell
python -m celery -A server beat -l INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler --max-interval 60
python -m celery -A server worker -P threads -l INFO -c 10 -Q celery --heartbeat-interval 10 -n celery@%h --without-mingle
```

#### 3.任务监控[windows可能会异常]

```shell
python -m celery -A server flower -logging=info --url_prefix=api/flower --auto_refresh=False
```

> 地址与端口由 `config.yml` 的 `CELERY_FLOWER_HOST` / `CELERY_FLOWER_PORT` 决定（未配置认证时仅允许绑定
> 127.0.0.1，见 `common/management/commands/services/services/flower.py`）。
```

## 捐赠

如果你觉得这个项目帮助到了你，可以 [star](https://github.com/nineaiyu/xadmin-server) 表示鼓励；捐赠方式与捐赠者权益见[捐赠页](https://docs.dvcloud.xin/donate.html)。
