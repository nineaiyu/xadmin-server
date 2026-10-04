#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器 GUI 端点：模型清单 / 字段计划 / 产物预览 / zip 下载。

适配层与安全口径见 :mod:`system.utils.codegen_gui`（复用 generate_crud 引擎，
不落盘不写库；权限走菜单种子权限点，默认仅超管可用，授予角色即开放）。
"""

from urllib.parse import quote

from django.http import HttpResponse
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import viewsets
from rest_framework.decorators import action

from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.utils import codegen_gui


class SystemCodeGenViewSet(viewsets.ViewSet):
    """代码生成器（GUI 化 generate_crud）：只读引擎适配，产物预览与下载"""

    @extend_schema(
        description="可生成 CRUD 的模型清单（仓库内一级 app 的普通模型）",
        responses=get_default_response_schema(
            {
                "data": build_array_type(
                    build_object_type(
                        properties={
                            "label": build_basic_type(OpenApiTypes.STR),
                            "app_label": build_basic_type(OpenApiTypes.STR),
                            "verbose_name": build_basic_type(OpenApiTypes.STR),
                            "table": build_basic_type(OpenApiTypes.STR),
                            "field_count": build_basic_type(OpenApiTypes.NUMBER),
                        }
                    )
                )
            }
        ),
    )
    @action(methods=["get"], detail=False, url_path="models")
    def models(self, request, *args, **kwargs):
        return ApiResponse(data=codegen_gui.list_generatable_models())

    @extend_schema(
        description="选中模型的字段计划与命名默认值（GUI 表单初始值，含可绑定字典类型）",
        responses=get_default_response_schema(
            {
                "data": build_object_type(
                    properties={
                        "label": build_basic_type(OpenApiTypes.STR),
                        "verbose_name": build_basic_type(OpenApiTypes.STR),
                        "defaults": build_object_type(
                            properties={
                                "component": build_basic_type(OpenApiTypes.STR),
                                "url_prefix": build_basic_type(OpenApiTypes.STR),
                                "frontend_dir": build_basic_type(OpenApiTypes.STR),
                            }
                        ),
                        "fields": build_array_type(
                            build_object_type(
                                properties={
                                    "name": build_basic_type(OpenApiTypes.STR),
                                    "verbose_name": build_basic_type(OpenApiTypes.STR),
                                    "type": build_basic_type(OpenApiTypes.STR),
                                    "in_table": build_basic_type(OpenApiTypes.BOOL),
                                    "in_search": build_basic_type(OpenApiTypes.BOOL),
                                    "can_search": build_basic_type(OpenApiTypes.BOOL),
                                    "required": build_basic_type(OpenApiTypes.BOOL),
                                    "is_relation": build_basic_type(OpenApiTypes.BOOL),
                                    "has_choices": build_basic_type(OpenApiTypes.BOOL),
                                    "default_input_type": build_basic_type(OpenApiTypes.STR),
                                    "can_filter_custom": build_basic_type(OpenApiTypes.BOOL),
                                }
                            )
                        ),
                        "dict_types": build_array_type(
                            build_object_type(
                                properties={
                                    "code": build_basic_type(OpenApiTypes.STR),
                                    "label": build_basic_type(OpenApiTypes.STR),
                                }
                            )
                        ),
                    }
                )
            }
        ),
    )
    @action(methods=["get"], detail=False, url_path="model-fields")
    def model_fields(self, request, *args, **kwargs):
        label = request.query_params.get("label") or ""
        try:
            return ApiResponse(data=codegen_gui.model_plan(label))
        except codegen_gui.CodegenError as exc:
            return ApiResponse(code=1001, detail=str(exc))

    @extend_schema(
        description="生成产物预览（不落盘）：按表单配置渲染全部文件内容（含 NEXT_STEPS.md）",
        request=build_object_type(
            properties={
                "model": build_basic_type(OpenApiTypes.STR),
                "component": build_basic_type(OpenApiTypes.STR),
                "url_prefix": build_basic_type(OpenApiTypes.STR),
                "frontend_dir": build_basic_type(OpenApiTypes.STR),
                "menu_parent": build_basic_type(OpenApiTypes.STR),
                "menu_icon": build_basic_type(OpenApiTypes.STR),
                # build_basic_type 的 stub 返回 dict | None：与 file.py 的 `or {}` 同口径
                "include_fields": build_array_type(build_basic_type(OpenApiTypes.STR) or {}),
                "exclude_fields": build_array_type(build_basic_type(OpenApiTypes.STR) or {}),
                # 字段级覆盖：name / include / label / required / read_only / in_table /
                # in_search / input_type（仅关联字段）/ dict_code（仅非关联字段），顺序即字段序
                "fields": build_array_type(build_object_type() or {}),
                "with_import_export": build_basic_type(OpenApiTypes.BOOL),
                "with_tags": build_basic_type(OpenApiTypes.BOOL),
                "with_tests": build_basic_type(OpenApiTypes.BOOL),
                "with_module": build_basic_type(OpenApiTypes.BOOL),
                "module_id": build_basic_type(OpenApiTypes.STR),
                "module_level": build_basic_type(OpenApiTypes.STR),
                "skip_menu_seed": build_basic_type(OpenApiTypes.BOOL),
            },
            required=["model"],
        ),
        responses=get_default_response_schema(
            {
                "data": build_array_type(
                    build_object_type(
                        properties={
                            "label": build_basic_type(OpenApiTypes.STR),
                            "path": build_basic_type(OpenApiTypes.STR),
                            "content": build_basic_type(OpenApiTypes.STR),
                            "mode": build_basic_type(OpenApiTypes.STR),
                            "key": build_basic_type(OpenApiTypes.STR),
                            "notice": build_basic_type(OpenApiTypes.STR),
                        }
                    )
                )
            }
        ),
    )
    @action(methods=["post"], detail=False, url_path="preview")
    def preview(self, request, *args, **kwargs):
        try:
            return ApiResponse(data=codegen_gui.build_artifacts(request.data or {}))
        except codegen_gui.CodegenError as exc:
            return ApiResponse(code=1001, detail=str(exc))

    @extend_schema(
        description="生成产物打包下载（zip，路径 = 仓库相对路径；models 传多模型清单走批量打包）",
        request=build_object_type(
            properties={
                "model": build_basic_type(OpenApiTypes.STR),
                "models": build_array_type(build_basic_type(OpenApiTypes.STR) or {}),
            },
            required=[],
        ),
        responses={200: inline_serializer(name="zipFile", fields={})},
    )
    @action(methods=["post"], detail=False, url_path="download")
    def download(self, request, *args, **kwargs):
        try:
            payload = codegen_gui.build_zip(request.data or {})
        except codegen_gui.CodegenError as exc:
            return ApiResponse(code=1001, detail=str(exc))
        model_label = str((request.data or {}).get("model") or "codegen").replace(".", "-")
        response = HttpResponse(payload, content_type="application/zip")
        filename = f"generated-{model_label}.zip"
        response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
        return response
