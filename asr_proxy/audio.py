# -*- coding: utf-8 -*-
"""音频预处理：统一转为 16kHz 单声道 PCM WAV；裁首尾静音；探测时长。

优化说明：输入已是 16k 单声道 16-bit PCM WAV 时，跳过 ffmpeg 直接复用
（ffmpeg 进程启动约 0.12s，是预处理的主要成本）；裁首尾静音有纯 Python
近似实现，避免在常见路径上拉起 ffmpeg。仅格式不符/压缩格式才走 ffmpeg。
"""
from __future__ import annotations

import array
import io
import os
import re
import struct
import subprocess
import tempfile
import wave
from typing import Optional, Tuple

# 首尾静音切除：先正放切开头，再反转切结尾（再反转回来）
_TRIM_SILENCE_AF = (
    "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-40dB:"
    "detection=peak,"
    "areverse,"
    "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-40dB:"
    "detection=peak,"
    "areverse"
)

# -40dB 峰值阈值（16-bit PCM）：0.01 * 32768 ≈ 328
_TRIM_THRESHOLD = 328
_TRIM_WINDOW_MS = 50

# ffmpeg / ffprobe 子进程超时（秒）：异常输入或卡死的子进程不得挂住请求
SUBPROCESS_TIMEOUT_SEC = 30.0
# 临时文件后缀白名单：后缀来自客户端 filename，禁止路径分隔符与控制字符
_SAFE_SUFFIX_RE = re.compile(r"[A-Za-z0-9._-]{1,16}\Z")


def safe_temp_suffix(suffix: str) -> str:
    """把客户端可控的扩展名收敛到白名单，非法值统一回退 .wav。"""
    s = (suffix or "").strip()
    return s if _SAFE_SUFFIX_RE.fullmatch(s) else ".wav"


def converted_wav_path(
    src_path: str,
    sample_rate: int = 16000,
    trim_silence: bool = False,
) -> str:
    """与 to_wav_mono 一致的输出路径，供调用方在失败分支也能清理残留文件。"""
    rate = int(sample_rate or 16000)
    tag = "_trim" if trim_silence else ""
    return f"{src_path}_{rate}{tag}.wav"


def to_wav_mono(
    src_path: str,
    sample_rate: int = 16000,
    ffmpeg: str = "ffmpeg",
    *,
    trim_silence: bool = False,
    timeout: float = SUBPROCESS_TIMEOUT_SEC,
) -> str:
    """用 ffmpeg 把任意音频转成指定采样率单声道 PCM WAV，返回新临时文件路径。"""
    rate = int(sample_rate or 16000)
    dst_path = converted_wav_path(src_path, rate, trim_silence)
    cmd = [
        ffmpeg, "-y", "-i", src_path,
        "-ar", str(rate), "-ac", "1", "-c:a", "pcm_s16le",
    ]
    if trim_silence:
        cmd.extend(["-af", _TRIM_SILENCE_AF])
    cmd.append(dst_path)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"ffmpeg convert timeout after {timeout}s") from e
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg convert failed: {(proc.stderr or '')[-500:]}")
    return dst_path


def to_wav_16k_mono(
    src_path: str,
    ffmpeg: str = "ffmpeg",
    *,
    trim_silence: bool = False,
) -> str:
    """用 ffmpeg 把任意音频转成 16kHz 单声道 PCM WAV，返回新临时文件路径。"""
    return to_wav_mono(
        src_path, sample_rate=16000, ffmpeg=ffmpeg, trim_silence=trim_silence,
    )


def parse_wav_format(data: bytes) -> Optional[dict]:
    """解析 RIFF/WAVE 头，返回 fmt 与 data chunk 信息；非 WAV 或解析失败返回 None。

    返回结构：{"fmt": {"format","channels","sample_rate","bits"},
              "data_offset": int, "data_size": int}
    format=1 表示 PCM（无压缩）。
    """
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    fmt = None
    data_offset = None
    data_size = None
    offset = 12
    try:
        while offset + 8 <= len(data):
            chunk_id = data[offset:offset + 4]
            (size,) = struct.unpack("<I", data[offset + 4:offset + 8])
            body = offset + 8
            if chunk_id == b"fmt " and size >= 16 and len(data) >= body + 16:
                audio_format, num_channels, sample_rate, _, _, bits = struct.unpack(
                    "<HHIIHH", data[body:body + 16]
                )
                fmt = {
                    "format": audio_format,
                    "channels": num_channels,
                    "sample_rate": sample_rate,
                    "bits": bits,
                }
            elif chunk_id == b"data":
                data_offset = body
                data_size = size
                break
            # chunk 按偶数字节对齐
            offset = body + size + (size & 1)
    except (struct.error, IndexError):
        return None
    if fmt is None or data_offset is None:
        return None
    return {"fmt": fmt, "data_offset": data_offset, "data_size": data_size}


def is_target_pcm_wav(audio_bytes: bytes, sample_rate: int = 16000) -> bool:
    """是否已是可直接复用的目标格式（PCM / 指定采样率 / 单声道 / 16-bit）。"""
    info = parse_wav_format(audio_bytes)
    fmt = info["fmt"] if info else None
    return bool(
        fmt
        and int(fmt["format"]) == 1
        and int(fmt["sample_rate"]) == int(sample_rate)
        and int(fmt["channels"]) == 1
        and int(fmt["bits"]) == 16
    )


def extract_pcm_data(audio_bytes: bytes, info: Optional[dict] = None) -> bytes:
    """从 WAV 字节中取出 data chunk 的 PCM 数据（越界时截断）。"""
    info = info or parse_wav_format(audio_bytes)
    if info is None:
        raise ValueError("not a WAV file")
    off = int(info["data_offset"])
    size = int(info["data_size"])
    return audio_bytes[off:off + size]


def build_pcm_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
    """把 16-bit 单声道 PCM 打包成标准 WAV 字节。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def trim_silence_pcm(
    pcm: bytes,
    sample_rate: int = 16000,
    *,
    threshold: int = _TRIM_THRESHOLD,
    window_ms: int = _TRIM_WINDOW_MS,
) -> bytes:
    """纯 Python 近似 ffmpeg silenceremove 的首尾静音裁切（-40dB / 50ms 窗口）。

    定位首个/末个超过阈值的采样点，分别向首/尾回退 window_ms 后裁切。
    与 ffmpeg silenceremove 存在约百毫秒级的边界差异（近似实现，保留略多），
    用于跳过 ffmpeg 进程启动；全静音返回 b""。
    """
    if not pcm or len(pcm) % 2:
        return pcm
    arr = array.array("h")
    arr.frombytes(pcm)
    n = len(arr)
    if n == 0:
        return pcm
    window = max(0, int(sample_rate * window_ms / 1000))
    # 分块粗筛：用 C 层的 max/min 跳过整段静音块，只在命中块内逐样本精定位，
    # 避免对几十万采样点跑纯 Python 循环。结果与原逐样本算法完全一致。
    step = window or n
    first = None
    for base in range(0, n, step):
        chunk = arr[base:base + step]
        if not chunk:
            continue
        if max(chunk) >= threshold or min(chunk) <= -threshold:
            for i in range(base, min(base + step, n)):
                v = arr[i]
                if v >= threshold or v <= -threshold:
                    first = i
                    break
            if first is not None:
                break
    if first is None:
        return b""
    start = max(0, first - window)

    last = None
    for base in range(n - 1, -1, -step):
        lo = max(0, base - step + 1)
        chunk = arr[lo:base + 1]
        if not chunk:
            continue
        if max(chunk) >= threshold or min(chunk) <= -threshold:
            for i in range(base, lo - 1, -1):
                v = arr[i]
                if v >= threshold or v <= -threshold:
                    last = i
                    break
            if last is not None:
                break
    if last is None:
        return b""
    end = min(n, last + 1 + window)
    return arr[start:end].tobytes()


def preprocess_audio_bytes(
    audio_bytes: bytes,
    filename: str = "audio.wav",
    ffmpeg: str = "ffmpeg",
    *,
    sample_rate: int = 16000,
    trim_silence: bool = True,
) -> Tuple[bytes, str]:
    """识别前统一预处理：转单声道指定采样率 WAV，可选裁首尾静音。

    输入已是 target 采样率单声道 16-bit PCM WAV 时直接复用/纯 Python 裁静音，
    避免 ffmpeg 进程开销；其余（压缩格式/采样率不符）走 ffmpeg。
    失败时抛出异常，由调用方决定是否回退原音频。

    返回 (wav_bytes, 新文件名)。
    """
    if not audio_bytes:
        raise ValueError("empty audio")
    info = parse_wav_format(audio_bytes)
    fmt = info["fmt"] if info else None
    is_target = (
        info is not None
        and fmt is not None
        and int(fmt["format"]) == 1          # PCM
        and int(fmt["sample_rate"]) == int(sample_rate)
        and int(fmt["channels"]) == 1
        and int(fmt["bits"]) == 16
    )
    if is_target:
        # 已是目标格式：跳过 ffmpeg
        if not trim_silence:
            return audio_bytes, "audio_16k.wav"
        pcm = extract_pcm_data(audio_bytes, info)
        trimmed = trim_silence_pcm(pcm, int(sample_rate))
        return build_pcm_wav(trimmed, int(sample_rate)), "audio_16k.wav"
    suffix = os.path.splitext(filename or "")[1] or ".wav"
    tmp = write_temp_audio(audio_bytes, suffix=suffix)
    wav_path = None
    try:
        wav_path = to_wav_mono(
            tmp,
            sample_rate=sample_rate,
            ffmpeg=ffmpeg,
            trim_silence=trim_silence,
        )
        with open(wav_path, "rb") as f:
            data = f.read()
        if not data:
            raise RuntimeError("preprocess produced empty wav")
        return data, "audio_16k.wav"
    finally:
        # to_wav_mono 抛异常时 wav_path 仍为 None，ffmpeg 可能已生成输出文件；
        # 用同一套路径规则显式清理，避免临时目录堆积。
        cleanup(tmp, wav_path, converted_wav_path(tmp, sample_rate, trim_silence))


def load_pcm_s16le(
    audio_bytes: bytes,
    ffmpeg: str = "ffmpeg",
    sample_rate: int = 16000,
) -> Tuple[bytes, int]:
    """读取 16-bit PCM：已是目标采样率单声道时直接读 data chunk，否则 ffmpeg 转换。

    供各 provider 在识别前使用，避免对已合规的 WAV 重复转换。
    """
    info = parse_wav_format(audio_bytes)
    fmt = info["fmt"] if info else None
    if (
        info is not None
        and fmt is not None
        and int(fmt["format"]) == 1
        and int(fmt["sample_rate"]) == int(sample_rate)
        and int(fmt["channels"]) == 1
        and int(fmt["bits"]) == 16
    ):
        return extract_pcm_data(audio_bytes, info), int(sample_rate)
    tmp = write_temp_audio(audio_bytes, suffix=".wav")
    wav_path = None
    try:
        wav_path = to_wav_mono(
            tmp, sample_rate=sample_rate, ffmpeg=ffmpeg, trim_silence=False,
        )
        from asr_proxy.providers.stream_util import wav_to_pcm_s16le
        return wav_to_pcm_s16le(wav_path)
    finally:
        cleanup(tmp, wav_path, converted_wav_path(tmp, sample_rate))


def write_temp_audio(audio_bytes: bytes, suffix: str = ".wav") -> str:
    """将音频字节写入临时文件，返回路径。后缀统一走白名单净化。"""
    fd, path = tempfile.mkstemp(suffix=safe_temp_suffix(suffix))
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
            timeout=SUBPROCESS_TIMEOUT_SEC,
        )
        if proc.returncode != 0:
            return None
        duration = float((proc.stdout or "").strip())
        return duration if duration > 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
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
