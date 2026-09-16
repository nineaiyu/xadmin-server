#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""测试日志与生产日志隔离（发布窗口硬门禁判据来源保护）。

背景：`data/logs/server.log` 是发布窗口两项硬门禁（CSP enforce 切换 / AES v1
解密关闭）的**唯一判据来源**——运维按该文件中 `CSP violation:` 与
`aes_v1_decrypt_used` 的连续清零天数决定是否切换开关。而 pytest（settings_test）
与 E2E 后端（settings_e2e）此前与生产共用同一份 LOGGING，测试流量持续写入该文件：

- 集成测试用 `AESCipherV2(...).encrypt()` 构造登录 / 改密请求，而服务端加密器
  产出的是 v1（`Salted__`）格式，请求回程必然命中 v1 解密观测点；
- 单测直接断言违规上报 / 观测日志会被记录，天然产生 `CSP violation:` 行。

两者叠加使生产日志的清零判据长期被自家测试污染。隔离口径：把三个文件 handler
（server / drf_exception / unexpected_exception）的落盘路径统一改写至
`tmp/test_logs/`，console 与断言能力（caplog / assertLogs）不受影响——只改落盘
路径，不改 logger 结构。
"""

import os

# 与 server/settings/logging.py 注册的文件 handler 对齐
FILE_HANDLERS = ("server", "drf_exception", "unexpected_exception")


def isolate_file_handlers(logging_config: dict, project_dir) -> str:
    """把测试环境的文件 handler 落盘路径改写至 `tmp/test_logs/`。

    必须在 `django.setup()`（dictConfig 实际应用配置）之前调用——各测试
    settings 模块尾部即满足。返回隔离后的日志目录，便于测试断言。
    """
    log_dir = os.path.join(str(project_dir), "tmp", "test_logs")
    os.makedirs(log_dir, exist_ok=True)
    handlers = logging_config.get("handlers", {})
    for name in FILE_HANDLERS:
        if name in handlers:
            handlers[name]["filename"] = os.path.join(log_dir, f"{name}.log")
    return log_dir
