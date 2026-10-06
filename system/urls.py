#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : urls
# author : ly_13
# date : 6/6/2023
from django.urls import URLPattern, include, path, re_path
from rest_framework.routers import SimpleRouter

from common.core.routers import NoDetailRouter
from system.views.admin.codegen import SystemCodeGenViewSet
from system.views.admin.config import SystemConfigViewSet, UserPersonalConfigViewSet
from system.views.admin.credential import CredentialViewSet
from system.views.admin.dict import DataDictViewSet
from system.views.admin.menu import MenuViewSet
from system.views.admin.modelfield import ModelLabelFieldViewSet
from system.views.admin.permission import DataPermissionViewSet
from system.views.admin.saved_view import SavedListViewSet
from system.views.platform.dashboard import DashboardViewSet
from system.views.platform.modules import SystemModuleViewSet
from system.views.platform.monitor import MonitorViewSet
from system.views.platform.tag import TagViewSet
from system.views.search.global_search import GlobalSearchAPIView
from system.views.search.menu import SearchMenuViewSet
from system.views.user.configs import ConfigsViewSet
from system.views.user.routes import UserRoutesAPIView

app_name = "system"

router = SimpleRouter(False)
no_detail_router = NoDetailRouter(False)

no_auth_url = [
    re_path("^captcha/", include("captcha.urls")),
]

auth_url: list[URLPattern] = []

router_url = [
    re_path("^routes$", UserRoutesAPIView.as_view(), name="user_routes"),
]

# 系统设置相关路由
router.register("menu", MenuViewSet, basename="menu")
router.register("permission", DataPermissionViewSet, basename="permission")
router.register("field", ModelLabelFieldViewSet, basename="model_label_field")
router.register("dict", DataDictViewSet, basename="data_dict")
# 列表「我的视图」
router.register("saved-views", SavedListViewSet, basename="saved_view")
# 代码生成器 GUI（只读引擎适配：模型清单/字段计划/预览/下载）
router.register("codegen", SystemCodeGenViewSet, basename="system-codegen")

# 配置相关
router.register("config/system", SystemConfigViewSet, basename="sysconfig")
# 凭据与密钥：只读聚合 + 重加密轮换
router.register("credentials", CredentialViewSet, basename="credential")
# 功能模块清单（只读）：模块等级/依赖/启停状态与裁剪配置片段
router.register("modules", SystemModuleViewSet, basename="module")
# AI 助手：配置（Setting 体系）与问答
# AI 知识库文档管理：上传/预览/启停/删除 + 仓库文档重建
# AI 配置档案：多套凭据/采样参数，激活唯一（无激活档案回落 Setting 通路）
router.register("config/user", UserPersonalConfigViewSet, basename="userconfig")

# 面板信息
router.register("dashboard", DashboardViewSet, basename="dashboard")
router.register("monitor", MonitorViewSet, basename="monitor")

# 仅数据搜索
router.register("search/menu", SearchMenuViewSet, basename="SearchMenu")

# 个人配置（ConfigsViewSet）
router.register("configs", ConfigsViewSet, basename="configs")
# 通用标签中心：标签 CRUD + 打标 / 批量打标
router.register("tags", TagViewSet, basename="tag")

# identity / file / audit / task 四域路由：经本文件同前缀挂载（ADR-057 D1.2 口径），
# 各域 urls.py 不设 app_name，注册项并入 system 命名空间——
# /api/system/* 路径、system: 视图名、权限点与 menu.json 全部零变化。
urlpatterns = no_auth_url + auth_url + router_url + router.urls + no_detail_router.urls
urlpatterns += [path("", include("identity.urls"))]
urlpatterns += [path("", include("file.urls"))]
urlpatterns += [path("", include("audit.urls"))]
urlpatterns += [path("", include("task.urls"))]
# 全局搜索：独立 GET 接口，权限码 retrieve:SystemGlobalSearch（种子登记）
urlpatterns += [path("global-search", GlobalSearchAPIView.as_view())]
