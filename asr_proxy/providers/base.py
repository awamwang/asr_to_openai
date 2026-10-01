# -*- coding: utf-8 -*-
"""ASR 服务商抽象基类。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

from asr_proxy.config import ProviderConfig


@dataclass
class AsrResult:
    """识别结果。"""
    text: str
    language: str = "chinese"
    duration: float = 0.0
    segments: Optional[List[dict]] = None
    provider: str = ""


class AsrProvider(ABC):
    """语音识别服务商接口。"""

    name: str = ""

    def __init__(self, config: ProviderConfig, ffmpeg_path: str = "ffmpeg"):
        self.config = config
        self.ffmpeg_path = ffmpeg_path

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def model_id(self) -> str:
        """该服务商对外暴露的默认模型 ID。"""
        return self.config.model or self.name

    def matches_model(self, model: str) -> bool:
        """判断请求的 model 是否应由本服务商处理。"""
        m = (model or "").strip().lower()
        if not m:
            return False
        if m == self.name.lower():
            return True
        if self.config.model and m == self.config.model.lower():
            return True
        return False

    @abstractmethod
    def recognize(self, audio_bytes: bytes, filename: str = "audio.wav") -> AsrResult:
        """识别音频字节，成功返回 AsrResult，失败抛出异常。"""
        raise NotImplementedError
