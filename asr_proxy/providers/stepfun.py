# -*- coding: utf-8 -*-
"""阶跃星辰 StepFun ASR：POST {base_url}/audio/transcriptions（OpenAI 兼容）。"""
from __future__ import annotations

import mimetypes
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx

from asr_proxy.config import ProviderConfig, normalize_proxy
from asr_proxy.providers.base import AsrProvider, AsrResult
from asr_proxy.providers.errors import AsrProviderError

DEFAULT_MODEL = "stepaudio-2.5-asr"
DEFAULT_BASE_URL = "https://api.stepfun.com/v1"
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
}


def _guess_content_type(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in _CONTENT_TYPES:
        return _CONTENT_TYPES[ext]
    guessed, _ = mimetypes.guess_type(filename or "")
    return guessed or "application/octet-stream"


def _segments_from_payload(payload: Dict[str, Any]) -> List[dict]:
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
    text = str(payload.get("text") or payload.get("result") or "").strip()
    language = str(payload.get("language") or "").strip() or "unknown"
    try:
        duration = float(payload.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return text, language, duration, _segments_from_payload(payload)


def _classify_http_error(status: int, body: str) -> str:
    if status in (401, 403):
        return "auth"
    low = (body or "").lower()
    if any(k in low for k in ("invalid api", "unauthorized", "api key", "apikey", "auth")):
        return "auth"
    if any(k in low for k in ("quota", "insufficient", "balance", "billing")):
        return "quota"
    if any(k in low for k in ("not activated", "not open", "未开通", "permission")):
        return "permission" if "permission" in low else "not_activated"
    return "upstream"


class StepFunProvider(AsrProvider):
    """阶跃星辰 OpenAI 兼容语音转写。

    配置：api_key / base_url / proxy / timeout / models
    环境变量：STEPFUN_API_KEY、STEPFUN_BASE_URL 等
    """

    name = "stepfun"

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
            raise AsrProviderError(
                "阶跃星辰未启用（缺少 api_key）",
                code="auth", provider=self.name,
            )
        if not audio_bytes:
            raise AsrProviderError(
                "empty audio", code="upstream", provider=self.name,
            )

        used_model = (model or "").strip() or self.default_model
        safe_name = (filename or "audio.wav").strip() or "audio.wav"
        content_type = _guess_content_type(safe_name)

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
            try:
                resp = client.post(
                    self._transcribe_url(),
                    headers=self._auth_headers(),
                    data=data,
                    files=files,
                )
            except httpx.RequestError as e:
                raise AsrProviderError(
                    f"转写请求失败: {type(e).__name__}: {e}",
                    code="upstream",
                    provider=self.name,
                    model=used_model,
                ) from e
        finally:
            if owns_client:
                client.close()

        body = (resp.text or "")[:500]
        if resp.status_code != 200:
            raise AsrProviderError(
                f"转写错误 status={resp.status_code} body={body}",
                code=_classify_http_error(resp.status_code, body),
                provider=self.name,
                model=used_model,
            )

        try:
            payload = resp.json()
        except ValueError as e:
            raise AsrProviderError(
                f"返回非 JSON: {body[:200]}",
                code="upstream",
                provider=self.name,
                model=used_model,
            ) from e
        if not isinstance(payload, dict):
            raise AsrProviderError(
                f"响应格式异常: {type(payload).__name__}",
                code="upstream",
                provider=self.name,
                model=used_model,
            )

        text, language_out, duration, segments = _parse_transcription(payload)
        if not text:
            raise AsrProviderError(
                "返回空文本",
                code="empty",
                provider=self.name,
                model=used_model,
            )

        return AsrResult(
            text=text,
            language=language_out,
            duration=duration,
            segments=segments or None,
            provider=self.name,
            model=used_model,
        )
