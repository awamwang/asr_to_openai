# -*- coding: utf-8 -*-
"""音频预处理：统一转为 16kHz 单声道 PCM WAV。"""
from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Optional


def to_wav_16k_mono(src_path: str, ffmpeg: str = "ffmpeg") -> str:
    """用 ffmpeg 把任意音频转成 16kHz 单声道 PCM WAV，返回新临时文件路径。"""
    dst_path = src_path + "_16k.wav"
    cmd = [
        ffmpeg, "-y", "-i", src_path,
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", dst_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg convert failed: {(proc.stderr or '')[-500:]}")
    return dst_path


def write_temp_audio(audio_bytes: bytes, suffix: str = ".wav") -> str:
    """将音频字节写入临时文件，返回路径。"""
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(audio_bytes)
    return path


def cleanup(*paths: Optional[str]) -> None:
    """删除临时文件，忽略失败。"""
    for p in paths:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
