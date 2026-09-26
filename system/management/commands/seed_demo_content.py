#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""内容类演示数据：通知公告 / 聊天室 / 知识库 / 文件中心 / 审批委托 / Webhook / 开放平台应用。

与其余 seed_demo_* 命令的分工：定义类数据随 ``load_init_json`` 作为内置示例灌入
（loadjson/*.json）；本命令补充**实例类内容数据**，让各功能页面开箱有内容可看：

- 通知公告：1 条已发布公告（全员可见）+ 1 条草稿（管理端可见）；
- 聊天室：公共房间历史消息（用户消息 + 系统提示）；
- 知识库：2 篇上传文档（走 ``upsert_upload_document``，分块/检索链路完整）；
- 文件中心：2 个真实小文件（落盘，可预览/下载）；
- 审批委托：演示申请人 → 演示审批人的一条生效委托（依赖 seed_demo_flows 的演示用户；
  委托人必须是演示账号——超管作为委托人会让超管的待办被整体转走）；
- Webhook：1 条订阅 + 2 条投递审计（成功 / 重试中）；
- 开放平台：1 个演示应用（scopes/配额齐备，密钥只存哈希）。

幂等：按固定标识（标题前缀 / 幂等键 / 固定 pk / 唯一名 / 固定文档名）创建，重复执行不重复；
清理：``--reset`` 与 ``--clean-only`` 按同一批标识物理解除（all_objects 绕过软删）。

用法：

    python manage.py seed_demo_content                 # 幂等生成
    python manage.py seed_demo_content --reset         # 先清理本命令数据再生成
    python manage.py seed_demo_content --clean-only    # 只清理（seed_demo_clean 编排调用）
"""

import hashlib
from datetime import timedelta

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils import timezone

from ai.models.ai import AiKnowledgeDocument
from ai.utils.ai import remove_chunks, upsert_upload_document
from approval.models.approval import ApprovalDelegation
from message import chat as chat_service
from message.models import ChatMessage
from notifications.models import MessageContent
from system.models import UserInfo
from system.models.token import ApiApplication
from system.models.upload import UploadFile
from system.models.webhook import WebhookDelivery, WebhookSubscription
from system.utils.webhook import encrypt_secret

# ---- 幂等 / 清理标识（演示数据统一带可识别的固定标识） ----
NOTICE_TITLE_PREFIX = "演示："
CHAT_CLIENT_PREFIX = "demo-content-"
KNOWLEDGE_NAME_PREFIX = "演示-"
UPLOAD_FILENAME_PREFIX = "演示-"
DELEGATION_PK = "6eed0009-0000-4000-8000-000000000001"
WEBHOOK_NAME = "演示-审批事件订阅"
DELIVERY_PKS = [
    "6eed000b-0000-4000-8000-000000000001",
    "6eed000b-0000-4000-8000-000000000002",
]
APP_NAME = "演示-开放平台应用"

# (标题, 级别, 富文本, 是否发布)；已发布公告会触发全员站内信推送（演示环境预期行为）
NOTICE_PLAN = [
    (
        "欢迎使用 xAdmin 演示环境",
        MessageContent.LevelChoices.PRIMARY,
        "<p>本环境已预置组织、审批、请假、表单、数据分析与内容等演示数据，可直接体验各功能页面。</p>",
        True,
    ),
    (
        "（草稿）系统升级维护预告",
        MessageContent.LevelChoices.DANGER,
        "<p>计划于本周六 22:00 进行例行维护，预计 30 分钟。本公告尚未发布，仅管理端可见。</p>",
        False,
    ),
]

KNOWLEDGE_DOCS = [
    (
        "演示-新员工入职指南",
        """# 新员工入职指南

## 报到流程
1. 到行政部领取办公设备并登记工位；
2. 登录 xAdmin，完善个人资料并绑定邮箱；
3. 由直属主管分配部门与角色权限。

## 常用功能
- 请假申请：系统管理 → 请假申请，提交后由直属主管审批；
- 动态表单：表单采集 → 我要填报，可提交入职登记等信息；
- 审批中心：我的申请与待办审批集中查看。
""",
    ),
    (
        "演示-费用报销说明",
        """# 费用报销说明

## 报销标准
- 差旅交通：实报实销，需附行程单；
- 市内交通：单次不超过 100 元；
- 招待费用：需提前申请，注明事由与对象。

## 审批路径
金额小于 1000 元由直属主管审批；大于等于 1000 元需财务复核后由总经理审批。
""",
    ),
]

# (文件名, MIME, 内容)
UPLOAD_FILES = [
    (
        "演示-产品需求说明.md",
        "text/markdown",
        "# 产品需求说明（演示文件）\n\n本文件由 seed_demo_content 生成，用于演示文件中心的分类/预览/下载能力。\n",
    ),
    (
        "演示-会议纪要.txt",
        "text/plain",
        "演示周会纪要\n1. 审批流新增财务复核节点；\n2. 数据分析看板补充月度趋势卡；\n3. 下周进行上线前全量回归。\n",
    ),
]


class Command(BaseCommand):
    help = "生成内容类演示数据（通知公告/聊天室/知识库/文件中心/审批委托/Webhook/开放平台应用）"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="先清理本命令生成的演示数据再生成")
        parser.add_argument("--clean-only", action="store_true", help="只清理，不生成（seed_demo_clean 编排调用）")

    # ---------------------------------------------------------------- 入口

    def handle(self, *args, **options):
        if options["reset"] or options["clean_only"]:
            self._clean()
            if options["clean_only"]:
                return

        admin = UserInfo.objects.filter(is_superuser=True, is_active=True).order_by("pk").first()
        if admin is None:
            self.stdout.write(self.style.WARNING("no active superuser found; seed_demo_content skipped"))
            return

        self._create_notices(admin)
        self._create_chat(admin)
        self._create_knowledge(admin)
        self._create_files(admin)
        self._create_delegation(admin)
        self._create_webhook(admin)
        self._create_api_app(admin)
        self.stdout.write(self.style.SUCCESS("seed_demo_content done"))

    # ---------------------------------------------------------------- 清理

    def _clean(self):
        removed = MessageContent.all_objects.filter(title__startswith=NOTICE_TITLE_PREFIX).delete()[0]
        self.stdout.write(f"removed demo notices: {removed}")

        removed = ChatMessage.objects.filter(client_msg_id__startswith=CHAT_CLIENT_PREFIX).delete()[0]
        self.stdout.write(f"removed demo chat messages: {removed}")

        docs = AiKnowledgeDocument.objects.filter(path__startswith=f"upload/{KNOWLEDGE_NAME_PREFIX}")
        doc_count = docs.count()
        for doc in docs:
            remove_chunks(doc.path)
        docs.delete()
        self.stdout.write(f"removed demo knowledge documents: {doc_count}")

        removed = UploadFile.all_objects.filter(filename__startswith=UPLOAD_FILENAME_PREFIX, is_upload=True).delete()[0]
        self.stdout.write(f"removed demo upload files: {removed}")

        removed = ApprovalDelegation.objects.filter(pk=DELEGATION_PK).delete()[0]
        self.stdout.write(f"removed demo approval delegation: {removed}")

        removed = WebhookSubscription.objects.filter(name=WEBHOOK_NAME).delete()[0]
        self.stdout.write(f"removed demo webhook subscription (deliveries cascade): {removed}")

        removed = ApiApplication.objects.filter(name=APP_NAME).delete()[0]
        self.stdout.write(f"removed demo api application: {removed}")

    # ---------------------------------------------------------------- 各内容块

    def _create_notices(self, admin: UserInfo):
        created = 0
        for index, (title, level, message, publish) in enumerate(NOTICE_PLAN):
            full_title = f"{NOTICE_TITLE_PREFIX}{title}"
            if MessageContent.objects.filter(title=full_title).exists():
                continue
            # publish=True 的公告会经 post_save 信号推送到全员站内信（演示预期效果）
            notice = MessageContent.objects.create(
                notice_type=MessageContent.NoticeChoices.NOTICE,
                level=level,
                title=full_title,
                message=message,
                publish=publish,
                creator=admin,
                modifier=admin,
            )
            stamp = timezone.now() - timedelta(days=index + 1)
            MessageContent.objects.filter(pk=notice.pk).update(created_time=stamp, updated_time=stamp)
            created += 1
        self.stdout.write(f"demo notices ready: {created}")

    def _create_chat(self, admin: UserInfo):
        room = chat_service.get_public_room()
        if room is None:
            self.stdout.write(self.style.WARNING("public chat room unavailable; demo chat messages skipped"))
            return
        applier = UserInfo.objects.filter(username="demo_flow_lily", is_active=True).first()
        plan = [
            (f"{CHAT_CLIENT_PREFIX}1", admin, "大家好，欢迎使用 xAdmin 演示环境～", 240),
            (f"{CHAT_CLIENT_PREFIX}2", applier, "收到，我先去体验一下审批流程。", 200),
            (f"{CHAT_CLIENT_PREFIX}3", None, "提示：演示数据可用 seed_demo_clean 一键清理。", 150),
        ]
        created = 0
        for client_id, sender, content, minutes_ago in plan:
            if sender is None and client_id.endswith("2"):
                continue
            if ChatMessage.objects.filter(client_msg_id=client_id).exists():
                continue
            message_type = ChatMessage.MessageType.SYSTEM if sender is None else ChatMessage.MessageType.TEXT
            message, is_created = chat_service.create_message(
                room, sender, content, message_type=message_type, client_msg_id=client_id
            )
            if is_created:
                stamp = timezone.now() - timedelta(minutes=minutes_ago)
                ChatMessage.objects.filter(pk=message.pk).update(created_time=stamp, updated_time=stamp)
                created += 1
        self.stdout.write(f"demo chat messages ready: {created}")

    def _create_knowledge(self, admin: UserInfo):
        created = 0
        for name, content in KNOWLEDGE_DOCS:
            path = f"upload/{name}.md"
            if AiKnowledgeDocument.objects.filter(path=path).exists():
                continue
            _doc, is_created = upsert_upload_document(name, content, creator=admin)
            if is_created:
                created += 1
        self.stdout.write(f"demo knowledge documents ready: {created}")

    def _create_files(self, admin: UserInfo):
        created = 0
        for filename, mime_type, content in UPLOAD_FILES:
            if UploadFile.all_objects.filter(filename=filename, creator=admin).exists():
                continue
            payload = content.encode("utf-8")
            upload = UploadFile(
                filename=filename,
                mime_type=mime_type,
                filesize=len(payload),
                md5sum=hashlib.md5(payload).hexdigest(),
                is_upload=True,
                is_tmp=False,
                creator=admin,
                modifier=admin,
            )
            upload.filepath.save(filename, ContentFile(payload), save=False)
            upload.save()
            created += 1
        self.stdout.write(f"demo upload files ready: {created}")

    def _create_delegation(self, admin: UserInfo):
        """演示委托：委托双方一律用演示账号，**禁止把超管作为委托人**。

        委托语义是「待办归属替换」——节点解析到 xadmin 时任务会被整体转给代理人。
        历史版本曾以超管为委托人（委托给不可登录的演示账号），导致超管在所有流程的
        待办恒为空、且任务落到无人可登录的账号上永久卡死。委托功能本身仍完整演示。
        """
        delegator = UserInfo.objects.filter(username="demo_flow_lily", is_active=True).first()
        delegate = UserInfo.objects.filter(username="demo_flow_chen", is_active=True).first()
        if delegator is None or delegate is None:
            self.stdout.write(
                self.style.WARNING(
                    "demo_flow_lily/demo_flow_chen missing (run seed_demo_flows first); delegation skipped"
                )
            )
            return
        now = timezone.now()
        ApprovalDelegation.objects.update_or_create(
            pk=DELEGATION_PK,
            defaults={
                "delegator": delegator,
                "delegate": delegate,
                "start_time": now - timedelta(hours=1),
                "end_time": now + timedelta(days=7),
                "flow_codes": [],
                "is_active": True,
                "remark": "演示：李莉出差期间由陈工代审",
                "creator": admin,
                "modifier": admin,
            },
        )
        self.stdout.write("demo approval delegation ready")

    def _create_webhook(self, admin: UserInfo):
        subscription, _created = WebhookSubscription.objects.update_or_create(
            name=WEBHOOK_NAME,
            defaults={
                "url": "https://example.com/xadmin/webhook",
                "secret": encrypt_secret("demo-webhook-secret"),
                "events": ["approval.submitted", "approval.approved"],
                "description": "演示订阅：审批单提交 / 通过时 POST 签名 JSON",
                "is_active": True,
                "creator": admin,
                "modifier": admin,
            },
        )
        if not WebhookDelivery.objects.filter(pk__in=DELIVERY_PKS).exists():
            now = timezone.now()
            WebhookDelivery.objects.create(
                pk=DELIVERY_PKS[0],
                subscription=subscription,
                event="approval.approved",
                payload={
                    "event": "approval.approved",
                    "schema_version": 1,
                    "occurred_at": now.isoformat(),
                    "data": {"title": "李莉的年假申请（演示）", "result": "approved"},
                },
                status=WebhookDelivery.Status.SUCCESS,
                attempt=1,
                response_code=200,
                response_body='{"ok": true}',
                duration=0.12,
                creator=admin,
                modifier=admin,
            )
            WebhookDelivery.objects.create(
                pk=DELIVERY_PKS[1],
                subscription=subscription,
                event="approval.submitted",
                payload={
                    "event": "approval.submitted",
                    "schema_version": 1,
                    "occurred_at": now.isoformat(),
                    "data": {"title": "李莉的请假申请（演示）", "applicant": "demo_flow_lily"},
                },
                status=WebhookDelivery.Status.FAILED,
                attempt=3,
                response_code=500,
                response_body="Internal Server Error",
                duration=2.5,
                next_retry_at=now + timedelta(minutes=10),
                creator=admin,
                modifier=admin,
            )
        self.stdout.write("demo webhook subscription ready")

    def _create_api_app(self, admin: UserInfo):
        if ApiApplication.objects.filter(name=APP_NAME).exists():
            self.stdout.write("demo api application already exists, skip")
            return
        # 延迟导入：避免模块加载期依赖视图层
        from system.views.open import build_callback_secret, build_client_credentials

        client_id, _raw_secret, secret_hash, secret_prefix = build_client_credentials()
        _callback_raw, callback_encrypted = build_callback_secret()
        ApiApplication.objects.create(
            name=APP_NAME,
            client_id=client_id,
            client_secret_hash=secret_hash,
            client_secret_prefix=secret_prefix,
            callback_secret_encrypted=callback_encrypted,
            scopes=["GET ^/api/system/user/?$", "GET ^/api/system/dept/?$"],
            callback_urls=["https://example.com/xadmin/callback"],
            rate_limit_per_minute=60,
            daily_quota=1000,
            is_active=True,
            creator=admin,
            modifier=admin,
        )
        # 明文密钥只存哈希：演示环境如需调用，请在「API 应用」页重置凭证获取新明文
        self.stdout.write("demo api application ready (secret stored as hash only)")
