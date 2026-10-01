# -*- coding: utf-8 -*-
"""阿里云百炼 DashScope ASR 服务商。"""
from __future__ import annotations

from http import HTTPStatus

from asr_proxy.audio import cleanup, to_wav_16k_mono, write_temp_audio
from asr_proxy.config import ProviderConfig
from asr_proxy.providers.base import AsrProvider, AsrResult


class AliyunProvider(AsrProvider):
    """qwen-audio 流式 ASR（SDK 非流式 call）适配。"""

    name = "aliyun"

    def __init__(self, config: ProviderConfig, ffmpeg_path: str = "ffmpeg"):
        super().__init__(config, ffmpeg_path)

    def _configure_dashscope(self):
        import dashscope
        dashscope.api_key = self.config.api_key
        if self.config.workspace_id:
            dashscope.base_websocket_api_url = (
                f"wss://{self.config.workspace_id}.cn-beijing.maas.aliyuncs.com"
                f"/api-ws/v1/inference"
            )
        return dashscope

    def recognize(self, audio_bytes: bytes, filename: str = "audio.wav") -> AsrResult:
        if not self.enabled:
            raise RuntimeError("阿里云 ASR 未启用（缺少 api_key）")
        if not audio_bytes:
            raise ValueError("empty audio")

        from dashscope.audio.asr import Recognition

        import os
        suffix = os.path.splitext(filename or "")[1] or ".wav"
        tmp = write_temp_audio(audio_bytes, suffix=suffix)
        wav = None
        try:
            wav = to_wav_16k_mono(tmp, self.ffmpeg_path)
            self._configure_dashscope()
            model = self.config.model or "qwen-audio-3.1-asr-flash-streaming"
            recognition = Recognition(
                model=model, format="wav", sample_rate=16000, callback=None,
            )
            result = recognition.call(wav)
            if result.status_code != HTTPStatus.OK:
                raise RuntimeError(f"DashScope error: {result.message}")

            raw = result.get_sentence()
            if isinstance(raw, dict):
                sentences = [raw]
            elif isinstance(raw, list):
                sentences = [s for s in raw if isinstance(s, dict)]
            else:
                sentences = []

            text = "".join((s.get("text", "") or "") for s in sentences)
            segments = []
            duration_ms = 0
            for i, s in enumerate(sentences):
                b = s.get("begin_time", 0) or 0
                e = s.get("end_time", 0) or 0
                duration_ms = max(duration_ms, e)
                segments.append({
                    "id": i,
                    "start": b / 1000.0,
                    "end": e / 1000.0,
                    "text": s.get("text", "") or "",
                })
            if not text:
                raise RuntimeError("DashScope 返回空文本")
            return AsrResult(
                text=text,
                language="chinese",
                duration=duration_ms / 1000.0,
                segments=segments,
                provider=self.name,
            )
        finally:
            cleanup(tmp, wav)
