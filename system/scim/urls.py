# -*- coding: utf-8 -*-
"""SCIM 2.0 路由（挂载于 /api/scim/v2/）。

路径遵循 RFC 7644：资源集合为复数名词，无尾随斜杠（与项目 router 口径一致）。
"""

from django.urls import path

from system.scim.views import (
    GroupDetailView,
    GroupsView,
    ResourceTypesView,
    SchemasView,
    ServiceProviderConfigView,
    UserDetailView,
    UsersView,
)

app_name = "scim"

urlpatterns = [
    path("ServiceProviderConfig", ServiceProviderConfigView.as_view(), name="service-provider-config"),
    path("Schemas", SchemasView.as_view(), name="schemas"),
    path("ResourceTypes", ResourceTypesView.as_view(), name="resource-types"),
    path("Users", UsersView.as_view(), name="users"),
    path("Users/<str:pk>", UserDetailView.as_view(), name="user-detail"),
    path("Groups", GroupsView.as_view(), name="groups"),
    path("Groups/<str:pk>", GroupDetailView.as_view(), name="group-detail"),
]
