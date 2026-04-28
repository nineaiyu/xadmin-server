from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from rest_framework.decorators import action

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, ImportExportDataAction
from common.core.pagination import DynamicPageNumber
from common.core.response import ApiResponse
from announcement.models import Announcement
from announcement.serializers import AnnouncementSerializer


class AnnouncementAdminFilter(BaseFilterSet):
    title = filters.CharFilter(field_name='title', lookup_expr='icontains')
    is_top = filters.BooleanFilter(field_name='is_top')
    is_published = filters.BooleanFilter(field_name='is_published')
    status = filters.NumberFilter(field_name='status')

    class Meta:
        model = Announcement
        fields = ['title', 'is_top', 'is_published', 'status', 'created_time', 'publish_time']


class AnnouncementAdminViewSet(BaseModelSet, ImportExportDataAction):
    """系统公告管理"""
    queryset = Announcement.objects.all()
    serializer_class = AnnouncementSerializer
    ordering_fields = ['created_time', 'publish_time', 'is_top']
    filterset_class = AnnouncementAdminFilter
    pagination_class = DynamicPageNumber(1000)

    def perform_create(self, serializer):
        instance = serializer.save(creator=self.request.user, dept_belong=self.request.user.dept)
        if instance.is_published and not instance.publish_time:
            instance.publish_time = timezone.now()
            instance.save(update_fields=['publish_time'])

    def perform_update(self, serializer):
        instance = serializer.save(modifier=self.request.user)
        if instance.is_published and not instance.publish_time:
            instance.publish_time = timezone.now()
            instance.save(update_fields=['publish_time'])

    @action(methods=['post'], detail=True)
    def publish(self, request, *args, **kwargs):
        """发布公告"""
        instance = self.get_object()
        if not instance.is_published:
            instance.is_published = True
            instance.status = Announcement.StatusChoices.PUBLISHED
            instance.publish_time = timezone.now()
            instance.modifier = request.user
            instance.save(update_fields=['is_published', 'status', 'publish_time', 'modifier'])
        return ApiResponse(detail=_("发布成功"))

    @action(methods=['post'], detail=True)
    def unpublish(self, request, *args, **kwargs):
        """下架公告"""
        instance = self.get_object()
        if instance.is_published:
            instance.is_published = False
            instance.status = Announcement.StatusChoices.DRAFT
            instance.modifier = request.user
            instance.save(update_fields=['is_published', 'status', 'modifier'])
        return ApiResponse(detail=_("下架成功"))
