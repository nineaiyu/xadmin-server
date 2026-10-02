"""Django settings：数据库连接配置（自 base.py 按域拆出）。

经 base.py 的 star-import 并入 settings 命名空间；`_resolve_db_engine` 为
下划线私有名，base.py 显式再导出以维持既有导入路径（tests/unit/common/test_db_check.py）。
"""

from ..const import CONFIG


def _resolve_db_engine(value: str) -> str:
    """数据库后端解析：短名 → Django 后端路径，其余按完整路径原样使用（第三方后端）。

    短名清单与 config_example.yml 的注释一致（sqlite3 / mysql / oracle / postgresql / vastbase）。
    """
    name = value.lower()
    if name in ("mysql", "oracle", "postgresql", "sqlite3"):
        return f"django.db.backends.{name}"
    if name == "vastbase":
        return "django_vastbase_backend"
    return value


DB_OPTIONS: dict[str, object] = {}
DB_ENGINE = CONFIG.DB_ENGINE.lower()
ENGINE = _resolve_db_engine(CONFIG.DB_ENGINE)

if DB_ENGINE == "postgresql":
    # 连接建立超时（演练第五轮·网络分区修复，2026-09-16）：PG 断网时 TCP 无响应，
    # 无该超时会让 DB 操作挂到 TCP 默认超时（实测 health 20s+ 完全无响应）；
    # 局域网建连 <10ms，3s 充裕且保证故障时快速失败（池/非池模式均透传 psycopg）
    DB_OPTIONS["connect_timeout"] = 3
    # 半开连接快速失败（第十七轮·真丢包演练修复，2026-09-18）：TCP 丢包时已建立连接
    # 进入半开态（对端收不到包、本端不知情），查询/SELECT 1 判活阻塞在 recv，无 socket 级
    # 超时则要等 TCP 重传耗竭（Linux 默认 ~15 分钟）——期间 worker 同步处理线程被逐个占死
    # （实测 health 亦排队无响应，~1 分钟自愈依赖 TCP 重传成功）。tcp_user_timeout 让内核在
    # 未确认数据超时后强制断开连接（recv 立即报错 → 池淘汰重建）；keepalives 三件套用于
    # 空闲连接的探活。注：tcp_user_timeout 仅 Linux 生效（libpq 在其它平台忽略该参数）。
    DB_OPTIONS["tcp_user_timeout"] = 30000  # ms；未确认数据 30s 即断开
    DB_OPTIONS["keepalives"] = 1
    DB_OPTIONS["keepalives_idle"] = 30  # 空闲 30s 开始探测
    DB_OPTIONS["keepalives_interval"] = 10  # 探测间隔 10s
    DB_OPTIONS["keepalives_count"] = 3  # 3 次未应答判定连接死亡

# ASGI 形态下 ASGIHandler 为每请求创建独立线程（ThreadSensitiveContext），
# 线程随请求结束消亡，持久连接机制（CONN_MAX_AGE）在此形态下无效，等效每请求新建 DB
# 连接——压测 ~600rps 时临时端口耗尽致 13-27% 500。postgresql 引擎默认启用 Django 5.1+
# server 端连接池（psycopg3 + psycopg_pool）；池模式下 CONN_MAX_AGE 必须为 0，
# CONN_HEALTH_CHECKS 使池在取用连接前做轻量存活校验
DB_POOL_ENABLED = DB_ENGINE == "postgresql" and bool(CONFIG.DB_POOL)
if DB_POOL_ENABLED:
    # 池判活回调替换（真实 SELECT 1）：psycopg_pool 默认 check_connection 用空查询，
    # 检测不到「PG 重启后半开连接」（2030-03 演练实测坏连接反复被取出、服务不自愈，
    # 直到进程重启）。Django 硬编码读取 ConnectionPool.check_connection 且不允许
    # OPTIONS["pool"] 重复传 check（实测 duplicate keyword 启动失败），故在配置期
    # 替换该静态方法——settings 加载早于任何池创建，对全部池生效。
    from psycopg_pool import ConnectionPool

    from common.db import check_db_connection

    # 运行期替换第三方类方法（配置期生效于全部连接池，mypy 视其为不可赋值）
    ConnectionPool.check_connection = staticmethod(check_db_connection)  # type: ignore[method-assign, assignment]

    DB_OPTIONS["pool"] = {
        "min_size": int(CONFIG.DB_POOL_MIN_SIZE),
        "max_size": max(int(CONFIG.DB_POOL_MIN_SIZE), int(CONFIG.DB_POOL_MAX_SIZE)),
        # 取用连接的最长等待（psycopg_pool 默认 30s）：故障（半开/丢包）时池重建期间
        # 请求快速失败（≤5s），而不是每个请求排队等满 30s（第十七轮演练复演实测）。
        # 正常负载取用 <10ms，5s 余量充足；容量由 min/max_size 控制不受影响。
        "timeout": 5,
        # 失败连接的重连调度间隔（psycopg_pool 默认 300s）：DB 恢复后健康指示 10s 级回正，
        # 而不是最长等 5 分钟（第十七轮复演观察：删规则后 db 指示恢复慢且抖动）。
        "reconnect_timeout": 10,
    }

DATABASES = {
    "default": {
        "ENGINE": ENGINE,
        "NAME": CONFIG.DB_DATABASE,
        "HOST": CONFIG.DB_HOST,
        "PORT": CONFIG.DB_PORT,
        "USER": CONFIG.DB_USER,
        "PASSWORD": CONFIG.DB_PASSWORD,
        "ATOMIC_REQUESTS": True,
        "CONN_MAX_AGE": 0 if DB_POOL_ENABLED else 600,
        "CONN_HEALTH_CHECKS": DB_POOL_ENABLED,
        "OPTIONS": DB_OPTIONS,
    }
}

if DB_ENGINE == "mysql":
    DB_OPTIONS["init_command"] = "SET sql_mode='STRICT_TRANS_TABLES'"
    DB_OPTIONS["charset"] = "utf8mb4"
    DB_OPTIONS["collation"] = "utf8mb4_bin"

# https://docs.djangoproject.com/zh-hans/5.0/topics/db/multi-db/#automatic-database-routing
# 读写分离 可能会出现 the current database router prevents this relation.
# 1.项目设置了router读写分离，且在ORM create()方法中，使用了前边filter()方法得到的数据，
# 2.由于django是惰性查询，前边的filter()并没有立即查询，而在create()中引用了filter()的数据时，执行了filter()，
# 3.此时写操作的db指针指向write_db，filter()的db指针指向read_db，两者发生冲突，导致服务禁止了此次与mysql的交互
# 解决办法：
# 在前边filter()方法中，使用using()方法，使filter()立即与数据库交互，查出数据。
# Author.objects.using("default")
# >>> p = Person(name="Fred")
# >>> p.save(using="second")  # (statement 2)

DATABASE_ROUTERS = ["common.core.db.router.DBRouter"]
