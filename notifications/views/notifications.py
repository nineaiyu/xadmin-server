from drf_spectacular.plumbing import build_array_type, build_object_type, build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin

from common.core.modelset import DetailUpdateModelSet
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from notifications.backends import BACKEND
from notifications.models import SystemMsgSubscription, UserMsgSubscription
from notifications.notifications import system_msgs, user_msgs
from notifications.serializers import SystemMsgSubscriptionSerializer, SystemMsgSubscriptionByCategorySerializer, \
    UserMsgSubscriptionSerializer, UserMsgSubscriptionByCategorySerializer


class MsgSubscriptionBackend(object):
    @extend_schema(
        parameters=None,
        responses=get_default_response_schema(
            {
                'data': build_array_type(
                    build_object_type(
                        properties={
                            'value': build_basic_type(OpenApiTypes.STR),
                            'label': build_basic_type(OpenApiTypes.STR)
                        }
                    )
                )
            }
        )
    )
    @action(methods=['get'], detail=False)
    def backends(self, request, *args, **kwargs):
        """获取消息通知后端"""
        return ApiResponse(
            data=[{'value': backend, 'label': backend.label} for backend in BACKEND if backend.is_enable])


class SystemMsgSubscriptionViewSet(ListModelMixin, DetailUpdateModelSet, MsgSubscriptionBackend):
    """系统消息订阅"""
    lookup_field = 'message_type'
    queryset = SystemMsgSubscription.objects.all()
    serializer_class = SystemMsgSubscriptionSerializer
    list_serializer_class = SystemMsgSubscriptionByCategorySerializer

    @extend_schema(responses={200: SystemMsgSubscriptionByCategorySerializer})
    def list(self, request, *args, **kwargs):
        """获取系统消息订阅列表"""
        data = []
        category_children_mapper = {}

        subscriptions = self.get_queryset()
        msg_type_sub_mapper = {}
        for sub in subscriptions:
            msg_type_sub_mapper[sub.message_type] = sub

        for msg in system_msgs:
            message_type = msg['message_type']
            message_type_label = msg['message_type_label']
            category = msg['category']
            category_label = msg['category_label']

            if category not in category_children_mapper:
                children = []

                data.append({
                    'category': category,
                    'category_label': category_label,
                    'children': children
                })
                category_children_mapper[category] = children

            sub = msg_type_sub_mapper[message_type]
            sub.message_type_label = message_type_label
            category_children_mapper[category].append(sub)

        serializer = self.get_serializer(data, many=True)
        return ApiResponse(data=serializer.data)


class UserMsgSubscriptionViewSet(ListModelMixin, DetailUpdateModelSet, MsgSubscriptionBackend):
    """用户消息订阅"""
    lookup_field = 'message_type'
    list_serializer_class = UserMsgSubscriptionByCategorySerializer
    serializer_class = UserMsgSubscriptionSerializer
    queryset = UserMsgSubscription.objects.all()

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)

    @extend_schema(responses={200: UserMsgSubscriptionByCategorySerializer})
    def list(self, request, *args, **kwargs):
        """获取用户消息订阅列表"""
        data = []
        category_children_mapper = {}
        msg_type_sub_mapper = {}
        for sub in self.get_queryset():
            msg_type_sub_mapper[sub.message_type] = sub

        for msg in user_msgs:
            message_type = msg['message_type']
            message_type_label = msg['message_type_label']
            category = msg['category']
            category_label = msg['category_label']

            if category not in category_children_mapper:
                children = []
                data.append({
                    'category': category,
                    'category_label': category_label,
                    'children': children
                })
                category_children_mapper[category] = children

            sub = msg_type_sub_mapper.get(message_type)
            if not sub:
                sub = UserMsgSubscription.objects.create(user=request.user, message_type=message_type,
                                                         receive_backends=[])
            sub.message_type_label = message_type_label
            category_children_mapper[category].append(sub)
        serializer = self.get_serializer(data, many=True)
        return ApiResponse(data=serializer.data)
