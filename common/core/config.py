#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : config
# author : ly_13
# date : 12/15/2023


import json
import logging
import re

from django.template import Context, Template, TemplateSyntaxError
from django.template.base import VariableNode
from rest_framework import serializers

from common.cache.storage import UserSystemConfigCache
from server import settings
from system.models import SystemConfig, UserPersonalConfig

logger = logging.getLogger(__name__)


class SystemConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = SystemConfig
        fields = "__all__"


def get_render_context(tmp: str, context: dict) -> str:
    template = Template(tmp)
    for node in template.nodelist:
        if isinstance(node, VariableNode):
            v_key = re.findall(r'<Variable Node: (.*)>', str(node))
            if v_key and v_key[0].isupper():
                context[v_key[0]] = getattr(SysConfig, v_key[0])
    context = Context(context)
    return template.render(context)


class ConfigCacheBase(object):
    def __init__(self, px='system', model=SystemConfig, cache=UserSystemConfigCache, serializer=SystemConfigSerializer,
                 timeout=60 * 60 * 24 * 30):
        self.px = px
        self.model = model
        self.cache = cache
        self.timeout = timeout
        self.serializer = serializer

    def invalid_config_cache(self, key='*'):
        UserSystemConfigCache(f'{self.px}_{key}').del_many()

    def get_render_value(self, value):
        if value:
            try:
                context_dict = {}
                for sys_obj_dict in self.model.objects.filter(is_active=True).values().all():
                    if re.findall('{{.*%s.*}}' % sys_obj_dict['key'], sys_obj_dict['value']):
                        logger.warning(f"get same render key. so continue")
                        continue
                    context_dict[sys_obj_dict['key']] = sys_obj_dict['value']
                try:
                    value = get_render_context(value, context_dict)
                except TemplateSyntaxError as e:
                    res_list = re.findall("Could not parse the remainder: '{{(.*?)}}'", str(e))
                    for res in res_list:
                        r_value = self.get_render_value(f'{{{{{res}}}}}')
                        value = value.replace(f'{{{{{res}}}}}', f'{r_value}')
                    value = self.get_render_value(value)
                except Exception as e:
                    logger.warning(f"db config - render failed {e}")
            except Exception as e:
                logger.warning(f"db config - render failed {e}")
        try:
            value = json.loads(value)
        except Exception as e:
            logger.warning(f"db config - json loads failed {e}")
        if isinstance(value, str):
            if value.isdigit():
                return int(value)
            v_group = re.findall('"(.*?)"', value)
            if v_group and len(v_group) == 1 and v_group[0].isdigit():
                return int(v_group[0])
        return value

    def get_value_from_db(self, key):
        data = self.serializer(self.model.objects.filter(is_active=True, key=key).first()).data
        if re.findall('{{.*%s.*}}' % data['key'], data['value']):
            logger.warning(f"get same render key:{key}. so get default value")
            data['key'] = ''
        return data

    def get_default_data(self, key, default_data):
        if default_data is None:
            default_data = {}
        return default_data

    def get_value(self, key, data=None):
        data = self.get_default_data(key, data)
        cache = self.cache(f'{self.px}_{key}')
        cache_data = cache.get_storage_cache()
        if cache_data is not None and cache_data.get('key', '') == key:
            return cache_data.get('value')
        db_data = self.get_value_from_db(key)
        d_key = db_data.get('key', '')
        if d_key != key and data is not None:
            db_data['value'] = json.dumps(data)
            db_data['key'] = key
        db_data['value'] = self.get_render_value(db_data['value'])
        cache.set_storage_cache(db_data, timeout=self.timeout)
        return db_data.get('value')

    def save_db(self, key, value, is_active, description, **kwargs):
        defaults = {'value': value}
        if is_active is not None:
            defaults['is_active'] = is_active
        if description is not None:
            defaults['description'] = description
        self.model.objects.update_or_create(key=key, defaults=defaults, **kwargs)

    def delete_db(self, key, **kwargs):
        self.model.objects.filter(key=key, **kwargs).delete()

    def set_value(self, key, value, is_active=None, description=None, **kwargs):
        if not isinstance(value, str):
            value = json.dumps(value)
        self.save_db(key, value, is_active, description, **kwargs)
        self.cache(f'{self.px}_{key}').del_storage_cache()

    def set_default_value(self, key, **kwargs):
        self.set_value(key, self.get_value(key, None), **kwargs)

    def del_value(self, key, **kwargs):
        self.delete_db(key, **kwargs)
        self.cache(f'{self.px}_{key}').del_storage_cache()

    def __getattribute__(self, name):
        try:
            return object.__getattribute__(self, name)
        except Exception as e:
            logger.error(f"__getattribute__ Error  {e}  {name}")
            return self.get_value(name)


class BaseConfCache(ConfigCacheBase):
    def __init__(self, *args, **kwargs):
        super(BaseConfCache, self).__init__(*args, **kwargs)

    @property
    def FILE_UPLOAD_SIZE(self):
        return self.get_value('FILE_UPLOAD_SIZE', settings.FILE_UPLOAD_SIZE)

    @property
    def PICTURE_UPLOAD_SIZE(self):
        return self.get_value('PICTURE_UPLOAD_SIZE', settings.PICTURE_UPLOAD_SIZE)


class ConfigCache(BaseConfCache):
    def __init__(self, *args, **kwargs):
        super(ConfigCache, self).__init__(*args, **kwargs)


SysConfig = ConfigCache()


class UserConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPersonalConfig
        fields = "__all__"


class UserPersonalConfigCache(ConfigCache):
    def __init__(self, user_obj):
        self.user_obj = user_obj
        super().__init__(f'user_{user_obj.pk}', UserPersonalConfig, UserSystemConfigCache, UserConfigSerializer)

    def get_default_data(self, key, default_data):
        n_data = getattr(SysConfig, key)
        if n_data is None:
            n_data = {}
        return n_data

    def delete_db(self, key, **kwargs):
        super(UserPersonalConfigCache, self).delete_db(key, owner=self.user_obj)

    def save_db(self, key, value, is_active=None, description=None, **kwargs):
        super(UserPersonalConfigCache, self).save_db(key, value, is_active, description, owner=self.user_obj)

    def set_default_value(self, key, **kwargs):
        super(UserPersonalConfigCache, self).set_default_value(key, owner=self.user_obj)


UserConfig = UserPersonalConfigCache
