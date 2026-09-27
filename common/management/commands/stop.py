from .services.command import Action, BaseActionCommand


class Command(BaseActionCommand):
    help = "Stop services"
    action = Action.stop.value  # type: ignore[attr-defined]  # TextChoices 成员的 value 由元类动态生成
