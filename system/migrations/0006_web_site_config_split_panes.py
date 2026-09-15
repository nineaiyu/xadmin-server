from django.core.cache import cache
from django.db import migrations

CONFIG_CACHE_PATTERN = "config_*"


def _clear_config_cache():
    """WEB_SITE_CONFIG 的系统槽缓存不会随本迁移失效：RunPython 里
    apps.get_model 返回历史模型，row.save() 发出的 post_save 信号 sender
    与 signal_handler 注册的真实 SystemConfig 不匹配。显式清理（系统行与
    个人槽均在 config_* 前缀下，全清即可，下次读取自动回源 DB）。"""
    try:
        cache.delete_pattern(CONFIG_CACHE_PATTERN)
    except NotImplementedError:
        # 无 delete_pattern 的缓存后端退化为整清
        cache.clear()


def add_split_panes(apps, schema_editor):
    """存量库的 WEB_SITE_CONFIG 种子行没有 SplitPanes 键（分栏宽度配置，
    前端整包读改写）：自服务 PATCH 走 dict merge（只接受既有键），缺键会被
    静默丢弃。此处幂等补键；新库由 load_init_json 种子直接带出，跳过。"""
    SystemConfig = apps.get_model("system", "SystemConfig")
    for row in SystemConfig.objects.filter(key="WEB_SITE_CONFIG", is_active=True):
        value = row.value if isinstance(row.value, dict) else {}
        if "SplitPanes" in value:
            continue
        value["SplitPanes"] = {}
        row.value = value
        row.save(update_fields=["value"])
    _clear_config_cache()


def remove_split_panes(apps, schema_editor):
    SystemConfig = apps.get_model("system", "SystemConfig")
    for row in SystemConfig.objects.filter(key="WEB_SITE_CONFIG"):
        value = row.value if isinstance(row.value, dict) else {}
        if "SplitPanes" not in value:
            continue
        value.pop("SplitPanes")
        row.value = value
        row.save(update_fields=["value"])
    _clear_config_cache()


class Migration(migrations.Migration):
    dependencies = [
        ("system", "0005_aiprofile"),
    ]

    operations = [
        migrations.RunPython(add_split_panes, remove_split_panes),
    ]
