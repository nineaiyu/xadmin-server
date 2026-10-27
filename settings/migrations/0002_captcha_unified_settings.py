import json

from django.db import migrations


def migrate_captcha_settings(apps, schema_editor):
    """把旧验证码模式/题型键迁移到统一协议键，并补图片回退默认值。

    - CAPTCHA_INTERACTIVE_TYPES -> CAPTCHA_ENABLED_TYPES（保留管理员已选题型）
    - 删除已废弃的 CAPTCHA_MODE
    - CAPTCHA_IMAGE_FALLBACK_ENABLED 缺失时补 true（已显式关闭的保持 false）
    """
    setting_model = apps.get_model("settings", "Setting")
    old = setting_model.objects.filter(name="CAPTCHA_INTERACTIVE_TYPES").first()
    new = setting_model.objects.filter(name="CAPTCHA_ENABLED_TYPES").first()
    if old is not None:
        if new is None:
            old.name = "CAPTCHA_ENABLED_TYPES"
            old.category = "captcha_interactive"
            old.save()
        else:
            old.delete()
    setting_model.objects.filter(name="CAPTCHA_MODE").delete()
    if not setting_model.objects.filter(name="CAPTCHA_IMAGE_FALLBACK_ENABLED").exists():
        setting_model.objects.create(
            name="CAPTCHA_IMAGE_FALLBACK_ENABLED",
            category="captcha_interactive",
            value=json.dumps(True),
        )


class Migration(migrations.Migration):
    dependencies = [
        ("settings", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(migrate_captcha_settings, migrations.RunPython.noop),
    ]
