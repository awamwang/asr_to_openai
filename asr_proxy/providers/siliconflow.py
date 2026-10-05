# -*- coding: utf-8 -*-
"""硅基流动 SenseVoice ASR：POST {base_url}/audio/transcriptions。

文档：https://siliconflow.readme.io/reference/createaudiotranscriptions
默认模型：FunAudioLLM/SenseVoiceSmall（中文优化，常有免费额度）。
"""
from __future__ import annotations

import mimetypes
import os
from typing import Any, Dict, List, Optional

import httpx

from asr_proxy.config import ProviderConfig, normalize_proxy
from asr_proxy.providers.base import AsrProvider, AsrResult
from asr_proxy.textutil import strip_model_tags

DEFAULT_MODEL = "FunAudioLLM/SenseVoiceSmall"
DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_TIMEOUT = 120.0

# 对外短名 → 上游完整模型 ID
_MODEL_ALIASES = {
    "sensevoice": DEFAULT_MODEL,
    "sensevoicesmall": DEFAULT_MODEL,
    "sensevoice-small": DEFAULT_MODEL,
    "funaudiollm/sensevoicesmall": DEFAULT_MODEL,
}

_CONTENT_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".webm": "audio/webm",
    ".mkv": "video/x-matroska",
}

def resolve_model_name(model: str) -> str:
    """将配置/请求中的模型名解析为上游 ID。"""
    m = (model or "").strip()
    if not m:
        return DEFAULT_MODEL
    key = m.lower().replace(" ", "")
    return _MODEL_ALIASES.get(key, m)


def clean_sensevoice_text(text: str) -> str:
    """去掉 SenseVoice 情感/语种等特殊标签，只保留正文（兼容旧调用）。"""
    return strip_model_tags(text).strip()


def _guess_content_type(filename: str) -> str:
    """根据文件名推断 Content-Type。"""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in _CONTENT_TYPES:
        return _CONTENT_TYPES[ext]
    guessed, _ = mimetypes.guess_type(filename or "")
    return guessed or "application/octet-stream"


class SiliconFlowProvider(AsrProvider):
    """硅基流动 OpenAI 兼容语音转写。

    配置项（config.yaml / 环境变量）：
      - api_key  ← SILICONFLOW_API_KEY / SF_API_KEY
      - base_url ← SILICONFLOW_BASE_URL（默认 https://api.siliconflow.cn/v1）
      - proxy    ← SILICONFLOW_PROXY（一般国内直连即可）
      - timeout  ← SILICONFLOW_TIMEOUT（秒）
      - models   ← SILICONFLOW_ASR_MODELS

    官方接口：POST {base_url}/audio/transcriptions
    """

    name = "siliconflow"

    def __init__(
        self,
        config: ProviderConfig,
        ffmpeg_path: str = "ffmpeg",
        client: Optional[httpx.Client] = None,
    ):
        super().__init__(config, ffmpeg_path)
        self.timeout = float(config.timeout or DEFAULT_TIMEOUT)
        self.proxy = normalize_proxy(config.proxy)
        self._client = client

    @property
    def default_model(self) -> str:
        return self.config.model or DEFAULT_MODEL

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.config.api_key}"}

    def _transcribe_url(self) -> str:
        base = (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")
        return f"{base}/audio/transcriptions"

    def _make_client(self) -> httpx.Client:
        kwargs: Dict[str, Any] = {"timeout": self.timeout}
        if self.proxy:
            kwargs["proxy"] = self.proxy
        return httpx.Client(**kwargs)

    def recognize(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        model: Optional[str] = None,
        language: Optional[str] = None,
    ) -> AsrResult:
        if not self.enabled:
            raise RuntimeError("硅基流动 ASR 未启用（缺少 api_key）")
        if not audio_bytes:
            raise ValueError("empty audio")

        requested = (model or "").strip() or self.default_model
        used_model = resolve_model_name(requested)
        safe_name = (filename or "audio.wav").strip() or "audio.wav"
        content_type = _guess_content_type(safe_name)

        # 官方目前仅要求 model + file，不强制 response_format / language
        data: Dict[str, str] = {"model": used_model}
        lang = (language or "").strip()
        if lang and lang.lower() not in ("auto", "null", "none"):
            data["language"] = lang

        files = {"file": (safe_name, audio_bytes, content_type)}

        client = self._client
        owns_client = client is None
        if owns_client:
            client = self._make_client()
        assert client is not None

        try:
            resp = client.post(
                self._transcribe_url(),
                headers=self._auth_headers(),
                data=data,
                files=files,
            )
        except httpx.RequestError as e:
            raise RuntimeError(
                f"硅基流动转写请求失败: {type(e).__name__}: {e}"
            ) from e
        finally:
            if owns_client:
                client.close()

        if resp.status_code != 200:
            body = (resp.text or "")[:500]
            raise RuntimeError(
                f"硅基流动转写错误 status={resp.status_code} body={body}"
            )

        try:
            payload = resp.json()
        except ValueError as e:
            raise RuntimeError(
                f"硅基流动返回非 JSON: {(resp.text or '')[:200]}"
            ) from e
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"硅基流动响应格式异常: {type(payload).__name__}"
            )

        text = clean_sensevoice_text(str(payload.get("text") or ""))
        if not text:
            raise RuntimeError("硅基流动返回空文本")

        return AsrResult(
            text=text,
            language=lang or "chinese",
            duration=0.0,
            segments=None,
            provider=self.name,
            model=requested if requested else used_model,
        )
