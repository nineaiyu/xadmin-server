from .services.command import Action, BaseActionCommand


class Command(BaseActionCommand):
    help = "Restart services"
    action = Action.restart.value
