# -*- coding: utf-8 -*-
"""安全与并发加固回归测试：全部本地 mock，不调用任何真实上游。"""
from __future__ import annotations

import asyncio
import base64
import binascii
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from asr_proxy.app import (
    _safe_filename,
    _try_decode_b64,
    create_app,
    looks_like_audio,
)
from asr_proxy.audio import build_pcm_wav
from asr_proxy.config import AppConfig, ProviderConfig
from asr_proxy.providers.base import AsrProvider, AsrResult


def _cfg(tmp_path, **overrides):
    params = dict(
        providers={"slow": ProviderConfig(name="slow", api_key="x", models=["m"])},
        auto_order=["slow"],
        audio_preprocess=False,
        audio_trim_silence=False,
        log_file=str(tmp_path / "hardening.log"),
    )
    params.update(overrides)
    return AppConfig(**params)


def _app(tmp_path, cfg, *, delay=0.0, text="识别OK", fail=False, counter=None):
    app = create_app(cfg)
    calls = counter if counter is not None else {"n": 0}

    class SlowProvider(AsrProvider):
        name = "slow"

        def recognize(self, audio_bytes, filename="audio.wav", model=None, language=None):
            calls["n"] += 1
            if fail:
                raise RuntimeError("SECRET_UPSTREAM_DETAIL")
            if delay:
                time.sleep(delay)
            return AsrResult(text=text, provider="slow", model="m")

    app.state.router_asr.providers["slow"] = SlowProvider(cfg.providers["slow"])
    return app


def _wav(frames: int = 200) -> bytes:
    return build_pcm_wav(b"\x00\x00" * frames, 16000)


def _body(b64: str, model: str = "auto") -> dict:
    return {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [{
                "type": "input_audio",
                "input_audio": {"data": "data:audio/wav;base64," + b64},
            }],
        }],
    }


class TestConcurrency:
    def test_three_concurrent_requests_do_not_block_event_loop(self, tmp_path):
        cfg = _cfg(tmp_path)
        app = _app(tmp_path, cfg, delay=0.4)
        body = _body(base64.b64encode(_wav()).decode())

        async def run():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                t0 = time.perf_counter()
                rs = await asyncio.gather(
                    *[c.post("/chat/completions", json=body) for _ in range(3)]
                )
                return time.perf_counter() - t0, [r.status_code for r in rs]

        elapsed, codes = asyncio.run(run())
        assert codes == [200, 200, 200]
        # 串行会被锁在 1.2s 以上；线程池化后应接近单次 0.4s
        assert elapsed < 0.9, f"并发仍被阻塞: {elapsed:.2f}s"


class TestRequestBoundary:
    def test_body_over_limit_returns_413(self, tmp_path):
        cfg = _cfg(tmp_path, max_body_bytes=1024)
        app = _app(tmp_path, cfg)
        body = _body(base64.b64encode(_wav(3000)).decode())
        r = TestClient(app).post("/chat/completions", json=body)
        assert r.status_code == 413

    def test_non_audio_long_strings_never_reach_upstream(self, tmp_path):
        counter = {"n": 0}
        cfg = _cfg(tmp_path)
        app = _app(tmp_path, cfg, counter=counter)
        # 20 个纯文本长串：旧实现会触发 20 次识别链
        body = {"model": "auto", "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "X" * 3000 + str(i)}
                for i in range(20)
            ],
        }]}
        r = TestClient(app).post("/chat/completions", json=body)
        assert r.status_code == 400
        assert counter["n"] == 0

    def test_very_short_wav_still_accepted(self, tmp_path):
        """不提高最小长度下限：极短但合法的 WAV 仍可识别。"""
        short = _wav(frames=60)  # 44 字节头 + 120 字节 PCM
        assert len(short) >= 100
        cfg = _cfg(tmp_path)
        app = _app(tmp_path, cfg)
        r = TestClient(app).post(
            "/chat/completions", json=_body(base64.b64encode(short).decode())
        )
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "识别OK"

    def test_b64_strict_validation(self):
        with pytest.raises(binascii.Error):
            _try_decode_b64("A" * 100 + "!!!!")
        assert _try_decode_b64("data:audio/wav;base64,QUJD") == b"ABC"
        assert _try_decode_b64("QU\nJD") == b"ABC"  # 折行兼容

    def test_looks_like_audio(self):
        assert looks_like_audio(b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 20)
        assert looks_like_audio(b"ID3" + b"\x00" * 20)
        assert looks_like_audio(b"OggS" + b"\x00" * 20)
        assert looks_like_audio(b"fLaC" + b"\x00" * 20)
        assert looks_like_audio(b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 10)
        assert not looks_like_audio(b"X" * 2000)


class TestErrorHandling:
    def test_upstream_error_sanitized_by_default(self, tmp_path):
        cfg = _cfg(tmp_path)
        app = _app(tmp_path, cfg, fail=True)
        r = TestClient(app).post(
            "/chat/completions", json=_body(base64.b64encode(_wav()).decode())
        )
        assert r.status_code == 502
        err = r.json()["error"]
        assert "SECRET_UPSTREAM_DETAIL" not in err["message"]
        assert err.get("request_id")

    def test_expose_errors_flag_restores_detail(self, tmp_path):
        cfg = _cfg(tmp_path, expose_errors=True)
        app = _app(tmp_path, cfg, fail=True)
        r = TestClient(app).post(
            "/chat/completions", json=_body(base64.b64encode(_wav()).decode())
        )
        assert r.status_code == 502
        assert "SECRET_UPSTREAM_DETAIL" in r.json()["error"]["message"]


class TestAuthAndPrivacy:
    def test_optional_token_default_off(self, tmp_path):
        cfg = _cfg(tmp_path)
        app = _app(tmp_path, cfg)
        r = TestClient(app).post(
            "/chat/completions", json=_body(base64.b64encode(_wav()).decode())
        )
        assert r.status_code == 200

    def test_optional_token_enforced_when_configured(self, tmp_path):
        cfg = _cfg(tmp_path, auth_token="s3cr3t")
        app = _app(tmp_path, cfg)
        client = TestClient(app)
        body = _body(base64.b64encode(_wav()).decode())
        assert client.get("/health").status_code == 200  # 健康检查放行
        assert client.post("/chat/completions", json=body).status_code == 401
        assert client.post(
            "/chat/completions",
            json=body,
            headers={"Authorization": "Bearer s3cr3t"},
        ).status_code == 200

    def test_asr_text_not_written_to_log_by_default(self, tmp_path):
        cfg = _cfg(tmp_path)
        app = _app(tmp_path, cfg, text="敏感语音内容ABC")
        r = TestClient(app).post(
            "/chat/completions", json=_body(base64.b64encode(_wav()).decode())
        )
        assert r.status_code == 200
        log = (tmp_path / "hardening.log").read_text(encoding="utf-8")
        assert "敏感语音内容ABC" not in log
        assert "len(text)=" in log

    def test_docs_disabled_by_default(self, tmp_path):
        app = _app(tmp_path, _cfg(tmp_path))
        client = TestClient(app)
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404

    def test_safe_filename_strips_control_chars_and_paths(self):
        cleaned = _safe_filename("../../evil.wav\r\n[ERROR] x")
        assert "\n" not in cleaned and "\r" not in cleaned
        assert "/" not in cleaned and "\\" not in cleaned
        assert _safe_filename("") == "audio.wav"


class TestConfigDefaults:
    def test_security_defaults(self):
        cfg = AppConfig()
        assert cfg.auth_token == ""
        assert cfg.expose_errors is False
        assert cfg.max_body_bytes == 25 * 1024 * 1024
        assert cfg.max_audio_candidates == 8
        assert cfg.log_asr_text is False
        assert cfg.enable_docs is False

    def test_security_env_override(self, monkeypatch, tmp_path):
        from asr_proxy.config import EXAMPLE_CONFIG_PATH, load_config

        monkeypatch.setenv("ASR_AUTH_TOKEN", "tok123")
        monkeypatch.setenv("ASR_EXPOSE_ERRORS", "1")
        monkeypatch.setenv("ASR_MAX_BODY_BYTES", "4096")
        cfg = load_config(EXAMPLE_CONFIG_PATH)
        assert cfg.auth_token == "tok123"
        assert cfg.expose_errors is True
        assert cfg.max_body_bytes == 4096
