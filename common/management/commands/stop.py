from .services.command import Action, BaseActionCommand


class Command(BaseActionCommand):
    help = "Stop services"
    action = Action.stop.value
