#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : message
# author : ly_13
# date : 9/15/2024
import os.path
from typing import Any

from django.conf import settings
from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from common.core.fields import DictChoiceField
from common.core.filter import get_filter_queryset
from common.core.serializers import BaseModelSerializer
from common.utils import get_logger
from file.services import UploadFile
from identity.services import UserInfo
from notifications.models import MessageContent, MessageUserRead

logger = get_logger(__name__)

# Markdown 正文长度上限：入库前会中和 HTML（sanitize_markdown），长度校验防体积滥用
MAX_MARKDOWN_LENGTH = 100_000


class NoticeMessageSerializer(BaseModelSerializer):
    # 通知级别字典化（notice_level）：管理员可维护文案/颜色，默认项随种子下发
    # （value 本身即 el-text 类型色，color 作为前端渲染的首选色源）；
    # merge 保证字典未配置/项被清时回退模型枚举
    level = DictChoiceField(
        dict_code="notice_level",
        fallback_choices=MessageContent.LevelChoices.choices,
        merge_fallback=True,
    )

    class Meta:
        model = MessageContent
        fields = [
            "pk",
            "title",
            "level",
            "publish",
            "notice_type",
            "notice_user",
            "notice_dept",
            "notice_role",
            "notice_post",
            "message_format",
            "message",
            "created_time",
            "user_count",
            "read_user_count",
            "extra_json",
            "files",
            "deleted_at",
        ]

        table_fields = ["pk", "title", "notice_type", "read_user_count", "publish", "created_time"]
        extra_kwargs = {
            "extra_json": {"read_only": True},
            "deleted_at": {"read_only": True},
            "notice_user": {
                "attrs": ["pk", "username"],
                "many": True,
                "format": "{username}",
                "read_only": False,
                "input_type": "api-search-user",
                "queryset": UserInfo.objects,
            },
            "notice_dept": {"attrs": ["pk", "name"], "many": True, "format": "{name}", "input_type": "api-search-dept"},
            "notice_role": {"attrs": ["pk", "name"], "many": True, "format": "{name}", "input_type": "api-search-role"},
            "notice_post": {"attrs": ["pk", "name"], "many": True, "format": "{name}", "input_type": "api-search-post"},
        }

    files = serializers.JSONField(write_only=True, label=_("Uploaded attachments"))
    user_count = serializers.SerializerMethodField(read_only=True, label=_("User count"))
    read_user_count = serializers.SerializerMethodField(read_only=True, label=_("Read user count"))

    def _requested_format(self) -> Any:
        """本次写入的正文格式（对象形态 ``{value,label}`` 与标量均兼容）。

        - 新建未传：模型默认 ``html``；
        - 更新未传：沿用实例当前格式，消息正文按既有格式校验（避免把 Markdown
          公告的正文误当 HTML 净化）。
        """
        initial = getattr(self, "initial_data", None) or {}
        value = initial.get("message_format")
        if isinstance(value, dict):
            value = value.get("value")
        if value:
            return value
        if self.instance is not None:
            return self.instance.message_format
        return MessageContent.MessageFormat.HTML

    def validate_message(self, value: Any) -> Any:
        # 公告/站内信内容按格式分流：
        # - html：入库前按白名单净化（前端 NoticeShow 以 v-html 渲染，防存储型 XSS）；
        # - markdown：剥离源码中的 HTML 标签（保留 #/**/表格/围栏代码块），
        #   任何客户端渲染器都拿不到可执行标签，不再只依赖 Web 端 html:false。
        from common.utils.sanitize import sanitize_markdown, sanitize_rich_text

        if self._requested_format() == MessageContent.MessageFormat.MARKDOWN:
            if len(value or "") > MAX_MARKDOWN_LENGTH:
                raise ValidationError(_("The Markdown content is too long"))
            return sanitize_markdown(value)
        return sanitize_rich_text(value)

    @extend_schema_field(serializers.IntegerField)
    def get_read_user_count(self, obj: Any) -> Any:
        if obj.notice_type in MessageContent.get_user_choices():
            # 整页一次聚合查询，替代每条消息一次 COUNT
            counts = self._page_read_counts(obj)
            if counts is None:
                return MessageUserRead.objects.filter(
                    notice=obj, unread=False, owner_id__in=obj.notice_user.all()
                ).count()
            return counts.get(obj.pk, 0)

        elif obj.notice_type in MessageContent.get_notice_choices():
            # 公告类已读人数口径即 notice_user 关联数（DEPT/ROLE/POST 不直填接收人时为 0），
            # 复用 notice_user 整页聚合，替代逐行 count 回表
            counts = self._page_notice_user_counts(obj)
            if counts is None:
                return obj.notice_user.count()
            return counts.get(obj.pk, 0)

        return 0

    @extend_schema_field(serializers.IntegerField)
    def get_user_count(self, obj: Any) -> Any:
        if obj.notice_type in (
            MessageContent.NoticeChoices.DEPT,
            MessageContent.NoticeChoices.ROLE,
            MessageContent.NoticeChoices.POST,
        ):
            # 部门/角色/岗位按目标人群展开，按类型整页聚合，替代逐行 count 回表
            counts = self._page_target_user_counts(obj)
            if counts is not None:
                return counts.get(obj.pk, 0)
        else:
            # 以 notice_user 表达接收人的类型（USER/SYSTEM/NOTICE）：整页一次聚合查询
            counts = self._page_notice_user_counts(obj)
            if counts is not None:
                return counts.get(obj.pk, 0)
        # 非整页序列化（单对象/嵌套）退回逐对象查询
        if obj.notice_type == MessageContent.NoticeChoices.DEPT:
            return UserInfo.objects.filter(dept__in=obj.notice_dept.all()).count()
        if obj.notice_type == MessageContent.NoticeChoices.ROLE:
            # 与列表可见域同口径：直挂角色 ∪ 所属启用部门挂角色
            roles = obj.notice_role.filter(is_active=True, deleted_at__isnull=True)
            return UserInfo.objects.filter(Q(roles__in=roles) | Q(dept__is_active=True, dept__roles__in=roles)).count()
        if obj.notice_type == MessageContent.NoticeChoices.POST:
            # 仅启用岗位的在岗用户（与审批人解析同口径）
            return (
                UserInfo.objects.filter(is_active=True, posts__in=obj.notice_post.all())
                .filter(posts__is_active=True, posts__deleted_at__isnull=True)
                .distinct()
                .count()
            )
        return obj.notice_user.count()

    def _page_notice_user_counts(self, obj: Any) -> Any:
        """整页消息的 notice_user 关联人数，一次聚合查询得到 {notice_pk: user_count}。

        同时服务两个字段：user_count（以 notice_user 表达接收人的 USER/SYSTEM/NOTICE）
        与公告类 read_user_count（口径即 notice_user 关联数）。软删除接收人与
        notice_user 关联管理器同样排除——join 不经过 related manager，需显式过滤。
        非整页序列化（单对象/嵌套）返回 None，退回逐对象查询。
        """
        # 必须传 obj：get_page_instances 依赖 default 的类型判定当前是否为整页序列化，
        # 不传参（default=None）时恒返回 []，批量化会静默失效退回逐行查询
        page = self.get_page_instances(obj)
        if not page:
            return None
        cached = self.context.get("_page_notice_user_counts")
        if cached is not None:
            return cached
        recipients = self._page_recipient_counts(obj)
        if recipients is None:
            return None
        counts = {pk: values["user"] for pk, values in recipients.items()}
        self.context["_page_notice_user_counts"] = counts
        return counts

    def _page_recipient_counts(self, obj: Any) -> Any:
        """整页「接收人数 + 已读数」一次聚合成 {notice_pk: {"user": n, "read": m}}。

        两个口径都走 notice_user through 表：合并后 USER/NOTICE 列表每页只发一条
        聚合查询（历史上 user_count 与 read_user_count 各一条）；软删除接收人
        在 join 侧显式排除，与逐对象查询一致。
        """
        page = self.get_page_instances(obj)
        if not page:
            return None
        cached = self.context.get("_page_recipient_counts")
        if cached is not None:
            return cached
        # all_objects：回收站列表序列化软删除消息时计数不归零（与逐对象查询同口径）
        rows = (
            MessageContent.all_objects.filter(pk__in=[item.pk for item in page])
            .annotate(
                user_total=Count("notice_user", filter=Q(notice_user__deleted_at__isnull=True), distinct=True),
                read_total=Count(
                    "notice_user",
                    filter=Q(messageuserread__unread=False, notice_user__deleted_at__isnull=True),
                    distinct=True,
                ),
            )
            .values_list("pk", "user_total", "read_total")
        )
        counts = {pk: {"user": user_total, "read": read_total} for pk, user_total, read_total in rows}
        self.context["_page_recipient_counts"] = counts
        return counts

    def _page_target_user_counts(self, obj: Any) -> Any:
        """整页 DEPT/ROLE/POST 消息按目标人群展开的人数，每类一次聚合查询得到 {notice_pk: user_count}。

        口径与逐对象查询一致：DEPT 为目标部门下的在册用户；ROLE 沿角色展开的关联行数
        （与 `UserInfo.objects.filter(roles__in=...).count()` 相同，一人持多命中角色会计多次）；
        POST 仅启用岗位的在岗用户去重（与审批人解析同口径）。软删除的角色/岗位/用户
        与逐对象链路（M2M 关联管理器过滤软删除行）同样排除，join 侧需显式补齐条件。
        非整页序列化（单对象/嵌套）返回 None，退回逐对象查询。
        """
        if obj.notice_type not in (
            MessageContent.NoticeChoices.DEPT,
            MessageContent.NoticeChoices.ROLE,
            MessageContent.NoticeChoices.POST,
        ):
            return None
        # 同上：必须传 obj，否则批量化静默失效
        page = [item for item in self.get_page_instances(obj) if item.notice_type == obj.notice_type]
        if not page:
            return None
        cache_key = f"_page_target_user_counts_{obj.notice_type}"
        cached = self.context.get(cache_key)
        if cached is not None:
            return cached

        def _build() -> dict[Any, int]:
            queryset = MessageContent.all_objects.filter(pk__in=[item.pk for item in page])
            if obj.notice_type == MessageContent.NoticeChoices.DEPT:
                # dept 为 FK，每名用户至多命中一次
                queryset = queryset.annotate(
                    user_total=Count(
                        "notice_dept__dept_query",
                        filter=Q(notice_dept__dept_query__deleted_at__isnull=True),
                        distinct=True,
                    )
                )
            elif obj.notice_type == MessageContent.NoticeChoices.ROLE:
                queryset = queryset.annotate(
                    user_total=Count(
                        "notice_role__userinfo",
                        filter=Q(
                            notice_role__is_active=True,
                            notice_role__deleted_at__isnull=True,
                            notice_role__userinfo__deleted_at__isnull=True,
                        ),
                        distinct=True,
                    )
                    + Count(
                        "notice_role__deptinfo__dept_query",
                        filter=Q(
                            notice_role__is_active=True,
                            notice_role__deleted_at__isnull=True,
                            notice_role__deptinfo__is_active=True,
                            notice_role__deptinfo__dept_query__deleted_at__isnull=True,
                        ),
                        distinct=True,
                    )
                )
            else:
                queryset = queryset.annotate(
                    user_total=Count(
                        "notice_post__post_query",
                        filter=Q(
                            notice_post__deleted_at__isnull=True,
                            notice_post__post_query__deleted_at__isnull=True,
                            notice_post__post_query__is_active=True,
                            notice_post__post_query__posts__is_active=True,
                            notice_post__post_query__posts__deleted_at__isnull=True,
                        ),
                        distinct=True,
                    )
                )
            return dict(queryset.values_list("pk", "user_total"))

        counts = _build()
        self.context[cache_key] = counts
        return counts

    def _page_read_counts(self, obj: Any) -> Any:
        """整页消息的已读人数，一次聚合查询得到 {notice_pk: read_count}。

        仅对"按用户通知"类型有效；非整页序列化（单对象/嵌套）返回 None，退回逐对象查询。
        """
        # 同上：必须传 obj，否则 get_page_instances 恒返回 []，批量化静默失效
        page = self.get_page_instances(obj)
        page = [item for item in page if item.notice_type in MessageContent.get_user_choices()]
        if not page:
            return None
        cached = self.context.get("_page_read_counts")
        if cached is not None:
            return cached
        # 已读数与 notice_user 人数同源（through 表 messageuserread.unread=False），
        # 由 _page_recipient_counts 一次聚合产出，避免每页多一条 COUNT。
        recipients = self._page_recipient_counts(obj)
        if recipients is None:
            return None
        counts = {item.pk: recipients.get(item.pk, {}).get("read", 0) for item in page}
        self.context["_page_read_counts"] = counts
        return counts

    def validate_notice_type(self, val: Any) -> Any:
        if self.request.method == "POST":
            if val == MessageContent.NoticeChoices.NOTICE:
                raise ValidationError(_("Parameter error. System announcement cannot be created"))
            if val == MessageContent.NoticeChoices.SYSTEM:
                # 系统通知由后端（SiteMessageUtil 经 ORM）生成、接收人由代码指定且创建后不可编辑，
                # 不开放经此接口手工创建，避免产出接收对象不受控又无法修正的记录
                raise ValidationError(_("Parameter error. System notification cannot be created"))
        return val

    def validate_files(self, value: Any) -> Any:
        # 前端仅提交「已上传文件路径字符串数组」（富文本编辑器收集的链接）；
        # 形状畸变（非数组 / 元素非字符串）会让路径解析拿到脏输入——字符串被逐字符
        # 拆分、对象直接 AttributeError（落 500）或误清空附件，统一按参数错误拒绝
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValidationError(_("The attachments must be a list of file paths"))
        return value

    def validate(self, attrs: Any) -> Any:
        # 格式创建后不可修改：同一份正文换格式会改变解释方式（HTML 源码按 Markdown
        # 渲染会丢排版，反之亦然），故编辑期直接拒绝而不是静默覆盖
        if self.instance is not None and self._requested_format() != self.instance.message_format:
            raise ValidationError({"message_format": _("The message format cannot be modified after creation")})

        notice_type = attrs.get("notice_type")

        if notice_type == MessageContent.NoticeChoices.ROLE:
            attrs.pop("notice_dept", None)
            attrs.pop("notice_user", None)
            attrs.pop("notice_post", None)
            if not attrs.get("notice_role"):
                raise ValidationError(_("The notice role cannot be null"))

        if notice_type == MessageContent.NoticeChoices.DEPT:
            attrs.pop("notice_user", None)
            attrs.pop("notice_role", None)
            attrs.pop("notice_post", None)
            if not attrs.get("notice_dept"):
                raise ValidationError(_("The notice department cannot be null"))

        if notice_type == MessageContent.NoticeChoices.POST:
            attrs.pop("notice_user", None)
            attrs.pop("notice_dept", None)
            attrs.pop("notice_role", None)
            if not attrs.get("notice_post"):
                raise ValidationError(_("The notice post cannot be null"))

        if notice_type == MessageContent.NoticeChoices.USER:
            attrs.pop("notice_role", None)
            attrs.pop("notice_dept", None)
            attrs.pop("notice_post", None)
            if not attrs.get("notice_user"):
                raise ValidationError(_("The notice user cannot be null"))

        files = attrs.get("files")
        if files is not None:
            del attrs["files"]
            queryset = UploadFile.objects.filter(
                filepath__in=[file.split(os.path.join("/", settings.MEDIA_URL))[-1] for file in files]
            )
            attrs["file"] = get_filter_queryset(queryset, self.request.user).all()
        return attrs

    def update(self, instance: Any, validated_data: Any) -> Any:
        validated_data.pop("notice_type", None)  # 不能修改消息类型
        if instance.notice_type == MessageContent.NoticeChoices.SYSTEM:  # 系统通知不允许修改
            raise ValidationError(_("The system notice cannot be update"))
        return super().update(instance, validated_data)


class AnnouncementSerializer(NoticeMessageSerializer):
    def validate_notice_type(self, val: Any) -> Any:
        if MessageContent.NoticeChoices.NOTICE == val:
            return val
        raise ValidationError(_("Parameter error"))


class NoticePublishSerializer(serializers.Serializer):
    """公告发布状态切换载荷：布尔强校验（缺 key / 错型返回 400，不再落库异常）。"""

    publish = serializers.BooleanField(label=_("Publish"))


class NoticeUserReadStateSerializer(serializers.Serializer):
    """已读状态切换载荷：未传 unread 沿用既有口径（标记为未读）。"""

    unread = serializers.BooleanField(required=False, default=True, label=_("Unread"))


class NoticeUserReadMessageSerializer(BaseModelSerializer):
    class Meta:
        model = MessageUserRead
        fields = ["pk", "notice_info", "notice_type", "owner", "unread", "updated_time"]
        read_only_fields = [x.name for x in MessageUserRead._meta.fields]
        extra_kwargs = {"owner": {"attrs": ["pk", "username"], "read_only": True}}

    notice_type = serializers.CharField(source="notice.get_notice_type_display", read_only=True, label=_("Notice type"))

    notice_info = NoticeMessageSerializer(
        fields=["pk", "level", "title", "notice_type", "message_format", "message", "publish"],
        read_only=True,
        source="notice",
        label=_("Notice message"),
    )


class UserNoticeSerializer(BaseModelSerializer):
    ignore_field_permission = True

    # 同公告管理口径：级别字典化（用户通知页标题色/文案），只读资源
    level = DictChoiceField(
        dict_code="notice_level",
        fallback_choices=MessageContent.LevelChoices.choices,
        merge_fallback=True,
        read_only=True,
    )

    class Meta:
        model = MessageContent
        fields = ["pk", "level", "title", "message_format", "message", "created_time", "unread", "notice_type"]
        table_fields = ["pk", "title", "unread", "notice_type", "created_time"]
        read_only_fields = ["pk", "notice_user", "notice_type"]

    unread = serializers.SerializerMethodField(label=_("Unread"))

    @extend_schema_field(serializers.BooleanField)
    def get_unread(self, obj: Any) -> Any:
        # 整页一次查询当前用户的已读记录，查询数与消息条数解耦。
        # 语义与旧实现逐字段对齐（owner + notice 唯一，每条消息至多一行）：
        # - USER/SYSTEM：存在 unread=True 的记录 -> 未读；
        # - NOTICE/DEPT/ROLE：不存在任何记录 -> 未读（公告创建时不生成 read 行）。
        owner = self.context.get("request").user
        cache_key = f"_user_read_map_{owner.pk}"
        read_map = self.context.get(cache_key)
        if read_map is None:
            read_map = {}
            page = self.get_page_instances(obj)
            rows = MessageUserRead.objects.filter(owner=owner, notice_id__in=[item.pk for item in page]).values_list(
                "notice_id", "unread"
            )
            for notice_id, unread in rows:
                has_any_row, has_unread_row = read_map.get(notice_id, (False, False))
                read_map[notice_id] = (True, has_unread_row or unread)
            self.context[cache_key] = read_map
        has_any_row, has_unread_row = read_map.get(obj.pk, (False, False))
        if obj.notice_type in MessageContent.get_user_choices():
            return has_unread_row
        elif obj.notice_type in MessageContent.get_notice_choices():
            return not has_any_row
        return True
