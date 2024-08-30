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
from django.http import QueryDict
from django.urls import URLPattern, URLResolver
from django.utils.module_loading import import_string
from django.utils.termcolors import make_style

from common.base.magic import import_from_string

logger = logging.getLogger(__name__)


def check_show_url(url):
    for prefix in settings.PERMISSION_SHOW_PREFIX:
        if re.match(prefix, url):
            return True


def ignore_white_url(url):
    for prefix in settings.ROUTE_IGNORE_URL:
        if re.match(prefix, f"/{url.replace('$', '')}"):
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
            url = pre_url + item.pattern.regex.pattern.lstrip('^')
            # url = url.replace('^', '').replace('$', '')

            if check_show_url(url) and not ignore_white_url(url):
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
    xadmin_apps = []
    for app in settings.XADMIN_APPS:
        if '.' in app:
            xadmin_apps.append(import_string(app).name)
        else:
            xadmin_apps.append(app)
    # xadmin_apps = [x.split('.')[0] for x in settings.XADMIN_APPS]
    for name, value in apps.app_configs.items():
        if name not in xadmin_apps: continue
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
                settings.PERMISSION_WHITE_URL.update(urls)
        except Exception as e:
            logger.warning(f"auto register {name} permission_white_reurl failed. {e}")


def get_query_post_pks(request):
    if isinstance(request.data, QueryDict):
        pks = request.data.getlist('pks', [])
    else:
        pks = request.data.get('pks', [])
    return pks


class PrintLogFormat(object):
    def __init__(self, base_str=''):
        self.base_str = base_str
        self.bold_error = make_style(opts=('bold',), fg='magenta')
        self._info = make_style(fg='green')
        self._error = make_style(fg='red')
        self._warning = make_style(fg='yellow')
        self._debug = make_style(fg='blue')

    def info(self, msg, *args, **kwargs):
        logger.info(f"{self.base_str} {msg}", *args, **kwargs)
        if logger.isEnabledFor(logging.INFO):
            print('{0: <50}'.format(self.bold_error(self.base_str)), '{0: >60}'.format(self._info(msg)))

    def error(self, msg, *args, **kwargs):
        logger.error(f"{self.base_str} {msg}", *args, **kwargs)
        if logger.isEnabledFor(logging.ERROR):
            print('{0: <50}'.format(self.bold_error(self.base_str)), '{0: >60}'.format(self._error(msg)))

    def debug(self, msg, *args, **kwargs):
        logger.debug(f"{self.base_str} {msg}", *args, **kwargs)
        if logger.isEnabledFor(logging.DEBUG):
            print('{0: <50}'.format(self.bold_error(self.base_str)), '{0: >60}'.format(self._debug(msg)))

    def warning(self, msg, *args, **kwargs):
        logger.warning(f"{self.base_str} {msg}", *args, **kwargs)
        if logger.isEnabledFor(logging.WARNING):
            print('{0: <50}'.format(self.bold_error(self.base_str)), '{0: >60}'.format(self._warning(msg)))
