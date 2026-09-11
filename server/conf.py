#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#

import errno
import json
import logging
import os
import sys
import types
from importlib import import_module

import yaml

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger("xadmin.conf")


def import_string(dotted_path):
    try:
        module_path, class_name = dotted_path.rsplit(".", 1)
    except ValueError as err:
        raise ImportError("%s doesn't look like a module path" % dotted_path) from err

    module = import_module(module_path)

    try:
        return getattr(module, class_name)
    except AttributeError as err:
        raise ImportError('Module "%s" does not define a "%s" attribute/class' % (module_path, class_name)) from err


class DoesNotExist(Exception):
    pass


class Config(dict):
    base = {
        "SECRET_KEY": "",
        "DEBUG": False,
        "DEBUG_DEV": False,
        # django-silk 性能剖析开关（性能基线）：仅允许 DEBUG/DEBUG_DEV 环境开启，
        # 依赖在 requirements-dev.txt（django-silk）；开启后需执行 migrate 创建 silk 表
        "SILK_ENABLED": False,
        # Prometheus 指标（默认关闭）：启用需同时配置 METRICS_TOKEN，
        # 抓取方以 Authorization: Bearer <token> 访问 /api/common/api/metrics
        "METRICS_ENABLED": False,
        "METRICS_TOKEN": "",
        "LOG_LEVEL": "WARNING",
        # 应用日志格式 text（默认）/ json（结构化，供 Loki/ELK 采集）
        "LOG_FORMAT": "text",
        # 按天滚动的日志保留天数，超出后整体清理日期目录（0 表示不清理）
        "LOG_BACKUP_COUNT": 30,
        # Sentry 错误聚合；DSN 为空时完全不初始化（sentry-sdk 已在 requirements.txt）
        "SENTRY_DSN": "",
        "SENTRY_ENVIRONMENT": "production",
        "SENTRY_TRACES_SAMPLE_RATE": 0.0,
        "XADMIN_APPS": [],
        # 表前缀 abc_
        "DB_PREFIX": "",
        # redis
        "REDIS_HOST": "redis",
        "REDIS_PORT": 6379,
        "REDIS_PASSWORD": "",
        "DEFAULT_CACHE_ID": 1,
        "CHANNEL_LAYERS_CACHE_ID": 2,
        "CELERY_BROKER_CACHE_ID": 3,
        # database
        "DB_ENGINE": "mysql",
        "DB_HOST": "mariadb",
        "DB_PORT": 3306,
        "DB_DATABASE": "xadmin",
        "DB_USER": "server",
        "DB_PASSWORD": "",
        # PostgreSQL server 端连接池（Django 5.1+，需 psycopg3）。
        # 仅 DB_ENGINE=postgresql 生效；开启后 CONN_MAX_AGE 自动归零（连接生命周期由池管理）。
        # 容量核算：GUNICORN_MAX_WORKER × DB_POOL_MAX_SIZE + celery 子进程数 × DB_POOL_MAX_SIZE
        # 应小于 PG max_connections
        "DB_POOL": True,
        "DB_POOL_MIN_SIZE": 2,
        "DB_POOL_MAX_SIZE": 8,
        # HOST 校验白名单，生产环境必须配置，如 ['xadmin.example.com']；DEBUG 模式默认放行
        "ALLOWED_HOSTS": [],
        # CORS 跨域配置，同源部署（nginx 反代）无需配置；跨域部署请配置白名单
        "CORS_ALLOW_ALL_ORIGINS": False,
        "CORS_ALLOWED_ORIGINS": [],
        "LANGUAGE_CODE": "zh-hans",
        "TIME_ZONE": "Asia/Shanghai",
        # 服务配置
        "HTTP_BIND_HOST": "0.0.0.0",
        "HTTP_LISTEN_PORT": 8896,
        "GUNICORN_MAX_WORKER": 4,
        "CELERY_WORKER_COUNT": 10,
        # heavy 队列（导入/导出/批量重任务）worker 配置。
        # CPU 密集的 Excel 导出可把 POOL 改为 'prefork' 提升吞吐（threads 池受 GIL 限制）；
        # 默认维持 threads，与 default 队列保持相同的运行时状态共享行为
        "CELERY_HEAVY_POOL": "threads",
        "CELERY_HEAVY_CONCURRENCY": 4,
        # DRF BasicAuthentication 总开关：base64 明文凭证，默认关闭；
        # 本地调试需要时在 config.yml 显式开启
        "BASIC_AUTH_ENABLED": False,
        # celery flower 任务监控配置
        "CELERY_FLOWER_PORT": 5566,
        "CELERY_FLOWER_HOST": "127.0.0.1",
        # Flower 监控 basic-auth（格式 用户:密码），生产环境必须配置；
        # 未配置时 Flower 仅允许绑定 127.0.0.1 供本机调试，绑定其他地址将拒绝启动
        "CELERY_FLOWER_AUTH": "",
    }
    libs = {
        # REST_FRAMEWORK
        "DEFAULT_THROTTLE_RATES": {},
        # SIMPLE_JWT
        "ACCESS_TOKEN_LIFETIME": 3600,  # Unit: second
        "REFRESH_TOKEN_LIFETIME": 15 * 24 * 3600,  # Unit: second
    }
    settings = {
        # 密码安全配置
        "SECURITY_PASSWORD_MIN_LENGTH": 10,
        "SECURITY_ADMIN_USER_PASSWORD_MIN_LENGTH": 6,
        "SECURITY_PASSWORD_UPPER_CASE": True,
        "SECURITY_PASSWORD_LOWER_CASE": False,
        "SECURITY_PASSWORD_NUMBER": True,
        "SECURITY_PASSWORD_SPECIAL_CHAR": False,
        # 用户登录限制的规则
        "SECURITY_LOGIN_LIMIT_COUNT": 7,
        "SECURITY_LOGIN_LIMIT_TIME": 30,  # Unit: minute
        "SECURITY_CHECK_DIFFERENT_CITY_LOGIN": True,
        # 新设备/新 IP 登录提醒（异常登录提醒第二维度，与异地城市提醒互补；默认关闭渐进启用）
        "SECURITY_ABNORMAL_LOGIN_ALERT_ENABLED": False,
        "SECURITY_LOGIN_BASELINE_DAYS": 30,  # 判定基线窗口（近 N 天成功登录历史）
        # 登录IP限制的规则
        "SECURITY_LOGIN_IP_BLACK_LIST": [],
        "SECURITY_LOGIN_IP_WHITE_LIST": [],
        "SECURITY_LOGIN_IP_LIMIT_COUNT": 50,
        "SECURITY_LOGIN_IP_LIMIT_TIME": 30,  # Unit: minute
        # 登陆规则
        "SECURITY_LOGIN_ACCESS_ENABLED": True,
        "SECURITY_LOGIN_CAPTCHA_ENABLED": True,
        "SECURITY_LOGIN_ENCRYPTED_ENABLED": True,
        "SECURITY_LOGIN_TEMP_TOKEN_ENABLED": True,
        "SECURITY_LOGIN_BY_EMAIL_ENABLED": True,
        "SECURITY_LOGIN_BY_SMS_ENABLED": False,
        "SECURITY_LOGIN_BY_BASIC_ENABLED": True,
        # 注册规则
        "SECURITY_REGISTER_ACCESS_ENABLED": True,
        "SECURITY_REGISTER_CAPTCHA_ENABLED": True,
        "SECURITY_REGISTER_ENCRYPTED_ENABLED": True,
        "SECURITY_REGISTER_TEMP_TOKEN_ENABLED": True,
        "SECURITY_REGISTER_BY_EMAIL_ENABLED": True,
        "SECURITY_REGISTER_BY_SMS_ENABLED": False,
        "SECURITY_REGISTER_BY_BASIC_ENABLED": True,
        # 忘记密码规则
        "SECURITY_RESET_PASSWORD_ACCESS_ENABLED": True,
        "SECURITY_RESET_PASSWORD_CAPTCHA_ENABLED": True,
        "SECURITY_RESET_PASSWORD_TEMP_TOKEN_ENABLED": True,
        "SECURITY_RESET_PASSWORD_ENCRYPTED_ENABLED": True,
        "SECURITY_RESET_PASSWORD_BY_EMAIL_ENABLED": True,
        "SECURITY_RESET_PASSWORD_BY_SMS_ENABLED": False,
        # 绑定邮箱
        "SECURITY_BIND_EMAIL_ACCESS_ENABLED": True,
        "SECURITY_BIND_EMAIL_CAPTCHA_ENABLED": True,
        "SECURITY_BIND_EMAIL_TEMP_TOKEN_ENABLED": True,
        "SECURITY_BIND_EMAIL_ENCRYPTED_ENABLED": True,
        # 绑定手机
        "SECURITY_BIND_PHONE_ACCESS_ENABLED": True,
        "SECURITY_BIND_PHONE_CAPTCHA_ENABLED": True,
        "SECURITY_BIND_PHONE_TEMP_TOKEN_ENABLED": True,
        "SECURITY_BIND_PHONE_ENCRYPTED_ENABLED": True,
        # MFA / 敏感操作二次验证
        "SECURITY_MFA_CONFIRM_ENABLED": True,  # 敏感操作二次验证总开关
        "SECURITY_MFA_CONFIRM_BACKENDS": ["otp", "sms", "email", "password"],  # 允许的验证方式
        "SECURITY_MFA_VERIFY_TTL": 3600,  # MFA 方式确认有效期（秒）
        "SECURITY_MFA_PASSWORD_CONFIRM_TTL": 300,  # 密码方式确认有效期（秒）
        "SECURITY_MFA_LOGIN_PROTECT_ENABLED": True,  # 绑定 OTP 的用户登录时强制二次验证
        "SECURITY_MFA_LOGIN_TOKEN_TTL": 300,  # 登录 MFA 临时令牌有效期（秒）
        "SECURITY_MFA_OTP_VALID_WINDOW": 1,  # OTP 容错窗口（前后各 N 个周期）
        "SECURITY_MFA_OTP_ISSUER": "XAdmin",  # OTP 绑定 URI 中的签发方名称
        # 资源告警阈值（check_server_performance_period 周期检查，超标时邮件/站内信通知超管）
        "SECURITY_MONITOR_DISK_USED_MAX": 80,  # 磁盘使用率阈值（%）
        "SECURITY_MONITOR_MEMORY_USED_MAX": 85,  # 内存使用率阈值（%）
        "SECURITY_MONITOR_CPU_PERCENT_MAX": 80,  # CPU 使用率阈值（%）
        "SECURITY_MONITOR_CPU_LOAD_MAX": 5,  # 单核 CPU 负载阈值
        # 基本配置
        "SITE_URL": "http://127.0.0.1:8000",
        "FRONT_END_WEB_WATERMARK_ENABLED": False,  # 前端水印展示
        "PERMISSION_FIELD_ENABLED": True,  # 字段权限控制
        "PERMISSION_DATA_ENABLED": True,  # 数据权限控制
        "REFERER_CHECK_ENABLED": False,  # referer 校验
        "EXPORT_MAX_LIMIT": 20000,  # 限制导出数据数量
        # 异步导出记录与产物保留天数（下载中心），超期由 auto_clean_export_record_job 清理
        "EXPORT_FILE_KEEP_DAYS": 7,
        # 异步导入记录/源文件/错误报告保留天数（下载中心），超期由 auto_clean_import_record_job 清理
        "IMPORT_RECORD_KEEP_DAYS": 30,
        "IMPORT_FAIL_RATE_LIMIT": 0.5,  # 异步导入失败率中止阈值；0 表示不按失败率中止
        "IMPORT_ASYNC_MAX_RUNNING": 3,  # 同一用户同时进行中的异步导入任务上限；0 表示不限制
        "IMPORT_VALIDATE_ERROR_LIMIT": 200,  # 导入前校验返回的错误行明细上限
        # 软删除回收站保留天数，超过后由 purge_soft_deleted 周期任务物理清除
        "RECYCLE_BIN_RETENTION_DAYS": 30,
        # 敏感操作审批：拦截路径正则清单（默认空 = 休眠，渐进启用；仅对显式挂载
        # ApprovalRequired 装饰器的 action 生效），审批通过后携一次性令牌重发放行
        "APPROVAL_REQUIRED_PATHS": [],
        # 审批人角色 code 清单（默认空 = 全部在用超管；申请人不能自审）
        "APPROVAL_APPROVER_ROLES": [],
        # 审批通过后令牌有效期（秒）
        "APPROVAL_TOKEN_TTL": 300,
        # 待审批单超时天数（超时由 auto_expire_approval_job 置 EXPIRED）
        "APPROVAL_PENDING_TIMEOUT": 3,
        # 审批单保留天数（超过由 auto_clean_approval_job 分批删除）
        "APPROVAL_KEEP_DAYS": 180,
        # 待审批超时提醒阈值（小时）：由每日提醒任务对未处理的单补发一次提醒；0 = 不提醒
        "APPROVAL_REMIND_HOURS": 24,
        # PAT 凭证级限流速率（SimpleRateThrottle 速率串；空或 0 = 不限）
        "PAT_RATE_LIMIT": "60/min",
        # 个人文件存储配额（MB；0 = 不限）
        "FILE_STORAGE_QUOTA_MB": 0,
        # 个人上传文件数量上限（0 = 不限）
        "FILE_UPLOAD_COUNT_LIMIT": 0,
        # 正式上传文件保留天数（0 = 不清理）：仅清理非临时、无业务引用的历史文件
        "FILE_KEEP_DAYS": 0,
        # 文本预览读取上限（字节）：超出即截断并提示下载查看
        "FILE_PREVIEW_TEXT_MAX_BYTES": 256 * 1024,
        # 预览缩略图宽度（像素）：列表行内缩略图 / 抽屉大图
        "FILE_PREVIEW_THUMB_WIDTH": 240,
        "FILE_PREVIEW_IMAGE_WIDTH": 1280,
        # 预览缓存保留天数：派生产物，过期删除后按需重建
        "FILE_PREVIEW_CACHE_KEEP_DAYS": 7,
        # 第三方登录 provider 列表（空 = 整体休眠，登录页不显示第三方入口）
        "OAUTH_PROVIDERS": [],
        # 字段级审计 diff 白名单（模型 _meta.label），为空表示关闭；
        # 命中白名单的 update 请求会额外做 2 次查询以计算 old/new，按需开启
        "AUDIT_DIFF_MODELS": [],
        # 验证码配置
        "VERIFY_CODE_TTL": 5 * 60,  # Unit: second
        "VERIFY_CODE_LIMIT": 60,
        "VERIFY_CODE_LENGTH": 6,
        "VERIFY_CODE_LOWER_CASE": False,
        "VERIFY_CODE_UPPER_CASE": False,
        "VERIFY_CODE_DIGIT_CASE": True,
        # 邮件配置
        "EMAIL_ENABLED": False,
        "EMAIL_HOST": "",
        "EMAIL_PORT": 465,
        "EMAIL_HOST_USER": "",
        "EMAIL_HOST_PASSWORD": "",
        "EMAIL_FROM": "",
        "EMAIL_RECIPIENT": "",
        "EMAIL_SUBJECT_PREFIX": "Xadmin-Server ",
        "EMAIL_USE_SSL": True,
        "EMAIL_USE_TLS": False,
        # 短信配置
        "SMS_ENABLED": False,
        "SMS_BACKEND": "alibaba",
        "SMS_TEST_PHONE": "",
        # 阿里云短信配置
        "ALIBABA_ACCESS_KEY_ID": "",
        "ALIBABA_ACCESS_KEY_SECRET": "",
        "ALIBABA_VERIFY_SIGN_NAME": "",
        "ALIBABA_VERIFY_TEMPLATE_CODE": "",
        # 图片验证码
        "CAPTCHA_IMAGE_SIZE": (120, 40),  # 设置 captcha 图片大小
        "CAPTCHA_CHALLENGE_FUNCT": "captcha.helpers.math_challenge",
        "CAPTCHA_LENGTH": 4,  # 字符个数,仅针对随机字符串生效
        "CAPTCHA_TIMEOUT": 5,  # 超时(minutes)
        "CAPTCHA_FONT_SIZE": 26,
        "CAPTCHA_BACKGROUND_COLOR": "#ffffff",
        "CAPTCHA_FOREGROUND_COLOR": "#001100",
        "CAPTCHA_NOISE_FUNCTIONS": ("captcha.helpers.noise_arcs", "captcha.helpers.noise_dots"),
    }

    defaults = {
        "API_LOG_ENABLE": True,
        # 忽略日志记录, 支持model 或者 request_path, 不支持正则
        "API_LOG_IGNORE": {
            "system.OperationLog": ["GET"],
            "/api/common/api/health": ["GET"],
        },
        "API_LOG_METHODS": ["POST", "DELETE", "PUT", "PATCH"],
        "API_MODEL_MAP": {
            "/api/system/refresh": "Token刷新",
            "/api/flower": "定时任务",
        },
    }
    defaults.update(base)
    defaults.update(libs)
    defaults.update(settings)
    old_config_map = {}

    def __init__(self, *args):
        super().__init__(*args)

    def convert_type(self, k, v):
        default_value = self.defaults.get(k)
        if default_value is None:
            return v
        tp = type(default_value)
        # 对bool特殊处理
        if tp is bool and isinstance(v, str):
            if v.lower() in ("true", "1"):
                return True
            else:
                return False
        if tp in [list, dict] and isinstance(v, str):
            try:
                v = json.loads(v)
                return v
            except json.JSONDecodeError:
                return v

        try:
            if tp in [list, dict]:
                v = json.loads(v)
            else:
                v = tp(v)
        except Exception:
            pass
        return v

    def __repr__(self):
        return "<%s %s>" % (self.__class__.__name__, dict.__repr__(self))

    def get_from_config(self, item):
        try:
            value = super().__getitem__(item)
        except KeyError:
            value = None
        return value

    def get_from_env(self, item):
        value = os.environ.get(item, None)
        if value is not None:
            value = self.convert_type(item, value)
        return value

    def get(self, item, default=None):
        # 再从配置文件中获取
        value = self.get_from_config(item)
        if value is None:
            value = self.get_from_env(item)

        # 因为要递归，所以优先从上次返回的递归中获取
        if default is None:
            default = self.defaults.get(item)
        if value is None and item in self.old_config_map:
            return self.get(self.old_config_map[item], default)
        if value is None:
            value = default
        return value

    def __getitem__(self, item):
        return self.get(item)

    def __getattr__(self, item):
        return self.get(item)


class ConfigManager:
    config_class = Config

    def __init__(self, root_path=None):
        self.root_path = root_path
        self.config = self.config_class()

    def from_pyfile(self, filename="config.py", silent=False):

        if self.root_path:
            filename = os.path.join(self.root_path, filename)
        d = types.ModuleType("config")
        d.__file__ = filename
        try:
            with open(filename, mode="rb") as config_file:
                exec(compile(config_file.read(), filename, "exec"), d.__dict__)
        except IOError as e:
            if silent and e.errno in (errno.ENOENT, errno.EISDIR):
                return False
            e.strerror = "Unable to load configuration file (%s)" % e.strerror
            return False
        self.from_object(d)
        return True

    def from_object(self, obj):
        if isinstance(obj, str):
            obj = import_string(obj)
        for key in dir(obj):
            if key.isupper():
                self.config[key] = getattr(obj, key)

    def from_json(self, filename, silent=False):
        if self.root_path:
            filename = os.path.join(self.root_path, filename)
        try:
            with open(filename) as json_file:
                obj = json.loads(json_file.read())
        except IOError as e:
            if silent and e.errno in (errno.ENOENT, errno.EISDIR):
                return False
            e.strerror = "Unable to load configuration file (%s)" % e.strerror
            raise
        return self.from_mapping(obj)

    def from_yaml(self, filename, silent=False):
        if self.root_path:
            filename = os.path.join(self.root_path, filename)
        try:
            with open(filename, "rt", encoding="utf8") as f:
                obj = yaml.safe_load(f)
        except IOError as e:
            if silent and e.errno in (errno.ENOENT, errno.EISDIR):
                return False
            e.strerror = "Unable to load configuration file (%s)" % e.strerror
            raise
        if obj:
            return self.from_mapping(obj)
        return True

    def from_mapping(self, *mapping, **kwargs):
        mappings = []
        if len(mapping) == 1:
            if hasattr(mapping[0], "items"):
                mappings.append(mapping[0].items())
            else:
                mappings.append(mapping[0])
        elif len(mapping) > 1:
            raise TypeError("expected at most 1 positional argument, got %d" % len(mapping))
        mappings.append(kwargs.items())
        for mapping in mappings:
            for key, value in mapping:
                if key.isupper():
                    self.config[key] = value
        return True

    def load_from_object(self):
        sys.path.insert(0, PROJECT_DIR)
        try:
            from config import config as c
        except ImportError:
            return False
        if c:
            self.from_object(c)
            return True
        else:
            return False

    def load_from_yml(self):
        for i in ["config.yml", "config.yaml"]:
            if not os.path.isfile(os.path.join(self.root_path, i)):
                continue
            loaded = self.from_yaml(i)
            if loaded:
                return True
        return False

    @classmethod
    def load_user_config(cls, root_path=None, config_class=None):
        config_class = config_class or Config
        cls.config_class = config_class
        if not root_path:
            root_path = PROJECT_DIR

        manager = cls(root_path=root_path)
        if manager.from_pyfile():
            config = manager.config
        elif manager.load_from_object():
            config = manager.config
        elif manager.load_from_yml():
            config = manager.config
        else:
            msg = """

            Error: No config file found.

            You can run `cp config_example.yml config.yml`, and edit it.
            """
            raise ImportError(msg)

        return config
