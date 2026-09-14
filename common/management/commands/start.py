from .services.command import Action, BaseActionCommand


class Command(BaseActionCommand):
    help = "Start services"
    action = Action.start.value
