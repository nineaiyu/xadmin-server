# xadmin-server
xadmin-基于Django+vue3的rbac权限管理系统


### 生成数据表并迁移
```shell
python manage.py makemigrations
python manage.py migrate
```
### 导入默认菜单
```shell
python manage.py loaddata loadjson/menu.json
```
### 创建管理员账户
```shell
python manage.py createsuperuser
```

### 启动程序
```shell
python manage.py start all
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
