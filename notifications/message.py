from django.db import transaction
from django.db.models import QuerySet

from common.core.config import batch_user_config
from common.utils import get_logger
from identity.services import UserInfo
from message.utils import get_online_users
from notifications.serializers.message import NoticeMessageSerializer
from notifications.tasks import json_safe, push_messages_job

logger = get_logger(__name__)

from notifications.models import MessageContent

SYSTEM: int = MessageContent.NoticeChoices.SYSTEM  # type: ignore[assignment]  # Choices 成员由元类生成（运行期为枚举值）


class SiteMessageUtil:
    @classmethod
    def send_msg(
        cls,
        subject,
        message,
        user_ids=None,
        level=MessageContent.LevelChoices.DEFAULT,
        notice_type=MessageContent.NoticeChoices.SYSTEM,
    ):
        if not user_ids:
            raise ValueError("No recipient is specified")

        cls.base_notify(user_ids, subject, message, notice_type, level)

    @classmethod
    def push_notice_messages(cls, notify_obj, pks):
        notice_message = NoticeMessageSerializer(
            fields=["pk", "level", "title", "notice_type", "message"], instance=notify_obj, ignore_field_permission=True
        ).data
        notice_message["message_type"] = "notify_message"
        online_pks = set(get_online_users())
        if not online_pks:
            return notify_obj
        if isinstance(pks, QuerySet):
            # 目标为全量用户（如系统公告）时交给数据库求交集，
            # 避免把整张用户表物化成 Python set 后再与在线集合比对
            targets = set(pks.filter(pk__in=online_pks).values_list("pk", flat=True))
        else:
            targets = set(pks) & online_pks
        if not targets:
            return notify_obj
        # 整个推送循环一次桥接完成，用户开关一次批量读取，
        # 不再出现"每用户一次桥接 + ~4 条命令 + 双重序列化"的串行放大
        enabled = batch_user_config(sorted(targets), "PUSH_MESSAGE_NOTICE", True)
        pending = [pk for pk in sorted(targets) if enabled.get(pk, True)]
        if not pending:
            return notify_obj
        # 全量扇出交由 Celery 异步投递（数千年在线时逐人 group_send 会拖住请求线程）；
        # 参数经 JSON 清洗（UUID / gettext_lazy 等非原生类型转字符串）
        push_messages_job.delay(json_safe(pending), json_safe(notice_message))
        return notify_obj

    @classmethod
    def store_notice(
        cls,
        users: list | QuerySet,
        title: str,
        message: str,
        notice_type: int,
        level: MessageContent.LevelChoices,
        extra_json: dict | None = None,
    ):
        """仅落库（不推送）：供已有实时投递链路的调用方做「持久化兜底」。

        与 ``base_notify`` 的差别是跳过 ``push_notice_messages``——调用方自身
        负责实时推送（如聊天提醒的 WS 投递），避免同一提醒双通道重复推送。
        """
        recipients = users if isinstance(users, (QuerySet, list)) else [users]
        with transaction.atomic():
            notify_obj = MessageContent.objects.create(
                title=title, publish=True, message=message, level=level, notice_type=notice_type, extra_json=extra_json
            )
            notify_obj.notice_user.set(recipients)
        return notify_obj

    @classmethod
    def base_notify(
        cls,
        users: list | QuerySet,
        title: str,
        message: str,
        notice_type: int,
        level: MessageContent.LevelChoices,
        extra_json: dict | None = None,
    ):
        notify_obj = cls.store_notice(users, title, message, notice_type, level, extra_json)
        recipients = users if isinstance(users, (QuerySet, list)) else [users]
        cls.push_notice_messages(
            notify_obj, [user.pk for user in recipients] if isinstance(recipients[0], UserInfo) else recipients
        )
        return notify_obj

    @classmethod
    def notify_success(
        cls, users: list | QuerySet, title: str, message: str, notice_type: int = SYSTEM, extra_json: dict | None = None
    ):
        # type ignore[arg-type]：Choices 成员同上（元类在运行期转为枚举成员）
        return cls.base_notify(
            users,
            title,
            message,
            notice_type,
            MessageContent.LevelChoices.SUCCESS,  # type: ignore[arg-type]  # Choices 元类（运行期为枚举成员）
            extra_json,
        )

    @classmethod
    def notify_info(
        cls, users: list | QuerySet, title: str, message: str, notice_type: int = SYSTEM, extra_json: dict | None = None
    ):
        # type ignore[arg-type]：Choices 成员同上（元类在运行期转为枚举成员）
        return cls.base_notify(
            users,
            title,
            message,
            notice_type,
            MessageContent.LevelChoices.PRIMARY,  # type: ignore[arg-type]  # Choices 元类（运行期为枚举成员）
            extra_json,
        )

    @classmethod
    def notify_error(
        cls, users: list | QuerySet, title: str, message: str, notice_type: int = SYSTEM, extra_json: dict | None = None
    ):
        # type ignore[arg-type]：Choices 成员同上（元类在运行期转为枚举成员）
        return cls.base_notify(
            users,
            title,
            message,
            notice_type,
            MessageContent.LevelChoices.DANGER,  # type: ignore[arg-type]  # Choices 元类（运行期为枚举成员）
            extra_json,
        )
