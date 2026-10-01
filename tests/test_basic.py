# -*- coding: utf-8 -*-
"""基础测试：配置、路由、auto 切换、闪电说格式、test_asr_16k.wav。"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from asr_proxy.app import create_app, _find_audio_b64, _try_decode_b64
from asr_proxy.config import AppConfig, ProviderConfig, load_config
from asr_proxy.providers import ProviderRouter
from asr_proxy.providers.base import AsrResult
from asr_proxy.providers.aliyun import AliyunProvider
from asr_proxy.providers.grok import GrokProvider

WAV_PATH = ROOT / "test_asr_16k.wav"


@pytest.fixture
def wav_bytes() -> bytes:
    assert WAV_PATH.is_file(), f"缺少测试音频: {WAV_PATH}"
    return WAV_PATH.read_bytes()


@pytest.fixture
def cfg_aliyun_only() -> AppConfig:
    return AppConfig(
        host="127.0.0.1",
        port=8004,
        log_file=str(ROOT / "test_asr_proxy.log"),
        ffmpeg_path=os.environ.get("FFMPEG_PATH", "ffmpeg"),
        auto_order=["aliyun", "grok"],
        providers={
            "aliyun": ProviderConfig(
                name="aliyun",
                api_key="sk-test-aliyun",
                workspace_id="ws-test",
                model="qwen-audio-3.1-asr-flash-streaming",
            ),
            "grok": ProviderConfig(
                name="grok",
                api_key="",
                base_url="https://api.x.ai/v1",
                model="",
            ),
        },
    )


@pytest.fixture
def cfg_both_enabled() -> AppConfig:
    return AppConfig(
        host="127.0.0.1",
        port=8004,
        log_file=str(ROOT / "test_asr_proxy.log"),
        ffmpeg_path="ffmpeg",
        auto_order=["grok", "aliyun"],
        providers={
            "aliyun": ProviderConfig(
                name="aliyun",
                api_key="sk-test-aliyun",
                model="qwen-audio-3.1-asr-flash-streaming",
            ),
            "grok": ProviderConfig(
                name="grok",
                api_key="xai-test-key",
                base_url="https://api.x.ai/v1",
                model="grok-stt",
            ),
        },
    )


class TestConfig:
    def test_example_config_loads(self):
        cfg = load_config(ROOT / "config.example.yaml")
        assert "aliyun" in cfg.providers
        assert "grok" in cfg.providers
        assert "aliyun" in cfg.auto_order

    def test_api_key_enables_provider(self, cfg_aliyun_only):
        enabled = cfg_aliyun_only.enabled_providers()
        assert [p.name for p in enabled] == ["aliyun"]

    def test_empty_key_disables(self):
        cfg = AppConfig(providers={
            "aliyun": ProviderConfig(name="aliyun", api_key=""),
            "grok": ProviderConfig(name="grok", api_key="  "),
        })
        assert cfg.enabled_providers() == []


class TestAudioHelpers:
    def test_find_audio_in_shandianshuo_body(self, wav_bytes):
        b64 = base64.b64encode(wav_bytes).decode()
        body = {
            "model": "auto",
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "input_audio",
                    "input_audio": {"data": "data:audio/wav;base64," + b64},
                }],
            }],
        }
        found = _find_audio_b64(body)
        assert found
        decoded = _try_decode_b64(found[0])
        assert decoded[:4] == b"RIFF"
        assert len(decoded) == len(wav_bytes)


class TestAutoFailover:
    def test_auto_skips_grok_stub_to_aliyun(self, cfg_both_enabled, wav_bytes):
        router = ProviderRouter(cfg_both_enabled)

        def fake_aliyun_recognize(self, audio_bytes, filename="audio.wav"):
            return AsrResult(text="你好世界", provider="aliyun")

        with patch.object(AliyunProvider, "recognize", fake_aliyun_recognize):
            result, errors = router.recognize(wav_bytes, model="auto")
        assert result.text == "你好世界"
        assert result.provider == "aliyun"
        # grok 在 auto_order 最前，应先失败再切到 aliyun
        assert any("grok" in e.lower() or "NotImplemented" in e for e in errors)

    def test_auto_all_fail(self, cfg_both_enabled, wav_bytes):
        router = ProviderRouter(cfg_both_enabled)

        def boom(self, audio_bytes, filename="audio.wav"):
            raise RuntimeError("mock fail")

        with patch.object(AliyunProvider, "recognize", boom):
            with pytest.raises(RuntimeError, match="全部服务商识别失败"):
                router.recognize(wav_bytes, model="auto")

    def test_explicit_model_aliyun(self, cfg_both_enabled, wav_bytes):
        router = ProviderRouter(cfg_both_enabled)

        def fake(self, audio_bytes, filename="audio.wav"):
            return AsrResult(text="指定模型", provider="aliyun")

        with patch.object(AliyunProvider, "recognize", fake):
            result, errors = router.recognize(
                wav_bytes, model="qwen-audio-3.1-asr-flash-streaming",
            )
        assert result.text == "指定模型"
        assert errors == []

    def test_grok_raises_not_implemented(self, cfg_both_enabled, wav_bytes):
        p = GrokProvider(cfg_both_enabled.providers["grok"])
        with pytest.raises(NotImplementedError):
            p.recognize(wav_bytes)


class TestHttpApi:
    def test_health_and_models(self, cfg_aliyun_only):
        app = create_app(cfg_aliyun_only)
        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert any(p["name"] == "aliyun" for p in data["enabled_providers"])
        assert "auto" in data["models"]

        models = client.get("/v1/models").json()
        ids = [m["id"] for m in models["data"]]
        assert "auto" in ids

    def test_chat_completions_shandianshuo_format(self, cfg_aliyun_only, wav_bytes):
        app = create_app(cfg_aliyun_only)
        client = TestClient(app)
        b64 = base64.b64encode(wav_bytes).decode()

        def fake(self, audio_bytes, filename="audio.wav"):
            assert len(audio_bytes) == len(wav_bytes)
            return AsrResult(text="闪电说测试文本", provider="aliyun")

        body = {
            "model": "auto",
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "input_audio",
                    "input_audio": {"data": "data:audio/wav;base64," + b64},
                }],
            }],
        }
        with patch.object(AliyunProvider, "recognize", fake):
            r = client.post("/chat/completions", json=body)
        assert r.status_code == 200
        data = r.json()
        assert data["choices"][0]["message"]["content"] == "闪电说测试文本"
        assert data.get("provider") == "aliyun"

    def test_transcriptions_multipart(self, cfg_aliyun_only, wav_bytes):
        app = create_app(cfg_aliyun_only)
        client = TestClient(app)

        def fake(self, audio_bytes, filename="audio.wav"):
            return AsrResult(
                text="multipart识别",
                language="chinese",
                duration=1.5,
                segments=[{"id": 0, "start": 0.0, "end": 1.5, "text": "multipart识别"}],
                provider="aliyun",
            )

        with patch.object(AliyunProvider, "recognize", fake):
            r = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test_asr_16k.wav", wav_bytes, "audio/wav")},
                data={"model": "auto"},
            )
        assert r.status_code == 200
        data = r.json()
        assert data["text"] == "multipart识别"
        assert data["provider"] == "aliyun"


@pytest.mark.integration
class TestAliyunLive:
    """需要真实 DASHSCOPE_API_KEY 才会跑；无 Key 时自动跳过。"""

    def test_recognize_wav(self, wav_bytes):
        cfg = load_config()
        if not cfg.providers["aliyun"].enabled:
            pytest.skip("未配置阿里云 api_key，跳过真实识别")
        router = ProviderRouter(cfg)
        result, errors = router.recognize(wav_bytes, model="aliyun", filename="test_asr_16k.wav")
        assert result.text
        assert result.provider == "aliyun"
        print(f"识别结果: {result.text!r} errors={errors}")
