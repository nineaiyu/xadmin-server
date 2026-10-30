from typing import Any

from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from rest_framework.decorators import action
from rest_framework.request import Request

from common.core.approval import ApprovalRequired
from common.core.filter import BaseFilterSet, PkMultipleFilter
from common.core.modelset import BaseModelSet, ImportExportDataAction, RecycleBinAction
from common.core.pagination import DynamicPageNumber
from common.core.response import API_SUCCESS_CODE, ApiResponse
from common.utils import get_logger
from demo.models import Book
from demo.serializers.book import BookSerializer
from demo.services import submit_book

logger = get_logger(__name__)


class BookViewSetFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")
    author = filters.CharFilter(field_name="author", lookup_expr="icontains")
    publisher = filters.CharFilter(field_name="publisher", lookup_expr="icontains")

    # 自定义的搜索模板，针对用户搜索，前端已经内置 api-search-user 模板处理
    managers2 = PkMultipleFilter(input_type="api-search-user")

    # 自定义的搜索模板，默认是带有choices的下拉框，当数据多的话，体验不好，所以这里改为输入框，前端已经内置 input 处理
    # 关联关系搜索的时候，默认是主键pk
    managers = PkMultipleFilter(input_type="input")

    class Meta:
        model = Book
        fields = [
            "name",
            "isbn",
            "author",
            "publisher",
            "is_active",
            "status",
            "publication_date",
            "price",
            "created_time",
            "managers",
            "managers2",
        ]  # fields用于前端自动生成的搜索表单


class BookViewSet(RecycleBinAction, BaseModelSet, ImportExportDataAction):
    """书籍"""  # 这个 书籍 的注释得写， 否则菜单中可能会显示null，访问日志记录中也可能显示异常

    queryset = Book.objects.all()
    serializer_class = BookSerializer
    ordering_fields = ["created_time"]
    filterset_class = BookViewSetFilter
    pagination_class = DynamicPageNumber(1000)  # 表示最大分页数据1000条，如果注释，则默认最大100条数据

    # 敏感操作审批（二次确认演示）：删除 / 批量删除挂载 ApprovalRequired 装饰器。
    # 是否真正拦截由系统配置 APPROVAL_REQUIRED_PATHS（路径正则清单）决定——默认空 = 休眠；
    # 命中时先建审批单（响应 412，所有用户一律拦截），审批通过后携带令牌重放才真正执行
    # （申请人不能自审；前端已内置令牌暂存与自动携带）。演示环境由 seed_demo_book 写入拦截清单。
    @ApprovalRequired()
    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Any:
        """删除{cls}数据

        高危：可经 APPROVAL_REQUIRED_PATHS 纳入审批。
        """
        return super().destroy(request, *args, **kwargs)

    @ApprovalRequired()
    @action(methods=["post"], detail=False, url_path="batch-destroy")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def batch_destroy(self, request: Request, *args: Any, **kwargs: Any) -> Any:
        """批量删除{cls}（高危：与删除同口径纳入审批）"""
        return super().batch_destroy(request, *args, **kwargs)

    @action(methods=["post"], detail=True)  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def push(self, request: Request, *args: Any, **kwargs: Any) -> Any:
        """推送到其他服务"""  # 这个 推送到其他服务 的注释得写， 否则菜单中可能会显示null，访问日志记录中也可能显示异常

        # 自定义 POST 动作示例：真实写入一条推送审计（可追溯），不再是「只返回成功」的假交互
        # （落库走 audit.services.record：截断/creator 解析/异常兜底内建）
        from audit.services import record

        instance = self.get_object()
        record(
            module="DEMO:BookPush",
            object_pk=str(instance.pk),
            path=request.path,
            method="POST",
            # 审计字段口径与 oplog_recorder 一致：status_code=业务码、response_code=HTTP 状态
            status_code=API_SUCCESS_CODE,
            response_code=200,
            response_result=f"push {instance.name}",
            creator=request.user,
            modifier=request.user,
        )
        return ApiResponse(detail=_("%(name)s push succeeded") % {"name": instance.name})

    @action(methods=["post"], detail=True)  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def submit(self, request: Request, *args: Any, **kwargs: Any) -> Any:
        """提交上架审批"""  # 接入审批流引擎：终态经信号回写 status（见 demo/services.py）

        instance = self.get_object()
        ok, detail = submit_book(instance, request.user)
        if not ok:
            # 业务拒绝统一传输口径：HTTP 400 + 业务码（前端 badRequestStrategy 提示 detail），
            # 不再走 200+1001 双轨
            return ApiResponse(code=1001, detail=detail, status=400)
        return ApiResponse(detail=detail)
