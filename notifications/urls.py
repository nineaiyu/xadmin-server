from django.urls import re_path
from rest_framework.routers import SimpleRouter

from notifications.views.message import NoticeMessageViewSet, NoticeUserReadMessageViewSet
from notifications.views.notifications import SystemMsgSubscriptionViewSet, BackendListAPIView, \
    UserMsgSubscriptionViewSet
from notifications.views.user_site_msg import UserSiteMessageViewSet

router = SimpleRouter(False)

# 消息通知路由
router.register('notice-messages', NoticeMessageViewSet, basename='notice-messages')
router.register('user-read-messages', NoticeUserReadMessageViewSet, basename='user-read-messages')
router.register('site-messages', UserSiteMessageViewSet, basename='site-messages')


# 消息订阅配置
router.register('system-msg-subscription', SystemMsgSubscriptionViewSet, basename='system-msg-subscription')
router.register('user-msg-subscription', UserMsgSubscriptionViewSet, basename='user-msg-subscription')

urls = [
    re_path('^backends$', BackendListAPIView.as_view(), name='notifications-backends'),
]

urlpatterns = router.urls + urls
