# -*- coding: utf-8 -*-
"""Groq Whisper ASR：POST {base_url}/audio/transcriptions（OpenAI 兼容）。"""
from __future__ import annotations

import mimetypes
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx

from asr_proxy.config import ProviderConfig, normalize_proxy
from asr_proxy.providers.base import AsrProvider, AsrResult

DEFAULT_MODEL = "whisper-large-v3"
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_TIMEOUT = 300.0

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


def _guess_content_type(filename: str) -> str:
    """根据文件名推断 Content-Type。"""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in _CONTENT_TYPES:
        return _CONTENT_TYPES[ext]
    guessed, _ = mimetypes.guess_type(filename or "")
    return guessed or "application/octet-stream"


def _segments_from_payload(payload: Dict[str, Any]) -> List[dict]:
    """从 verbose_json 响应提取 segments。"""
    raw = payload.get("segments")
    if not isinstance(raw, list):
        return []
    segments: List[dict] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        segments.append({
            "id": int(item.get("id", i) or i),
            "start": float(item.get("start") or 0),
            "end": float(item.get("end") or 0),
            "text": str(item.get("text") or "").strip(),
        })
    return segments


def _parse_transcription(payload: Dict[str, Any]) -> Tuple[str, str, float, List[dict]]:
    """解析 OpenAI/Groq transcription JSON。"""
    text = str(payload.get("text") or "").strip()
    language = str(payload.get("language") or "").strip() or "unknown"
    try:
        duration = float(payload.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return text, language, duration, _segments_from_payload(payload)


class GroqProvider(AsrProvider):
    """Groq OpenAI 兼容语音转写。

    配置项（config.yaml / 环境变量）：
      - api_key  ← GROQ_API_KEY
      - base_url ← GROQ_BASE_URL（默认 https://api.groq.com/openai/v1）
      - proxy    ← GROQ_PROXY（访问 Groq 常需代理）
      - timeout  ← GROQ_TIMEOUT（秒）
      - models   ← GROQ_ASR_MODELS / GROQ_DEFAULT_MODEL

    官方接口：POST {base_url}/audio/transcriptions
    """

    name = "groq"

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
            raise RuntimeError("Groq ASR 未启用（缺少 api_key）")
        if not audio_bytes:
            raise ValueError("empty audio")

        used_model = (model or "").strip() or self.default_model
        safe_name = (filename or "audio.wav").strip() or "audio.wav"
        content_type = _guess_content_type(safe_name)

        data: Dict[str, str] = {
            "model": used_model,
            # verbose_json 带回 duration / segments，便于对齐阿里云返回
            "response_format": "verbose_json",
        }
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
            hint = ""
            if not self.proxy:
                hint = "（若无法直连 Groq，请在 config.yaml 配置 providers.groq.proxy）"
            raise RuntimeError(
                f"Groq 转写请求失败: {type(e).__name__}: {e}{hint}"
            ) from e
        finally:
            if owns_client:
                client.close()

        if resp.status_code != 200:
            body = (resp.text or "")[:500]
            raise RuntimeError(
                f"Groq 转写错误 status={resp.status_code} body={body}"
            )

        try:
            payload = resp.json()
        except ValueError as e:
            raise RuntimeError(
                f"Groq 返回非 JSON: {(resp.text or '')[:200]}"
            ) from e
        if not isinstance(payload, dict):
            raise RuntimeError(f"Groq 响应格式异常: {type(payload).__name__}")

        text, language_out, duration, segments = _parse_transcription(payload)
        if not text:
            raise RuntimeError("Groq 返回空文本")

        return AsrResult(
            text=text,
            language=language_out,
            duration=duration,
            segments=segments or None,
            provider=self.name,
            model=used_model,
        )
