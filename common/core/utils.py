#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : utils
# author : ly_13
# date : 6/2/2023
import logging
import re
from collections import OrderedDict

from django.apps import apps
from django.conf import settings
from django.urls import URLPattern, URLResolver
from django.utils.module_loading import import_string

from common.base.magic import import_from_string

logger = logging.getLogger(__name__)


def check_show_url(url):
    for prefix in settings.PERMISSION_SHOW_PREFIX:
        if re.match(prefix, url):
            return True


def recursion_urls(pre_namespace, pre_url, urlpatterns, url_ordered_dict):
    """递归去获取URL
    :param pre_namespace: namespace前缀，以后用户拼接name
    :param pre_url: url前缀，以后用于拼接url
    :param urlpatterns: 路由关系列表
    :param url_ordered_dict: 用于保存递归中获取的所有路由
    """
    for item in urlpatterns:
        if isinstance(item, URLPattern):
            if not item.name:
                continue

            if pre_namespace:
                name = "%s:%s" % (pre_namespace, item.name)
            else:
                name = item.name
            if not item.name:
                raise Exception('URL路由中必须设置name属性')
            url = pre_url + item.pattern.regex.pattern.lstrip('^')
            # url = url.replace('^', '').replace('$', '')

            if check_show_url(url):
                url_ordered_dict[name] = {'name': name, 'url': url}

        elif isinstance(item, URLResolver):  # 路由分发，递归操作
            if pre_namespace:
                if item.namespace:
                    namespace = "%s:%s" % (pre_namespace, item.namespace)
                else:
                    namespace = item.namespace
            else:
                if item.namespace:
                    namespace = item.namespace
                else:
                    namespace = None
            recursion_urls(namespace, pre_url + item.pattern.regex.pattern.lstrip('^'), item.url_patterns,
                           url_ordered_dict)


def get_all_url_dict(pre_url='/'):
    """
       获取项目中所有的URL（必须有name别名）
    """
    url_ordered_dict = OrderedDict()
    md = import_string(settings.ROOT_URLCONF)
    url_ordered_dict['#'] = {'name': '#', 'url': '#'}
    recursion_urls(None, pre_url, md.urlpatterns, url_ordered_dict)  # 递归去获取所有的路由
    return url_ordered_dict.values()


def auto_register_app_url(urlpatterns):
    for name, value in apps.app_configs.items():
        if name in settings.CONFIG_IGNORE_APPS: continue
        # try:
        urls = import_from_string(f"{name}.config.URLPATTERNS")
        logger.info(f"auto register {name} url success")
        if urls:
            urlpatterns.extend(urls)
            for url in urls:
                settings.PERMISSION_SHOW_PREFIX.append(url.pattern.regex.pattern.lstrip('^'))
            settings.PERMISSION_DATA_AUTH_APPS.append(name)
        # except Exception as e:
        #     logger.warning(f"auto register {name} url failed. {e}")
        #     continue

        try:
            urls = import_from_string(f"{name}.config.PERMISSION_WHITE_REURL")
            if urls:
                settings.PERMISSION_WHITE_URL.extend(urls)
        except Exception as e:
            logger.warning(f"auto register {name} permission_white_reurl failed. {e}")
