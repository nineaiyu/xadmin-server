# xadmin-server
xadmin-基于Django+vue3的rbac权限管理系统

前端 [xadmin-client](https://github.com/nineaiyu/xadmin-client) 

### 在线预览
[https://xadmin.dvcloud.xin/](https://xadmin.dvcloud.xin/)
账号密码：admin/admin123

### 生成数据表并迁移
```shell
python manage.py makemigrations
python manage.py migrate
```

### 创建管理员账户

```shell
python manage.py createsuperuser
```

### 启动程序
##### a.本地环境直接启动 

```shell
python manage.py start all
```

##### b.容器化启动

```shell
docker compose up -d
```

### 导入默认菜单

```shell
python manage.py loaddata loadjson/menu.json
```

# 附录

### 容器部署

```shell
docker compose up
```

### 保存当前菜单为文件

```shell
python manage.py dumpdata system.MenuMeta system.Menu -o loadjson/menu.json
```
