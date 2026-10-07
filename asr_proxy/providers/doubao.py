# -*- coding: utf-8 -*-
"""豆包/火山引擎流式语音识别（ASR 1.0 / 2.0）。

协议文档：
https://docs.volcengine.com/docs/6561/1354869

默认走流式输入模式 bigmodel_nostream（整段上传后出最终结果，准确率更高），
适合闪电说「先录音再识别」场景。
"""
from __future__ import annotations

import gzip
import json
import queue
import struct
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from asr_proxy.audio import load_pcm_s16le
from asr_proxy.config import ProviderConfig
from asr_proxy.providers.base import AsrProvider, AsrResult
from asr_proxy.providers.stream_util import (
    DEFAULT_SEGMENT_MS,
    send_pcm_chunks,
)

DEFAULT_WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
DEFAULT_TIMEOUT = 90.0
MODEL_NAME = "bigmodel"

# 对外模型别名 → 资源 ID（X-Api-Resource-Id）
# 1.0 = bigasr；2.0 = seedasr
_MODEL_RESOURCE: Dict[str, str] = {
    "asr-1.0": "volc.bigasr.sauc.duration",
    "asr-2.0": "volc.seedasr.sauc.duration",
    "asr-1.0-concurrent": "volc.bigasr.sauc.concurrent",
    "asr-2.0-concurrent": "volc.seedasr.sauc.concurrent",
    "bigasr": "volc.bigasr.sauc.duration",
    "seedasr": "volc.seedasr.sauc.duration",
    "1.0": "volc.bigasr.sauc.duration",
    "2.0": "volc.seedasr.sauc.duration",
}

_MSG_FULL_CLIENT = 0b0001
_MSG_AUDIO_ONLY = 0b0010
_MSG_FULL_SERVER = 0b1001
_MSG_ERROR = 0b1111

_FLAG_NO_SEQ = 0b0000
_FLAG_POS_SEQ = 0b0001
_FLAG_NEG_SEQ = 0b0010
_FLAG_NEG_WITH_SEQ = 0b0011

_SER_NONE = 0b0000
_SER_JSON = 0b0001
_COMP_NONE = 0b0000
_COMP_GZIP = 0b0001

# 常见 language 别名 → 豆包 language 码（仅 nostream 生效）
_LANG_MAP = {
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "chinese": "zh-CN",
    "en": "en-US",
    "en-us": "en-US",
    "english": "en-US",
    "ja": "ja-JP",
    "jp": "ja-JP",
    "japanese": "ja-JP",
    "ko": "ko-KR",
    "korean": "ko-KR",
    "yue": "yue-CN",
    "cantonese": "yue-CN",
}


def resolve_resource_id(model: str) -> str:
    """将配置中的模型名解析为 X-Api-Resource-Id。"""
    m = (model or "").strip()
    if not m:
        return _MODEL_RESOURCE["asr-2.0"]
    key = m.lower()
    if key in _MODEL_RESOURCE:
        return _MODEL_RESOURCE[key]
    # 允许直接填资源 ID
    if m.startswith("volc."):
        return m
    return m


def _normalize_language(language: Optional[str]) -> str:
    raw = (language or "").strip()
    if not raw or raw.lower() in ("auto", "null", "none"):
        return ""
    return _LANG_MAP.get(raw.lower(), raw)


def _build_header(msg_type: int, flags: int, serialization: int, compression: int) -> bytes:
    return bytes([
        (0b0001 << 4) | 0b0001,  # version=1, header_size=1 → 4 bytes
        (msg_type << 4) | flags,
        (serialization << 4) | compression,
        0x00,
    ])


def _build_full_client_request(seq: int, payload: dict) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    compressed = gzip.compress(raw)
    header = _build_header(_MSG_FULL_CLIENT, _FLAG_POS_SEQ, _SER_JSON, _COMP_GZIP)
    return header + struct.pack(">i", seq) + struct.pack(">I", len(compressed)) + compressed


def _build_audio_only_request(seq: int, audio_chunk: bytes, is_last: bool) -> bytes:
    flags = _FLAG_NEG_WITH_SEQ if is_last else _FLAG_POS_SEQ
    actual_seq = -seq if is_last else seq
    compressed = gzip.compress(audio_chunk)
    header = _build_header(_MSG_AUDIO_ONLY, flags, _SER_NONE, _COMP_GZIP)
    return header + struct.pack(">i", actual_seq) + struct.pack(">I", len(compressed)) + compressed


def _decode_payload(serialization: int, compression: int, payload: bytes) -> Any:
    data = payload
    if compression == _COMP_GZIP and data:
        data = gzip.decompress(data)
    if serialization == _SER_JSON and data:
        return json.loads(data.decode("utf-8"))
    return data


def parse_server_frame(frame: bytes) -> dict:
    """解析服务端二进制帧。"""
    if len(frame) < 4:
        raise RuntimeError("豆包 ASR 响应帧过短")
    header_size = frame[0] & 0x0F
    message_type = frame[1] >> 4
    flags = frame[1] & 0x0F
    serialization = frame[2] >> 4
    compression = frame[2] & 0x0F
    offset = header_size * 4

    sequence = None
    is_last = False
    if flags & 0x01:
        sequence = struct.unpack(">i", frame[offset:offset + 4])[0]
        offset += 4
    if flags & 0x02:
        is_last = True

    if message_type == _MSG_FULL_SERVER:
        payload_size = struct.unpack(">I", frame[offset:offset + 4])[0]
        offset += 4
        payload = frame[offset:offset + payload_size]
        return {
            "message_type": message_type,
            "sequence": sequence,
            "is_last": is_last,
            "payload": _decode_payload(serialization, compression, payload),
        }

    if message_type == _MSG_ERROR:
        error_code = struct.unpack(">I", frame[offset:offset + 4])[0]
        offset += 4
        payload_size = struct.unpack(">I", frame[offset:offset + 4])[0]
        offset += 4
        payload = frame[offset:offset + payload_size]
        try:
            detail = _decode_payload(serialization, compression, payload)
        except Exception:
            detail = payload.decode("utf-8", errors="replace")
        return {
            "message_type": message_type,
            "sequence": sequence,
            "is_last": is_last,
            "error_code": error_code,
            "error": detail,
        }

    return {
        "message_type": message_type,
        "sequence": sequence,
        "is_last": is_last,
        "payload": None,
    }


def extract_result(payload: Any) -> Tuple[str, List[dict], float]:
    """从 full server response payload 提取文本、分句与时长。"""
    if not isinstance(payload, dict):
        return "", [], 0.0

    result = payload.get("result")
    text = ""
    utterances: List[dict] = []
    if isinstance(result, dict):
        text = str(result.get("text") or "").strip()
        raw_utts = result.get("utterances")
        if isinstance(raw_utts, list):
            utterances = [u for u in raw_utts if isinstance(u, dict)]
    elif isinstance(result, list):
        # 兼容部分响应把 result 写成 list
        for item in result:
            if isinstance(item, dict) and item.get("text"):
                text += str(item.get("text") or "")
                utts = item.get("utterances")
                if isinstance(utts, list):
                    utterances.extend(u for u in utts if isinstance(u, dict))
        text = text.strip()

    segments: List[dict] = []
    duration_ms = 0
    for i, u in enumerate(utterances):
        start = int(u.get("start_time") or 0)
        end = int(u.get("end_time") or 0)
        duration_ms = max(duration_ms, end)
        segments.append({
            "id": i,
            "start": start / 1000.0,
            "end": end / 1000.0,
            "text": str(u.get("text") or "").strip(),
        })

    audio_info = payload.get("audio_info")
    if isinstance(audio_info, dict):
        try:
            duration_ms = max(duration_ms, int(audio_info.get("duration") or 0))
        except (TypeError, ValueError):
            pass

    return text, segments, duration_ms / 1000.0


class DoubaoProvider(AsrProvider):
    """豆包流式语音识别（火山 openspeech WebSocket）。

    配置项：
      - api_key  ← DOUBAO_API_KEY / VOLC_API_KEY / VOLC_ACCESS_KEY
        新版控制台：X-Api-Key；旧版：Access Token
      - app_id   ← DOUBAO_APP_ID / VOLC_APP_ID / VOLC_APP_KEY
        旧版控制台 APP ID（有值则走旧鉴权）
      - base_url ← DOUBAO_WS_URL（默认 bigmodel_nostream）
      - timeout  ← DOUBAO_TIMEOUT
      - models   ← DOUBAO_ASR_MODELS（asr-2.0 / asr-1.0 …）
    """

    name = "doubao"

    def __init__(self, config: ProviderConfig, ffmpeg_path: str = "ffmpeg"):
        super().__init__(config, ffmpeg_path)
        self.timeout = float(config.timeout or DEFAULT_TIMEOUT)
        self.ws_url = (config.base_url or DEFAULT_WS_URL).strip() or DEFAULT_WS_URL
        self.app_id = (getattr(config, "app_id", None) or config.workspace_id or "").strip()
        self.segment_ms = DEFAULT_SEGMENT_MS
        # 连接预热：保持「上次使用的接口」的连接，把建连移出下次请求的关键路径。
        # 豆包 WS 协议不提供连接复用（收完 is_last 服务端即断开），因此只能力求
        # 「后台常驻 1 个已连接未使用的 WS」，请求取用、用后关闭、后台补建。
        self.warm_enabled = bool(getattr(config, "warm", True))
        self.warm_ttl = float(getattr(config, "warm_ttl", 0) or 20.0)
        self._warm_pool: "queue.Queue" = queue.Queue(maxsize=1)
        self._warm_lock = threading.Lock()
        self._warmer_alive = False
        # 上次成功识别的资源 ID（预热连接按它建连，模型与连接绑定）
        self._last_resource_id = "volc.bigasr.sauc.duration"

    @property
    def default_model(self) -> str:
        return self.config.model or "asr-2.0"

    def _auth_headers(self, connect_id: str) -> Dict[str, str]:
        headers = {
            "X-Api-Resource-Id": "",  # 调用时按模型覆盖
            "X-Api-Connect-Id": connect_id,
            "X-Api-Request-Id": connect_id,
            "X-Api-Sequence": "-1",
        }
        if self.app_id:
            # 旧版控制台：App-Key + Access-Key
            headers["X-Api-App-Key"] = self.app_id
            headers["X-Api-Access-Key"] = self.config.api_key
        else:
            # 新版控制台：仅 X-Api-Key
            headers["X-Api-Key"] = self.config.api_key
        return headers

    def recognize(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        model: Optional[str] = None,
        language: Optional[str] = None,
    ) -> AsrResult:
        if not self.enabled:
            raise RuntimeError("豆包 ASR 未启用（缺少 api_key）")
        if not audio_bytes:
            raise ValueError("empty audio")

        used_model = (model or "").strip() or self.default_model
        resource_id = resolve_resource_id(used_model)
        pcm, _rate = load_pcm_s16le(audio_bytes, self.ffmpeg_path)
        text, segments, duration = self._recognize_ws(
            pcm,
            resource_id=resource_id,
            language=_normalize_language(language),
        )
        if not text:
            raise RuntimeError("豆包 ASR 返回空文本")
        return AsrResult(
            text=text,
            language=_normalize_language(language) or "chinese",
            duration=duration,
            segments=segments or None,
            provider=self.name,
            model=used_model,
        )

    def _connect_ws(self, resource_id: str):
        """建立豆包 WebSocket 连接（鉴权头 + 资源 ID）。失败抛 RuntimeError。"""
        import websocket
        connect_id = str(uuid.uuid4())
        headers = self._auth_headers(connect_id)
        headers["X-Api-Resource-Id"] = resource_id
        # websocket-client 要求 list["Key: Value"]
        header_list = [f"{k}: {v}" for k, v in headers.items()]
        try:
            return websocket.create_connection(
                self.ws_url,
                header=header_list,
                timeout=self.timeout,
                enable_multithread=True,
            )
        except Exception as e:
            raise RuntimeError(
                f"豆包 ASR WebSocket 连接失败: {type(e).__name__}: {e}"
            ) from e

    # ── 连接预热池 ────────────────────────────────────────────────────
    def keep_warm(self) -> None:
        """识别成功后调用：后台补建 1 个已连接未使用的 WS，供下次复用。

        池满/已有预热线程在跑时直接返回；建连失败静默放弃（下次请求时
        再走正常建连路径），不阻塞调用方。
        """
        if not self.warm_enabled:
            return
        if self._warm_pool.full():
            return
        with self._warm_lock:
            if self._warmer_alive:
                return
            self._warmer_alive = True
        threading.Thread(
            target=self._warm_worker, daemon=True, name="doubao-warm"
        ).start()

    def _warm_worker(self) -> None:
        try:
            try:
                ws = self._connect_ws(self._last_resource_id)
            except Exception:
                return
            try:
                ws._warm_ts = time.monotonic()
                ws._warm_resource = self._last_resource_id
                try:
                    self._warm_pool.put_nowait(ws)
                except queue.Full:
                    self._close_ws(ws)
            except Exception:
                self._close_ws(ws)
        finally:
            with self._warm_lock:
                self._warmer_alive = False

    def _take_warm_ws(self, resource_id: str):
        """从池中取预热连接；无效（资源不符/空闲过久/被服务端断开）则丢弃返回 None。"""
        if not self.warm_enabled:
            return None
        try:
            ws = self._warm_pool.get_nowait()
        except queue.Empty:
            return None
        if getattr(ws, "_warm_resource", None) != resource_id:
            self._close_ws(ws)
            return None
        if self.warm_ttl > 0:
            ts = getattr(ws, "_warm_ts", 0) or 0
            if time.monotonic() - ts > self.warm_ttl:
                self._close_ws(ws)
                return None
        return ws

    @staticmethod
    def _close_ws(ws) -> None:
        try:
            ws.close()
        except Exception:
            pass

    def _recognize_ws(
        self,
        pcm: bytes,
        resource_id: str,
        language: str = "",
    ) -> Tuple[str, List[dict], float]:
        try:
            import websocket
        except ImportError as e:
            raise RuntimeError(
                "缺少依赖 websocket-client，请执行: pip install websocket-client"
            ) from e

        self._last_resource_id = resource_id
        # 预热连接可能已被服务端空闲断开：失败时重建一次再跑，避免整个请求失败
        for attempt in (0, 1):
            ws = self._take_warm_ws(resource_id)
            if ws is None:
                ws = self._connect_ws(resource_id)
            try:
                return self._run_ws_session(ws, pcm, language)
            except (
                websocket.WebSocketConnectionClosedException,
                ConnectionError,
                OSError,
                websocket.WebSocketTimeoutException,
            ) as e:
                self._close_ws(ws)
                if attempt == 0:
                    continue
                raise RuntimeError(
                    f"豆包 ASR WebSocket 会话失败: {type(e).__name__}: {e}"
                ) from e
            finally:
                self._close_ws(ws)

    def _run_ws_session(
        self,
        ws,
        pcm: bytes,
        language: str,
    ) -> Tuple[str, List[dict], float]:
        """在已建立的 WS 连接上完成一次 nostream 识别会话。"""
        import websocket

        final_text = ""
        final_segments: List[dict] = []
        final_duration = 0.0
        seen_last = False

        audio_cfg: Dict[str, Any] = {
            "format": "pcm",
            "codec": "raw",
            "rate": 16000,
            "bits": 16,
            "channel": 1,
        }
        # language 仅 nostream 支持
        if language and "nostream" in self.ws_url.lower():
            audio_cfg["language"] = language

        full_payload = {
            "user": {"uid": "asr-proxy"},
            "audio": audio_cfg,
            "request": {
                "model_name": MODEL_NAME,
                "enable_itn": True,
                "enable_punc": bool(getattr(self.config, "enable_punc", True)),
                "enable_ddc": False,
                "show_utterances": True,
                "result_type": "full",
            },
        }

        seq = 1
        ws.send(_build_full_client_request(seq, full_payload), opcode=websocket.ABNF.OPCODE_BINARY)
        seq += 1

        def _send_chunk(chunk: bytes, is_last: bool) -> None:
            nonlocal seq
            ws.send(
                _build_audio_only_request(seq, chunk, is_last),
                opcode=websocket.ABNF.OPCODE_BINARY,
            )
            if not is_last:
                seq += 1

        send_pcm_chunks(
            pcm,
            _send_chunk,
            segment_ms=self.segment_ms,
            sample_rate=16000,
            pace=False,
        )

        while True:
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException as e:
                raise RuntimeError(
                    f"豆包 ASR 等待响应超时（{self.timeout}s）"
                ) from e
            except websocket.WebSocketConnectionClosedException as e:
                if seen_last:
                    break
                raise RuntimeError(
                    "豆包 ASR WebSocket 在收到最终结果前关闭"
                ) from e

            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            if not isinstance(raw, (bytes, bytearray)):
                continue

            parsed = parse_server_frame(bytes(raw))
            if parsed["message_type"] == _MSG_ERROR:
                raise RuntimeError(
                    f"豆包 ASR 错误码={parsed.get('error_code')} "
                    f"详情={parsed.get('error')!r}"
                )

            if parsed["message_type"] == _MSG_FULL_SERVER:
                text, segments, duration = extract_result(parsed.get("payload"))
                if text:
                    final_text = text
                    final_segments = segments
                    final_duration = duration
                if parsed.get("is_last"):
                    seen_last = True
                    break

        if not seen_last and not final_text:
            raise RuntimeError("豆包 ASR 未返回最终结果")
        return final_text, final_segments, final_duration
