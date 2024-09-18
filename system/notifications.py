from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from common.utils.request import get_request_ip, get_browser
from common.utils.timezone import local_now_display
from notifications.notifications import UserMessage


class DifferentCityLoginMessage(UserMessage):
    category = 'AccountSecurity'
    category_label = _('Account Security')
    message_type_label = _('Different city login reminder')

    def __init__(self, user, ip, city):
        self.ip = ip
        self.city = city
        super().__init__(user)

    def get_html_msg(self) -> dict:
        now = local_now_display()
        subject = _('Different city login reminder')
        context = dict(
            subject=subject,
            name=self.user.nickname,
            username=self.user.username,
            ip=self.ip,
            time=now,
            city=self.city,
        )
        message = render_to_string('notify/msg_different_city.html', context)
        return {
            'subject': subject,
            'message': message
        }

    @classmethod
    def gen_test_msg(cls):
        from system.models import UserInfo
        user = UserInfo.objects.first()
        ip = '8.8.8.8'
        city = '洛杉矶'
        return cls(user, ip, city)


class ResetPasswordSuccessMsg(UserMessage):
    category = 'AccountSecurity'
    category_label = _('Account Security')
    message_type_label = _('Reset password reminder')

    def __init__(self, user, request):
        super().__init__(user)
        self.ip_address = get_request_ip(request)
        self.browser = get_browser(request)

    def get_html_msg(self) -> dict:
        user = self.user

        subject = _('Reset password success')
        context = {
            'name': user.nickname,
            'username': user.username,
            'ip_address': self.ip_address,
            'browser': self.browser,
        }
        message = render_to_string('notify/msg_rest_password_success.html', context)
        return {
            'subject': subject,
            'message': message
        }

    @classmethod
    def gen_test_msg(cls):
        pass
