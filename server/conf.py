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

logger = logging.getLogger('xadmin.conf')


def import_string(dotted_path):
    try:
        module_path, class_name = dotted_path.rsplit('.', 1)
    except ValueError as err:
        raise ImportError("%s doesn't look like a module path" % dotted_path) from err

    module = import_module(module_path)

    try:
        return getattr(module, class_name)
    except AttributeError as err:
        raise ImportError(
            'Module "%s" does not define a "%s" attribute/class' %
            (module_path, class_name)) from err


class DoesNotExist(Exception):
    pass


class Config(dict):
    base = {
        'SECRET_KEY': '',
        'DEBUG': False,
        'DEBUG_DEV': False,
        'LOG_LEVEL': "WARNING",
        'XADMIN_APPS': [],
        # 表前缀 abc_
        'DB_PREFIX': '',
        # redis
        'REDIS_HOST': 'redis',
        'REDIS_PORT': 6379,
        'REDIS_PASSWORD': '',
        'DEFAULT_CACHE_ID': 1,
        'CHANNEL_LAYERS_CACHE_ID': 2,
        'CELERY_BROKER_CACHE_ID': 3,
        # database
        'DB_ENGINE': 'mysql',
        'DB_HOST': 'mariadb',
        'DB_PORT': 3306,
        'DB_DATABASE': 'xadmin',
        'DB_USER': 'server',
        'DB_PASSWORD': '',
        'LANGUAGE_CODE': 'zh-hans',
        'TIME_ZONE': 'Asia/Shanghai',
        # 服务配置
        'HTTP_BIND_HOST': '0.0.0.0',
        'HTTP_LISTEN_PORT': 8896,
        'GUNICORN_MAX_WORKER': 4,
        # celery flower 任务监控配置
        'CELERY_FLOWER_PORT': 5566,
        'CELERY_FLOWER_HOST': '127.0.0.1',
        'CELERY_FLOWER_AUTH': 'flower:flower123.',
    }
    libs = {
        # REST_FRAMEWORK
        'DEFAULT_THROTTLE_RATES': {},
        # SIMPLE_JWT
        'ACCESS_TOKEN_LIFETIME': 3600,  # Unit: second
        'REFRESH_TOKEN_LIFETIME': 15 * 24 * 3600,  # Unit: second
    }
    settings = {
        # 密码安全配置
        'SECURITY_PASSWORD_MIN_LENGTH': 6,
        'SECURITY_ADMIN_USER_PASSWORD_MIN_LENGTH': 6,
        'SECURITY_PASSWORD_UPPER_CASE': False,
        'SECURITY_PASSWORD_LOWER_CASE': False,
        'SECURITY_PASSWORD_NUMBER': False,
        'SECURITY_PASSWORD_SPECIAL_CHAR': False,
        # 用户登录限制的规则
        'SECURITY_LOGIN_LIMIT_COUNT': 7,
        'SECURITY_LOGIN_LIMIT_TIME': 30,  # Unit: minute
        'SECURITY_CHECK_DIFFERENT_CITY_LOGIN': True,
        # 登录IP限制的规则
        'SECURITY_LOGIN_IP_BLACK_LIST': [],
        'SECURITY_LOGIN_IP_WHITE_LIST': [],
        'SECURITY_LOGIN_IP_LIMIT_COUNT': 50,
        'SECURITY_LOGIN_IP_LIMIT_TIME': 30,  # Unit: minute
        # 登陆规则
        'SECURITY_LOGIN_ACCESS_ENABLED': True,
        'SECURITY_LOGIN_CAPTCHA_ENABLED': True,
        'SECURITY_LOGIN_ENCRYPTED_ENABLED': True,
        'SECURITY_LOGIN_TEMP_TOKEN_ENABLED': True,
        'SECURITY_LOGIN_BY_EMAIL_ENABLED': True,
        'SECURITY_LOGIN_BY_SMS_ENABLED': False,
        'SECURITY_LOGIN_BY_BASIC_ENABLED': True,
        # 注册规则
        'SECURITY_REGISTER_ACCESS_ENABLED': True,
        'SECURITY_REGISTER_CAPTCHA_ENABLED': True,
        'SECURITY_REGISTER_ENCRYPTED_ENABLED': True,
        'SECURITY_REGISTER_TEMP_TOKEN_ENABLED': True,
        'SECURITY_REGISTER_BY_EMAIL_ENABLED': True,
        'SECURITY_REGISTER_BY_SMS_ENABLED': False,
        'SECURITY_REGISTER_BY_BASIC_ENABLED': True,
        # 忘记密码规则
        'SECURITY_RESET_PASSWORD_ACCESS_ENABLED': True,
        'SECURITY_RESET_PASSWORD_CAPTCHA_ENABLED': True,
        'SECURITY_RESET_PASSWORD_TEMP_TOKEN_ENABLED': True,
        'SECURITY_RESET_PASSWORD_ENCRYPTED_ENABLED': True,
        'SECURITY_RESET_PASSWORD_BY_EMAIL_ENABLED': True,
        'SECURITY_RESET_PASSWORD_BY_SMS_ENABLED': False,
        # 绑定邮箱
        'SECURITY_BIND_EMAIL_ACCESS_ENABLED': True,
        'SECURITY_BIND_EMAIL_CAPTCHA_ENABLED': True,
        'SECURITY_BIND_EMAIL_TEMP_TOKEN_ENABLED': True,
        'SECURITY_BIND_EMAIL_ENCRYPTED_ENABLED': True,
        # 绑定手机
        'SECURITY_BIND_PHONE_ACCESS_ENABLED': True,
        'SECURITY_BIND_PHONE_CAPTCHA_ENABLED': True,
        'SECURITY_BIND_PHONE_TEMP_TOKEN_ENABLED': True,
        'SECURITY_BIND_PHONE_ENCRYPTED_ENABLED': True,
        # 基本配置
        'SITE_URL': 'http://127.0.0.1:8000',
        'FRONT_END_WEB_WATERMARK_ENABLED': False,  # 前端水印展示
        'PERMISSION_FIELD_ENABLED': True,  # 字段权限控制
        'PERMISSION_DATA_ENABLED': True,  # 数据权限控制
        'REFERER_CHECK_ENABLED': False,  # referer 校验
        'EXPORT_MAX_LIMIT': 20000,  # 限制导出数据数量
        # 验证码配置
        'VERIFY_CODE_TTL': 5 * 60,  # Unit: second
        'VERIFY_CODE_LIMIT': 60,
        'VERIFY_CODE_LENGTH': 6,
        'VERIFY_CODE_LOWER_CASE': False,
        'VERIFY_CODE_UPPER_CASE': False,
        'VERIFY_CODE_DIGIT_CASE': True,
        # 邮件配置
        'EMAIL_ENABLED': False,
        'EMAIL_HOST': "",
        'EMAIL_PORT': 465,
        'EMAIL_HOST_USER': "",
        'EMAIL_HOST_PASSWORD': "",
        'EMAIL_FROM': "",
        'EMAIL_RECIPIENT': "",
        'EMAIL_SUBJECT_PREFIX': "Xadmin-Server ",
        'EMAIL_USE_SSL': True,
        'EMAIL_USE_TLS': False,
        # 短信配置
        'SMS_ENABLED': False,
        'SMS_BACKEND': 'alibaba',
        'SMS_TEST_PHONE': '',
        # 阿里云短信配置
        'ALIBABA_ACCESS_KEY_ID': '',
        'ALIBABA_ACCESS_KEY_SECRET': '',
        'ALIBABA_VERIFY_SIGN_NAME': '',
        'ALIBABA_VERIFY_TEMPLATE_CODE': '',
        # 图片验证码
        'CAPTCHA_IMAGE_SIZE': (120, 40),  # 设置 captcha 图片大小
        'CAPTCHA_CHALLENGE_FUNCT': 'captcha.helpers.math_challenge',
        'CAPTCHA_LENGTH': 4,  # 字符个数,仅针对随机字符串生效
        'CAPTCHA_TIMEOUT': 5,  # 超时(minutes)
        'CAPTCHA_FONT_SIZE': 26,
        'CAPTCHA_BACKGROUND_COLOR': "#ffffff",
        'CAPTCHA_FOREGROUND_COLOR': "#001100",
        'CAPTCHA_NOISE_FUNCTIONS': ("captcha.helpers.noise_arcs", "captcha.helpers.noise_dots"),
    }

    defaults = {
        'API_LOG_ENABLE': True,
        # 忽略日志记录, 支持model 或者 request_path, 不支持正则
        'API_LOG_IGNORE': {
            'system.OperationLog': ['GET'],
            '/api/common/api/health': ['GET'],
        },
        'API_LOG_METHODS': ["POST", "DELETE", "PUT", "PATCH"],
        'API_MODEL_MAP': {
            "/api/system/refresh": "Token刷新",
            "/api/flower": "定时任务",
        }
    }
    defaults.update(base)
    defaults.update(libs)
    defaults.update(settings)
    old_config_map = {

    }

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
        return '<%s %s>' % (self.__class__.__name__, dict.__repr__(self))

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

    def from_pyfile(self, filename='config.py', silent=False):

        if self.root_path:
            filename = os.path.join(self.root_path, filename)
        d = types.ModuleType('config')
        d.__file__ = filename
        try:
            with open(filename, mode='rb') as config_file:
                exec(compile(config_file.read(), filename, 'exec'), d.__dict__)
        except IOError as e:
            if silent and e.errno in (errno.ENOENT, errno.EISDIR):
                return False
            e.strerror = 'Unable to load configuration file (%s)' % e.strerror
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
            e.strerror = 'Unable to load configuration file (%s)' % e.strerror
            raise
        return self.from_mapping(obj)

    def from_yaml(self, filename, silent=False):
        if self.root_path:
            filename = os.path.join(self.root_path, filename)
        try:
            with open(filename, 'rt', encoding='utf8') as f:
                obj = yaml.safe_load(f)
        except IOError as e:
            if silent and e.errno in (errno.ENOENT, errno.EISDIR):
                return False
            e.strerror = 'Unable to load configuration file (%s)' % e.strerror
            raise
        if obj:
            return self.from_mapping(obj)
        return True

    def from_mapping(self, *mapping, **kwargs):
        mappings = []
        if len(mapping) == 1:
            if hasattr(mapping[0], 'items'):
                mappings.append(mapping[0].items())
            else:
                mappings.append(mapping[0])
        elif len(mapping) > 1:
            raise TypeError(
                'expected at most 1 positional argument, got %d' % len(mapping)
            )
        mappings.append(kwargs.items())
        for mapping in mappings:
            for (key, value) in mapping:
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
        for i in ['config.yml', 'config.yaml']:
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
