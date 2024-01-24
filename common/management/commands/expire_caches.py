from django.core.management.base import BaseCommand

from common.cache.storage import RedisCacheBase


class Command(BaseCommand):
    help = 'Expire caches'

    def add_arguments(self, parser):
        parser.add_argument(
            "args", metavar="cache key", nargs="+", help="please input cache key"
        )

    def handle(self, *args, **options):
        for key in args:
            if key.endswith("*"):
                RedisCacheBase(key).del_many()
            else:
                RedisCacheBase(key).del_storage_cache()
