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
    model: str = ""


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
    def model_names(self) -> List[str]:
        """该服务商配置的模型名列表。"""
        return list(self.config.models)

    @property
    def default_model(self) -> str:
        """默认使用的模型名。"""
        return self.config.model or self.name

    @property
    def model_id(self) -> str:
        """对外暴露的默认模型 ID（服务商/模型）。"""
        return f"{self.name}/{self.default_model}"

    def public_model_ids(self) -> List[str]:
        """该服务商对外暴露的全部模型 ID。"""
        ids = [f"{self.name}/{m}" for m in self.model_names]
        if f"{self.name}/auto" not in ids:
            ids.append(f"{self.name}/auto")
        return ids

    def has_model(self, model_name: str) -> bool:
        """判断模型名是否属于本服务商。"""
        m = (model_name or "").strip().lower()
        if not m:
            return False
        return any(m == x.lower() for x in self.model_names)

    def matches_model(self, model: str) -> bool:
        """判断请求的 model 是否应由本服务商处理（兼容旧格式）。"""
        m = (model or "").strip().lower()
        if not m:
            return False
        if m == self.name.lower():
            return True
        if "/" in m:
            provider, _, rest = m.partition("/")
            if provider == self.name.lower():
                return rest == "auto" or self.has_model(rest) or not rest
            return False
        return self.has_model(m)

    @abstractmethod
    def recognize(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        model: Optional[str] = None,
        language: Optional[str] = None,
    ) -> AsrResult:
        """识别音频字节，成功返回 AsrResult，失败抛出异常。"""
        raise NotImplementedError
