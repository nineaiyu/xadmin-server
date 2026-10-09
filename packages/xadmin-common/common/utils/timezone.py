from datetime import datetime
from typing import Any

from django.utils import timezone as dj_timezone


def as_current_tz(dt: datetime) -> Any:
    return dt.astimezone(dj_timezone.get_current_timezone())


def utc_now() -> Any:
    return dj_timezone.now()


def local_now() -> Any:
    return dj_timezone.localtime(dj_timezone.now())


def local_now_display(fmt: str = "%Y-%m-%d %H:%M:%S") -> Any:
    return local_now().strftime(fmt)


def local_now_filename() -> Any:
    return local_now().strftime("%Y%m%d-%H%M%S")


def local_now_date_display(fmt: str = "%Y-%m-%d") -> Any:
    return local_now().strftime(fmt)


def local_zero_hour(fmt: str = "%Y-%m-%d") -> Any:
    return datetime.strptime(local_now().strftime(fmt), fmt)
