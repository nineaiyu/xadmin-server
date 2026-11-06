"""
ASGI config for server project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/asgi/
"""

import os
import uuid
from typing import Any

import django
from channels.auth import AuthMiddlewareStack
from channels.db import database_sync_to_async
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.conf import settings
from django.core.handlers.asgi import ASGIHandler, ASGIRequest
from django.utils.module_loading import import_string

from common.core.atomic_read import SafeMethodAtomicSkipMixin
from common.core.modules import ModuleTrimWebsocketMiddleware
from common.core.url_inventory import collect_app_ws_urls
from common.utils import get_logger
from server.utils import set_current_request

logger = get_logger(__name__)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.settings")
django.setup(set_prefix=False)  # 等价 get_asgi_application 的 setup（后者返回无混入的默认 handler）


class XadminASGIHandler(SafeMethodAtomicSkipMixin, ASGIHandler):
    """HTTP 入口 handler：纯读请求免 ATOMIC_REQUESTS（见 packages/xadmin-common/common/core/atomic_read.py）。"""


django_asgi_app = XadminASGIHandler()

# 写到上面会导致gunicorn启动失败。WS 路由按约定自动收集（<app>/routing.py），
# 新业务应用无需修改本工程层文件；收集必须在 django.setup() 之后执行
urlpatterns = collect_app_ws_urls()


@database_sync_to_async  # type: ignore[untyped-decorator]  # 第三方装饰器（channels）无类型存根：函数自身标注完整，此处不因装饰器降级
def get_signature_user(scope: dict[str, Any]) -> Any:
    if scope["type"] == "websocket":
        scope["method"] = "GET"

    request = ASGIRequest(scope, None)
    for backend_str in settings.REST_FRAMEWORK.get("DEFAULT_AUTHENTICATION_CLASSES"):
        try:
            backend = import_string(backend_str)
            result = backend().authenticate(request)
            # DRF 语义：认证类可返回 None（未命中该凭证类型，交由下一个后端）
            if result is None:
                continue
            user, auth = result
            if user:
                user.auth = auth
                request.user = user
                request.request_uuid = uuid.uuid4()
                set_current_request(request)
                logger.info("web socket auth success")
                return user
        except Exception as e:
            logger.warning(f"web socket auth failed by {backend_str}. Exception: {e}")
    logger.error("web socket auth failed.")
    return None


class WsSignatureAuthMiddleware:
    """JWT 签名认证中间件（尽力而为，fail-open 到 consumer 自检）。

    认证成功把 user 写入 scope；失败不拒绝连接——WS 路由按 app 自动收集
    （``collect_app_ws_urls``），是否要求认证是各 consumer 的业务决策
    （与 HTTP 侧视图自带 permission_classes 同构）。**契约：每个要求登录的
    consumer 必须在 connect() 里自检 ``is_authenticated`` 并 reject**，
    不得依赖本中间件拦截（message/ws/consumers.py 是参照实现）。
    二开注册匿名可连通道（如公开大屏）也因此不被误伤。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> Any:
        user = await get_signature_user(scope)
        if user:
            scope["user"] = user
        return await self.app(scope, receive, send)


application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            # 模块裁剪的 WS 准入先于认证中间件：停用模块的通道不进入认证与 consumer
            ModuleTrimWebsocketMiddleware(WsSignatureAuthMiddleware(AuthMiddlewareStack(URLRouter(urlpatterns))))
        ),
    }
)
