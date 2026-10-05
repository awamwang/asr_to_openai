# -*- coding: utf-8 -*-
"""音频预处理：统一转为 16kHz 单声道 PCM WAV；探测音频时长。"""
from __future__ import annotations

import os
import struct
import subprocess
import tempfile
from typing import Optional


def to_wav_mono(
    src_path: str,
    sample_rate: int = 16000,
    ffmpeg: str = "ffmpeg",
) -> str:
    """用 ffmpeg 把任意音频转成指定采样率单声道 PCM WAV，返回新临时文件路径。"""
    rate = int(sample_rate or 16000)
    dst_path = f"{src_path}_{rate}.wav"
    cmd = [
        ffmpeg, "-y", "-i", src_path,
        "-ar", str(rate), "-ac", "1", "-c:a", "pcm_s16le", dst_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg convert failed: {(proc.stderr or '')[-500:]}")
    return dst_path


def to_wav_16k_mono(src_path: str, ffmpeg: str = "ffmpeg") -> str:
    """用 ffmpeg 把任意音频转成 16kHz 单声道 PCM WAV，返回新临时文件路径。"""
    return to_wav_mono(src_path, sample_rate=16000, ffmpeg=ffmpeg)


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


def _wav_duration_from_header(audio_bytes: bytes) -> Optional[float]:
    """解析 RIFF/WAVE 头估算时长（秒），非 WAV 或解析失败返回 None。"""
    if len(audio_bytes) < 12 or audio_bytes[:4] != b"RIFF" or audio_bytes[8:12] != b"WAVE":
        return None

    byte_rate = 0
    data_size = None
    offset = 12
    try:
        while offset + 8 <= len(audio_bytes):
            chunk_id = audio_bytes[offset:offset + 4]
            (size,) = struct.unpack("<I", audio_bytes[offset + 4:offset + 8])
            body = offset + 8
            if chunk_id == b"fmt " and size >= 16:
                fmt = audio_bytes[body:body + size]
                if len(fmt) >= 16:
                    _, num_channels, sample_rate, br, _, _ = struct.unpack(
                        "<HHIIHH", fmt[:16]
                    )
                    byte_rate = br or (sample_rate * num_channels)
            elif chunk_id == b"data":
                data_size = size
                break
            # chunk 按偶数字节对齐
            offset = body + size + (size & 1)
    except (struct.error, IndexError):
        return None

    if byte_rate and data_size is not None:
        duration = data_size / float(byte_rate)
        return duration if duration > 0 else None
    return None


def _ffprobe_duration(audio_bytes: bytes, ffmpeg_path: str = "ffmpeg") -> Optional[float]:
    """用 ffprobe（ffmpeg 同目录/同 PATH）探测时长（秒），失败返回 None。"""
    probe = "ffprobe"
    parent = os.path.dirname(ffmpeg_path or "")
    if parent:
        probe = os.path.join(parent, "ffprobe")
    tmp = write_temp_audio(audio_bytes, suffix=".audio")
    try:
        proc = subprocess.run(
            [
                probe, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                tmp,
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return None
        duration = float((proc.stdout or "").strip())
        return duration if duration > 0 else None
    except (OSError, ValueError):
        return None
    finally:
        cleanup(tmp)


def probe_audio_duration(audio_bytes: bytes, ffmpeg_path: str = "ffmpeg") -> Optional[float]:
    """探测音频时长（秒）。

    优先解析 WAV 头（免外部命令）；非 WAV 或解析失败时退回 ffprobe；
    两者都失败返回 None（由调用方降级：不做时长硬限过滤；费用策略用 1s 档比价）。
    """
    if not audio_bytes:
        return None
    duration = _wav_duration_from_header(audio_bytes)
    if duration is not None:
        return duration
    return _ffprobe_duration(audio_bytes, ffmpeg_path)
