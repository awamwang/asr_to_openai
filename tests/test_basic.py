# -*- coding: utf-8 -*-
"""基础测试：配置、路由、auto 切换、闪电说格式、test_asr_16k.wav。"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from asr_proxy.app import create_app, _find_audio_b64, _try_decode_b64
from asr_proxy.config import AppConfig, ProviderConfig, load_config
from asr_proxy.providers import ProviderRouter
from asr_proxy.providers.base import AsrResult
from asr_proxy.providers.aliyun import (
    AliyunProvider,
    MODE_FILETRANS,
    MODE_FLASH,
    MODE_RECOGNITION,
    resolve_asr_mode,
    _extract_flash_text,
)
from asr_proxy.providers.groq import GroqProvider

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
        auto_order=["aliyun", "groq"],
        providers={
            "aliyun": ProviderConfig(
                name="aliyun",
                api_key="sk-test-aliyun",
                workspace_id="ws-test",
                models=[
                    "qwen-audio-3.1-asr-flash-streaming",
                    "qwen3-asr-flash-realtime",
                ],
            ),
            "groq": ProviderConfig(
                name="groq",
                api_key="",
                base_url="https://api.groq.com/openai/v1",
                models=[],
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
        auto_order=["groq", "aliyun"],
        providers={
            "aliyun": ProviderConfig(
                name="aliyun",
                api_key="sk-test-aliyun",
                models=[
                    "qwen-audio-3.1-asr-flash-streaming",
                    "qwen3-asr-flash-realtime",
                ],
            ),
            "groq": ProviderConfig(
                name="groq",
                api_key="gsk-test-key",
                base_url="https://api.groq.com/openai/v1",
                proxy="http://127.0.0.1:7881",
                models=["whisper-large-v3", "whisper-large-v3-turbo"],
            ),
        },
    )


class TestAsrModeRouting:
    def test_resolve_by_suffix(self):
        assert resolve_asr_mode("qwen-audio-3.1-asr-flash-streaming") == MODE_RECOGNITION
        assert resolve_asr_mode("qwen3-asr-flash-realtime") == MODE_RECOGNITION
        assert resolve_asr_mode("qwen3-asr-flash-realtime-2025-10-27") == MODE_RECOGNITION
        assert resolve_asr_mode("fun-asr-realtime") == MODE_RECOGNITION
        assert resolve_asr_mode("qwen-audio-3.0-asr-flash") == MODE_FLASH
        assert resolve_asr_mode("qwen-audio-3.1-asr-flash") == MODE_FLASH
        assert resolve_asr_mode("qwen3-asr-flash") == MODE_FLASH
        assert resolve_asr_mode("qwen3-asr-flash-2025-09-08") == MODE_FLASH
        assert resolve_asr_mode("fun-asr-flash-2026-06-15") == MODE_FLASH
        assert resolve_asr_mode("qwen-audio-3.0-asr-flash-filetrans") == MODE_FILETRANS
        assert resolve_asr_mode("qwen3-asr-flash-filetrans") == MODE_FILETRANS

    def test_extract_flash_text(self):
        text, segments, duration = _extract_flash_text({
            "text": "你好世界",
            "output": {
                "sentence": {
                    "text": "你好世界",
                    "begin_time": 100,
                    "end_time": 1500,
                },
            },
        })
        assert text == "你好世界"
        assert duration == 1.5
        assert segments[0]["text"] == "你好世界"


class TestConfig:
    def test_example_config_loads(self):
        cfg = load_config(ROOT / "config.example.yaml")
        assert "aliyun" in cfg.providers
        assert "groq" in cfg.providers
        assert "aliyun" in cfg.auto_order
        assert cfg.providers["aliyun"].models
        assert cfg.providers["aliyun"].model == cfg.providers["aliyun"].models[0]
        assert "qwen-audio-3.0-asr-flash" in cfg.providers["aliyun"].models
        assert "whisper-large-v3" in cfg.providers["groq"].models
        assert "groq.com" in cfg.providers["groq"].base_url

    def test_api_key_enables_provider(self, cfg_aliyun_only):
        enabled = cfg_aliyun_only.enabled_providers()
        assert [p.name for p in enabled] == ["aliyun"]

    def test_empty_key_disables(self):
        cfg = AppConfig(providers={
            "aliyun": ProviderConfig(name="aliyun", api_key=""),
            "groq": ProviderConfig(name="groq", api_key="  "),
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
    def test_auto_skips_grok_failure_to_aliyun(self, cfg_both_enabled, wav_bytes):
        router = ProviderRouter(cfg_both_enabled)

        def fake_aliyun_recognize(self, audio_bytes, filename="audio.wav", model=None, language=None):
            return AsrResult(
                text="你好世界",
                provider="aliyun",
                model=model or "qwen-audio-3.1-asr-flash-streaming",
            )

        def fake_grok_recognize(self, audio_bytes, filename="audio.wav", model=None, language=None):
            raise RuntimeError("Groq 转写错误 status=401")

        with patch.object(AliyunProvider, "recognize", fake_aliyun_recognize):
            with patch.object(GroqProvider, "recognize", fake_grok_recognize):
                result, errors = router.recognize(wav_bytes, model="auto")
        assert result.text == "你好世界"
        assert result.provider == "aliyun"
        # grok 在 auto_order 最前，应先失败再切到 aliyun
        assert any("groq" in e.lower() for e in errors)
        # 成功后记忆，下次 auto 从 aliyun 开始
        assert router._last_provider == "aliyun"

    def test_auto_remembers_provider_for_next_request(self, cfg_both_enabled, wav_bytes):
        router = ProviderRouter(cfg_both_enabled)
        calls = []

        def fake_aliyun(self, audio_bytes, filename="audio.wav", model=None, language=None):
            calls.append(("aliyun", model))
            return AsrResult(text="ok", provider="aliyun", model=model or "m1")

        def fake_grok(self, audio_bytes, filename="audio.wav", model=None, language=None):
            calls.append(("groq", model))
            raise RuntimeError("mock groq fail")

        with patch.object(AliyunProvider, "recognize", fake_aliyun):
            with patch.object(GroqProvider, "recognize", fake_grok):
                router.recognize(wav_bytes, model="auto")
                calls.clear()
                router.recognize(wav_bytes, model="auto")

        # 第二次应从上次成功的 aliyun 开始，不再先打 grok
        assert calls[0][0] == "aliyun"

    def test_auto_does_not_switch_models_inside_provider(self, cfg_both_enabled, wav_bytes):
        router = ProviderRouter(cfg_both_enabled)
        candidates = router.resolve("auto")
        # 每个服务商只出现一次
        names = [c.provider.name for c in candidates]
        assert names == ["groq", "aliyun"]
        assert candidates[0].model == "whisper-large-v3"
        assert candidates[1].model == "qwen-audio-3.1-asr-flash-streaming"

    def test_provider_auto_switches_models(self, cfg_both_enabled, wav_bytes):
        router = ProviderRouter(cfg_both_enabled)
        tried = []

        def fake(self, audio_bytes, filename="audio.wav", model=None, language=None):
            tried.append(model)
            if model == "qwen-audio-3.1-asr-flash-streaming":
                raise RuntimeError("first model fail")
            return AsrResult(text="第二模型成功", provider="aliyun", model=model)

        with patch.object(AliyunProvider, "recognize", fake):
            result, errors = router.recognize(wav_bytes, model="aliyun/auto")
        assert result.text == "第二模型成功"
        assert result.model == "qwen3-asr-flash-realtime"
        assert tried == [
            "qwen-audio-3.1-asr-flash-streaming",
            "qwen3-asr-flash-realtime",
        ]
        assert any("first model fail" in e for e in errors)

        # 下次 aliyun/auto 从成功模型开始
        next_cands = router.resolve("aliyun/auto")
        assert [c.model for c in next_cands] == [
            "qwen3-asr-flash-realtime",
            "qwen-audio-3.1-asr-flash-streaming",
        ]

    def test_router_logs_used_and_skipped(self, cfg_both_enabled, wav_bytes):
        logs: list[str] = []

        def fake_aliyun(self, audio_bytes, filename="audio.wav", model=None, language=None):
            return AsrResult(text="ok", provider="aliyun", model=model or "m1")

        def fake_grok(self, audio_bytes, filename="audio.wav", model=None, language=None):
            raise NotImplementedError("stub")

        router = ProviderRouter(cfg_both_enabled, log_fn=logs.append)
        with patch.object(AliyunProvider, "recognize", fake_aliyun):
            with patch.object(GroqProvider, "recognize", fake_grok):
                router.recognize(wav_bytes, model="auto")

        joined = "\n".join(logs)
        assert "使用模型=aliyun/" in joined
        assert "跳过=" in joined
        assert any("groq" in line for line in logs if "跳过=" in line)

    def test_explicit_provider_slash_model(self, cfg_both_enabled, wav_bytes):
        router = ProviderRouter(cfg_both_enabled)
        used = []

        def fake(self, audio_bytes, filename="audio.wav", model=None, language=None):
            used.append(model)
            return AsrResult(text="指定模型", provider="aliyun", model=model)

        with patch.object(AliyunProvider, "recognize", fake):
            result, errors = router.recognize(
                wav_bytes, model="aliyun/qwen3-asr-flash-realtime",
            )
        assert result.text == "指定模型"
        assert used == ["qwen3-asr-flash-realtime"]
        assert errors == []

    def test_auto_all_fail(self, cfg_both_enabled, wav_bytes):
        router = ProviderRouter(cfg_both_enabled)

        def boom(self, audio_bytes, filename="audio.wav", model=None, language=None):
            raise RuntimeError("mock fail")

        with patch.object(AliyunProvider, "recognize", boom):
            with pytest.raises(RuntimeError, match="全部服务商识别失败"):
                router.recognize(wav_bytes, model="auto")

    def test_explicit_model_aliyun_legacy(self, cfg_both_enabled, wav_bytes):
        router = ProviderRouter(cfg_both_enabled)

        def fake(self, audio_bytes, filename="audio.wav", model=None, language=None):
            return AsrResult(text="指定模型", provider="aliyun", model=model)

        with patch.object(AliyunProvider, "recognize", fake):
            result, errors = router.recognize(
                wav_bytes, model="qwen-audio-3.1-asr-flash-streaming",
            )
        assert result.text == "指定模型"
        assert errors == []

    def test_grok_recognize_posts_multipart(self, cfg_both_enabled, wav_bytes):
        """断言发往 Groq /audio/transcriptions 的请求契约。"""
        captured = {}

        class FakeResp:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "text": "hello from groq",
                    "language": "en",
                    "duration": 1.25,
                    "segments": [
                        {"id": 0, "start": 0.0, "end": 1.25, "text": "hello from groq"},
                    ],
                }

        class FakeClient:
            def post(self, url, headers=None, data=None, files=None):
                captured["url"] = url
                captured["headers"] = headers
                captured["data"] = data
                captured["files"] = files
                return FakeResp()

            def close(self):
                pass

        p = GroqProvider(
            cfg_both_enabled.providers["groq"],
            client=FakeClient(),
        )
        result = p.recognize(wav_bytes, filename="voice.wav", model="whisper-large-v3")

        assert result.text == "hello from groq"
        assert result.language == "en"
        assert result.duration == 1.25
        assert result.provider == "groq"
        assert result.model == "whisper-large-v3"
        assert result.segments and result.segments[0]["text"] == "hello from groq"

        assert captured["url"] == "https://api.groq.com/openai/v1/audio/transcriptions"
        assert captured["headers"]["Authorization"] == "Bearer gsk-test-key"
        assert captured["data"]["model"] == "whisper-large-v3"
        assert captured["data"]["response_format"] == "verbose_json"
        filename, content, content_type = captured["files"]["file"]
        assert filename == "voice.wav"
        assert content == wav_bytes
        assert content_type == "audio/wav"

    def test_grok_upstream_error_raises(self, cfg_both_enabled, wav_bytes):
        class FakeResp:
            status_code = 401
            text = '{"error":"Invalid API Key"}'

            def json(self):
                return {"error": "Invalid API Key"}

        class FakeClient:
            def post(self, url, headers=None, data=None, files=None):
                return FakeResp()

            def close(self):
                pass

        p = GroqProvider(cfg_both_enabled.providers["groq"], client=FakeClient())
        with pytest.raises(RuntimeError, match="status=401"):
            p.recognize(wav_bytes)

    def test_flash_model_uses_multimodal_path(self, cfg_aliyun_only, wav_bytes):
        p = AliyunProvider(cfg_aliyun_only.providers["aliyun"], ffmpeg_path="ffmpeg")
        called = {}

        class FakeResp:
            status_code = 200
            code = None
            message = "ok"
            output = {
                "text": "非流式识别成功",
                "output": {"sentence": {"text": "非流式识别成功"}},
            }

        def fake_call(**kwargs):
            called.update(kwargs)
            return FakeResp()

        mock_file = type("F", (), {"read": lambda self: b"RIFF"})()

        with patch("asr_proxy.providers.aliyun.to_wav_16k_mono", return_value="fake.wav"):
            with patch("asr_proxy.providers.aliyun.cleanup"):
                with patch("builtins.open", return_value=mock_file):
                    with patch(
                        "dashscope.MultiModalConversation.call",
                        side_effect=fake_call,
                    ):
                        result = p.recognize(
                            wav_bytes,
                            filename="test.wav",
                            model="qwen-audio-3.0-asr-flash",
                        )
        assert result.text == "非流式识别成功"
        assert result.model == "qwen-audio-3.0-asr-flash"
        assert called.get("model") == "qwen-audio-3.0-asr-flash"
        assert called.get("format") == "wav"

    def test_filetrans_model_raises_clear_error(self, cfg_aliyun_only, wav_bytes):
        p = AliyunProvider(cfg_aliyun_only.providers["aliyun"], ffmpeg_path="ffmpeg")
        with patch("asr_proxy.providers.aliyun.to_wav_16k_mono", return_value="fake.wav"):
            with patch("asr_proxy.providers.aliyun.cleanup"):
                with pytest.raises(RuntimeError, match="filetrans"):
                    p.recognize(
                        wav_bytes,
                        filename="test.wav",
                        model="qwen-audio-3.0-asr-flash-filetrans",
                    )


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
        assert "aliyun/qwen-audio-3.1-asr-flash-streaming" in data["models"]
        assert "aliyun/auto" in data["models"]

        models = client.get("/v1/models").json()
        ids = [m["id"] for m in models["data"]]
        assert "auto" in ids
        assert "aliyun/qwen3-asr-flash-realtime" in ids
        assert "aliyun/auto" in ids

    def test_chat_completions_shandianshuo_format(self, cfg_aliyun_only, wav_bytes):
        app = create_app(cfg_aliyun_only)
        client = TestClient(app)
        b64 = base64.b64encode(wav_bytes).decode()

        def fake(self, audio_bytes, filename="audio.wav", model=None, language=None):
            assert len(audio_bytes) == len(wav_bytes)
            return AsrResult(
                text="闪电说测试文本",
                provider="aliyun",
                model=model or "qwen-audio-3.1-asr-flash-streaming",
            )

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
        assert data.get("model") == "aliyun/qwen-audio-3.1-asr-flash-streaming"

    def test_transcriptions_multipart(self, cfg_aliyun_only, wav_bytes):
        app = create_app(cfg_aliyun_only)
        client = TestClient(app)

        def fake(self, audio_bytes, filename="audio.wav", model=None, language=None):
            return AsrResult(
                text="multipart识别",
                language="chinese",
                duration=1.5,
                segments=[{"id": 0, "start": 0.0, "end": 1.5, "text": "multipart识别"}],
                provider="aliyun",
                model=model or "qwen-audio-3.1-asr-flash-streaming",
            )

        with patch.object(AliyunProvider, "recognize", fake):
            r = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test_asr_16k.wav", wav_bytes, "audio/wav")},
                data={"model": "aliyun/auto"},
            )
        assert r.status_code == 200
        data = r.json()
        assert data["text"] == "multipart识别"
        assert data["provider"] == "aliyun"
        assert data["model"].startswith("aliyun/")

    def test_chat_failure_returns_non_200(self, cfg_aliyun_only, wav_bytes):
        app = create_app(cfg_aliyun_only)
        client = TestClient(app)
        b64 = base64.b64encode(wav_bytes).decode()

        def boom(self, audio_bytes, filename="audio.wav", model=None, language=None):
            raise RuntimeError("mock recognize fail")

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
        with patch.object(AliyunProvider, "recognize", boom):
            r = client.post("/chat/completions", json=body)
        assert r.status_code == 502
        assert "mock recognize fail" in r.json()["error"]["message"]

    def test_chat_unknown_provider_returns_non_200(self, cfg_aliyun_only, wav_bytes):
        app = create_app(cfg_aliyun_only)
        client = TestClient(app)
        b64 = base64.b64encode(wav_bytes).decode()
        body = {
            "model": "not-a-provider/auto",
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "input_audio",
                    "input_audio": {"data": "data:audio/wav;base64," + b64},
                }],
            }],
        }
        r = client.post("/chat/completions", json=body)
        assert r.status_code == 502
        assert "error" in r.json()


@pytest.mark.integration
class TestAliyunLive:
    """需要真实 DASHSCOPE_API_KEY 才会跑；无 Key 时自动跳过。"""

    def test_recognize_wav(self, wav_bytes):
        cfg = load_config()
        if not cfg.providers["aliyun"].enabled:
            pytest.skip("未配置阿里云 api_key，跳过真实识别")
        router = ProviderRouter(cfg)
        result, errors = router.recognize(
            wav_bytes, model="aliyun/auto", filename="test_asr_16k.wav",
        )
        assert result.text
        assert result.provider == "aliyun"
        print(f"识别结果: {result.text!r} model={result.model} errors={errors}")
