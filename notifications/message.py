import json
from typing import List, Dict

from django.db import transaction
from rest_framework.utils import encoders

from common.core.config import UserConfig
from common.utils import get_logger
from message.utils import push_message, get_online_users
from notifications.serializers.message import NoticeMessageSerializer
from system.models import UserInfo

logger = get_logger(__name__)

from django.db.models import QuerySet

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
        for pk in set(pks) & set(get_online_users()):
            if UserConfig(pk).PUSH_MESSAGE_NOTICE:
                push_message(pk, json.loads(json.dumps(notice_message, cls=encoders.JSONEncoder, ensure_ascii=False)))
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
