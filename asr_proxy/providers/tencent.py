# -*- coding: utf-8 -*-
"""腾讯云 ASR：录音文件识别极速版（Flash）+ 实时语音识别（WebSocket）。

Flash：https://cloud.tencent.com/document/product/1093/52097
实时：https://cloud.tencent.com/document/product/1093/48982

模型约定：
  - `16k_zh` / `16k_zh-PY` … → Flash 同步上传
  - `realtime-16k_zh` … → 实时 WebSocket（engine_model_type 去掉 realtime- 前缀）
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import random
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

from asr_proxy.audio import cleanup, to_wav_mono, write_temp_audio
from asr_proxy.config import ProviderConfig
from asr_proxy.providers.base import AsrProvider, AsrResult
from asr_proxy.providers.errors import AsrProviderError
from asr_proxy.providers.stream_util import (
    DEFAULT_SEGMENT_MS,
    StableSentenceCollector,
    send_pcm_chunks,
    wav_to_pcm_s16le,
)

DEFAULT_MODEL = "16k_zh"
DEFAULT_TIMEOUT = 300.0
FLASH_HOST = "asr.cloud.tencent.com"
REALTIME_HOST = "asr.cloud.tencent.com"
REALTIME_PREFIX = "realtime-"
# voice_format=1 → PCM（实时接口）
REALTIME_VOICE_FORMAT_PCM = 1
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

# Flash / 实时业务错误码 → 统一 code
_CODE_MAP = {
    4002: "auth",
    4003: "not_activated",
    4004: "quota",
    4005: "quota",
}


def is_realtime_model(model: str) -> bool:
    """是否走实时 WebSocket 通道。"""
    m = (model or "").strip().lower()
    return m.startswith(REALTIME_PREFIX)


def realtime_engine_type(model: str) -> str:
    """realtime-16k_zh → 16k_zh。"""
    m = (model or "").strip()
    if m.lower().startswith(REALTIME_PREFIX):
        return m[len(REALTIME_PREFIX):]
    return m or DEFAULT_MODEL


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


def build_realtime_query(params: Dict[str, Any]) -> str:
    """实时接口查询串（不含 signature；字典序）。"""
    items = sorted((str(k), str(v)) for k, v in params.items() if v is not None)
    return "&".join(f"{k}={v}" for k, v in items)


def realtime_signature(secret_key: str, sign_url: str) -> str:
    """实时签名：HMAC-SHA1(sign_url) → Base64（尚未 urlencode）。"""
    digest = hmac.new(
        secret_key.encode("utf-8"),
        sign_url.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def build_realtime_ws_url(
    app_id: str,
    secret_id: str,
    secret_key: str,
    engine_model_type: str,
    *,
    voice_id: Optional[str] = None,
    timestamp: Optional[int] = None,
    nonce: Optional[int] = None,
    expired: Optional[int] = None,
    needvad: int = 1,
    voice_format: int = REALTIME_VOICE_FORMAT_PCM,
) -> str:
    """组装带 signature 的 wss URL。"""
    ts = int(timestamp if timestamp is not None else time.time())
    n = int(nonce if nonce is not None else random.randint(0, 0x7FFFFFFF))
    exp = int(expired if expired is not None else ts + 24 * 3600)
    vid = voice_id or str(uuid.uuid4())
    params = {
        "engine_model_type": engine_model_type,
        "expired": exp,
        "needvad": needvad,
        "nonce": n,
        "secretid": secret_id,
        "timestamp": ts,
        "voice_format": voice_format,
        "voice_id": vid,
    }
    query = build_realtime_query(params)
    path = f"/asr/v2/{app_id}"
    sign_url = f"{REALTIME_HOST}{path}?{query}"
    sig = realtime_signature(secret_key, sign_url)
    return f"wss://{REALTIME_HOST}{path}?{query}&signature={quote(sig, safe='')}"


def _map_code(code: int) -> str:
    return _CODE_MAP.get(int(code), "upstream")


def _join_clauses(left: str, right: str) -> str:
    """合并短停顿切出的两段：去掉左侧句末停顿标点后直接拼接，不再插入逗号。"""
    l = (left or "").rstrip()
    r = (right or "").lstrip()
    if not l:
        return r
    if not r:
        return l
    while l and l[-1] in _PAUSE_PUNCT | frozenset("，,、；;"):
        l = l[:-1]
    return l + r


def smooth_spoken_text(text: str) -> str:
    """口语停顿清洗（委托统一出口；兼容旧调用）。"""
    from asr_proxy.textutil import normalize_asr_text
    return normalize_asr_text(text)


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


def apply_realtime_message(
    collector: StableSentenceCollector,
    payload: Dict[str, Any],
) -> bool:
    """处理一条实时识别 JSON。返回 True 表示已收到 final=1。"""
    code = payload.get("code")
    try:
        code_int = int(code)
    except (TypeError, ValueError):
        code_int = -1
    if code_int != 0:
        msg = str(payload.get("message") or "").strip() or f"code={code_int}"
        raise AsrProviderError(msg, code=_map_code(code_int), provider="tencent")

    if int(payload.get("final") or 0) == 1:
        return True

    result = payload.get("result")
    if not isinstance(result, dict):
        return False
    # slice_type: 0 开始 / 1 中间 / 2 一句话结束（稳定）
    try:
        slice_type = int(result.get("slice_type"))
    except (TypeError, ValueError):
        slice_type = -1
    text = str(result.get("voice_text_str") or "").strip()
    try:
        start_ms = int(result.get("start_time") or 0)
        end_ms = int(result.get("end_time") or 0)
    except (TypeError, ValueError):
        start_ms, end_ms = 0, 0
    collector.note_duration_ms(end_ms)
    if slice_type == 2 and text:
        collector.add(text, start_ms, end_ms, stable=True)
    return False


class TencentProvider(AsrProvider):
    """腾讯云 ASR：Flash 极速版 + 实时 WebSocket。

    配置：secret_id / secret_key / app_id / models（默认 16k_zh）
    """

    name = "tencent"

    def __init__(
        self,
        config: ProviderConfig,
        ffmpeg_path: str = "ffmpeg",
        client: Optional[httpx.Client] = None,
        segment_ms: int = DEFAULT_SEGMENT_MS,
        pace_realtime: bool = True,
        sleep_fn: Optional[Any] = None,
    ):
        super().__init__(config, ffmpeg_path)
        self.timeout = float(config.timeout or DEFAULT_TIMEOUT)
        self._client = client
        self.segment_ms = int(segment_ms or DEFAULT_SEGMENT_MS)
        self.pace_realtime = bool(pace_realtime)
        self._sleep_fn = sleep_fn

    @property
    def default_model(self) -> str:
        return self.config.model or DEFAULT_MODEL

    def _make_client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout)

    def _require_creds(self) -> Tuple[str, str, str]:
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
        return app_id, secret_id, secret_key

    def recognize(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        model: Optional[str] = None,
        language: Optional[str] = None,
    ) -> AsrResult:
        _ = language  # 语种由 engine_type / engine_model_type 决定
        app_id, secret_id, secret_key = self._require_creds()
        if not audio_bytes:
            raise AsrProviderError(
                "empty audio", code="upstream", provider=self.name,
            )

        used_model = (model or "").strip() or self.default_model
        if used_model != "16k_zh_en" and used_model.endswith("_2.0"):
            raise AsrProviderError(
                "录音文件·大模型 2.0 异步需公网音频 URL，当前代理仅支持 Flash / 实时 WebSocket",
                code="upstream",
                provider=self.name,
                model=used_model,
            )

        if is_realtime_model(used_model):
            return self._recognize_realtime(
                audio_bytes,
                filename=filename,
                used_model=used_model,
                app_id=app_id,
                secret_id=secret_id,
                secret_key=secret_key,
            )
        return self._recognize_flash(
            audio_bytes,
            filename=filename,
            used_model=used_model,
            app_id=app_id,
            secret_id=secret_id,
            secret_key=secret_key,
        )

    def _recognize_flash(
        self,
        audio_bytes: bytes,
        *,
        filename: str,
        used_model: str,
        app_id: str,
        secret_id: str,
        secret_key: str,
    ) -> AsrResult:
        engine_type = used_model if used_model else DEFAULT_MODEL
        # filter_punc: 0不过滤 1句末 2全部。闪电说默认 2，避免停顿逗号刷屏
        filter_punc = getattr(self.config, "filter_punc", 2)
        try:
            filter_punc = max(0, min(2, int(filter_punc)))
        except (TypeError, ValueError):
            filter_punc = 2
        params = {
            "engine_type": engine_type,
            "extra_punc": 0,
            "filter_punc": filter_punc,
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
                code=_map_code(code_int),
                provider=self.name,
                model=used_model,
            )

        text, duration, segments = _extract_flash_text(
            payload,
            max_sentence_silence=self.config.max_sentence_silence,
        )
        # 即便上游仍带回停顿逗号，本地再清一遍（filter_punc=2 时通常已无）
        text = smooth_spoken_text(text)
        if segments:
            for seg in segments:
                seg["text"] = smooth_spoken_text(str(seg.get("text") or ""))
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

    def _recognize_realtime(
        self,
        audio_bytes: bytes,
        *,
        filename: str,
        used_model: str,
        app_id: str,
        secret_id: str,
        secret_key: str,
    ) -> AsrResult:
        engine = realtime_engine_type(used_model)
        target_rate = 8000 if engine.startswith("8k_") else 16000
        suffix = os.path.splitext(filename or "")[1] or ".wav"
        tmp = write_temp_audio(audio_bytes, suffix=suffix)
        wav = None
        try:
            wav = to_wav_mono(tmp, sample_rate=target_rate, ffmpeg=self.ffmpeg_path)
            pcm, rate = wav_to_pcm_s16le(wav)
            text, segments, duration = self._recognize_realtime_ws(
                pcm,
                sample_rate=rate or target_rate,
                engine_model_type=engine,
                app_id=app_id,
                secret_id=secret_id,
                secret_key=secret_key,
                used_model=used_model,
            )
            text = smooth_spoken_text(text)
            if segments:
                for seg in segments:
                    seg["text"] = smooth_spoken_text(str(seg.get("text") or ""))
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
        finally:
            cleanup(tmp, wav)

    def _recognize_realtime_ws(
        self,
        pcm: bytes,
        *,
        sample_rate: int,
        engine_model_type: str,
        app_id: str,
        secret_id: str,
        secret_key: str,
        used_model: str,
    ) -> Tuple[str, List[dict], float]:
        try:
            import websocket
        except ImportError as e:
            raise AsrProviderError(
                "缺少依赖 websocket-client，请执行: pip install websocket-client",
                code="upstream",
                provider=self.name,
                model=used_model,
            ) from e

        ws_url = build_realtime_ws_url(
            app_id, secret_id, secret_key, engine_model_type,
        )
        try:
            ws = websocket.create_connection(
                ws_url,
                timeout=self.timeout,
                enable_multithread=True,
            )
        except Exception as e:
            raise AsrProviderError(
                f"实时 WebSocket 连接失败: {type(e).__name__}: {e}",
                code="upstream",
                provider=self.name,
                model=used_model,
            ) from e

        collector = StableSentenceCollector()
        seen_final = False

        def _recv_json() -> Optional[dict]:
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException as e:
                raise AsrProviderError(
                    f"实时识别等待响应超时（{self.timeout}s）",
                    code="upstream",
                    provider=self.name,
                    model=used_model,
                ) from e
            except websocket.WebSocketConnectionClosedException as e:
                if seen_final:
                    return None
                raise AsrProviderError(
                    "实时 WebSocket 在收到 final 前关闭",
                    code="upstream",
                    provider=self.name,
                    model=used_model,
                ) from e
            if raw is None:
                return None
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", errors="replace")
            if not isinstance(raw, str) or not raw.strip():
                return None
            try:
                payload = json.loads(raw)
            except ValueError as e:
                raise AsrProviderError(
                    f"实时返回非 JSON: {raw[:200]}",
                    code="upstream",
                    provider=self.name,
                    model=used_model,
                ) from e
            if not isinstance(payload, dict):
                return None
            return payload

        try:
            # 握手确认
            hello = _recv_json()
            if hello is None:
                raise AsrProviderError(
                    "实时识别无握手响应",
                    code="upstream",
                    provider=self.name,
                    model=used_model,
                )
            try:
                if apply_realtime_message(collector, hello):
                    seen_final = True
            except AsrProviderError as e:
                e.model = used_model
                raise

            def _send(chunk: bytes, is_last: bool) -> None:
                nonlocal seen_final
                if chunk:
                    ws.send(chunk, opcode=websocket.ABNF.OPCODE_BINARY)
                # 边发边收，避免服务端缓冲积压
                ws.settimeout(0.05)
                try:
                    while True:
                        try:
                            raw = ws.recv()
                        except websocket.WebSocketTimeoutException:
                            break
                        except websocket.WebSocketConnectionClosedException:
                            break
                        if isinstance(raw, (bytes, bytearray)):
                            raw = raw.decode("utf-8", errors="replace")
                        if not isinstance(raw, str) or not raw.strip():
                            continue
                        try:
                            payload = json.loads(raw)
                        except ValueError:
                            continue
                        if isinstance(payload, dict) and apply_realtime_message(
                            collector, payload,
                        ):
                            seen_final = True
                            break
                finally:
                    ws.settimeout(self.timeout)

                if is_last:
                    ws.send(
                        json.dumps({"type": "end"}),
                        opcode=websocket.ABNF.OPCODE_TEXT,
                    )

            send_pcm_chunks(
                pcm,
                _send,
                segment_ms=self.segment_ms,
                sample_rate=sample_rate or 16000,
                pace=self.pace_realtime,
                sleep_fn=self._sleep_fn,
            )

            # 收尾直到 final=1
            while not seen_final:
                payload = _recv_json()
                if payload is None:
                    break
                try:
                    if apply_realtime_message(collector, payload):
                        seen_final = True
                except AsrProviderError as e:
                    e.model = used_model
                    raise

            text, segments, duration = collector.finalize()
            if not seen_final and not text:
                raise AsrProviderError(
                    "实时识别未返回最终结果",
                    code="upstream",
                    provider=self.name,
                    model=used_model,
                )
            return text, segments, duration
        finally:
            try:
                ws.close()
            except Exception:
                pass
