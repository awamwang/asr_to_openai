# -*- coding: utf-8 -*-
"""阿里云百炼 DashScope ASR 服务商。"""
from __future__ import annotations

import base64
import os
from http import HTTPStatus
from typing import List, Optional, Tuple

from asr_proxy.audio import cleanup, to_wav_16k_mono, write_temp_audio
from asr_proxy.config import ProviderConfig
from asr_proxy.providers.base import AsrProvider, AsrResult

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
    ) -> AsrResult:
        if not self.enabled:
            raise RuntimeError("阿里云 ASR 未启用（缺少 api_key）")
        if not audio_bytes:
            raise ValueError("empty audio")

        suffix = os.path.splitext(filename or "")[1] or ".wav"
        tmp = write_temp_audio(audio_bytes, suffix=suffix)
        wav = None
        used_model = (
            (model or "").strip()
            or self.default_model
            or "qwen-audio-3.1-asr-flash-streaming"
        )
        mode = resolve_asr_mode(used_model)
        try:
            wav = to_wav_16k_mono(tmp, self.ffmpeg_path)
            self._configure_dashscope()
            if mode == MODE_FLASH:
                return self._recognize_flash(wav, used_model)
            if mode == MODE_FILETRANS:
                raise RuntimeError(
                    f"模型 {used_model} 为异步 filetrans，"
                    "当前代理尚未支持（需要公网音频 URL）"
                )
            return self._recognize_streaming(wav, used_model)
        finally:
            cleanup(tmp, wav)

    def _recognize_streaming(self, wav_path: str, used_model: str) -> AsrResult:
        from dashscope.audio.asr import Recognition

        recognition = Recognition(
            model=used_model, format="wav", sample_rate=16000, callback=None,
        )
        result = recognition.call(wav_path)
        if result.status_code != HTTPStatus.OK:
            raise RuntimeError(f"DashScope error: {result.message}")

        raw = result.get_sentence()
        if isinstance(raw, dict):
            sentences = [raw]
        elif isinstance(raw, list):
            sentences = [s for s in raw if isinstance(s, dict)]
        else:
            sentences = []

        text = "".join((s.get("text", "") or "") for s in sentences)
        segments = []
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
        if not text:
            raise RuntimeError("DashScope 返回空文本")
        return AsrResult(
            text=text,
            language="chinese",
            duration=duration_ms / 1000.0,
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
            raise RuntimeError(
                f"DashScope flash error: {result.code or ''} {result.message}"
            )

        text, segments, duration = _extract_flash_text(result.output)
        if not text:
            raise RuntimeError("DashScope flash 返回空文本")
        return AsrResult(
            text=text,
            language="chinese",
            duration=duration,
            segments=segments or None,
            provider=self.name,
            model=used_model,
        )
