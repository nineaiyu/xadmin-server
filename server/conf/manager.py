#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""配置管理器：从 py/json/yaml/对象装配配置并加载用户配置。"""

import errno
import json
import logging
import os
import secrets
import string
import sys
import types

import yaml

logger = logging.getLogger("xadmin.conf")


from .config import PROJECT_DIR, Config, import_string

# 自动生成的 SECRET_KEY 持久化位置（相对项目根；data/ 已在 .gitignore 中）。
# 持久化是为了重启与多进程一致：SECRET_KEY 同时是 JWT 签名与字段级加密（signer）的密钥，
# 丢失会导致登录态失效与已加密数据无法解密。
AUTO_SECRET_KEY_FILE = os.path.join("data", ".secret_key")
AUTO_SECRET_KEY_LENGTH = 49
SECRET_KEY_AUTO_GENERATE = "SECRET_KEY_AUTO_GENERATE"
# 内部标记：本次配置来自 config_example.yml 回落（不参与用户配置）
FALLBACK_FLAG = "_XADMIN_EXAMPLE_FALLBACK"


def _random_secret_key() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(AUTO_SECRET_KEY_LENGTH))


def _load_or_create_auto_secret_key(root_path: str):
    """读取或创建自动生成的 SECRET_KEY；返回 ``(value, created)``。

    并发启动（多 worker 同时加载 settings）时用 O_EXCL 抢占，失效方读回既有值，
    保证全进程使用同一密钥。
    """
    secret_path = os.path.join(root_path, AUTO_SECRET_KEY_FILE)
    try:
        with open(secret_path, encoding="utf8") as f:
            value = f.read().strip()
        if value:
            return value, False
    except OSError:
        pass

    value = _random_secret_key()
    try:
        os.makedirs(os.path.dirname(secret_path), exist_ok=True)
        fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            with open(secret_path, encoding="utf8") as f:
                exist = f.read().strip()
            if exist:
                return exist, False
        except OSError:
            pass
        return value, False
    except OSError as e:
        logger.warning("[xadmin] SECRET_KEY 自动生成后无法持久化（%s），本次仅存于内存。", e)
        return value, True
    with os.fdopen(fd, "w", encoding="utf8") as f:
        f.write(value)
    return value, True


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
        except OSError as e:
            if silent and e.errno in (errno.ENOENT, errno.EISDIR):
                return False
            e.strerror = f"Unable to load configuration file ({e.strerror})"
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
        except OSError as e:
            if silent and e.errno in (errno.ENOENT, errno.EISDIR):
                return False
            e.strerror = f"Unable to load configuration file ({e.strerror})"
            raise
        return self.from_mapping(obj)

    def from_yaml(self, filename, silent=False):
        if self.root_path:
            filename = os.path.join(self.root_path, filename)
        try:
            with open(filename, encoding="utf8") as f:
                obj = yaml.safe_load(f)
        except OSError as e:
            if silent and e.errno in (errno.ENOENT, errno.EISDIR):
                return False
            e.strerror = f"Unable to load configuration file ({e.strerror})"
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
            raise TypeError(f"expected at most 1 positional argument, got {len(mapping)}")
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

    @staticmethod
    def _fallback_to_example(manager, root_path):
        """未找到用户配置时回落 config_example.yml（开箱即用）；回落失败返回 None。"""
        if not os.path.isfile(os.path.join(root_path, "config_example.yml")):
            return None
        if not manager.from_yaml("config_example.yml"):
            return None
        manager.config[FALLBACK_FLAG] = True
        logger.warning(
            "\n[xadmin] 未找到 config.yml / config.py，已自动使用 config_example.yml 的内置默认配置启动。\n"
            "[xadmin] 默认值面向开箱体验（数据库/Redis 主机名为容器服务名 postgresql/redis，供 docker compose 形态使用）。\n"
            "[xadmin] 需要自定义配置时：cp config_example.yml config.yml 后修改；\n"
            "[xadmin] 所有配置项均可用同名环境变量覆盖（环境变量仅在该键未被配置文件赋值时生效）。\n"
        )
        return manager.config

    @staticmethod
    def _ensure_secret_key(config, root_path):
        """SECRET_KEY 缺失时的开箱即用兜底：自动生成并持久化。

        触发条件（满足其一）：无配置文件回落 config_example.yml / DEBUG=true /
        显式 SECRET_KEY_AUTO_GENERATE=true；其余场景保持现状（由 settings 校验拒绝启动），
        避免生产环境静默使用自动生成密钥。
        """
        if config.get("SECRET_KEY"):
            return
        auto = (
            bool(config.get(FALLBACK_FLAG)) or bool(config.get("DEBUG")) or bool(config.get(SECRET_KEY_AUTO_GENERATE))
        )
        if not auto:
            return
        value, created = _load_or_create_auto_secret_key(root_path)
        config["SECRET_KEY"] = value
        action = "已自动生成并保存到" if created else "已复用"
        logger.warning(
            "\n[xadmin] 未检测到 SECRET_KEY，%s %s（仅用于开发/首次体验）。\n"
            "[xadmin] 生产环境请通过 config.yml 或环境变量 SECRET_KEY 显式配置（多实例部署必须一致）；\n"
            "[xadmin] 使用自动密钥时请持久化 data/ 目录——密钥丢失将导致登录态失效与已加密数据无法解密。\n",
            action,
            os.path.join(root_path, AUTO_SECRET_KEY_FILE),
        )

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
            config = cls._fallback_to_example(manager, root_path)
            if config is None:
                msg = """

               Error: No config file found.

               You can run `cp config_example.yml config.yml`, and edit it.
               """
                raise ImportError(msg)

        cls._ensure_secret_key(config, root_path)
        return config
