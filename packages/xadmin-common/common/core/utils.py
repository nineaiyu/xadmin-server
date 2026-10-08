#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : utils
# author : ly_13
# date : 6/2/2023
import datetime
import logging
import re
from collections import OrderedDict, defaultdict, deque
from functools import lru_cache
from importlib import import_module

from django.apps import apps
from django.http import QueryDict
from django.urls import URLPattern, URLResolver
from django.utils.module_loading import import_string
from django.utils.termcolors import make_style

from common.base.magic import import_from_string
from common.decorators import cached_method
from common.settings_contract import kernel_required_setting, kernel_setting

logger = logging.getLogger(__name__)


def get_doc_first_line(doc):
    """取 docstring 首行；多行长说明只保留首行，供操作日志/菜单权限等单行字段使用。"""
    if not doc:
        return ""
    lines = str(doc).strip().splitlines()
    return lines[0].strip() if lines else ""


# 权限点 path 编译缓存：权限点数量级 600+（×5 个 HTTP 方法维度），超过 re 模块
# 自带 512 条编译缓存——逐条回退匹配时会被冲刷导致反复重编译；本缓存让编译在
# 进程内只发生一次，并预计算字面量前缀供匹配时零语义风险短路。
_PERMISSION_LITERAL_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/_-:~")


@lru_cache(maxsize=8192)
def _compile_permission_pattern(pattern: str):
    """编译权限点 path（已去前导 ``/``）→ ``(字面量前缀, 编译后正则)``。

    正则为 None 表示坏正则（该权限点视为不命中，不影响其它权限点）。
    前缀 = 编译模式从开头起连续的字面量字符（到首个正则元字符为止）；
    目标地址不以该前缀开头必然不匹配，可直接短路（宁短勿错）。
    """
    full = f"/{pattern}" if pattern.endswith("$") else f"/{pattern}(/.*)?"
    stop = len(full)
    for index, char in enumerate(full):
        if char not in _PERMISSION_LITERAL_CHARS:
            stop = index
            break
    prefix = full[:stop]
    try:
        return prefix, re.compile(full)
    except re.error:
        return prefix, None


def permission_path_matches(permission_path: str, url: str) -> bool:
    """权限点 path 是否覆盖请求地址（唯一实现，三处消费方共用）。

    权限点 path 与 ``menu.path`` 同格式，两种语义：

    - 带 ``$`` 后缀 = 精确锚定：仅自身整段匹配，不覆盖子路径；
    - 无 ``$`` 后缀 = 段边界前缀：自身与子路径均覆盖（``api/user`` 覆盖
      ``/api/user/1``），且不跨字符粘连（``api/user`` 不命中 ``/api/userfoo``）；
    - 坏正则视为不命中（单个坏权限点不能让该用户所有受控请求 500）。

    历史上运行期判定 / 权限点扫描 / 应用授权各有平行实现，其中一处漏改锚定；
    口径收敛到本函数，消费方只做「精确优先 + 逐条回退」编排，勿再自写正则。

    匹配性能：编译与字面量前缀经进程内缓存（``_compile_permission_pattern``），
    回退遍历（O(权限点数)）中前缀不符的条目在 startswith 处短路，
    且不依赖 re 模块 512 条编译缓存（超限即反复重编译）。
    """
    pattern = str(permission_path or "").lstrip("/")
    target = str(url or "")
    if not target.startswith("/"):
        target = f"/{target}"
    prefix, compiled = _compile_permission_pattern(pattern)
    if compiled is None or not target.startswith(prefix):
        return False
    try:
        return compiled.fullmatch(target) is not None
    except re.error:
        return False


def check_show_url(url):
    for prefix in kernel_setting("PERMISSION_SHOW_PREFIX"):
        if re.match(prefix, url):
            return True


def ignore_white_url(url):
    for prefix in kernel_setting("ROUTE_IGNORE_URL"):
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
                name = f"{pre_namespace}:{item.name}"
            else:
                name = item.name
            url = pre_url + item.pattern.regex.pattern.lstrip("^")
            # url = url.replace('^', '').replace('$', '')

            if check_show_url(url) and not ignore_white_url(url):
                url_ordered_dict[name] = {"name": name, "url": url, "view": item.lookup_str}
                try:
                    view_set = import_string(item.lookup_str)
                    url_ordered_dict[name]["label"] = get_doc_first_line(view_set.__doc__)
                except Exception:
                    # 视图 docstring 解析失败：仅少一行文档说明，不影响路由收录
                    pass

        elif isinstance(item, URLResolver):  # 路由分发，递归操作
            new_pre_url = pre_url + item.pattern.regex.pattern.lstrip("^")
            if not check_show_url(new_pre_url):
                continue
            if pre_namespace:
                if item.namespace:
                    namespace = f"{pre_namespace}:{item.namespace}"
                else:
                    namespace = item.namespace
            else:
                if item.namespace:
                    namespace = item.namespace
                else:
                    namespace = None
            recursion_urls(namespace, new_pre_url, item.url_patterns, url_ordered_dict)


@cached_method(ttl=-1)
def get_all_url_dict(pre_url="/"):
    """
    获取项目中所有的URL（必须有name别名）
    """
    url_ordered_dict = OrderedDict()
    md = import_string(kernel_required_setting("ROOT_URLCONF"))
    url_ordered_dict["#"] = {"name": "#", "url": "#", "view": "#", "label": "#"}
    recursion_urls(None, pre_url, md.urlpatterns, url_ordered_dict)  # 递归去获取所有的路由
    return url_ordered_dict.values()


def collect_app_ws_urls():
    """按 INSTALLED_APPS 收集各应用的 WebSocket 路由（约定：<app>/routing.py 的 urlpatterns）。

    与 HTTP 侧 auto_register_app_url 同思路：新业务 app 自带 routing.py 即自动接入
    server/asgi.py，无需修改工程层文件；无 routing 模块的应用（含三方库）静默跳过。
    必须在 django.setup() 完成后调用（asgi.py 中置于 get_asgi_application() 之后）。
    """
    collected = []
    for app_config in apps.get_app_configs():
        try:
            routing = import_module(f"{app_config.name}.routing")
        except ModuleNotFoundError as e:
            # 仅吞掉「无 routing.py」这一种情况；routing.py 内部 import 失败必须暴露
            if e.name not in (f"{app_config.name}.routing", app_config.name):
                raise
            continue
        urlpatterns = getattr(routing, "urlpatterns", None)
        if urlpatterns:
            collected.extend(urlpatterns)
            logger.info(f"auto register {app_config.name} websocket url success")
    return collected


def auto_register_app_url(urlpatterns):
    xadmin_apps = []
    for app in kernel_setting("XADMIN_APPS"):
        if "." in app:
            xadmin_apps.append(import_string(app).name)
        else:
            xadmin_apps.append(app)
    # xadmin_apps = [x.split('.')[0] for x in settings.XADMIN_APPS]
    for name, value in apps.app_configs.items():
        if name not in xadmin_apps:
            continue

        # 使用 value.name 替代 name，以正确处理 apps.xxxx.apps.XxxxConfig 这样的嵌套结构
        app_module_name = value.name

        try:
            urls = import_from_string(f"{app_module_name}.config.URLPATTERNS")
        except ImportError:
            # 允许「先注册 XADMIN_APPS、后由 generate_crud 生成 config.py」的顺序
            # （注册后模型才在 INSTALLED_APPS 内，生成器才找得到模型——硬失败会把
            # 新 app 的引导流程锁死）。缺 config.py / URLPATTERNS 时路由不注入，
            # 用可操作的告警代替 ModuleNotFoundError 崩溃。
            logger.warning(
                f"应用 {name} 已注册进 XADMIN_APPS，但缺少 {app_module_name}/config.py 的 URLPATTERNS，"
                f"该应用路由未注入（生成方式：python manage.py generate_crud <app>.<Model>；改后需重启进程）"
            )
            continue
        logger.info(f"auto register {name} url success")
        if urls:
            urlpatterns.extend(urls)
            for url in urls:
                kernel_setting("PERMISSION_SHOW_PREFIX").append(url.pattern.regex.pattern.lstrip("^"))
            kernel_setting("PERMISSION_DATA_AUTH_APPS").append(name)

        try:
            urls = import_from_string(f"{app_module_name}.config.PERMISSION_WHITE_REURL")
            if urls:
                kernel_setting("PERMISSION_WHITE_URL").update(urls)
        except Exception as e:
            logger.warning(f"auto register {name} permission_white_reurl failed. {e}")


def get_query_post_pks(request):
    if isinstance(request.data, QueryDict):
        pks = request.data.getlist("pks", [])
    else:
        pks = request.data.get("pks", [])
    return pks


class PrintLogFormat:
    def __init__(self, base_str="", title_width=80, body_width=60, logger_enable=False):
        self.base_str = base_str
        self.logger_enable = logger_enable
        self.title_width = title_width
        self.body_width = body_width
        self.bold_error = make_style(opts=("bold",), fg="magenta")
        self._info = make_style(fg="green")
        self._error = make_style(fg="red")
        self._warning = make_style(fg="yellow")
        self._debug = make_style(fg="blue")

    def __print(self, title, body):
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"{now} {title}"
            if self.title_width < 1
            else "{0: <{title_width}}".format(f"{now} {title}", title_width=self.title_width),
            body if self.body_width < 1 else "{0: >{body_width}}".format(body, body_width=self.body_width),
        )

    def info(self, msg, *args, **kwargs):
        if self.logger_enable:
            logger.info(f"{self.base_str} {msg}", *args, **kwargs)
        if logger.isEnabledFor(logging.INFO):
            self.__print(self.bold_error(self.base_str), self._info(msg))

    def error(self, msg, *args, **kwargs):
        if self.logger_enable:
            logger.error(f"{self.base_str} {msg}", *args, **kwargs)
        if logger.isEnabledFor(logging.ERROR):
            self.__print(self.bold_error(self.base_str), self._error(msg))

    def debug(self, msg, *args, **kwargs):
        if self.logger_enable:
            logger.debug(f"{self.base_str} {msg}", *args, **kwargs)
        if logger.isEnabledFor(logging.DEBUG):
            self.__print(self.bold_error(self.base_str), self._debug(msg))

    def warning(self, msg, *args, **kwargs):
        if self.logger_enable:
            logger.warning(f"{self.base_str} {msg}", *args, **kwargs)
        if logger.isEnabledFor(logging.WARNING):
            self.__print(self.bold_error(self.base_str), self._warning(msg))


def topological_sort(data, pk="pk", parent="parent"):
    # 构建图和入度表
    graph = defaultdict(list)
    in_degree = {item[pk]: 0 for item in data}
    nodes = set()
    new_data = {}
    for item in data:
        node_id = item[pk]
        new_data[node_id] = item
        parent_id = item[parent]
        if isinstance(parent_id, dict):
            parent_id = item[parent].get(pk)
        nodes.add(node_id)
        if parent_id is not None:
            graph[parent_id].append(node_id)
            if parent_id in in_degree:
                in_degree[node_id] += 1

    # 找到所有入度为0的节点
    queue = deque([node for node in nodes if in_degree[node] == 0])
    sorted_order = []

    while queue:
        current = queue.popleft()
        sorted_order.append(current)
        for neighbor in graph[current]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # 如果排序后的列表长度不等于原始列表长度，说明存在环
    if len(sorted_order) != len(nodes):
        raise ValueError("Circular dependencies exist")

    return [new_data[node_id] for node_id in sorted_order]


def has_self_fields(model, keys):
    """
    仅仅支持判断 ForeignKey 自关联，不支持多对对自关联判断
    """
    for field in model._meta.fields:
        if (
            field.is_relation
            and field.related_model is not None
            and field.related_model == model
            and field.name in keys
        ):
            return field.name
