# -*- coding: utf-8 -*-
"""腾讯云录音文件识别极速版（Flash）：本地上传同步返回。

文档：https://cloud.tencent.com/document/product/1093/52097
engine_type 取自配置/请求的模型名（如 16k_zh、16k_zh-PY）。
大模型 2.0 异步需公网 URL，未接入。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from asr_proxy.config import ProviderConfig
from asr_proxy.providers.base import AsrProvider, AsrResult
from asr_proxy.providers.errors import AsrProviderError

DEFAULT_MODEL = "16k_zh"
DEFAULT_TIMEOUT = 300.0
FLASH_HOST = "asr.cloud.tencent.com"
_PAUSE_PUNCT = frozenset("。．.")

_FORMAT_BY_EXT = {
    ".wav": "wav",
    ".pcm": "pcm",
    ".mp3": "mp3",
    ".m4a": "m4a",
    ".aac": "aac",
    ".amr": "amr",
    ".ogg": "ogg-opus",
    ".opus": "ogg-opus",
    ".speex": "speex",
    ".silk": "silk",
}

# Flash 业务错误码 → 统一 code
_FLASH_CODE_MAP = {
    4002: "auth",
    4003: "not_activated",
    4004: "quota",
    4005: "quota",
}


def _voice_format(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    return _FORMAT_BY_EXT.get(ext, "wav")


def build_flash_query(params: Dict[str, Any]) -> str:
    """按 key 字典序拼接查询串（签名与请求 URL 共用；值不额外 percent-encode）。"""
    items = sorted((str(k), str(v)) for k, v in params.items() if v is not None)
    return "&".join(f"{k}={v}" for k, v in items)


def flash_authorization(
    app_id: str,
    secret_id: str,
    secret_key: str,
    query: str,
) -> str:
    """HMAC-SHA1 + Base64，签名原文 = POST + host + path + ? + query。"""
    path = f"/asr/flash/v1/{app_id}"
    sign_str = f"POST{FLASH_HOST}{path}?{query}"
    digest = hmac.new(
        secret_key.encode("utf-8"),
        sign_str.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _map_flash_code(code: int) -> str:
    return _FLASH_CODE_MAP.get(int(code), "upstream")


def _join_clauses(left: str, right: str) -> str:
    """合并短停顿切出的两段：把停顿句号改成逗号，避免「的。这类。」式碎句。"""
    l = (left or "").rstrip()
    r = (right or "").lstrip()
    if not l:
        return r
    if not r:
        return l
    if l[-1] in _PAUSE_PUNCT:
        l = l[:-1]
        if l and l[-1] not in "，,、；;":
            return f"{l}，{r}"
        return l + r
    return l + r


def merge_flash_sentences(
    sentences: List[Dict[str, Any]],
    silence_ms: int,
) -> List[Dict[str, Any]]:
    """按句间静音合并 sentence_list，对齐阿里云 max_sentence_silence。"""
    if not sentences:
        return []
    thresh = max(0, int(silence_ms))
    out = [dict(sentences[0])]
    for sent in sentences[1:]:
        prev = out[-1]
        try:
            gap = int(sent["start_ms"]) - int(prev["end_ms"])
        except (TypeError, ValueError, KeyError):
            gap = thresh + 1
        if gap <= thresh:
            prev["text"] = _join_clauses(
                str(prev.get("text") or ""),
                str(sent.get("text") or ""),
            )
            try:
                prev["end_ms"] = max(int(prev["end_ms"]), int(sent["end_ms"]))
            except (TypeError, ValueError, KeyError):
                pass
        else:
            out.append(dict(sent))
    return out


def _extract_flash_text(
    payload: Dict[str, Any],
    max_sentence_silence: int = 1300,
) -> Tuple[str, float, List[dict]]:
    """从 flash_result 提取全文、时长(秒)、segments。有分句时按静音合并后再拼全文。"""
    duration_ms = payload.get("audio_duration") or 0
    try:
        duration = float(duration_ms) / 1000.0
    except (TypeError, ValueError):
        duration = 0.0

    results = payload.get("flash_result") or []
    if not isinstance(results, list) or not results:
        return "", duration, []

    texts: List[str] = []
    segments: List[dict] = []
    seg_id = 0
    for ch in results:
        if not isinstance(ch, dict):
            continue
        raw_sents: List[Dict[str, Any]] = []
        for sent in ch.get("sentence_list") or []:
            if not isinstance(sent, dict):
                continue
            st = str(sent.get("text") or "").strip()
            if not st:
                continue
            try:
                start_ms = int(float(sent.get("start_time") or 0))
                end_ms = int(float(sent.get("end_time") or 0))
            except (TypeError, ValueError):
                start_ms, end_ms = 0, 0
            raw_sents.append({"text": st, "start_ms": start_ms, "end_ms": end_ms})
        merged = merge_flash_sentences(raw_sents, max_sentence_silence)
        if merged:
            texts.append("".join(str(s.get("text") or "") for s in merged))
            for s in merged:
                segments.append({
                    "id": seg_id,
                    "start": float(s.get("start_ms") or 0) / 1000.0,
                    "end": float(s.get("end_ms") or 0) / 1000.0,
                    "text": str(s.get("text") or ""),
                })
                seg_id += 1
        else:
            channel_text = str(ch.get("text") or "").strip()
            if channel_text:
                texts.append(channel_text)

    return "".join(texts).strip(), duration, segments


class TencentProvider(AsrProvider):
    """腾讯云 ASR 极速版（本地 bytes 上传）。

    配置：secret_id / secret_key / app_id / models（默认 16k_zh）
    """

    name = "tencent"

    def __init__(
        self,
        config: ProviderConfig,
        ffmpeg_path: str = "ffmpeg",
        client: Optional[httpx.Client] = None,
    ):
        super().__init__(config, ffmpeg_path)
        self.timeout = float(config.timeout or DEFAULT_TIMEOUT)
        self._client = client

    @property
    def default_model(self) -> str:
        return self.config.model or DEFAULT_MODEL

    def _make_client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout)

    def recognize(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        model: Optional[str] = None,
        language: Optional[str] = None,
    ) -> AsrResult:
        _ = language  # Flash 用语种由 engine_type 决定
        if not self.enabled:
            raise AsrProviderError(
                "腾讯云未启用（缺少 secret_id/secret_key）",
                code="auth", provider=self.name,
            )
        app_id = (self.config.app_id or "").strip()
        secret_id = (self.config.secret_id or "").strip()
        secret_key = (self.config.secret_key or "").strip()
        if not app_id:
            raise AsrProviderError(
                "缺少 app_id（账号 APPID）",
                code="auth", provider=self.name,
            )
        if not audio_bytes:
            raise AsrProviderError(
                "empty audio", code="upstream", provider=self.name,
            )

        used_model = (model or "").strip() or self.default_model
        if used_model != "16k_zh_en" and used_model.endswith("_2.0"):
            raise AsrProviderError(
                "录音文件·大模型 2.0 异步需公网音频 URL，当前代理仅支持本地上传的极速版 Flash",
                code="upstream",
                provider=self.name,
                model=used_model,
            )
        engine_type = used_model if used_model else DEFAULT_MODEL

        params = {
            "engine_type": engine_type,
            # 签名示例中的 extra_punc=0：不额外插标点，减轻停顿处乱加句号
            "extra_punc": 0,
            "filter_punc": 0,
            "first_channel_only": 1,
            "secretid": secret_id,
            "speaker_diarization": 0,
            "timestamp": int(time.time()),
            "voice_format": _voice_format(filename),
            "word_info": 0,
        }
        query = build_flash_query(params)
        auth = flash_authorization(app_id, secret_id, secret_key, query)
        url = f"https://{FLASH_HOST}/asr/flash/v1/{app_id}?{query}"
        headers = {
            "Host": FLASH_HOST,
            "Authorization": auth,
            "Content-Type": "application/octet-stream",
        }

        client = self._client
        owns_client = client is None
        if owns_client:
            client = self._make_client()
        assert client is not None

        try:
            try:
                resp = client.post(url, headers=headers, content=audio_bytes)
            except httpx.RequestError as e:
                raise AsrProviderError(
                    f"Flash 请求失败: {type(e).__name__}: {e}",
                    code="upstream",
                    provider=self.name,
                    model=used_model,
                ) from e
        finally:
            if owns_client:
                client.close()

        try:
            payload = resp.json()
        except ValueError as e:
            raise AsrProviderError(
                f"返回非 JSON status={resp.status_code} body={(resp.text or '')[:200]}",
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

        code = payload.get("code")
        try:
            code_int = int(code)
        except (TypeError, ValueError):
            code_int = -1
        if code_int != 0:
            msg = str(payload.get("message") or "").strip() or f"code={code_int}"
            raise AsrProviderError(
                msg,
                code=_map_flash_code(code_int),
                provider=self.name,
                model=used_model,
            )

        text, duration, segments = _extract_flash_text(
            payload,
            max_sentence_silence=self.config.max_sentence_silence,
        )
        if not text:
            raise AsrProviderError(
                "返回空文本",
                code="empty",
                provider=self.name,
                model=used_model,
            )

        return AsrResult(
            text=text,
            language="chinese",
            duration=duration,
            segments=segments or None,
            provider=self.name,
            model=used_model,
        )
