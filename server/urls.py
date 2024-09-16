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
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from common.celery.flower import CeleryFlowerView
from common.core.utils import auto_register_app_url
from common.utils.media import media_serve

SpectacularAPIView.get = xframe_options_exempt(SpectacularAPIView.get)
SpectacularSwaggerView.get = xframe_options_exempt(SpectacularSwaggerView.get)
SpectacularRedocView.get = xframe_options_exempt(SpectacularRedocView.get)

swagger_apis = [
    re_path('^api-docs/schema/', SpectacularAPIView.as_view(), name='schema'),
    re_path('^api-docs/swagger/$', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    re_path('^api-docs/redoc/$', SpectacularRedocView.as_view(url_name='schema'), name='schema-redoc'),
]

urlpatterns = [
    re_path('^admin/', admin.site.urls),
    re_path('^api/system/', include('system.urls')),
    re_path('^api/settings/', include('settings.urls')),
    re_path('^api/notifications/', include('notifications.urls')),
    re_path('^api/common/', include('common.urls')),
    re_path('^api/flower/(?P<path>.*)$', CeleryFlowerView.as_view(), name='flower-view'),
    # media路径配置 开发环境可以启动下面配置，正式环境需要让nginx读取资源，无需进行转发
    re_path('^media/(?P<path>.*)$', media_serve, {'document_root': settings.MEDIA_ROOT}),
    re_path('^api/static/(?P<path>.*)$', static_serve, {'document_root': settings.STATIC_ROOT})
]

urlpatterns = swagger_apis + urlpatterns
auto_register_app_url(urlpatterns)
