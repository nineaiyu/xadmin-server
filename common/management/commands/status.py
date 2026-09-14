from .services.command import Action, BaseActionCommand


class Command(BaseActionCommand):
    help = "Show services status"
    action = Action.status.value
