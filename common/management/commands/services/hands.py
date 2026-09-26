import os
import sys
import time

from django.conf import settings
from django.core import management
from django.core.management.base import SystemCheckError
from django.db.utils import OperationalError

from common.core.utils import PrintLogFormat
from common.utils import test_ip_connectivity
from common.utils.file import download_file
from server.const import CONFIG
from settings.services import Setting
from system.services import scan_permission_gaps

logger = PrintLogFormat("xAdmin API Server", title_width=30, body_width=0)

try:
    from server import const

    __version__ = const.VERSION
except ImportError as e:
    print(f"Not found __version__: {e}")
    print("Python is: ")
    logger.info(sys.executable)
    __version__ = "Unknown"
    sys.exit(1)

HTTP_HOST = CONFIG.HTTP_BIND_HOST or "127.0.0.1"
HTTP_PORT = CONFIG.HTTP_LISTEN_PORT or 8896
GUNICORN_MAX_WORKER = CONFIG.GUNICORN_MAX_WORKER or 10
CELERY_FLOWER_HOST = CONFIG.CELERY_FLOWER_HOST or "127.0.0.1"
CELERY_FLOWER_PORT = CONFIG.CELERY_FLOWER_PORT or 5555
CELERY_FLOWER_AUTH = CONFIG.CELERY_FLOWER_AUTH or ""
DEBUG = CONFIG.DEBUG or False
APPS_DIR = settings.BASE_DIR
LOG_DIR = os.path.join(APPS_DIR, "data", "logs")
TMP_DIR = os.path.join(APPS_DIR, "tmp")
CELERY_WORKER_COUNT = CONFIG.CELERY_WORKER_COUNT or 10


def check_port_is_used():
    for _ in range(5):
        if not test_ip_connectivity(HTTP_HOST, HTTP_PORT):
            return
        else:
            logger.error(f"Check LISTEN {HTTP_HOST}:{HTTP_PORT} failed, Address already in use, try")
        time.sleep(1)
    logger.error(f"Check LISTEN {HTTP_HOST}:{HTTP_PORT} failed, exit")
    sys.exit(10)


def check_database_connection():
    for i in range(60):
        logger.info(f"Check database connection: {i}")
        try:
            management.call_command("check", "--database", "default")
            management.call_command("expire_caches", "system")
            logger.info("Database connect success")
            return
        except OperationalError:
            logger.warning("Database not setup, retry")
        except SystemCheckError as exc:
            # 确定性错误（模型/索引/配置级）：重试 60 次也不会自愈——快速失败并保留完整原因
            # （2026-09-18 部署实测：GinIndex 检查失败曾重试刷屏 60s×N 轮后才退出，启动即挂死）
            logger.error(f"System check failed (deterministic, not retrying): {exc}")
            sys.exit(12)
        except Exception as exc:
            logger.warning(f"Unexpect error occur: {str(exc)}")
        time.sleep(1)
    logger.error("Connection database failed, exit")
    sys.exit(10)


def perform_db_migrate():
    logger.info("Check database structure change ...")
    logger.info("Migrate model change to database ...")
    try:
        management.call_command("migrate")
    except Exception as e:
        logger.error(f"Perform migrate failed, {e} exit")
        sys.exit(11)


def collect_static():
    logger.info("Collect static files")
    try:
        management.call_command("collectstatic", "--no-input", "-c", verbosity=0, interactive=False)
        logger.info("Collect static files done")
    except Exception:
        pass


def compile_i18n_file():
    # django_mo_file = os.path.join(PROJECT_DIR, 'locale', 'zh', 'LC_MESSAGES', 'django.mo')
    # if os.path.exists(django_mo_file):
    #     return
    os.chdir(os.path.join(APPS_DIR))
    management.call_command("compilemessages", verbosity=0)
    logger.info("Compile i18n files done")


def download_ip_db(force=False):
    db_path_url_mapper = {
        ("system", "GeoLite2-City.mmdb"): "https://jms-pkg.oss-cn-beijing.aliyuncs.com/ip/GeoLite2-City.mmdb",
        ("system", "ipipfree.ipdb"): "https://jms-pkg.oss-cn-beijing.aliyuncs.com/ip/ipipfree.ipdb",
    }
    for p, src in db_path_url_mapper.items():
        path = os.path.join(settings.DATA_DIR, *p)
        if not force and os.path.isfile(path) and os.path.getsize(path) > 1000:
            continue
        logger.info(f"Download ip db: {path}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        download_file(src, path)


def expire_caches():
    try:
        management.call_command("expire_caches", "config_*")
    except Exception:
        pass


def check_settings():
    # 启动自检依赖迁移就绪的表：查询失败在下方重试循环里降级
    for _ in range(60):
        try:
            Setting.objects.exists()
            time.sleep(1)
            return
        except Exception as exc:
            logger.warning(f"Unexpect error occur: {str(exc)}, retry")
        time.sleep(1)
    logger.error("check settings database failed, exit")
    sys.exit(10)


def celery_prepare():
    check_database_connection()
    check_settings()
    compile_i18n_file()
    download_ip_db()


def check_permission_gaps():
    """开发态启动自检：权限点缺口只告警不改库（生产不执行，避免启动开销）。

    缺口 = 代码里有路由但库内没有对应权限点 → 非超管访问将 403。
    修复命令：python manage.py sync_menu_permissions（如需回写种子加 --update-seed）。
    """
    if not DEBUG:
        return
    try:
        gaps = scan_permission_gaps()
        if gaps:
            first_route, first_method, _ = gaps[0]
            logger.warning(
                f"检测到 {len(gaps)} 条权限点缺口（非超管将 403），运行 `python manage.py sync_menu_permissions` 修复；"
                f"示例：{first_method} {first_route.url}"
            )
    except Exception as exc:
        logger.warning(f"权限点自检跳过：{exc}")


def server_prepare():
    check_database_connection()
    collect_static()
    compile_i18n_file()
    check_port_is_used()
    perform_db_migrate()
    expire_caches()
    download_ip_db()
    check_permission_gaps()
