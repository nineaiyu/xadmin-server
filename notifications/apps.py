from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class NotificationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'notifications'
    verbose_name = _('App Notifications')

    def ready(self):
        from notifications.backends import BACKEND  # noqa
        from . import signal_handlers  # noqa
        from . import notifications  # noqa
        super().ready()
