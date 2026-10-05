# -*- coding: utf-8 -*-
"""流式 ASR 共用：PCM 分片发送与稳定句收集。

豆包 / 腾讯实时等「整段文件切块走 WebSocket」通道复用此处，
避免各 Provider 各自维护一套 offset 循环与分句拼接。
"""
from __future__ import annotations

import wave
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, Tuple

# 16kHz * 16bit * mono → 32 bytes/ms；8k 则为 16 bytes/ms
PCM_BYTES_PER_MS_16K = 32
PCM_BYTES_PER_MS_8K = 16
DEFAULT_SEGMENT_MS = 200


def wav_to_pcm_s16le(wav_path: str) -> Tuple[bytes, int]:
    """读取 16-bit PCM WAV，返回 (pcm_bytes, sample_rate)。"""
    with wave.open(wav_path, "rb") as wf:
        if wf.getsampwidth() != 2:
            raise RuntimeError(
                f"仅支持 16-bit PCM WAV，当前 sampwidth={wf.getsampwidth()}"
            )
        rate = int(wf.getframerate() or 0)
        return wf.readframes(wf.getnframes()), rate


def pcm_bytes_per_ms(sample_rate: int) -> int:
    """按采样率估算 PCM 每毫秒字节数（16-bit mono）。"""
    if sample_rate <= 8000:
        return PCM_BYTES_PER_MS_8K
    return PCM_BYTES_PER_MS_16K


def iter_pcm_chunks(
    pcm: bytes,
    segment_ms: int = DEFAULT_SEGMENT_MS,
    sample_rate: int = 16000,
) -> Iterable[Tuple[bytes, bool]]:
    """按时长切 PCM，yield (chunk, is_last)。空 pcm 时 yield 一次 (b'', True)。"""
    segment_size = max(1, int(pcm_bytes_per_ms(sample_rate) * max(1, int(segment_ms))))
    if not pcm:
        yield b"", True
        return
    offset = 0
    total = len(pcm)
    while offset < total:
        end = min(offset + segment_size, total)
        yield pcm[offset:end], end >= total
        offset = end


def send_pcm_chunks(
    pcm: bytes,
    send_chunk: Callable[[bytes, bool], None],
    *,
    segment_ms: int = DEFAULT_SEGMENT_MS,
    sample_rate: int = 16000,
    pace: bool = False,
    sleep_fn: Optional[Callable[[float], None]] = None,
) -> None:
    """切分并发送 PCM。

    pace=True 时按 segment_ms 睡眠（腾讯实时要求约 1:1 实时率）。
    sleep_fn 可注入，便于单测。
    """
    import time as _time

    sleeper = sleep_fn or _time.sleep
    delay = (max(1, int(segment_ms)) / 1000.0) if pace else 0.0
    for chunk, is_last in iter_pcm_chunks(pcm, segment_ms, sample_rate):
        send_chunk(chunk, is_last)
        if pace and not is_last and delay > 0:
            sleeper(delay)


@dataclass
class StableSentenceCollector:
    """收集流式「稳定句」（如腾讯 slice_type=2），拼全文与 segments。"""

    sentences: List[dict] = field(default_factory=list)
    _duration_ms: int = 0

    def add(
        self,
        text: str,
        start_ms: int = 0,
        end_ms: int = 0,
        *,
        stable: bool = True,
    ) -> None:
        """追加一句。stable=False 的中间结果忽略（只留最终句）。"""
        if not stable:
            return
        t = (text or "").strip()
        if not t:
            return
        try:
            s = int(start_ms or 0)
            e = int(end_ms or 0)
        except (TypeError, ValueError):
            s, e = 0, 0
        self._duration_ms = max(self._duration_ms, e, s)
        self.sentences.append({
            "text": t,
            "start_ms": s,
            "end_ms": e,
        })

    def note_duration_ms(self, duration_ms: int) -> None:
        try:
            self._duration_ms = max(self._duration_ms, int(duration_ms or 0))
        except (TypeError, ValueError):
            pass

    def finalize(self) -> Tuple[str, List[dict], float]:
        """返回 (全文, segments, duration_sec)。"""
        segments: List[dict] = []
        parts: List[str] = []
        for i, s in enumerate(self.sentences):
            t = str(s.get("text") or "")
            if t:
                parts.append(t)
            segments.append({
                "id": i,
                "start": float(s.get("start_ms") or 0) / 1000.0,
                "end": float(s.get("end_ms") or 0) / 1000.0,
                "text": t,
            })
        text = "".join(parts).strip()
        return text, segments, self._duration_ms / 1000.0
