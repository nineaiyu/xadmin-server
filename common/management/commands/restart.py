from .services.command import Action, BaseActionCommand


class Command(BaseActionCommand):
    help = "Restart services"
    action = Action.restart.value  # type: ignore[attr-defined]  # TextChoices 成员的 value 由元类动态生成
