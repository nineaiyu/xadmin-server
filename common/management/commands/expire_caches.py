from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Expire caches'

    def handle(self, *args, **options):
        ...
