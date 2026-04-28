from django_filters import rest_framework as filters

from common.core.filter import BaseFilterSet
from common.core.modelset import OnlyListModelSet, DetailAction
from common.core.pagination import DynamicPageNumber
from announcement.models import Announcement
from announcement.serializers import AnnouncementUserSerializer


class AnnouncementUserFilter(BaseFilterSet):
    title = filters.CharFilter(field_name='title', lookup_expr='icontains')

    class Meta:
        model = Announcement
        fields = ['title', 'is_top', 'publish_time']


class AnnouncementUserViewSet(OnlyListModelSet, DetailAction):
    """用户公告查看"""
    serializer_class = AnnouncementUserSerializer
    ordering_fields = ['is_top', 'publish_time']
    filterset_class = AnnouncementUserFilter
    pagination_class = DynamicPageNumber(1000)

    def get_queryset(self):
        return Announcement.objects.filter(
            status=Announcement.StatusChoices.PUBLISHED
        ).order_by('-is_top', '-publish_time')
