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

```shell
python manage.py runserver 0.0.0.0:8896
python -m celery -A server flower --debug --url_prefix=api/flower --auto_refresh=False  --address=0.0.0.0 --port=5566
python -m celery -A server beat -l INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler --max-interval 60
python -m celery -A server worker -P prefork -l INFO --autoscale 10,3 -Q celery --heartbeat-interval 10 -n celery@%h --without-mingle
```
