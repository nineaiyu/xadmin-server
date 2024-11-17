#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : menu
# author : ly_13
# date : 10/29/2024
from django.contrib.auth.models import Group, Permission
from django.utils.module_loading import import_string
from rest_framework.routers import SimpleRouter
from rest_framework.viewsets import GenericViewSet

from common.core.modelset import NoDetailModelSet
from common.core.routers import NoDetailRouter
from common.core.utils import get_all_url_dict
from common.utils import get_logger

router = SimpleRouter(False)
no_detail_router = NoDetailRouter(False)
logger = get_logger(__file__)


def get_long_str(li):
    result = ''
    for i in zip(*li):
        if len(set(i)) == 1:
            result += i[0]
        else:
            break
    return result


def get_related_models(model):
    related_models = {model._meta.label_lower}
    for field in model._meta._get_fields(reverse=False):
        if field.is_relation and field.related_model and not issubclass(field.related_model, (Group, Permission)) \
                and field.name not in ['creator', 'modifier', 'dept_belong']:
            related_models.add(field.related_model._meta.label_lower)
    return related_models


def get_view_permissions(view_string, code_suffix=''):
    permissions = []

    url_paths = [url for url in get_all_url_dict('') if url.get('view') == view_string]
    if not url_paths:
        return permissions
    view_set = import_string(url_paths[0].get('view'))

    names = []
    for url_path in url_paths:
        name = url_path.get('name')
        for s in ['-list', '-detail']:
            if name.endswith(s):
                name = name[:-len(s)]
        names.append(name)

    basename = get_long_str(names).rstrip('-')
    is_view_set = True
    models = []
    try:
        if issubclass(view_set, GenericViewSet):
            if issubclass(view_set, NoDetailModelSet):
                routers = no_detail_router.get_routes(view_set)
            else:
                routers = router.get_routes(view_set)
            route_info = {r.name.format(basename=basename): r.mapping for r in routers}
            try:
                models = get_related_models(view_set.queryset.model)
            except Exception as e:
                logger.error(f'get_related_models {view_set} failed {e}', exc_info=True)
                pass
        else:
            is_view_set = False
            route_mapping = no_detail_router.routes[0].mapping
            route_info = {url_paths[0].get('name'): {item.lower(): route_mapping[item.lower()] for item in
                                                     set(view_set().allowed_methods) - {'OPTIONS'}}}
    except Exception as e:
        logger.warning(f"Exception while getting permissions for {view_string}: {e}")
        return permissions

    view_doc = view_set.__doc__

    for url_path in url_paths:
        methods = route_info.get(url_path.get('name'), {})
        for method, func_name in methods.items():
            if is_view_set and method.lower() == 'put':  # 忽略view set的put请求，使用patch请求
                continue
            try:
                action_doc = getattr(view_set, func_name if is_view_set else method).__doc__
            except Exception as e:
                logger.warning(f"Exception while getting action doc for {view_string}: {e}")
                continue
            try:
                action_doc = action_doc.format(cls=view_doc)
            except Exception:
                action_doc = view_doc

            code = func_name.title().replace('_', '').replace('-', '')
            permissions.append({
                'method': method.upper(),
                'url': url_path.get('url'),
                'code': f"{code[0].lower()}{code[1:]}:{code_suffix}",
                'description': action_doc if action_doc else view_set.__name__,
                'models': models if func_name in ['list', 'create', 'retrieve', 'update', 'partial_update'] else []
            })

    return permissions
