# -*- coding: utf-8 -*-
"""测试档公共基座：各测试 settings（real / pg / e2e）star-import 的单变量继承面。

不含任何数据库 / 缓存 / 通道层分支——分支由各档自带（settings_real 真 PG + 真 Redis、
settings_pg 真 PG + FakeRedis、settings_e2e 真 PG + FakeRedis + 内存 channel layer）。
原 sqlite 档 tests/settings_test.py 已于 2026-10-01 退役删除（全真容器化测试迁移方案
§四 阶段 3/4：CI 门禁与 E2E 全部真库化，sqlite 从测试链路禁用）；本模块即原档中
与数据库无关的公共覆盖面抽取。

注意：本模块不能放在 server/settings/ 包内——父包 __init__ 会强制加载 config.yml。
Config 注入（SECRET_KEY / XADMIN_APPS / DB_ENGINE / DB/REDIS 参数等）必须由各档在
导入本模块**之前**自行完成（本模块 star-import server.settings 只复用已加载的模块
对象，不会重复触发配置装配）。
"""

import os

from server.settings import *  # noqa: F401,F403

DEBUG = False
DEBUG_DEV = False
SECRET_KEY = "test-only-secret-key-0123456789abcdef"

# celery：eager + memory broker（真实 broker 只引入时序 flaky，不改变任务体正确性，
# 论证见全真容器化测试迁移方案 §3.4）
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
# URL 显式带主机名：memory transport 不使用 hostname（解析参数与 "memory://" 等价），
# 但 eager apply_async 仍会经 producer_or_acquire 克隆一条 kombu 连接，URL 无主机名时
# kombu 会打 "No hostname was supplied. Reverting to default 'localhost'" 告警。
CELERY_BROKER_URL = "memory://localhost/"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# 测试产生的上传文件统一写到 tmp 目录，避免污染 data/。
# xdist 并行（`pytest -n auto`）按 worker 再分子目录：各 worker 的数据库独立、自增主键
# 都从 1 开始，分片等按主键生成的存储路径（upload_sessions/<pk>/part-*）会**跨进程同名**
# ——一个 worker 清理文件后另一个 worker 又写入同路径，断言「已清理」随机失败（历史 flaky）
_worker = os.environ.get("PYTEST_XDIST_WORKER", "")
MEDIA_ROOT = (
    os.path.join(PROJECT_DIR, "tmp", "test_media", _worker)  # noqa: F405
    if _worker
    else os.path.join(PROJECT_DIR, "tmp", "test_media")  # noqa: F405
)

# 测试日志与生产日志隔离：data/logs/server.log 是发布窗口硬门禁（CSP enforce /
# AES v1 关闭）的唯一判据来源，测试流量不得写入（机制与背景见 tests/logging_isolation.py）
from tests.logging_isolation import isolate_file_handlers  # noqa: E402

isolate_file_handlers(LOGGING, PROJECT_DIR)  # noqa: F405
