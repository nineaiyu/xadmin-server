"""
WSGI config for server project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/wsgi/
"""

import os

import django
from django.core.handlers.wsgi import WSGIHandler

from common.core.atomic_read import SafeMethodAtomicSkipMixin

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.settings")
django.setup(set_prefix=False)


class XadminWSGIHandler(SafeMethodAtomicSkipMixin, WSGIHandler):
    """WSGI 入口 handler：纯读请求免 ATOMIC_REQUESTS（见 common/core/atomic_read.py）。"""


application = XadminWSGIHandler()
