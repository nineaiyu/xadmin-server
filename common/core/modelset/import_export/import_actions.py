#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""导入导出：导入前校验与异步导入（import-headers / import-validate / import-async）。"""

import codecs
import json

from django.conf import settings
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema

from common.core.import_mapping import first_column_candidates, writable_field_options
from common.core.permission_meta import parent_fallback_action
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger

from .celery_utils import _flatten_row_errors

logger = get_logger(__name__)


class ImportAsyncAction:
    """导入前校验与异步导入（大数据量场景，记录与错误报告在下载中心获取）。

    协议与同步 import-data 完全同源：请求体即文件原始内容（Content-Type
    text/csv / text/xlsx），由同一套文件解析器（ExcelFileParser/CSVFileParser）
    解析为行数据；`action` 走查询参数。差异仅在后续处理：
    - import-validate：逐行校验不落库，同步返回错误行定位；
    - import-async：行数据序列化为 JSON 落 UploadFile(is_tmp=True)，任务内
      直接读行导入（大文件不塞 broker 消息，也不重复解析）。
    """

    def _get_rows_and_titles(self, request):
        """从文件解析器产物中取行数据与原表头（含列映射预处理）。"""
        self._resolve_import_mapping(request)
        rows = request.data
        if isinstance(rows, dict):
            rows = [rows]
        pairs = getattr(request, "jms_context", {}).get("column_title_field_pairs") or []
        column_titles = [title for title, _field in pairs if title]
        return rows, column_titles

    @staticmethod
    def _field_titles(request):
        """字段名 → 原始表头（错误报告按原始列名展示，便于与源文件对照）。"""
        pairs = getattr(request, "jms_context", {}).get("column_title_field_pairs") or []
        return {field: title for title, field in pairs if field}

    def _resolve_import_mapping(self, request):
        """把列映射解析进 ``request.jms_context``，供文件解析器在解析表头时取用。

        - ``template_id`` 优先（模板已持久化，取目标模型下「共享 + 本人」可见者）；
        - 其次 ``mapping`` 查询参数（前端直接提交的映射 JSON）；
        - ``ignore_unknown`` 缺省 true：未映射列丢弃；传 false 时保留并记入未匹配列。

        必须在访问 ``request.data``（触发文件解析）之前调用，否则映射不生效。
        """
        from django.apps import apps
        from rest_framework.exceptions import ValidationError

        template_id = request.query_params.get("template_id")
        mapping = None
        if template_id:
            template_model = apps.get_model("system", "ImportTemplate")
            model_label = self.get_queryset().model._meta.label_lower
            template = (
                template_model.objects.filter(pk=template_id, model=model_label)
                .filter(Q(is_shared=True) | Q(creator=request.user))
                .first()
            )
            if template is None:
                raise ValidationError({"detail": _("Import template not found")})
            mapping = template.mapping
        else:
            raw_mapping = request.query_params.get("mapping")
            if raw_mapping:
                try:
                    mapping = json.loads(raw_mapping)
                except (TypeError, ValueError):
                    raise ValidationError({"detail": _("Invalid import mapping")}) from None
                if not isinstance(mapping, dict):
                    raise ValidationError({"detail": _("Invalid import mapping")})
        if not mapping:
            return
        ignore_unknown = str(request.query_params.get("ignore_unknown", "true")).lower() not in ["false", "0", "no"]
        jms_context = getattr(request, "jms_context", None) or {}
        jms_context["import_mapping"] = {"mapping": mapping, "ignore_unknown": ignore_unknown}
        request.jms_context = jms_context

    def _import_context(self, request):
        """提取导入上下文：目标模型、视图路径、提交者。"""
        model = self.get_queryset().model
        view_path = f"{self.__class__.__module__}.{self.__class__.__name__}"
        return model, view_path, getattr(request.user, "pk", None)

    def _check_running_limit(self, request):
        """同用户并发上限（IMPORT_ASYNC_MAX_RUNNING，0=不限制），超限返回提示文案。"""
        from django.apps import apps

        from common.core.config import SysConfig, get_personal_int_config

        # 真实个人行优先，未设置回退系统级
        max_running = get_personal_int_config(
            request.user, "IMPORT_ASYNC_MAX_RUNNING", SysConfig.IMPORT_ASYNC_MAX_RUNNING
        )
        if max_running <= 0:
            return None
        import_record_model = apps.get_model("system", "ImportRecord")
        running = import_record_model.objects.filter(
            creator=request.user,
            status__in=[import_record_model.Status.PENDING, import_record_model.Status.RUNNING],
        ).count()
        if running >= max_running:
            return _("Too many import tasks in progress (limit {}), please wait for them to finish").format(max_running)
        return None

    @staticmethod
    def _save_rows_file(rows, user, filename):
        """行数据序列化为 JSON 落 UploadFile(is_tmp=True)，供任务内读取。"""
        from django.apps import apps
        from django.core.files.base import ContentFile

        upload_model = apps.get_model("system", "UploadFile")
        content = json.dumps(rows, ensure_ascii=False, default=str).encode("utf-8")
        instance = upload_model(
            filename=filename,
            filesize=len(content),
            mime_type="application/json",
            is_tmp=True,
            is_upload=False,
            creator=user,
        )
        instance.filepath.save(filename, ContentFile(content), save=False)
        instance.save()
        return instance

    def _create_import_record(self, request, model, action_type, column_titles):
        from django.apps import apps
        from django.utils import timezone as dj_timezone

        import_record_model = apps.get_model("system", "ImportRecord")
        name = "import_{}".format(dj_timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S"))
        return import_record_model.objects.create(
            name=name,
            module=str(model._meta.verbose_name),
            path=request.path,
            action=action_type,
            params={
                "action": action_type,
                "column_titles": column_titles,
                # 错误报告按原始列名展示（字段名 → 表头），便于与源文件对照
                "field_titles": self._field_titles(request),
            },
            # 显式赋值消除 threadlocal 注入的时序依赖（OwnerFilter 依赖 creator）
            creator=request.user if getattr(request.user, "pk", None) else None,
        )

    def _get_file_parser(self, request):
        """按 Content-Type 取当前视图可用的文件解析器实例（CSV / xlsx）。

        只认文件解析器（``BaseFileParser`` 子类）：DRF 的 JSONParser/FormParser 也带
        ``media_type``，误命中会让后续 ``check_content_length`` 抛 AttributeError（500）。
        """
        from common.drf.parsers.base import BaseFileParser

        content_type = (request.content_type or "").split(";")[0].strip()
        for parser in self.get_parsers():
            if getattr(parser, "media_type", None) != content_type:
                continue
            if isinstance(parser, BaseFileParser):
                return parser
            return None
        return None

    @extend_schema(
        request=OpenApiRequest(build_basic_type(OpenApiTypes.BINARY)),
        responses=get_default_response_schema(
            {
                "headers": build_basic_type(OpenApiTypes.STR),
                "candidates": build_basic_type(OpenApiTypes.STR),
            }
        ),
    )
    @parent_fallback_action(methods=["post"], detail=False, url_path="import-headers")
    def import_headers(self, request, *args, **kwargs):
        """读取导入文件首行表头并给出列映射候选{cls}（列映射步骤，不落库）"""
        from rest_framework.exceptions import ParseError

        from common.drf.parsers.base import BaseFileParser, FileContentOverflowedError

        parser = self._get_file_parser(request)
        if parser is None:
            return ApiResponse(code=1001, detail=_("Unsupported file type"))
        try:
            parser.serializer_cls = self.get_serializer_class()
            parser.serializer_fields = parser.serializer_cls().fields
        except Exception as e:
            logger.debug(e, exc_info=True)
            return ApiResponse(code=1001, detail=_("The resource does not support imports!"))
        parser.check_content_length(request.META)
        # 只需要首行表头，但 xlsx 是压缩包必须整体解压：按解析器上限做有界读取
        max_length = BaseFileParser.FILE_CONTENT_MAX_LENGTH
        stream_data = request.stream.read(max_length + 1)
        if len(stream_data) > max_length:
            raise FileContentOverflowedError(FileContentOverflowedError.default_detail.format(max_length))
        stream_data = stream_data.strip(codecs.BOM_UTF8)
        try:
            column_titles = list(parser.get_column_titles(parser.generate_rows(stream_data)))
        except Exception as e:
            logger.error(e, exc_info=True)
            raise ParseError(_("Parse file error: {}").format(str(e))) from e
        return ApiResponse(
            data={
                "headers": column_titles,
                "candidates": first_column_candidates(column_titles, parser.serializer_fields),
                "fields": writable_field_options(parser.serializer_fields),
                # 目标模型 label_lower：模板按模型隔离，前端据此过滤模板列表
                "model": self.get_queryset().model._meta.label_lower,
            }
        )

    @extend_schema(
        request=OpenApiRequest(build_basic_type(OpenApiTypes.BINARY)),
        responses=get_default_response_schema(
            {
                "total": build_basic_type(OpenApiTypes.NUMBER),
                "valid_count": build_basic_type(OpenApiTypes.NUMBER),
                "invalid_count": build_basic_type(OpenApiTypes.NUMBER),
                "errors_truncated": build_basic_type(OpenApiTypes.BOOL),
                "errors": build_object_type(
                    properties={
                        "row": build_basic_type(OpenApiTypes.NUMBER),
                        "field": build_basic_type(OpenApiTypes.STR),
                        "message": build_basic_type(OpenApiTypes.STR),
                    }
                ),
            }
        ),
    )
    @parent_fallback_action(methods=["post"], detail=False, url_path="import-validate")
    def import_validate(self, request, *args, **kwargs):
        """导入前校验{cls}数据（逐行校验不落库，返回字段级错误定位）"""
        from common.core.config import SysConfig

        rows, _column_titles = self._get_rows_and_titles(request)
        errors, limit = [], SysConfig.IMPORT_VALIDATE_ERROR_LIMIT
        invalid_count = 0
        for idx, row in enumerate(rows, start=1):
            serializer = self.get_serializer(data=row)
            if not serializer.is_valid():
                invalid_count += 1
                if len(errors) < limit:
                    # 字段级展开：{row, field, message}，前端可精确定位到具体字段
                    errors.extend(_flatten_row_errors(idx, serializer.errors, limit - len(errors)))
        return ApiResponse(
            data={
                "total": len(rows),
                "valid_count": len(rows) - invalid_count,
                "invalid_count": invalid_count,
                "errors_truncated": invalid_count > 0 and len(errors) >= limit,
                "errors": errors,
                # 字段名 → 原始表头：前端据此把错误定位到源文件列名
                "field_titles": self._field_titles(request),
                # 提供过映射但未命中映射的列（ignore_unknown=false 时非空）
                "unmatched_columns": (getattr(request, "jms_context", None) or {}).get("import_unmatched_columns")
                or [],
            }
        )

    @extend_schema(
        request=OpenApiRequest(build_basic_type(OpenApiTypes.BINARY)),
        responses=get_default_response_schema(),
    )
    @parent_fallback_action(methods=["post"], detail=False, url_path="import-async")
    def import_async(self, request, *args, **kwargs):
        """异步导入{cls}数据（大数据量，进度与错误报告在下载中心获取）"""
        from django.db import transaction
        from django.utils.module_loading import import_string

        action_type = request.query_params.get("action") or "create"
        if action_type not in ("create", "update"):
            return ApiResponse(code=1001, detail=_("Operation failed. Abnormal data"))
        rows, column_titles = self._get_rows_and_titles(request)
        if not rows:
            return ApiResponse(code=1001, detail=_("Operation failed. Abnormal data"))
        limit_tip = self._check_running_limit(request)
        if limit_tip:
            return ApiResponse(code=1001, detail=limit_tip)
        model, view_path, user_pk = self._import_context(request)
        record = self._create_import_record(request, model, action_type, column_titles)
        # 行数据落 UploadFile(is_tmp=True)：任务内直接读 JSON，不重复解析原文件
        record.source_file = self._save_rows_file(rows, request.user, f"{record.pk}_rows.json")
        record.save(update_fields=["source_file", "updated_time"])
        args = [str(record.pk), view_path, user_pk]
        task = import_string("system.tasks.async_import_data_task")
        if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
            # 测试/E2E：send_task/apply_async 在 eager 下不执行，改 apply 同步跑完
            task.apply(args=args, task_id=str(record.pk))
        else:
            transaction.on_commit(lambda: task.apply_async(args=args, task_id=str(record.pk)))
        return ApiResponse(
            data={"record_id": str(record.pk), "task_id": str(record.pk)},
            detail=_("Import task submitted"),
        )
