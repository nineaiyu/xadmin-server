#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""图片验证码音频合成（flite + 可选 sox 噪声）。

旧 ``captcha_audio`` 直连 DB 的链路已删除；本模块只负责把文本合成 WAV，
由统一协议的 ``image`` 题型音频端点调用。TTS 失败不影响图片题本身。
"""

import os
import re
import subprocess
import tempfile

from django.conf import settings

from common.utils import get_logger

logger = get_logger(__name__)


def _safe_key(key: str) -> str:
    """临时文件前缀只保留安全字符（挑战 id 正常为十六进制）。"""
    return re.sub(r"[^0-9a-zA-Z_-]", "", str(key or ""))[:32] or "audio"


def _temp_file(key: str, suffix: str) -> str:
    """唯一临时文件（``mkstemp`` 避免并发同名互相覆盖与符号链接风险）。"""
    handle, path = tempfile.mkstemp(prefix=f"captcha_{_safe_key(key)}_", suffix=suffix)
    os.close(handle)
    return path


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def synthesize_audio(text: str, key: str) -> str | None:
    """把文本合成为临时 WAV；未配置 flite 或合成失败返回 None。"""
    flite = str(getattr(settings, "CAPTCHA_FLITE_PATH", "") or "")
    if not flite or not text:
        return None
    path = _temp_file(key, ".wav")
    try:
        result = subprocess.run([flite, "-t", text, "-o", path], check=False, timeout=2, capture_output=True)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("captcha audio synth failed (challenge=%s): %s", _safe_key(key), exc)
        _remove(path)
        return None
    if result.returncode != 0 or not os.path.isfile(path):
        logger.warning(
            "captcha audio synth unavailable (challenge=%s, rc=%s)",
            _safe_key(key),
            result.returncode,
        )
        _remove(path)
        return None

    sox = str(getattr(settings, "CAPTCHA_SOX_PATH", "") or "")
    if sox:
        noise_path = _temp_file(key, "_noise.wav")
        merged_path = _temp_file(key, "_merged.wav")
        try:
            subprocess.run(
                [sox, "-r", "8000", "-n", noise_path, "synth", "2", "brownnoise", "gain", "-15"],
                check=False,
                timeout=2,
                capture_output=True,
            )
            subprocess.run(
                [sox, "-m", noise_path, path, "-t", "wavpcm", "-b", "16", merged_path],
                check=False,
                timeout=2,
                capture_output=True,
            )
            if os.path.isfile(merged_path) and os.path.getsize(merged_path) > 0:
                os.replace(merged_path, path)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("captcha audio noise mix failed (challenge=%s): %s", _safe_key(key), exc)
        finally:
            for leftover in (noise_path, merged_path):
                if os.path.isfile(leftover):
                    _remove(leftover)

    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        _remove(path)
        return None
    return path
