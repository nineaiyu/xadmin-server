# -*- coding: utf-8 -*-
"""公共测试 fixtures。"""

import pytest
from django.core.cache import cache
from django.db import connections
from rest_framework.test import APIClient

from identity.models import DeptInfo, UserInfo, UserRole
from server.utils import set_current_request
from system.models import Menu, MenuMeta


def pytest_configure(config):
    """真环境档（tests.settings_real，门禁缺省）预检：PG/Redis 不可达即 fail-fast。

    容器事故的教训（OrbStack 宿主故障连环杀容器，全真容器化测试迁移方案 §五 #20）：
    Connection refused / PoolTimeout 会在数千用例上铺开成误导性红海，且与容量类
    flaky（#15）难以区分。此处用裸 socket 探测（不触碰 Django 连接层，避开
    pytest-django 阻断器），环境变量读取与缺省值同 settings_real 口径；
    只对真环境档生效、只由非 xdist worker 进程执行；E2E（settings_e2e）不经 pytest
    启动，不受影响。
    """
    import os
    import socket

    if os.environ.get("DJANGO_SETTINGS_MODULE") != "tests.settings_real":
        return
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return
    targets = (
        ("PostgreSQL", os.environ.get("DB_HOST", "127.0.0.1"), int(os.environ.get("DB_PORT", "55433"))),
        ("Redis", os.environ.get("REDIS_HOST", "127.0.0.1"), int(os.environ.get("REDIS_PORT", "56379"))),
    )
    down = []
    for name, host, port in targets:
        try:
            socket.create_connection((host, port), timeout=1.0).close()
        except OSError:
            down.append(f"{name} {host}:{port}")
    if down:
        pytest.exit(
            "真环境档依赖不可达：{}\n先起依赖：docker compose -f compose.test.yml up -d".format("、".join(down)),
            returncode=1,
        )


@pytest.fixture(autouse=True)
def _clean_index_meta():
    """知识库索引元数据短缓存为进程内层，cache.clear() 清不到（跨测试会读到旧行）。

    独立 fixture 命名：部分测试文件自带 `_clean_cache`（同名覆盖 conftest 版本），
    清理必须挂在不被覆盖的名字上才能对所有测试生效。
    """
    try:
        from ai.utils.index_meta import invalidate_index_meta
    except ImportError:  # ai 应用被模块裁剪时不阻断
        yield
        return
    invalidate_index_meta()
    yield
    invalidate_index_meta()


@pytest.fixture(autouse=True)
def _clean_cache():
    """每个测试前后清空缓存，避免 MagicCacheData（权限缓存 24h）跨测试污染。

    真环境档（tests/settings_real.py）下 cache.clear() 走 django_redis 的 flushdb，
    会清掉整个逻辑库——多 worker 共享 Redis（>15 worker 时 db 循环复用、两 worker
    共库）下互踩。delete_pattern 的模式经 KEY_FUNCTION 同源拼前缀（make_pattern），
    传逻辑模式 "*" 即精确命中本 worker 键空间（settings_real 的 test_redis_key_func
    加 "tN:" 前缀），等价于「只清本 worker 前缀」、永不 flushdb；sqlite 档无该属性，
    保持 cache.clear() 原语义，PR 门禁零变化。
    注意模式必须是逻辑键（"*"），不能传 f"{prefix}:*"——否则会被 key_func 二次加前缀，
    变成 tN:tN:* 一个都清不到（首轮全量 30 例连环红的根因）。
    """
    from django.conf import settings as django_settings

    from common.core.config.base import ConfigCacheBase

    worker_scoped = getattr(django_settings, "TEST_REDIS_KEY_PREFIX", None) is not None

    def _flush():
        if worker_scoped:
            cache.delete_pattern("*")
        else:
            cache.clear()
        ConfigCacheBase._L1_STORE.clear()  # 配置 L1 为进程内层，cache 清理清不到

    _flush()
    yield
    _flush()


@pytest.fixture(scope="session")
def django_db_modify_db_settings(django_db_modify_db_settings_parallel_suffix):
    """丢弃「测试库换名前」误建的 PG 连接池，守护 nightly PG 档（tests/settings_pg.py）。

    Django postgres 后端的 pool property 是「读即建池」语义，而 `_cursor()` 在
    ensure_connection（pytest-django 的 DB 阻断点）**之前**会先经
    close_if_health_check_failed() 读一次 pool——应用启动后台线程
    （common/apps.py django_ready → Setting.refresh_all_settings）因此在
    pytest 收集阶段就把连接池固化到「尚未创建的测试库名」上；之后
    django_db_setup 换库名，migrate 经旧池取连接全部 PoolTimeout
    （2026-10-01 nightly PG 首轮 3630 errors 的根因，处置登记见
    docs/plans/容器化PG-nightly测试档立项-2026.10.md §五）。

    此处挂 pytest-django 官方扩展点（默认 tox/xdist 后缀链经参数原样保留），
    在换库名前 close_pool() 丢弃误建池，migrate 首次建连时按 test_<库名> 重建。
    sqlite 后端无 pool（无 close_pool 属性）——no-op，PR 门禁行为不变。
    """
    from django.db import connections

    for connection in connections.all():
        close_pool = getattr(connection, "close_pool", None)
        if close_pool is not None:
            close_pool()


def _worker_test_db_name():
    """当前进程测试用的 PG 库名；非 PG 档返回 None。"""
    from django.conf import settings as dj_settings

    db = dj_settings.DATABASES.get("default") or {}
    if "postgresql" not in db.get("ENGINE", ""):
        return None
    return db.get("NAME")


def _release_pool_connections():
    """在已知后台线程池内各归还一次连接（Django 连接是线程本地，必须在线程内执行）。"""

    def _close_all():
        from django.db import connections

        connections.close_all()

    from asgiref.sync import AsyncToSync, SyncToAsync

    from common.decorators import debounce
    from common.utils import health

    executors = {SyncToAsync.single_thread_executor, *AsyncToSync.loop_thread_executors.values()}
    executors.update(SyncToAsync.context_to_thread_executor.values())
    executors.add(health._probe_pool)  # 健康探测线程池：probe_db 借出的连接由本步归还
    if debounce._executor is not None:
        executors.add(debounce._executor)
    for executor in executors:
        try:
            executor.submit(_close_all).result(timeout=5)
        except Exception:  # noqa: BLE001 清理失败不阻断测试收尾
            continue


def _terminate_other_sessions(db_name):
    """终止测试库上的其它会话（借出连接不随池关闭释放，最后兜底）。"""
    import psycopg
    from django.conf import settings as dj_settings

    db = dj_settings.DATABASES["default"]
    try:
        with (
            psycopg.connect(
                host=db["HOST"],
                port=db["PORT"],
                dbname="postgres",
                user=db["USER"],
                password=db["PASSWORD"],
                connect_timeout=5,
                autocommit=True,
            ) as conn,
            conn.cursor() as cursor,
        ):
            cursor.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity where datname = %s and pid <> pg_backend_pid()",
                [db_name],
            )
    except Exception:  # noqa: BLE001 兜底清理失败不阻断测试收尾
        pass


@pytest.fixture(autouse=True, scope="session")
def _release_test_db_connections(django_db_setup):
    """销毁测试库前释放本 worker 借出的 PG 连接（防 DROP 被残留会话挡住）。

    真环境档走 psycopg 连接池，Django 的 ``close_pool()`` 只关闭空闲连接：「已借出」
    的连接要等持有者归还，而测试进程里的部分持有者不会归还（健康探测 / 防抖等后台
    线程池、asgiref 线程敏感执行器线程等）——pytest-django 销毁测试库时因此报
    "database is being accessed by other users"（PytestWarning）。

    这里先归还主线程与已知线程池的连接，再终止本库其余会话兜底（幂等）。依赖
    ``django_db_setup`` 保证 finalizer 先于其销毁测试库执行。
    """
    yield
    from django.db import connections

    db_name = _worker_test_db_name()
    for connection in connections.all(initialized_only=True):
        try:
            connection.close()
        except Exception:  # noqa: BLE001 连接可能已不可用
            continue
    _release_pool_connections()
    if db_name:
        _terminate_other_sessions(db_name)


@pytest.fixture(autouse=True, scope="session")
def _safe_channels_conn_recycle():
    """channels 的 database_sync_to_async 每次执行前后调 close_old_connections()。

    sqlite :memory: 上 Django 特意跳过 close（内存库关闭即销毁数据），该清理天然
    no-op；PG 真库（池模式 CONN_MAX_AGE=0，close_at 立即过期）则会把 pytest-django
    测试原子块内的连接关掉（closed_in_transaction=True），Django 禁止原子块内重连，
    消费者后续 ORM 全部炸「Cannot open a new connection in an atomic block」
    （2026-10-01 nightly PG 档首轮暴露 23 例，见 docs/plans/容器化PG-nightly测试档立项-2026.10.md §五）。

    生产语义保持不变：仅在「处于原子块中」时跳过回收（生产消费线程没有请求级原子，
    该分支不可达）；测试进程内的连接回收本无意义（事务回滚即还原状态）。
    """
    from channels import db as channels_db

    def _safe_close_old_connections(**kwargs):
        for conn in connections.all(initialized_only=True):
            if not conn.in_atomic_block:
                conn.close_if_unusable_or_obsolete()

    original = channels_db.close_old_connections
    channels_db.close_old_connections = _safe_close_old_connections
    yield
    channels_db.close_old_connections = original


@pytest.fixture(autouse=True, scope="session")
def _isolate_settings_pubsub():
    """测试进程内禁用 Setting 热更新 pub/sub。

    生产链路是「保存 → Redis pub/sub → 后台订阅线程回写 django.conf.settings」；
    测试里保留后台线程会让某个测试保存的配置被**异步**回灌全局 settings，
    造成同 worker 内跨测试污染（历史上表现为偶发的 LDAP/通知设置断言失败）。
    置为惰性桩后，需要验证回写效果的测试按
    tests/integration/test_monitor_settings.py 的约定显式调用 refresh_setting()。
    """
    from settings import signal_handlers

    class _NoopPubSub:
        def publish(self, data):
            return True

        def subscribe(self, *_args, **_kwargs):
            return None

    original = signal_handlers.setting_pub_sub
    signal_handlers.setting_pub_sub = _NoopPubSub()
    yield
    signal_handlers.setting_pub_sub = original


@pytest.fixture(autouse=True)
def _clean_thread_local():
    """每个测试后清理 thread-local 中残留的 request，避免污染序列化器测试。"""
    yield
    set_current_request(None)


@pytest.fixture
def api_client():
    # 附带 User-Agent：ApiLoggingMiddleware 直接取 META['HTTP_USER_AGENT']，缺失会 500
    return APIClient(HTTP_USER_AGENT="pytest-agent")


@pytest.fixture
def superuser(db):
    return UserInfo.objects.create_superuser(username="admin", email="admin@example.com", password="Admin@123456")


@pytest.fixture
def seed_creator_user(superuser):
    """loadjson 种子把 creator/modifier 硬编码为 1（生产口径：新装环境 init_data 的首个超管）。

    sqlite 的 AUTOINCREMENT 序列随事务回滚复位，superuser 在每个测试里恒为
    pk=1，种子可直接 loaddata；PG 的序列**不随事务回滚**，superuser 的 pk 会
    随同 worker 先前用例漂移（2026-10-01 nightly PG 档首轮暴露，见
    docs/plans/容器化PG-nightly测试档立项-2026.10.md §五），loaddata 解析
    creator=1 时报 UserInfo.DoesNotExist。此 fixture 依赖 superuser 并只在
    不变量被破坏时补一个 pk=1 的引用目标——sqlite 档恒为 no-op，PR 门禁零变化。

    需要装载含 creator 引用的种子的测试，显式请求本 fixture（置于 superuser 之后）。
    """
    if superuser.pk != 1 and not UserInfo.objects.filter(pk=1).exists():
        UserInfo.objects.create(pk=1, username="init-admin", password="!")  # "!" 为 Django 不可用密码标记
    return None


@pytest.fixture
def role(db):
    return UserRole.objects.create(name="普通用户", code="common")


@pytest.fixture
def dept(db):
    return DeptInfo.objects.create(name="研发部", code="dev")


@pytest.fixture
def normal_user(db, role):
    user = UserInfo.objects.create_user(username="zhangsan", password="Test@123456", nickname="张三")
    user.roles.add(role)
    return user


@pytest.fixture
def menu_factory(db):
    """创建菜单的工厂。权限类型菜单需绑定 path（正则，不带前导斜杠）与 method。"""

    def _make(
        name,
        path=None,
        method=None,
        menu_type=Menu.MenuChoices.PERMISSION,
        parent=None,
        is_active=True,
    ):
        meta = MenuMeta.objects.create(title=name)
        return Menu.objects.create(
            name=name,
            path=path or "",
            method=method,
            menu_type=menu_type,
            parent=parent,
            meta=meta,
            is_active=is_active,
        )

    return _make


@pytest.fixture
def auth_client(api_client, superuser):
    """以超级管理员身份请求（跳过权限校验，专注 ViewSet 冒烟）。"""
    api_client.force_authenticate(user=superuser)
    return api_client


@pytest.fixture
def module_config(settings):
    """应用一次功能模块裁剪配置（preset / enable / disable）并清空派生缓存。

    模块组合变更在生产环境需重启进程；测试中通过 settings + reset_module_state()
    模拟同等效果（见 common/core/modules/ 包）。
    """
    from common.core.modules import clear_override, reset_module_state

    def _apply(preset="full", enable=(), disable=()):
        settings.MODULE_PRESET = preset
        settings.MODULE_ENABLE = list(enable)
        settings.MODULE_DISABLE = list(disable)
        # 后台覆盖行优先于部署基线：残留行会让 settings 改动失效，先清干净
        try:
            clear_override()
        except Exception:  # noqa: BLE001 无库/表未建时无需清理
            pass
        reset_module_state()

    yield _apply
    reset_module_state()


@pytest.fixture
def module_override():
    """写入后台覆盖行（模拟管理页保存），并让解析结果按重启后语义生效。"""
    from common.core.modules import clear_override, reset_module_state, save_override

    def _apply(preset="full", enable=(), disable=(), reset=True):
        save_override(preset=preset, enable=enable, disable=disable)
        if reset:
            reset_module_state()

    yield _apply
    clear_override()
    reset_module_state()
