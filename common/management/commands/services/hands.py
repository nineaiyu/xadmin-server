import os
import sys
import time

from django.conf import settings
from django.core import management
from django.db.utils import OperationalError

HTTP_HOST = settings.HTTP_BIND_HOST or '127.0.0.1'
HTTP_PORT = settings.HTTP_LISTEN_PORT or 8080
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


def prepare():
    check_database_connection()
    collect_static()
    perform_db_migrate()
