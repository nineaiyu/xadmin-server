# xadmin-server

xadmin-基于Django+vue3的rbac权限管理系统

前端 [xadmin-client](https://github.com/nineaiyu/xadmin-client)

### 在线预览

[https://xadmin.dvcloud.xin/](https://xadmin.dvcloud.xin/)
账号密码：admin/admin123

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
cp config_example.yml config.yml   # 可跳过：不创建时自动使用内置默认配置并自动生成 SECRET_KEY
python manage.py migrate
python utils/init_data.py          # 幂等；--with-demo / --skip-ip-db / --admin-password
python manage.py start all -d
```

超管初始密码：`--admin-password` 或环境变量 `XADMIN_ADMIN_PASSWORD` 显式指定，未设置时随机生成并仅打印一次。

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
python -m celery -A server flower -logging=info --url_prefix=api/flower --auto_refresh=False  --address=0.0.0.0 --port=5566
```

## 捐赠or鼓励

如果你觉得这个项目帮助到了你，你可以[star](https://github.com/nineaiyu/xadmin-server)表示鼓励，也可以帮作者买一杯果汁🍹表示鼓励。

| 微信                                                                                     | 支付宝                                                                                     |
|----------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------|
| <img src="http://qiniu.cdn.xadmin.dvcloud.xin/pay/wxpay.jpg" height="188" width="188"> | <img src="http://qiniu.cdn.xadmin.dvcloud.xin/pay/alipay.jpg" height="188" width="188"> |
