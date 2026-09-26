from django.apps import AppConfig


class SystemConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "system"

    def ready(self):
        from . import signal_handler  # noqa
        from . import signal_task_execution  # noqa
        from .utils.dform_flow import register_approval_handlers  # noqa

        # 动态表单提交的「审批通过后自动落库」动作：进程启动时注册一次
        register_approval_handlers()

        # 数据字典解析器注册进框架层：DictChoiceField（common.core.fields）由此
        # 获得字典读取能力（带缓存 + 变更信号失效），common 保持零业务依赖
        from common.core.fields import register_dict_items_resolver

        from .utils.dict import get_dict_items

        register_dict_items_resolver(get_dict_items)

        super().ready()
