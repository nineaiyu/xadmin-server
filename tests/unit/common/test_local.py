# -*- coding: utf-8 -*-
"""common.local 请求持有器（自 server/utils.py 归位后的本体行为）。"""

from common.local import get_current_request, set_current_request


class TestCurrentRequest:
    def test_round_trip(self):
        assert get_current_request() is None
        set_current_request("fake-request")
        assert get_current_request() == "fake-request"
        set_current_request(None)
        assert get_current_request() is None

    def test_none_clears_binding(self):
        set_current_request("fake-request")
        set_current_request(None)
        assert get_current_request() is None
