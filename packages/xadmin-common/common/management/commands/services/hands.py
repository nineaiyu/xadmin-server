import os
import sys
import time

from django.core import management
from django.core.management.base import SystemCheckError
from django.db.utils import OperationalError

from common.contracts import Setting, scan_permission_gaps
from common.core.utils import PrintLogFormat
from common.injection import get_server_config, get_server_version
from common.settings_contract import kernel_required_setting
from common.utils import test_ip_connectivity
from common.utils.file import download_file

logger = PrintLogFormat("xAdmin API Server", title_width=30, body_width=0)

# 版本单一事实源在 server/const.py（文档门禁按此校验），经 common.injection 注入读取；
# 未注入（未经 Django settings 启动）直接抛异常，与旧 import 失败即退出的口径一致
__version__ = get_server_version()

CONFIG = get_server_config()

HTTP_HOST = CONFIG.HTTP_BIND_HOST or "127.0.0.1"
HTTP_PORT = CONFIG.HTTP_LISTEN_PORT or 8896
GUNICORN_MAX_WORKER = CONFIG.GUNICORN_MAX_WORKER or 10
CELERY_FLOWER_HOST = CONFIG.CELERY_FLOWER_HOST or "127.0.0.1"
CELERY_FLOWER_PORT = CONFIG.CELERY_FLOWER_PORT or 5555
CELERY_FLOWER_AUTH = CONFIG.CELERY_FLOWER_AUTH or ""
DEBUG = CONFIG.DEBUG or False
AUTO_MIGRATE = CONFIG.AUTO_MIGRATE if CONFIG.AUTO_MIGRATE is not None else True
APPS_DIR = kernel_required_setting("BASE_DIR")
LOG_DIR = os.path.join(APPS_DIR, "data", "logs")
TMP_DIR = os.path.join(APPS_DIR, "tmp")
CELERY_WORKER_COUNT = CONFIG.CELERY_WORKER_COUNT or 10


def check_port_is_used() -> None:
    for _ in range(5):
        if not test_ip_connectivity(HTTP_HOST, HTTP_PORT):
            return
        else:
            logger.error(f"Check LISTEN {HTTP_HOST}:{HTTP_PORT} failed, Address already in use, try")
        time.sleep(1)
    logger.error(f"Check LISTEN {HTTP_HOST}:{HTTP_PORT} failed, exit")
    sys.exit(10)


def check_database_connection() -> None:
    for i in range(60):
        logger.info(f"Check database connection: {i}")
        try:
            management.call_command("check", "--database", "default")
            # 清理配置缓存（通配前缀 config_*）：数据库就绪后按库值重建配置缓存。
            # 传具体键名走精确删除，键名不存在时是空操作（此处曾误传 "system"）
            management.call_command("expire_caches", "config_*")
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


def perform_db_migrate() -> None:
    logger.info("Check database structure change ...")
    logger.info("Migrate model change to database ...")
    try:
        management.call_command("migrate")
    except Exception as e:
        logger.error(f"Perform migrate failed, {e} exit")
        sys.exit(11)


def maybe_migrate() -> None:
    """按 AUTO_MIGRATE 决定是否在启动时迁移。

    多副本/滚动发布必须关掉（本容器是否迁移不可控且并发迁移互相竞争），
    改由一次性 migrate 服务先跑完再起副本；关闭时只记日志不退出，缺表由副本
    首次查询暴露（更符合「谁先准备好谁先服务」的滚动语义）。
    """
    if AUTO_MIGRATE:
        perform_db_migrate()
    else:
        logger.info("AUTO_MIGRATE=false, skip auto migrate. Run the one-off migrate service first.")


def collect_static() -> None:
    logger.info("Collect static files")
    try:
        management.call_command("collectstatic", "--no-input", "-c", verbosity=0, interactive=False)
        logger.info("Collect static files done")
    except Exception as exc:
        # 收集失败仅跳过（不阻断启动；静态缺失可在页面层/部署时发现），保留告警便于定位
        logger.warning(f"Collect static files failed: {exc}")


def compile_i18n_file() -> None:
    cwd = os.getcwd()
    os.chdir(os.path.join(APPS_DIR))
    try:
        management.call_command("compilemessages", verbosity=0)
    finally:
        # 还原工作目录：调用方（server_prepare / celery_prepare）之后仍按相对路径取文件
        os.chdir(cwd)
    logger.info("Compile i18n files done")


def download_ip_db(force: bool = False) -> None:
    db_path_url_mapper = {
        ("system", "GeoLite2-City.mmdb"): "https://jms-pkg.oss-cn-beijing.aliyuncs.com/ip/GeoLite2-City.mmdb",
        ("system", "ipipfree.ipdb"): "https://jms-pkg.oss-cn-beijing.aliyuncs.com/ip/ipipfree.ipdb",
    }
    for p, src in db_path_url_mapper.items():
        path = os.path.join(kernel_required_setting("DATA_DIR"), *p)
        if not force and os.path.isfile(path) and os.path.getsize(path) > 1000:
            continue
        logger.info(f"Download ip db: {path}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            download_file(src, path)
        except Exception as exc:
            # 单库下载失败不阻断启动（IP 归属查询按缺库降级），失败保留告警线索
            logger.warning(f"Download ip db failed: {path}, {exc}")


def expire_caches() -> None:
    try:
        management.call_command("expire_caches", "config_*")
    except Exception:
        # 缓存过期清理失败：不阻断启动（缓存本身有 TTL 兜底）
        pass


def check_settings() -> None:
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


def celery_prepare() -> None:
    check_database_connection()
    check_settings()
    compile_i18n_file()
    download_ip_db()


def check_permission_gaps() -> None:
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


def server_prepare() -> None:
    check_database_connection()
    collect_static()
    compile_i18n_file()
    check_port_is_used()
    maybe_migrate()
    expire_caches()
    download_ip_db()
    check_permission_gaps()
