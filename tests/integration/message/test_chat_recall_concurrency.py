# -*- coding: utf-8 -*-
"""聊天撤回的并发正确性测试（单独成档：依赖真实事务隔离，同 test_chat_reaction_concurrency）。

口径：撤回的读-判-写必须以行锁串行化。测试线程先持消息行锁、再放行并发的
第二次撤回、随后在锁内把消息置为已撤回并提交——第二次撤回在锁上等待、锁内
重读到「已撤回」后被拒。若实现退化为无锁读（普通 SELECT 不被行锁阻塞），
第二次撤回会在持锁事务提交前通过全部校验，等锁释放后照常落库（双快照、
双写），断言即失败。
"""

import threading
import time

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone

from message import chat as chat_service
from message.models import ChatMessage

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def alice(db):
    from identity.models import UserInfo

    return UserInfo.objects.create_user(username="recall-cc-alice", password="Test@123456", nickname="爱丽丝")


class TestRecallConcurrency:
    def test_concurrent_recall_single_winner(self, alice):
        room = chat_service.get_public_room()
        message, __ = chat_service.create_message(room, alice, "并发撤回")
        entered = threading.Event()
        errors = []
        succeeded = []

        def worker():
            try:
                entered.wait(timeout=10)
                chat_service.recall_message(alice, message.pk)
                succeeded.append(True)
            except DjangoValidationError:
                errors.append(True)
            finally:
                from django.db import connections

                connections.close_all()

        thread = threading.Thread(target=worker, name="recall-second")
        thread.start()
        with transaction.atomic():
            locked = ChatMessage.objects.select_for_update().get(pk=message.pk)
            entered.set()
            time.sleep(0.2)  # 放行 worker 的撤回读：有锁时它阻塞在行锁上等待本次提交
            locked.is_recalled = True
            locked.recalled_time = timezone.now()
            locked.content = ""
            locked.save(update_fields=["is_recalled", "recalled_time", "content", "updated_time"])
        thread.join(timeout=30)

        assert succeeded == [], "并发的第二次撤回不得成功（锁内重读已撤回即被拒）"
        assert len(errors) == 1
