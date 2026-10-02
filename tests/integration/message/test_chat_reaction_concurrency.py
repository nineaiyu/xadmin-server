# -*- coding: utf-8 -*-
"""聊天表情回应的并发正确性测试（单独成档：依赖真实事务隔离）。

并发用例需要多线程各自持有真实连接与事务（select_for_update 行锁生效前提），
必须运行在 ``django_db(transaction=True)``（TransactionTestCase）下；与常规
``django_db``（TestCase 事务包裹回滚）语义互斥，故与 test_chat_reaction.py 分档，
避免 marker 冲突。

口径：多个线程同时 add 不同 emoji，行锁串行化同一消息 extra 的读改写，
全部回应落库（若实现退化为无锁读改写，后写会覆盖前写，断言即失败）。
"""

import threading

import pytest

from message import chat as chat_service

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def alice(db):
    from system.models import UserInfo

    return UserInfo.objects.create_user(username="react-cc-alice", password="Test@123456", nickname="爱丽丝")


class TestReactionConcurrency:
    def test_concurrent_adds_all_persist(self, alice):
        room = chat_service.get_public_room()
        message, __ = chat_service.create_message(room, alice, "并发回应")
        emojis = ["👍", "🎉", "🚀", "❤️"]
        barrier = threading.Barrier(len(emojis))
        errors = []

        def worker(emoji):
            try:
                barrier.wait(timeout=10)
                chat_service.toggle_reaction(alice, message.pk, emoji, "add")
            except Exception as exc:  # noqa: BLE001 线程内异常收集后统一断言
                errors.append(exc)
            finally:
                from django.db import connections

                connections.close_all()

        threads = [threading.Thread(target=worker, args=(emoji,), name=f"react-{emoji}") for emoji in emojis]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert errors == []
        message.refresh_from_db()
        assert set((message.extra or {}).get("reactions") or {}) == set(emojis), "并发回应全部落库（无后写覆盖前写）"
