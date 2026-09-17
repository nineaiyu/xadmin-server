#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""配置管理器：从 py/json/yaml/对象装配配置并加载用户配置。"""

import errno
import json
import logging
import os
import sys
import types

import yaml

logger = logging.getLogger("xadmin.conf")


from .config import PROJECT_DIR, Config, import_string


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
