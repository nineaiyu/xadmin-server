#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""图片验证码音频合成（flite + 可选 sox 噪声）。

旧 ``captcha_audio`` 直连 DB 的链路已删除；本模块只负责把文本合成 WAV，
由统一协议的 ``image`` 题型音频端点调用。TTS 失败不影响图片题本身。
"""

import os
import subprocess
import tempfile

from django.conf import settings


def synthesize_audio(text: str, key: str) -> str | None:
    """把文本合成为临时 WAV；未配置 flite 或合成失败返回 None。"""
    flite = str(getattr(settings, "CAPTCHA_FLITE_PATH", "") or "")
    if not flite or not text:
        return None
    path = str(os.path.join(tempfile.gettempdir(), f"captcha_{key}.wav"))
    try:
        subprocess.run([flite, "-t", text, "-o", path], check=False, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None

    sox = str(getattr(settings, "CAPTCHA_SOX_PATH", "") or "")
    if sox and os.path.isfile(path):
        noise_path = str(os.path.join(tempfile.gettempdir(), f"captcha_{key}_noise.wav"))
        merged_path = str(os.path.join(tempfile.gettempdir(), f"captcha_{key}_merged.wav"))
        try:
            subprocess.run(
                [sox, "-r", "8000", "-n", noise_path, "synth", "2", "brownnoise", "gain", "-15"],
                check=False,
                timeout=2,
            )
            subprocess.run(
                [sox, "-m", noise_path, path, "-t", "wavpcm", "-b", "16", merged_path],
                check=False,
                timeout=2,
            )
            if os.path.isfile(merged_path):
                os.replace(merged_path, path)
        except (OSError, subprocess.SubprocessError):
            pass
        finally:
            for leftover in (noise_path, merged_path):
                if os.path.isfile(leftover):
                    try:
                        os.remove(leftover)
                    except OSError:
                        pass

    return path if os.path.isfile(path) else None
