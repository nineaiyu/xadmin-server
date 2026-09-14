import sys

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Clean up expired captcha hashkeys."

    def handle(self, **options):
        from captcha.models import CaptchaStore

        verbose = int(options.get("verbosity"))
        expired_keys = CaptchaStore.objects.filter(expiration__lte=timezone.now()).count()
        if verbose >= 1:
            print(f"Currently {expired_keys} expired hashkeys")
        try:
            CaptchaStore.remove_expired()
        except Exception:
            if verbose >= 1:
                print("Unable to delete expired hashkeys.")
            sys.exit(1)
        if verbose >= 1:
            if expired_keys > 0:
                print(f"{expired_keys} expired hashkeys removed.")
            else:
                print("No keys to remove.")
