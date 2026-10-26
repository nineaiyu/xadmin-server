from django.apps import AppConfig


class CaptchaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "captcha"

    def ready(self) -> None:
        from . import checks  # noqa: F401  启动期字体检查注册
