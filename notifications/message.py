from typing import List, Dict

from django.db import transaction
from django.db.models import QuerySet

from common.core.config import batch_user_config
from common.utils import get_logger
from message.utils import get_online_users, push_messages
from notifications.serializers.message import NoticeMessageSerializer
from system.services import UserInfo

logger = get_logger(__name__)

from notifications.models import MessageContent

SYSTEM = MessageContent.NoticeChoices.SYSTEM


class SiteMessageUtil:

    @classmethod
    def send_msg(cls, subject, message, user_ids=None, level=MessageContent.LevelChoices.DEFAULT,
                 notice_type=MessageContent.NoticeChoices.SYSTEM):
        if not user_ids:
            raise ValueError('No recipient is specified')

        cls.base_notify(user_ids, subject, message, notice_type, level)

    @classmethod
    def push_notice_messages(cls, notify_obj, pks):
        notice_message = NoticeMessageSerializer(
            fields=['pk', 'level', 'title', 'notice_type', 'message'],
            instance=notify_obj, ignore_field_permission=True).data
        notice_message['message_type'] = 'notify_message'
        online_pks = set(get_online_users())
        targets = set(pks) & online_pks
        if not targets:
            return notify_obj
        # PERF-08：整个推送循环一次桥接完成，用户开关一次批量读取，
        # 不再出现"每用户一次桥接 + ~4 条命令 + 双重序列化"的串行放大
        enabled = batch_user_config(sorted(targets), 'PUSH_MESSAGE_NOTICE', True)
        push_messages([pk for pk in sorted(targets) if enabled.get(pk, True)], notice_message)
        return notify_obj

    @classmethod
    def base_notify(cls, users: List | QuerySet, title: str, message: str, notice_type: int,
                    level: MessageContent.LevelChoices, extra_json: Dict = None):
        if isinstance(users, (QuerySet, list)):
            recipients = users
        else:
            recipients = [users]
        with transaction.atomic():
            notify_obj = MessageContent.objects.create(
                title=title,
                publish=True,
                message=message,
                level=level,
                notice_type=notice_type,
                extra_json=extra_json
            )
            notify_obj.notice_user.set(recipients)
        cls.push_notice_messages(notify_obj, [user.pk for user in recipients] if isinstance(recipients[0],
                                                                                            UserInfo) else recipients)
        return notify_obj

    @classmethod
    def notify_success(cls, users: List | QuerySet, title: str, message: str, notice_type: int = SYSTEM,
                       extra_json: Dict = None):
        return cls.base_notify(users, title, message, notice_type, MessageContent.LevelChoices.SUCCESS, extra_json)

    @classmethod
    def notify_info(cls, users: List | QuerySet, title: str, message: str, notice_type: int = SYSTEM,
                    extra_json: Dict = None):
        return cls.base_notify(users, title, message, notice_type, MessageContent.LevelChoices.PRIMARY, extra_json)

    @classmethod
    def notify_error(cls, users: List | QuerySet, title: str, message: str, notice_type: int = SYSTEM,
                     extra_json: Dict = None):
        return cls.base_notify(users, title, message, notice_type, MessageContent.LevelChoices.DANGER, extra_json)
