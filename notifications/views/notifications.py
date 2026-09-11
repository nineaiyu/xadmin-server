from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_array_type, build_object_type, build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin

from common.utils import get_logger

from common.core.modelset import DetailUpdateModelSet
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from notifications.backends import BACKEND
from notifications.models import SystemMsgSubscription, UserMsgSubscription
from notifications.notifications import get_message_cls, system_msgs, user_msgs
from notifications.serializers import (
    SystemMsgSubscriptionSerializer,
    SystemMsgSubscriptionByCategorySerializer,
    UserMsgSubscriptionSerializer,
    UserMsgSubscriptionByCategorySerializer,
)


logger = get_logger(__name__)


class MsgSubscriptionBackend(object):
    @extend_schema(
        parameters=None,
        responses=get_default_response_schema(
            {
                "data": build_array_type(
                    build_object_type(
                        properties={
                            "value": build_basic_type(OpenApiTypes.STR),
                            "label": build_basic_type(OpenApiTypes.STR),
                        }
                    )
                )
            }
        ),
    )
    @action(methods=["get"], detail=False)
    def backends(self, request, *args, **kwargs):
        """获取消息通知后端"""
        return ApiResponse(
            data=[{"value": backend, "label": backend.label} for backend in BACKEND if backend.is_enable]
        )

    #: 测试消息是否发给「当前登录用户」（个人订阅页=True；系统订阅页发给全部超管=False）
    test_msg_to_request_user = False

    @extend_schema(
        request=build_object_type(
            properties={"message_type": build_basic_type(OpenApiTypes.STR)},
            required=["message_type"],
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False)
    def test(self, request, *args, **kwargs):
        """发送测试消息（渠道连通性自检）

        按订阅行的 message_type 找到消息实现类并走真实发送链路：
        系统消息发给全部在用超管，用户消息发给当前用户（个人订阅页）或样例用户。
        """
        message_type = request.data.get("message_type")
        message_cls = get_message_cls(message_type) if message_type else None
        if message_cls is None:
            return ApiResponse(code=1004, detail=_("Unknown message type"))
        try:
            message_cls.send_test_msg(user=request.user if self.test_msg_to_request_user else None)
        except NotImplementedError:
            return ApiResponse(code=1004, detail=_("This message type does not support test sending"))
        except Exception as e:  # noqa: BLE001 渠道异常不暴露内部细节
            logger.exception("send test message failed: %s", e)
            return ApiResponse(code=1001, detail=_("Failed to send test message"))
        return ApiResponse(detail=_("Test message sent"))


class SystemMsgSubscriptionViewSet(ListModelMixin, DetailUpdateModelSet, MsgSubscriptionBackend):
    """系统消息订阅"""

    lookup_field = "message_type"
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
            message_type = msg["message_type"]
            message_type_label = msg["message_type_label"]
            category = msg["category"]
            category_label = msg["category_label"]

            if category not in category_children_mapper:
                children = []

                data.append({"category": category, "category_label": category_label, "children": children})
                category_children_mapper[category] = children

            sub = msg_type_sub_mapper[message_type]
            sub.message_type_label = message_type_label
            category_children_mapper[category].append(sub)

        serializer = self.get_serializer(data, many=True)
        return ApiResponse(data=serializer.data)


class UserMsgSubscriptionViewSet(ListModelMixin, DetailUpdateModelSet, MsgSubscriptionBackend):
    """用户消息订阅"""

    # 个人订阅页的「发送测试」发给自己（系统订阅页则发给全部超管）
    test_msg_to_request_user = True
    lookup_field = "message_type"
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
            message_type = msg["message_type"]
            message_type_label = msg["message_type_label"]
            category = msg["category"]
            category_label = msg["category_label"]

            if category not in category_children_mapper:
                children = []
                data.append({"category": category, "category_label": category_label, "children": children})
                category_children_mapper[category] = children

            sub = msg_type_sub_mapper.get(message_type)
            if not sub:
                sub = UserMsgSubscription.objects.create(
                    user=request.user, message_type=message_type, receive_backends=[]
                )
            sub.message_type_label = message_type_label
            category_children_mapper[category].append(sub)
        serializer = self.get_serializer(data, many=True)
        return ApiResponse(data=serializer.data)
