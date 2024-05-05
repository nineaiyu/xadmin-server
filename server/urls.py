"""
URL configuration for server project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.contrib import admin
from django.urls import include, re_path
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.static import serve as static_serve
from drf_yasg import openapi
from drf_yasg.views import get_schema_view

from common.celery.flower import CeleryFlowerView
from common.core.utils import auto_register_app_url
from common.utils.media import media_serve
from common.utils.swagger import CustomOpenAPISchemaGenerator, ApiLogin, ApiLogout

schema_view = get_schema_view(
    openapi.Info(
        title="Snippets API",
        default_version='v1',
        description="Django Xadmin Server",
        terms_of_service="https://github.com/nineaiyu/xadmin-server",
        contact=openapi.Contact(email="ly_1301b@163.com"),
    ),
    generator_class=CustomOpenAPISchemaGenerator,
    public=False,
    # #如果无需授权，需要将下面三行取消注释即可
    # public=True,
    # permission_classes=[permissions.AllowAny],
    # authentication_classes=[],
)

schema_view.get = xframe_options_exempt(schema_view.get)

swagger_apis = [
    re_path('^api-docs/swagger/$', schema_view.with_ui('swagger', cache_timeout=60), name='schema-swagger-ui'),
    re_path('^api-docs/redoc/$', schema_view.with_ui('redoc', cache_timeout=60), name='schema-redoc'),
    re_path('^api-docs/login/$', ApiLogin.as_view(), name='api-docs-login'),
    re_path('^api-docs/logout/$', ApiLogout.as_view(), name='api-docs-logout'),
]

urlpatterns = [
    re_path('^admin/', admin.site.urls),
    re_path('^api/system/', include('system.urls')),
    re_path('^api/flower/(?P<path>.*)$', CeleryFlowerView.as_view(), name='flower-view'),
    # media路径配置 开发环境可以启动下面配置，正式环境需要让nginx读取资源，无需进行转发
    re_path('^media/(?P<path>.*)$', media_serve, {'document_root': settings.MEDIA_ROOT}),
    re_path('^api/static/(?P<path>.*)$', static_serve, {'document_root': settings.STATIC_ROOT})
]

urlpatterns = swagger_apis + urlpatterns
auto_register_app_url(urlpatterns)
