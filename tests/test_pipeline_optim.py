# -*- coding: utf-8 -*-
"""容灾截断/冷却、统一文本清洗、音频预处理。"""
from __future__ import annotations

import struct
import wave
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from asr_proxy.audio import preprocess_audio_bytes, to_wav_16k_mono
from asr_proxy.config import AppConfig, ProviderConfig
from asr_proxy.providers import ProviderRouter
from asr_proxy.providers.base import AsrProvider, AsrResult
from asr_proxy.providers.errors import AsrProviderError
from asr_proxy.textutil import normalize_asr_text, strip_model_tags, strip_pause_commas


def _pcm_wav(seconds: float = 0.3, rate: int = 16000, amp: int = 8000) -> bytes:
    """生成短促可听 WAV（非静音），便于预处理测试。"""
    n = int(rate * seconds)
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", amp if (i // 40) % 2 == 0 else -amp) for i in range(n)
        )
        w.writeframes(frames)
    return buf.getvalue()


class _StubProvider(AsrProvider):
    name = "stub"

    def __init__(self, models, behave):
        super().__init__(ProviderConfig(name="stub", api_key="k", models=models))
        self._behave = behave  # model -> "ok"|"fail"|exception
        self.calls = []

    def recognize(self, audio_bytes, filename="audio.wav", model=None, language=None):
        m = model or self.default_model
        self.calls.append(m)
        action = self._behave.get(m, "ok")
        if isinstance(action, Exception):
            raise action
        if action == "fail":
            raise AsrProviderError("quota out", code="quota", provider=self.name, model=m)
        return AsrResult(text=f"ok:{m}", provider=self.name, model=m)


class TestTextutil:
    def test_sensevoice_tags(self):
        assert strip_model_tags("<|zh|><|NEUTRAL|>你好<|Speech|>") == "你好"

    def test_pause_commas(self):
        assert strip_pause_commas("主要，目的就是，给，一些") == "主要目的就是给一些"

    def test_normalize_pipeline(self):
        raw = "<|zh|>我的这个项目主要，目的就是，给。一些软件。。"
        assert normalize_asr_text(raw) == "我的这个项目主要目的就是给一些软件。"


class TestFailoverPolicy:
    def _router(self, **kwargs):
        cfg = AppConfig(
            max_failover_candidates=kwargs.get("cap", 4),
            failover_cooldown_sec=kwargs.get("hard", 300),
            failover_soft_cooldown_sec=kwargs.get("soft", 60),
            audio_preprocess=False,
            auto_order=["stub"],
            providers={
                "stub": ProviderConfig(
                    name="stub", api_key="k",
                    models=["a", "b", "c", "d", "e"],
                ),
            },
        )
        stub = _StubProvider(
            ["a", "b", "c", "d", "e"],
            kwargs.get("behave", {"a": "fail", "b": "ok"}),
        )
        return ProviderRouter(cfg, providers={"stub": stub}), stub

    def test_cap_strategy_free_speed(self):
        router, stub = self._router(cap=2, behave={"a": "ok"})
        with patch.object(router, "resolve") as resolve:
            # 模拟 strategy 排出 5 个候选
            from asr_proxy.providers import RouteCandidate
            resolve.return_value = [
                RouteCandidate(stub, m) for m in ["a", "b", "c", "d", "e"]
            ]
            result, _ = router.recognize(b"x", model="strategy-free+speed")
        assert result.text == "ok:a"
        assert stub.calls == ["a"]  # 截断后只需成功首个

    def test_cooldown_skips_next_request(self):
        router, stub = self._router(
            cap=4,
            behave={
                "a": AsrProviderError("no pack", code="quota", provider="stub", model="a"),
                "b": "ok",
            },
        )
        # 第一次：a 失败 → b 成功
        r1, _ = router.recognize(b"x", model="stub/auto")
        assert r1.text == "ok:b"
        assert stub.calls == ["a", "b"]
        stub.calls.clear()
        # 第二次：a 仍在冷却，直接从 b 开始（provider-auto 轮转仍含 a，但被冷却过滤）
        r2, _ = router.recognize(b"x", model="stub/auto")
        assert r2.text == "ok:b"
        assert "a" not in stub.calls


class TestAudioPreprocess:
    def test_preprocess_returns_wav(self):
        raw = _pcm_wav(0.2)
        out, name = preprocess_audio_bytes(
            raw, filename="t.wav", ffmpeg="ffmpeg", trim_silence=False,
        )
        assert name.endswith(".wav")
        assert out[:4] == b"RIFF"
        assert len(out) > 44
