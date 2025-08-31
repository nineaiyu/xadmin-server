import textwrap
import traceback
from functools import cached_property

from celery import shared_task
from django.utils.translation import gettext_lazy as _
from html2text import HTML2Text

from common.utils import get_logger
from common.utils.timezone import local_now
from notifications.backends import BACKEND
from notifications.models import SystemMsgSubscription, UserMsgSubscription
from system.models import UserInfo

logger = get_logger(__name__)
system_msgs = []
user_msgs = []


class MessageType(type):
    def __new__(cls, name, bases, attrs: dict):
        clz = type.__new__(cls, name, bases, attrs)

        if 'message_type_label' in attrs \
                and 'category' in attrs \
                and 'category_label' in attrs:
            message_type = clz.get_message_type()

            msg = {
                'message_type': message_type,
                'message_type_label': attrs['message_type_label'],
                'category': attrs['category'],
                'category_label': attrs['category_label'],
            }
            if issubclass(clz, SystemMessage):
                system_msgs.append(msg)
            elif issubclass(clz, UserMessage):
                user_msgs.append(msg)

        return clz


@shared_task(verbose_name=_('Publish the station message'))
def publish_task(receive_user_ids, backends_msg_mapper):
    Message.send_msg(receive_user_ids, backends_msg_mapper)


class Message(metaclass=MessageType):
    """
    这里封装了什么？
        封装不同消息的模板，提供统一的发送消息的接口
        - publish 该方法的实现与消息订阅的表结构有关
        - send_msg
    """
    message_type_label: str
    category: str
    category_label: str
    text_msg_ignore_links = True

    @classmethod
    def get_message_type(cls):
        return cls.__name__

    def publish_async(self):
        self.publish(is_async=True)

    @classmethod
    def gen_test_msg(cls):
        raise NotImplementedError

    def publish(self, is_async=False):
        raise NotImplementedError

    def get_backend_msg_mapper(self, backends):
        backends = set(backends)
        backends.add(BACKEND.SITE_MSG)  # 站内信必须发
        backends_msg_mapper = {}
        for backend in backends:
            backend = BACKEND(backend)
            if not backend.is_enable:
                continue
            get_msg_method = getattr(self, f'get_{backend}_msg', self.get_common_msg)
            msg = get_msg_method()
            backends_msg_mapper[backend] = msg
        return backends_msg_mapper

    @staticmethod
    def send_msg(receive_user_ids, backends_msg_mapper):
        for backend, msg in backends_msg_mapper.items():
            try:
                backend = BACKEND(backend)
                client = backend.client()
                users = UserInfo.objects.filter(id__in=receive_user_ids).all()
                client.send_msg(users, **msg)
            except NotImplementedError:
                continue
            except:
                traceback.print_exc()

    @classmethod
    def send_test_msg(cls):
        msg = cls.gen_test_msg()
        if not msg:
            return

        from system.models import UserInfo
        users = UserInfo.objects.filter(is_superuser=True)
        backends = []
        msg.send_msg(users, backends)

    @staticmethod
    def get_common_msg() -> dict:
        return {'subject': '', 'message': ''}

    def get_html_msg(self) -> dict:
        return self.get_common_msg()

    @staticmethod
    def html_to_markdown(html_msg):
        h = HTML2Text()
        h.body_width = 0
        content = html_msg['message']
        html_msg['message'] = h.handle(content)
        return html_msg

    def get_markdown_msg(self) -> dict:
        return self.html_to_markdown(self.get_html_msg())

    def get_text_msg(self) -> dict:
        h = HTML2Text()
        h.body_width = 90
        msg = self.get_html_msg()
        content = msg['message']
        h.ignore_links = self.text_msg_ignore_links
        msg['message'] = h.handle(content)
        return msg

    @cached_property
    def common_msg(self) -> dict:
        return self.get_common_msg()

    @cached_property
    def text_msg(self) -> dict:
        msg = self.get_text_msg()
        return msg

    @cached_property
    def markdown_msg(self):
        return self.get_markdown_msg()

    @cached_property
    def html_msg(self) -> dict:
        msg = self.get_html_msg()
        return msg

    @cached_property
    def html_msg_with_sign(self):
        msg = self.get_html_msg()
        msg['message'] = textwrap.dedent("""
        {}
        <small>
        <br />
        —
        <br />
        {}
        </small>
        """).format(msg['message'], self.signature)
        return msg

    @cached_property
    def text_msg_with_sign(self):
        msg = self.get_text_msg()
        msg['message'] = textwrap.dedent("""
        {}
        —
        {}
        """).format(msg['message'], self.signature)
        return msg

    @cached_property
    def signature(self):
        return 'Xadmin Server'

    # --------------------------------------------------------------
    # 支持不同发送消息的方式定义自己的消息内容，比如有些支持 html 标签
    def get_dingtalk_msg(self) -> dict:
        # 钉钉相同的消息一天只能发一次，所以给所有消息添加基于时间的序号，使他们不相同
        message = self.markdown_msg['message']
        time = local_now().strftime('%Y-%m-%d %H:%M:%S')
        suffix = '\n{}: {}'.format(_('Time'), time)

        return {
            'subject': self.markdown_msg['subject'],
            'message': message + suffix
        }

    def get_email_msg(self) -> dict:
        return self.html_msg_with_sign

    def get_site_msg_msg(self) -> dict:
        return self.html_msg

    def get_sms_msg(self) -> dict:
        return self.text_msg_with_sign

    @classmethod
    def get_all_sub_messages(cls):
        def get_subclasses(cls):
            """returns all subclasses of argument, cls"""
            if issubclass(cls, type):
                subclasses = cls.__subclasses__(cls)
            else:
                subclasses = cls.__subclasses__()
            for subclass in subclasses:
                subclasses.extend(get_subclasses(subclass))
            return subclasses

        messages_cls = get_subclasses(cls)
        return messages_cls

    @classmethod
    def test_all_messages(cls, ding=True, wecom=False):
        messages_cls = cls.get_all_sub_messages()

        for _cls in messages_cls:
            try:
                _cls.send_test_msg(ding=ding, wecom=wecom)
            except NotImplementedError:
                continue


class SystemMessage(Message):
    def publish(self, is_async=False):
        subscription = SystemMsgSubscription.objects.get(
            message_type=self.get_message_type()
        )

        # 只发送当前有效后端
        receive_backends = subscription.receive_backends
        receive_backends = BACKEND.filter_enable_backends(receive_backends)

        receive_user_ids = subscription.users.values_list('pk', flat=True).all()
        if not receive_user_ids:
            logger.warning(f"send system msg failed. No receive users found for {self}")
            return
        backends_msg_mapper = self.get_backend_msg_mapper(receive_backends)
        if is_async:
            publish_task.delay(receive_user_ids, backends_msg_mapper)
        else:
            self.send_msg(receive_user_ids, backends_msg_mapper)

    @classmethod
    def post_insert_to_db(cls, subscription: SystemMsgSubscription):
        pass

    @classmethod
    def gen_test_msg(cls):
        raise NotImplementedError


class UserMessage(Message):
    user: UserInfo

    def __init__(self, user):
        self.user = user

    def publish(self, is_async=False):
        """
        发送消息到用户配置的接收方式上
        """
        subscription = UserMsgSubscription.objects.filter(user=self.user, message_type=self.get_message_type()).first()
        if subscription:
            receive_backends = subscription.receive_backends
            receive_backends = BACKEND.filter_enable_backends(receive_backends)
        else:
            receive_backends = []

        backends_msg_mapper = self.get_backend_msg_mapper(receive_backends)
        receive_user_ids = [self.user.id]
        if is_async:
            publish_task.delay(receive_user_ids, backends_msg_mapper)
        else:
            self.send_msg(receive_user_ids, backends_msg_mapper)

    @classmethod
    def get_test_user(cls):
        from system.models import UserInfo
        return UserInfo.objects.all().first()

    @classmethod
    def gen_test_msg(cls):
        raise NotImplementedError
