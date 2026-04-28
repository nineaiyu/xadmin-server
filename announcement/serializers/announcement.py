from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from announcement import models


class AnnouncementSerializer(BaseModelSerializer):
    class Meta:
        model = models.Announcement
        fields = [
            'pk', 'title', 'content', 'is_top', 'status', 'publish_time',
            'creator', 'modifier', 'dept_belong', 'created_time', 'updated_time', 'description'
        ]
        table_fields = [
            'pk', 'title', 'is_top', 'status', 'publish_time', 'creator', 'created_time'
        ]
        read_only_fields = ['pk', 'creator', 'modifier', 'dept_belong', 'created_time', 'updated_time']
        extra_kwargs = {
            'creator': {
                'attrs': ['pk', 'username'], 'format': "{username}({pk})",
            },
            'modifier': {
                'attrs': ['pk', 'username'], 'format': "{username}({pk})",
            },
        }


class AnnouncementUserSerializer(BaseModelSerializer):
    creator_name = serializers.SerializerMethodField()

    class Meta:
        model = models.Announcement
        fields = [
            'pk', 'title', 'content', 'is_top', 'publish_time', 'creator_name', 'created_time'
        ]
        read_only_fields = fields

    def get_creator_name(self, obj):
        if obj.creator:
            return obj.creator.username
        return None
