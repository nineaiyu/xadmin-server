# -*- coding: utf-8 -*-
"""一次性缓存原子消费（``atomic_get_delete``）守护测试。

背景：OAuth state / nonce / 授权码、临时令牌等「一键一用」场景原先用
``cache.get`` 后 ``cache.delete`` 实现一次性语义，存在「读取后、删除前」
的并发重放窗口。现在统一走互斥标记的原子消费，本文件钉死：

1. 并发消费只有一方拿到值，其余方判负（fail-closed）；
2. 消费后原键即删（重放命中标记仍判负）；
3. 非一次性读取（可重复校验）不受影响。
"""

import threading

import pytest

from common.cache.storage import atomic_get_delete

pytestmark = pytest.mark.django_db


class TestAtomicGetDelete:
    def test_single_consume(self):
        from django.core.cache import cache

        cache.set("atomic_probe_single", {"ok": True}, 60)
        assert atomic_get_delete("atomic_probe_single") == {"ok": True}
        # 原键已删：即使标记过期，重放也拿不到值
        assert cache.get("atomic_probe_single") is None
        assert atomic_get_delete("atomic_probe_single") is None

    def test_concurrent_consume_single_winner(self):
        from django.core.cache import cache

        cache.set("atomic_probe_concurrent", "secret-once", 60)
        winners: list[str] = []
        lock = threading.Lock()
        barrier = threading.Barrier(8)

        def consume() -> None:
            barrier.wait()
            value = atomic_get_delete("atomic_probe_concurrent")
            if value is not None:
                with lock:
                    winners.append(value)

        threads = [threading.Thread(target=consume) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        # 恰好一个赢家：并发的八个消费只有一方通过（重放窗口关闭）
        assert winners == ["secret-once"]

    def test_missing_key_returns_none(self):
        assert atomic_get_delete("atomic_probe_absent") is None
