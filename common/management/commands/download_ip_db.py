from django.core.management.base import BaseCommand

from common.management.commands.services.hands import download_ip_db


class Command(BaseCommand):
    help = 'Download IP database'

    def add_arguments(self, parser):
        parser.add_argument('-f', '--force', nargs="?", help="force download database", default=False, const=True)

    def handle(self, *args, **options):
        download_ip_db(force=options['force'])
