#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""可观测性初始化：Sentry 错误聚合，DSN 未配置时零开销。

在 server/settings/base.py 加载末尾调用；config.yml 示例：
    SENTRY_DSN: "https://<key>@<host>/<project>"
    SENTRY_ENVIRONMENT: "production"
    SENTRY_TRACES_SAMPLE_RATE: 0.0
"""
import logging

from .const import CONFIG

logger = logging.getLogger('xadmin.monitoring')


def init_monitoring():
    if not CONFIG.SENTRY_DSN:
        return
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("SENTRY_DSN configured but sentry-sdk is not installed; skip error monitoring")
        return

    integrations = []
    try:
        from sentry_sdk.integrations.django import DjangoIntegration
        integrations.append(DjangoIntegration())
    except ImportError:
        pass
    try:
        from sentry_sdk.integrations.celery import CeleryIntegration
        integrations.append(CeleryIntegration())
    except ImportError:
        pass

    sentry_sdk.init(
        dsn=CONFIG.SENTRY_DSN,
        environment=CONFIG.SENTRY_ENVIRONMENT,
        traces_sample_rate=CONFIG.SENTRY_TRACES_SAMPLE_RATE,
        integrations=integrations,
        send_default_pii=False,  # 不上报用户 PII；request_uuid 由 DjangoIntegration 随事件携带
    )
    logger.info("Sentry error monitoring initialized. environment:%s", CONFIG.SENTRY_ENVIRONMENT)
