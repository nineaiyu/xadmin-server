import inspect
from importlib import import_module

from django.apps import AppConfig
from django.db.models.signals import post_save, post_migrate, m2m_changed
from django.dispatch import receiver
from django.utils.functional import LazyObject

from common.utils import get_logger
from common.utils.connection import RedisPubSub
from notifications.message import SiteMessageUtil
from notifications.models import SystemMsgSubscription, MessageContent
from notifications.notifications import SystemMessage
from system.models import UserInfo

logger = get_logger(__name__)


class NewSiteMsgSubPub(LazyObject):
    def _setup(self):
        self._wrapped = RedisPubSub('notifications.SiteMessageCome')


new_site_msg_chan = NewSiteMsgSubPub()


@receiver(post_migrate, dispatch_uid='notifications.signal_handlers.create_system_messages')
def create_system_messages(app_config: AppConfig, **kwargs):
    try:
        notifications_module = import_module('.notifications', app_config.module.__package__)

        for name, obj in notifications_module.__dict__.items():
            if name.startswith('_'):
                continue

            if not inspect.isclass(obj):
                continue

            if not issubclass(obj, SystemMessage):
                continue

            attrs = obj.__dict__
            if 'message_type_label' not in attrs:
                continue

            if 'category' not in attrs:
                continue

            if 'category_label' not in attrs:
                continue

            message_type = obj.get_message_type()
            sub, created = SystemMsgSubscription.objects.get_or_create(message_type=message_type)
            if not created:
                return

            try:
                obj.post_insert_to_db(sub)
                logger.info(f'Create MsgSubscription: package={app_config.module.__package__} type={message_type}')
            except:
                pass
    except ModuleNotFoundError:
        pass


# def invalid_notify_cache(pk):
#     """清理消息缓存"""
#     cache_response.invalid_cache(f'UserSiteMessageViewSet_unread_{pk}_*')
#     cache_response.invalid_cache(f'UserSiteMessageViewSet_list_{pk}_*')


def invalid_notify_caches(instance, pk_set):
    pks = []
    if instance.notice_type == MessageContent.NoticeChoices.USER:
        pks = pk_set
    if instance.notice_type == MessageContent.NoticeChoices.ROLE:
        pks = UserInfo.objects.filter(roles__in=pk_set).values_list('pk', flat=True)
    if instance.notice_type == MessageContent.NoticeChoices.DEPT:
        pks = UserInfo.objects.filter(dept__in=pk_set).values_list('pk', flat=True)
    if pks:
        if instance.publish:
            SiteMessageUtil.push_notice_messages(instance, set(pks))
        # for pk in set(pks):
        #     invalid_notify_cache(pk)


@receiver(post_save, sender=MessageContent)
def clean_notify_cache_handler_post_save(sender, instance, **kwargs):
    pk_set = None
    if instance.notice_type == MessageContent.NoticeChoices.NOTICE:
        # invalid_notify_cache('*')
        if instance.publish:
            SiteMessageUtil.push_notice_messages(instance, UserInfo.objects.values_list('pk', flat=True))
    elif instance.notice_type == MessageContent.NoticeChoices.DEPT:
        pk_set = instance.notice_dept.values_list('pk', flat=True)
    elif instance.notice_type == MessageContent.NoticeChoices.ROLE:
        pk_set = instance.notice_role.values_list('pk', flat=True)
    else:
        pk_set = instance.notice_user.values_list('pk', flat=True)
    if pk_set:
        invalid_notify_caches(instance, pk_set)
    logger.info(f"invalid cache {sender}")


@receiver(m2m_changed)
def clean_m2m_notify_cache_handler(sender, instance, **kwargs):
    if kwargs.get('action') in ['post_add', 'pre_remove']:
        # if issubclass(sender, MessageUserRead):
        #     for pk in kwargs.get('pk_set', []):
        #         invalid_notify_cache(pk)

        if isinstance(instance, MessageContent):
            invalid_notify_caches(instance, kwargs.get('pk_set', []))

# @receiver([post_save, pre_delete])
# def clean_notify_cache_handler(sender, instance, **kwargs):
#     if issubclass(sender, MessageUserRead):
#         invalid_notify_cache(instance.owner.pk)
