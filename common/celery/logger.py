from logging import StreamHandler
from threading import get_ident

from celery import current_task
from celery.signals import task_prerun, task_postrun

from common.celery.utils import get_celery_task_log_path, CELERY_LOG_MAGIC_MARK


class CeleryTaskLoggerHandler(StreamHandler):
    terminator = '\r\n'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        task_prerun.connect(self.on_task_start)
        task_postrun.connect(self.on_start_end)

    @staticmethod
    def get_current_task_id():
        if not current_task:
            return
        task_id = current_task.request.root_id
        return task_id

    def on_task_start(self, sender, task_id, **kwargs):
        return self.handle_task_start(task_id)

    def on_start_end(self, sender, task_id, **kwargs):
        return self.handle_task_end(task_id)

    def after_task_publish(self, sender, body, **kwargs):
        pass

    def emit(self, record):
        task_id = self.get_current_task_id()
        if not task_id:
            return
        try:
            self.write_task_log(task_id, record)
            self.flush()
        except Exception:
            self.handleError(record)

    def write_task_log(self, task_id, msg):
        pass

    def handle_task_start(self, task_id):
        pass

    def handle_task_end(self, task_id):
        pass


class CeleryThreadingLoggerHandler(CeleryTaskLoggerHandler):
    @staticmethod
    def get_current_thread_id():
        return str(get_ident())

    def emit(self, record):
        thread_id = self.get_current_thread_id()
        try:
            self.write_thread_task_log(thread_id, record)
            self.flush()
        except ValueError:
            self.handleError(record)

    def write_thread_task_log(self, thread_id, msg):
        pass

    def handle_task_start(self, task_id):
        pass

    def handle_task_end(self, task_id):
        pass

    def handleError(self, record) -> None:
        pass


class CeleryThreadTaskFileHandler(CeleryThreadingLoggerHandler):
    def __init__(self, *args, **kwargs):
        self.thread_id_fd_mapper = {}
        self.task_id_thread_id_mapper = {}
        super().__init__(*args, **kwargs)

    def write_thread_task_log(self, thread_id, record):
        f = self.thread_id_fd_mapper.get(thread_id, None)
        if not f:
            raise ValueError('Not found thread task file')
        msg = self.format(record)
        f.write(msg.encode())
        f.write(self.terminator.encode())
        f.flush()

    def flush(self):
        for f in self.thread_id_fd_mapper.values():
            f.flush()

    def handle_task_start(self, task_id):
        # log_path = get_celery_task_log_path(task_id.split('_')[0])
        log_path = get_celery_task_log_path(task_id)
        thread_id = self.get_current_thread_id()
        self.task_id_thread_id_mapper[task_id] = thread_id
        f = open(log_path, 'ab')
        self.thread_id_fd_mapper[thread_id] = f

    def handle_task_end(self, task_id):
        ident_id = self.task_id_thread_id_mapper.get(task_id, '')
        f = self.thread_id_fd_mapper.pop(ident_id, None)
        if f and not f.closed:
            f.write(CELERY_LOG_MAGIC_MARK)
            f.close()
        self.task_id_thread_id_mapper.pop(task_id, None)
