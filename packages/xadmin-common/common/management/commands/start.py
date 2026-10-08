from .services.command import Action, BaseActionCommand


class Command(BaseActionCommand):
    help = "Start services"
    action = Action.start.value  # type: ignore[attr-defined]  # TextChoices 成员的 value 由元类动态生成
