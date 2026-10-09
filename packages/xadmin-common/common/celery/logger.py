from logging import StreamHandler
from threading import get_ident
from typing import Any

from celery import current_task
from celery.signals import task_postrun, task_prerun

from common.celery.utils import CELERY_LOG_MAGIC_MARK, get_celery_task_log_path


class CeleryTaskLoggerHandler(StreamHandler[Any]):
    terminator = "\r\n"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        task_prerun.connect(self.on_task_start)
        task_postrun.connect(self.on_start_end)

    @staticmethod
    def get_current_task_id() -> Any:
        if not current_task:
            return
        task_id = current_task.request.root_id
        return task_id

    def on_task_start(self, sender: Any, task_id: Any, **kwargs: Any) -> Any:
        return self.handle_task_start(task_id)

    def on_start_end(self, sender: Any, task_id: Any, **kwargs: Any) -> Any:
        return self.handle_task_end(task_id)

    def after_task_publish(self, sender: Any, body: Any, **kwargs: Any) -> None:
        pass

    def emit(self, record: Any) -> None:
        task_id = self.get_current_task_id()
        if not task_id:
            return
        try:
            self.write_task_log(task_id, record)
            self.flush()
        except Exception:
            self.handleError(record)

    def write_task_log(self, task_id: Any, msg: Any) -> None:
        pass

    def handle_task_start(self, task_id: Any) -> None:
        pass

    def handle_task_end(self, task_id: Any) -> None:
        pass


class CeleryThreadingLoggerHandler(CeleryTaskLoggerHandler):
    @staticmethod
    def get_current_thread_id() -> Any:
        return str(get_ident())

    def emit(self, record: Any) -> None:
        thread_id = self.get_current_thread_id()
        try:
            self.write_thread_task_log(thread_id, record)
            self.flush()
        except ValueError:
            self.handleError(record)

    def write_thread_task_log(self, thread_id: Any, msg: Any) -> None:
        pass

    def handle_task_start(self, task_id: Any) -> None:
        pass

    def handle_task_end(self, task_id: Any) -> None:
        pass

    def handleError(self, record: Any) -> None:
        pass


class CeleryThreadTaskFileHandler(CeleryThreadingLoggerHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.thread_id_fd_mapper: dict[Any, Any] = {}
        self.task_id_thread_id_mapper: dict[str, Any] = {}
        super().__init__(*args, **kwargs)

    def write_thread_task_log(self, thread_id: Any, record: Any) -> None:
        f = self.thread_id_fd_mapper.get(thread_id, None)
        if not f:
            raise ValueError("Not found thread task file")
        msg = self.format(record)
        f.write(msg.encode())
        f.write(self.terminator.encode())
        f.flush()

    def flush(self) -> None:
        for f in self.thread_id_fd_mapper.values():
            f.flush()

    def handle_task_start(self, task_id: Any) -> None:
        # log_path = get_celery_task_log_path(task_id.split('_')[0])
        log_path = get_celery_task_log_path(task_id)
        thread_id = self.get_current_thread_id()
        self.task_id_thread_id_mapper[task_id] = thread_id
        f = open(log_path, "ab")
        self.thread_id_fd_mapper[thread_id] = f

    def handle_task_end(self, task_id: Any) -> None:
        ident_id = self.task_id_thread_id_mapper.get(task_id, "")
        f = self.thread_id_fd_mapper.pop(ident_id, None)
        if f and not f.closed:
            f.write(CELERY_LOG_MAGIC_MARK)
            f.close()
        self.task_id_thread_id_mapper.pop(task_id, None)
