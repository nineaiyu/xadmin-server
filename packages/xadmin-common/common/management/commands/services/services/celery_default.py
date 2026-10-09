from typing import Any

from .celery_base import CeleryBaseService

__all__ = ["CeleryDefaultService"]


class CeleryDefaultService(CeleryBaseService):
    def __init__(self, **kwargs: Any) -> None:
        kwargs["queue"] = "celery"
        super().__init__(**kwargs)
