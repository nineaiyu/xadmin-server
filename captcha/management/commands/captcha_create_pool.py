from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from captcha.models import CaptchaStore


class Command(BaseCommand):
    help = "Create a pool of random captchas."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--pool-size",
            type=int,
            default=1000,
            help="Number of new captchas to create, default=1000",
        )
        parser.add_argument(
            "--cleanup-expired",
            action="store_true",
            default=True,
            help="Cleanup expired captchas after creating new ones",
        )

    @transaction.atomic  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def handle(self, **options: Any) -> None:
        verbose = int(options.get("verbosity") or 0)
        count = int(options.get("pool_size") or 0)
        CaptchaStore.create_pool(count)
        verbose and self.stdout.write(f"Created {count} new captchas\n")
        options.get("cleanup_expired") and CaptchaStore.remove_expired()
        options.get("cleanup_expired") and verbose and self.stdout.write("Expired captchas cleaned up\n")
