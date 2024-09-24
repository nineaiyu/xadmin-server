import os
import sys
import time

from django.conf import settings
from django.core import management
from django.db.utils import OperationalError

from common.utils.file import download_file

HTTP_HOST = settings.HTTP_BIND_HOST or '127.0.0.1'
HTTP_PORT = settings.HTTP_LISTEN_PORT or 8080
GUNICORN_MAX_WORKER = settings.GUNICORN_MAX_WORKER or 10
CELERY_FLOWER_HOST = settings.CELERY_FLOWER_HOST or '127.0.0.1'
CELERY_FLOWER_PORT = settings.CELERY_FLOWER_PORT or 5555
CELERY_FLOWER_AUTH = settings.CELERY_FLOWER_AUTH or 'flower:flower'
DEBUG = settings.DEBUG or False
APPS_DIR = BASE_DIR = settings.BASE_DIR
LOG_DIR = os.path.join(BASE_DIR, 'logs')
TMP_DIR = os.path.join(LOG_DIR, 'tmp')


def check_database_connection():
    for i in range(60):
        print(f"Check database connection: {i}")
        try:
            management.call_command('check', '--database', 'default')
            print("Database connect success")
            return
        except OperationalError:
            print('Database not setup, retry')
        except Exception as exc:
            print('Unexpect error occur: {}'.format(str(exc)))
        time.sleep(1)
    print("Connection database failed, exit")
    sys.exit(10)


def perform_db_migrate():
    print("Check database structure change ...")
    print("Migrate model change to database ...")
    try:
        management.call_command('migrate')
    except Exception as e:
        print(f'Perform migrate failed, {e} exit')
        sys.exit(11)


def collect_static():
    print("Collect static files")
    try:
        management.call_command('collectstatic', '--no-input', '-c', verbosity=0, interactive=False)
        print("Collect static files done")
    except:
        pass


def compile_i18n_file():
    # django_mo_file = os.path.join(BASE_DIR, 'locale', 'zh', 'LC_MESSAGES', 'django.mo')
    # if os.path.exists(django_mo_file):
    #     return
    os.chdir(os.path.join(BASE_DIR))
    management.call_command('compilemessages', verbosity=0)
    print("Compile i18n files done")


def download_ip_db(force=False):
    db_base_dir = os.path.join(BASE_DIR, 'common', 'utils', 'ip')
    db_path_url_mapper = {
        ('geoip', 'GeoLite2-City.mmdb'): 'https://jms-pkg.oss-cn-beijing.aliyuncs.com/ip/GeoLite2-City.mmdb',
        ('ipip', 'ipipfree.ipdb'): 'https://jms-pkg.oss-cn-beijing.aliyuncs.com/ip/ipipfree.ipdb'
    }
    for p, src in db_path_url_mapper.items():
        path = os.path.join(db_base_dir, *p)
        if not force and os.path.isfile(path) and os.path.getsize(path) > 1000:
            continue
        print("Download ip db: {}".format(path))
        download_file(src, path)


def expire_caches():
    try:
        management.call_command('expire_caches', 'config_*')
    except:
        pass


def prepare():
    check_database_connection()
    collect_static()
    compile_i18n_file()
    perform_db_migrate()
    expire_caches()
    download_ip_db()
