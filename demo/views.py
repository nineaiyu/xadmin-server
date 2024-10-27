# Create your views here.


from django_filters import rest_framework as filters

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, ImportExportDataAction
from common.core.pagination import DynamicPageNumber
from common.utils import get_logger
from demo.models import Book
from demo.serializers.book import BookSerializer

logger = get_logger(__name__)


class BookFilter(BaseFilterSet):
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')
    author = filters.CharFilter(field_name='author', lookup_expr='icontains')
    publisher = filters.CharFilter(field_name='publisher', lookup_expr='icontains')

    class Meta:
        model = Book
        fields = ['name', 'isbn', 'author', 'publisher', 'is_active', 'publication_date', 'price',
                  'created_time']  # fields用于前端自动生成的搜索表单


class BookView(BaseModelSet, ImportExportDataAction):
    """书籍管理"""
    queryset = Book.objects.all()
    serializer_class = BookSerializer
    ordering_fields = ['created_time']
    filterset_class = BookFilter
    pagination_class = DynamicPageNumber(1000)  # 表示最大分页数据1000条，如果注释，则默认最大100条数据
