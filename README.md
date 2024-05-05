# xadmin-server

xadmin-基于Django+vue3的rbac权限管理系统

前端 [xadmin-client](https://github.com/nineaiyu/xadmin-client)

### 在线预览

[https://xadmin.dvcloud.xin/](https://xadmin.dvcloud.xin/)
账号密码：admin/admin123

## 本地环境运行 必须先配置好```redis```服务

#### 数据库默认使用的是sqlite3

## redis 配置

#### 打开配置文件```server/settings.py```,修改为自己的redis服务配置

```python
REDIS_PASSWORD = "nineven"
REDIS_HOST = "redis"
REDIS_PORT = 6379
```

## 数据库配置（开发环境默认使用的是sqlite3），正式环境建议使用MySQL或者postgresql

#### 打开配置文件```server/settings.py```,修改为自己的mysql服务配置

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'xadmin',
        'USER': 'server',
        'PASSWORD': 'KGzKjZpWBp4R4RSa',
        'HOST': 'mariadb',
        'PORT': 3306,
        'CONN_MAX_AGE': 600,
        # 设置MySQL的驱动
        # 'OPTIONS': {'init_command': 'SET storage_engine=INNODB'},
        'OPTIONS': {'init_command': 'SET sql_mode="STRICT_TRANS_TABLES"', 'charset': 'utf8mb4'}
    },
    # "default": {
    #     "ENGINE": "django.db.backends.sqlite3",
    #     "NAME": BASE_DIR / "db.sqlite3",
    # }
}
```

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

##### a.Linux 环境命令一键启动api服务

```shell
python manage.py start all
```

##### b.Windows或Linux 环境通过命令启动api服务

⚠️ Windows上面无法正常运行celery flower，导致任务监控无法正常使用，请使用Linux环境开发部署

```shell
python manage.py runserver 0.0.0.0:8896
python -m celery -A server flower --debug --url_prefix=api/flower --auto_refresh=False  --address=0.0.0.0 --port=5566
python -m celery -A server beat -l INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler --max-interval 60
python -m celery -A server worker -P prefork -l INFO --autoscale 10,3 -Q celery --heartbeat-interval 10 -n celery@%h --without-mingle
```

##### c.容器化启动api服务

```shell
docker compose up -d
```

### 首次启动，需要先创建管理用户，并进行导入默认菜单

```shell
python manage.py load_init_json
```

## [点击查看字段权限文档](docs/field-permission.md)

## [点击查看数据权限文档](docs/data-permission.md)

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
        proxy_pass http://127.0.0.1:8896;
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

    location ~ ^/(api|flower|media|api-docs) {
        proxy_pass http://127.0.0.1:8896;
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