# xadmin-server

xadmin-基于Django+vue3的rbac权限管理系统

前端 [xadmin-client](https://github.com/nineaiyu/xadmin-client)

### 在线预览

[https://xadmin.dvcloud.xin/](https://xadmin.dvcloud.xin/)
账号密码：admin/admin123

## 开发部署文档

[https://docs.dvcloud.xin/](https://docs.dvcloud.xin/)

## [Centos 9 Stream 安装部署](https://docs.dvcloud.xin/guide/installation-local.html)

## [Docker 容器化部署](https://docs.dvcloud.xin/guide/installation-docker.html)

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
