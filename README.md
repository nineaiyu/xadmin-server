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

### 首次启动，需要先创建管理用户，并进行导入默认菜单

```shell
python manage.py load_init_json
```

## 新应用开发流程

#### 1.通过命令创建应用一个movies的应用

```shell
python manage.py startapp movies
```

#### 2.在应用目录下添加应用配置 ```config.py```，用与系统自动读取配置

```python
from django.urls import path, include

# 路由配置，当添加APP完成时候，会自动注入路由到总服务
URLPATTERNS = [
    path('api/movies/', include('movies.urls')),
]

# 请求白名单，支持正则表达式，可参考settings.py里面的 PERMISSION_WHITE_URL
PERMISSION_WHITE_REURL = []

```

#### 3，若要使用字段权限，则需要继承 ```BaseModelSerializer``` 参考 ```system/utils/serializer.py```

```python
class ModelLabelFieldSerializer(BaseModelSerializer):
    class Meta:
        model = models.ModelLabelField
        fields = ['pk', 'name', 'label', 'parent', 'created_time', 'updated_time', 'field_type_display']
        read_only_fields = ['pk', 'name', 'label', 'parent', 'created_time', 'updated_time']

    field_type_display = serializers.CharField(source='get_field_type_display', read_only=True)
```

# 附录

### 容器部署

```shell
docker compose up
```

### 导出系统相关配置信息，包含角色，部门，菜单等配置

```shell
python manage.py dump_init_json
```

### nginx 前端代理

```shell
    location /ws/message {
        proxy_pass http://127.0.0.1:28896;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_redirect off;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Host $server_name;
        proxy_set_header X-Forwarded-Proto https; # https代理需求添加该参数
    }

    location ~ ^/(api|flower|media) {
        proxy_pass http://127.0.0.1:28896;
        proxy_send_timeout 180;
        proxy_connect_timeout 180;
        proxy_read_timeout 180;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Host $server_name;
        proxy_set_header X-Forwarded-Proto https; # https代理需求添加该参数
    }


    location / {
        try_files $uri $uri/  /index.html;
    }

```