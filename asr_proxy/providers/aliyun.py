# -*- coding: utf-8 -*-
"""阿里云百炼 DashScope ASR 服务商。"""
from __future__ import annotations

import base64
import os
import re
import wave
from http import HTTPStatus
from typing import Dict, List, Optional, Tuple

from asr_proxy.audio import cleanup, to_wav_16k_mono, write_temp_audio
from asr_proxy.config import ProviderConfig
from asr_proxy.providers.base import AsrProvider, AsrResult
from asr_proxy.providers.errors import AsrProviderError

# 识别通道：按模型名后缀自动路由
MODE_RECOGNITION = "recognition"  # WebSocket Recognition（streaming / realtime 家族）
MODE_FLASH = "flash"  # 同步非流式 multimodal（*-asr-flash）
MODE_FILETRANS = "filetrans"  # 异步录音文件转写（*-filetrans）


def resolve_asr_mode(model: str) -> str:
    """按模型名后缀选择调用通道。更具体的后缀优先。"""
    m = (model or "").strip().lower()
    if not m:
        return MODE_RECOGNITION
    if "-streaming" in m:
        return MODE_RECOGNITION
    if "-realtime" in m:
        return MODE_RECOGNITION
    if "-filetrans" in m:
        return MODE_FILETRANS
    if "-asr-flash" in m or m.startswith("fun-asr-flash"):
        return MODE_FLASH
    return MODE_RECOGNITION


_AUDIO_FRAME_BYTES = 12800
# 这些模型只挂在 /api-ws/v1/realtime，与 Recognition 的 /api-ws/v1/inference 不互通
_REALTIME_ONLY_PREFIXES = ("qwen3-asr-flash-realtime",)
# 流式结果为空时按模型回退到同步 flash（同一份音频整段识别）
_FLASH_FALLBACK_MODELS: Dict[str, str] = {
    "qwen-audio-3.1-asr-flash-streaming": "qwen-audio-3.1-asr-flash",
    "qwen-audio-3.0-asr-flash-streaming": "qwen-audio-3.0-asr-flash",
    "fun-asr-realtime": "fun-asr-flash-2026-06-15",
}


def _is_realtime_only(model: str) -> bool:
    m = (model or "").strip().lower()
    return any(m == p or m.startswith(p + "-") for p in _REALTIME_ONLY_PREFIXES)


def _classify_error(message: str, code: str = "") -> str:
    """把 DashScope 错误归类到 failover 的错误码。"""
    blob = f"{code or ''} {message or ''}".lower()
    if any(k in blob for k in (
        "budgetlimitexceeded", "allocationquota", "freetier",
        "free quota", "free tier", "arrearage", "quota", "budget",
    )):
        return "quota"
    if any(k in blob for k in (
        "invalidapikey", "invalid api-key", "invalid api key",
        "authentication", "unauthorized",
    )):
        return "auth"
    if any(k in blob for k in (
        "modelnotfound", "model not found", "not activated", "not opened",
    )):
        return "not_activated"
    if any(k in blob for k in ("permission", "no access", "notauthorised")):
        return "permission"
    return "upstream"


def _raise_dashscope_error(result, prefix: str = "DashScope error") -> None:
    message = str(getattr(result, "message", "") or "")
    code = str(getattr(result, "code", "") or "")
    detail = " ".join(x for x in (code, message) if x).strip() or prefix
    raise AsrProviderError(
        f"{prefix}: {detail}" if detail != prefix else prefix,
        code=_classify_error(message, code),
        provider="aliyun",
    )


def _norm_text(text: Optional[str]) -> str:
    return re.sub(r"[\s\W_]+", "", text or "")


def _wav_duration_seconds(path: str) -> float:
    try:
        with wave.open(path, "rb") as wf:
            rate = wf.getframerate() or 16000
            return wf.getnframes() / float(rate)
    except Exception:
        return 0.0


class _SentenceCollector:
    """收集 Recognition 的增量结果，保留流结束前未定句的尾句。

    dashscope 的同步 Recognition.call() 只收 end_time 非空的句子；固定时长
    切出的音频常在句中断开，尾句没有 end_time 会被丢弃，最终表现为空文本。
    """

    def __init__(self):
        self.sentences: List[dict] = []
        self.pending: Optional[dict] = None
        self.error = None

    def on_open(self) -> None:
        return

    def on_complete(self) -> None:
        return

    def on_close(self) -> None:
        return

    def on_error(self, result) -> None:
        self.error = result

    def on_event(self, result) -> None:
        sentence = result.get_sentence() if hasattr(result, "get_sentence") else None
        if isinstance(sentence, dict):
            self._add(sentence)
        elif isinstance(sentence, list):
            for item in sentence:
                if isinstance(item, dict):
                    self._add(item)

    def _add(self, sentence: dict) -> None:
        if not (sentence.get("text") or "").strip():
            return
        if sentence.get("end_time") is not None:
            self.sentences.append(sentence)
            self.pending = None
        else:
            self.pending = sentence

    def merged(self) -> List[dict]:
        result = list(self.sentences)
        if not self.pending:
            return result
        pending_text = _norm_text(self.pending.get("text"))
        if not pending_text:
            return result
        last_text = _norm_text(result[-1].get("text")) if result else ""
        # 尾句可能是上一句的未定稿（去标点后包含于最终句），避免重复
        if pending_text not in last_text:
            result.append(self.pending)
        return result


def _as_dict(obj) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "items"):
        try:
            return dict(obj.items())
        except Exception:
            pass
    return {}


def _extract_flash_text(output) -> Tuple[str, List[dict], float]:
    """解析非流式 flash 响应，返回 (text, segments, duration_sec)。"""
    d = _as_dict(output)
    text = (d.get("text") or "") if d else ""
    nested = d.get("output")
    sentence = None
    if isinstance(nested, dict):
        sentence = nested.get("sentence")
    elif nested is not None:
        sentence = _as_dict(nested).get("sentence")

    sentences: List[dict] = []
    if isinstance(sentence, dict):
        sentences = [sentence]
        if not text:
            text = sentence.get("text", "") or ""
    elif isinstance(sentence, list):
        sentences = [s for s in sentence if isinstance(s, dict)]
        if not text:
            text = "".join((s.get("text", "") or "") for s in sentences)

    # 兼容部分接口把 choices 塞进多模态结构
    if not text:
        choices = d.get("choices") or []
        if choices:
            msg = _as_dict(_as_dict(choices[0]).get("message"))
            content = msg.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = []
                for c in content:
                    if isinstance(c, str):
                        parts.append(c)
                    elif isinstance(c, dict) and c.get("text"):
                        parts.append(str(c["text"]))
                text = "".join(parts)

    segments: List[dict] = []
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
    return (text or "").strip(), segments, duration_ms / 1000.0


class AliyunProvider(AsrProvider):
    """阿里云 ASR：按模型后缀路由 Recognition / 非流式 flash。"""

    name = "aliyun"

    def __init__(self, config: ProviderConfig, ffmpeg_path: str = "ffmpeg"):
        super().__init__(config, ffmpeg_path)

    def _configure_dashscope(self):
        import dashscope
        dashscope.api_key = self.config.api_key
        if self.config.workspace_id:
            ws = self.config.workspace_id
            dashscope.base_websocket_api_url = (
                f"wss://{ws}.cn-beijing.maas.aliyuncs.com"
                f"/api-ws/v1/inference"
            )
            dashscope.base_http_api_url = (
                f"https://{ws}.cn-beijing.maas.aliyuncs.com/api/v1"
            )
        return dashscope

    def recognize(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        model: Optional[str] = None,
        language: Optional[str] = None,
    ) -> AsrResult:
        if not self.enabled:
            raise AsrProviderError(
                "阿里云 ASR 未启用（缺少 api_key）",
                code="auth", provider=self.name,
            )
        if not audio_bytes:
            raise ValueError("empty audio")

        used_model = (
            (model or "").strip()
            or self.default_model
            or "qwen-audio-3.1-asr-flash-streaming"
        )
        if _is_realtime_only(used_model):
            raise AsrProviderError(
                f"模型 {used_model} 走 /api-ws/v1/realtime 协议，"
                "当前代理不支持；请改用 qwen-audio-3.1-asr-flash-streaming 等流式模型",
                code="not_activated", provider=self.name, model=used_model,
            )
        mode = resolve_asr_mode(used_model)

        suffix = os.path.splitext(filename or "")[1] or ".wav"
        tmp = write_temp_audio(audio_bytes, suffix=suffix)
        wav = None
        try:
            wav = to_wav_16k_mono(tmp, self.ffmpeg_path)
            self._configure_dashscope()
            if mode == MODE_FLASH:
                return self._recognize_flash(wav, used_model)
            if mode == MODE_FILETRANS:
                raise AsrProviderError(
                    f"模型 {used_model} 为异步 filetrans，"
                    "当前代理尚未支持（需要公网音频 URL）",
                    code="not_activated", provider=self.name, model=used_model,
                )
            try:
                return self._recognize_streaming(wav, used_model)
            except AsrProviderError as exc:
                fallback = _FLASH_FALLBACK_MODELS.get(used_model.lower())
                if exc.code != "empty" or not fallback:
                    raise
                return self._recognize_flash(wav, fallback)
        finally:
            cleanup(tmp, wav)

    def _recognize_streaming(self, wav_path: str, used_model: str) -> AsrResult:
        from dashscope.audio.asr import Recognition

        collector = _SentenceCollector()
        recognition = Recognition(
            model=used_model,
            format="wav",
            sample_rate=16000,
            callback=collector,
            semantic_punctuation_enabled=self.config.semantic_punctuation_enabled,
            max_sentence_silence=self.config.max_sentence_silence,
        )
        try:
            try:
                recognition.start()
            except Exception as exc:
                raise AsrProviderError(
                    f"DashScope 建连失败: {exc}",
                    code=_classify_error(str(exc)),
                    provider=self.name, model=used_model,
                ) from exc
            with open(wav_path, "rb") as fh:
                while True:
                    chunk = fh.read(_AUDIO_FRAME_BYTES)
                    if not chunk:
                        break
                    if collector.error is not None:
                        break
                    try:
                        recognition.send_audio_frame(chunk)
                    except Exception:
                        if collector.error is not None:
                            break
                        raise
        finally:
            try:
                recognition.stop()
            except Exception:
                pass

        if collector.error is not None:
            _raise_dashscope_error(collector.error, "DashScope error")

        sentences = collector.merged()
        text = "".join((s.get("text", "") or "") for s in sentences).strip()
        if not text:
            raise AsrProviderError(
                "DashScope 返回空文本",
                code="empty", provider=self.name, model=used_model,
            )

        audio_sec = _wav_duration_seconds(wav_path)
        duration_ms = int(round(audio_sec * 1000))
        segments = []
        for i, s in enumerate(sentences):
            begin = s.get("begin_time", 0) or 0
            end = s.get("end_time")
            if end is None:
                end = duration_ms if i == len(sentences) - 1 else begin
            segments.append({
                "id": i,
                "start": begin / 1000.0,
                "end": end / 1000.0,
                "text": s.get("text", "") or "",
            })
        last_end = segments[-1]["end"] if segments else 0.0
        return AsrResult(
            text=text,
            language="chinese",
            duration=max(audio_sec, last_end),
            segments=segments,
            provider=self.name,
            model=used_model,
        )

    def _recognize_flash(self, wav_path: str, used_model: str) -> AsrResult:
        """同步非流式：qwen-audio/qwen3/fun-asr 的 *-asr-flash 系列。"""
        from dashscope import MultiModalConversation

        audio_b64 = base64.b64encode(open(wav_path, "rb").read()).decode()
        data_uri = f"data:audio/wav;base64,{audio_b64}"
        messages = [{
            "role": "user",
            "content": [{
                "type": "input_audio",
                "input_audio": {"data": data_uri},
            }],
        }]
        result = MultiModalConversation.call(
            model=used_model,
            messages=messages,
            api_key=self.config.api_key,
            workspace=self.config.workspace_id or None,
            format="wav",
            sample_rate="16000",
        )
        if result.status_code != HTTPStatus.OK:
            _raise_dashscope_error(result, "DashScope flash error")

        text, segments, duration = _extract_flash_text(result.output)
        if not text:
            raise AsrProviderError(
                "DashScope flash 返回空文本",
                code="empty", provider=self.name, model=used_model,
            )
        return AsrResult(
            text=text,
            language="chinese",
            duration=duration,
            segments=segments or None,
            provider=self.name,
            model=used_model,
        )
