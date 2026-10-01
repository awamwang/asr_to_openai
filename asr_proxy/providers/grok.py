# -*- coding: utf-8 -*-
"""Grok（xAI）ASR 占位：仅保留配置，识别逻辑待实现。"""
from __future__ import annotations

from asr_proxy.config import ProviderConfig
from asr_proxy.providers.base import AsrProvider, AsrResult


class GrokProvider(AsrProvider):
    """xAI Grok Speech-to-Text 占位实现。

    配置项（config.yaml / 环境变量）：
      - api_key  ← XAI_API_KEY / GROK_API_KEY
      - base_url ← GROK_BASE_URL / XAI_BASE_URL（默认 https://api.x.ai/v1）
      - model    ← GROK_ASR_MODEL / XAI_ASR_MODEL

    官方接口参考：POST https://api.x.ai/v1/stt
    """

    name = "grok"

    def __init__(self, config: ProviderConfig, ffmpeg_path: str = "ffmpeg"):
        super().__init__(config, ffmpeg_path)

    def recognize(self, audio_bytes: bytes, filename: str = "audio.wav") -> AsrResult:
        if not self.enabled:
            raise RuntimeError("Grok ASR 未启用（缺少 api_key）")
        # 占位：填写 API Key 后可启用，但调用会明确报未实现，便于 auto 切换到其他服务商
        raise NotImplementedError(
            "Grok ASR 尚未实现。"
            f"已配置 base_url={self.config.base_url!r} model={self.config.model!r}，"
            "待后续对接 xAI /v1/stt。"
        )
