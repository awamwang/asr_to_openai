# -*- coding: utf-8 -*-
"""阶跃 / 腾讯极速版 / 百度 Provider 单测。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import httpx
import pytest

from asr_proxy.config import ProviderConfig
from asr_proxy.providers.baidu import BaiduProvider
from asr_proxy.providers.errors import AsrProviderError
from asr_proxy.providers.stepfun import StepFunProvider
from asr_proxy.providers.stream_util import (
    StableSentenceCollector,
    iter_pcm_chunks,
    send_pcm_chunks,
)
from asr_proxy.providers.tencent import (
    TencentProvider,
    _extract_flash_text,
    apply_realtime_message,
    build_flash_query,
    build_realtime_ws_url,
    flash_authorization,
    is_realtime_model,
    merge_flash_sentences,
    smooth_spoken_text,
    realtime_engine_type,
    realtime_signature,
)


def _mock_response(
    status: int = 200,
    payload: Optional[Dict[str, Any]] = None,
    text: str = "",
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.text = text or (json.dumps(payload) if payload is not None else "")
    if payload is not None:
        resp.json.return_value = payload
    else:
        resp.json.side_effect = ValueError("not json")
    return resp


class TestAsrProviderError:
    def test_str_includes_code_and_model(self):
        err = AsrProviderError(
            "无权限", code="permission", provider="baidu", model="80001",
        )
        assert str(err) == "[permission] baidu/80001: 无权限"


class TestStepFunProvider:
    def test_success(self):
        client = MagicMock()
        client.post.return_value = _mock_response(
            200, {"text": "你好", "language": "zh", "duration": 1.2},
        )
        p = StepFunProvider(
            ProviderConfig(name="stepfun", api_key="sk-test", models=["stepaudio-2.5-asr"]),
            client=client,
        )
        result = p.recognize(b"RIFF....", filename="a.wav")
        assert result.text == "你好"
        assert result.provider == "stepfun"
        assert result.model == "stepaudio-2.5-asr"

    def test_auth_401(self):
        client = MagicMock()
        client.post.return_value = _mock_response(
            401, {"error": {"message": "Invalid API key"}},
        )
        p = StepFunProvider(
            ProviderConfig(name="stepfun", api_key="bad", models=["stepaudio-2.5-asr"]),
            client=client,
        )
        with pytest.raises(AsrProviderError) as ei:
            p.recognize(b"audio")
        assert ei.value.code == "auth"


class TestTencentFlash:
    def test_query_sorted(self):
        q = build_flash_query({
            "voice_format": "wav",
            "engine_type": "16k_zh_en",
            "secretid": "AKIDxxx",
            "timestamp": 1,
        })
        assert q.startswith("engine_type=16k_zh_en&")
        assert "secretid=AKIDxxx" in q
        assert q.index("engine_type") < q.index("secretid") < q.index("timestamp") < q.index("voice_format")

    def test_authorization_hmac_sha1(self):
        query = "engine_type=16k_zh&secretid=SID&timestamp=1&voice_format=wav"
        sig = flash_authorization("125", "SID", "SKEY", query)
        expect = base64.b64encode(
            hmac.new(
                b"SKEY",
                f"POSTasr.cloud.tencent.com/asr/flash/v1/125?{query}".encode(),
                hashlib.sha1,
            ).digest()
        ).decode()
        assert sig == expect

    def test_success(self):
        client = MagicMock()
        client.post.return_value = _mock_response(200, {
            "code": 0,
            "message": "",
            "audio_duration": 1500,
            "flash_result": [{"text": "腾讯测试", "channel_id": 0, "sentence_list": []}],
        })
        p = TencentProvider(
            ProviderConfig(
                name="tencent",
                secret_id="SID",
                secret_key="SKEY",
                app_id="1255532176",
                models=["16k_zh_en"],
            ),
            client=client,
        )
        result = p.recognize(b"wavdata", filename="t.wav")
        assert result.text == "腾讯测试"
        assert result.duration == pytest.approx(1.5)
        url = client.post.call_args.kwargs.get("url") or client.post.call_args[0][0]
        assert "extra_punc=0" in url
        assert "filter_punc=2" in url

    def test_merge_short_pause_sentences(self):
        payload = {
            "code": 0,
            "audio_duration": 4000,
            "flash_result": [{
                "text": "再去看一下。腾讯云的。具有免费额度。",
                "channel_id": 0,
                "sentence_list": [
                    {"text": "再去看一下。", "start_time": 0, "end_time": 800},
                    {"text": "腾讯云的。", "start_time": 950, "end_time": 1600},
                    {"text": "具有免费额度。", "start_time": 1750, "end_time": 2800},
                ],
            }],
        }
        text, duration, segs = _extract_flash_text(payload, max_sentence_silence=2500)
        assert duration == pytest.approx(4.0)
        assert text == "再去看一下腾讯云的具有免费额度。"
        assert len(segs) == 1
        assert smooth_spoken_text(
            "我的这个项目主要，目的就是，给，一些软件。"
        ) == "我的这个项目主要目的就是给一些软件。"

    def test_keep_long_pause_sentences(self):
        sents = [
            {"text": "第一句。", "start_ms": 0, "end_ms": 500},
            {"text": "第二句。", "start_ms": 4000, "end_ms": 4800},
        ]
        merged = merge_flash_sentences(sents, 2500)
        assert len(merged) == 2

    def test_not_activated_4003(self):
        client = MagicMock()
        client.post.return_value = _mock_response(200, {
            "code": 4003,
            "message": "AppID 服务未开通",
        })
        p = TencentProvider(
            ProviderConfig(
                name="tencent",
                secret_id="SID",
                secret_key="SKEY",
                app_id="125",
                models=["16k_zh_en"],
            ),
            client=client,
        )
        with pytest.raises(AsrProviderError) as ei:
            p.recognize(b"wav")
        assert ei.value.code == "not_activated"

    def test_auth_4002(self):
        client = MagicMock()
        client.post.return_value = _mock_response(200, {
            "code": 4002,
            "message": "鉴权失败",
        })
        p = TencentProvider(
            ProviderConfig(
                name="tencent",
                secret_id="SID",
                secret_key="SKEY",
                app_id="125",
                models=["16k_zh_en"],
            ),
            client=client,
        )
        with pytest.raises(AsrProviderError) as ei:
            p.recognize(b"wav")
        assert ei.value.code == "auth"

    def test_async_model_rejected(self):
        p = TencentProvider(
            ProviderConfig(
                name="tencent",
                secret_id="SID",
                secret_key="SKEY",
                app_id="125",
                models=["16k_zh_en"],
            ),
            client=MagicMock(),
        )
        with pytest.raises(AsrProviderError) as ei:
            p.recognize(b"wav", model="16k_zh_en_2.0")
        assert "公网" in ei.value.message or "URL" in ei.value.message


class TestStreamUtil:
    def test_iter_pcm_chunks_marks_last(self):
        pcm = b"x" * 10000  # > 6400
        chunks = list(iter_pcm_chunks(pcm, segment_ms=200, sample_rate=16000))
        assert len(chunks) >= 2
        assert all(isinstance(c, bytes) for c, _ in chunks)
        assert chunks[-1][1] is True
        assert all(not last for _, last in chunks[:-1])

    def test_send_pcm_chunks_pace_calls_sleep(self):
        slept = []
        sent = []

        def _send(chunk, is_last):
            sent.append((chunk, is_last))

        send_pcm_chunks(
            b"\x00" * 12800,
            _send,
            segment_ms=200,
            sample_rate=16000,
            pace=True,
            sleep_fn=lambda s: slept.append(s),
        )
        assert len(sent) == 2
        assert slept == [0.2]  # 仅非 last 休眠

    def test_stable_collector(self):
        c = StableSentenceCollector()
        c.add("你好", 0, 500, stable=True)
        c.add("中间", 500, 800, stable=False)  # 忽略
        c.add("世界", 800, 1200, stable=True)
        text, segs, dur = c.finalize()
        assert text == "你好世界"
        assert len(segs) == 2
        assert dur == pytest.approx(1.2)


class TestTencentRealtime:
    def test_model_routing_helpers(self):
        assert is_realtime_model("realtime-16k_zh")
        assert not is_realtime_model("16k_zh")
        assert realtime_engine_type("realtime-16k_zh") == "16k_zh"
        assert realtime_engine_type("16k_zh") == "16k_zh"

    def test_realtime_signature_and_url(self):
        url = build_realtime_ws_url(
            "125", "SID", "SKEY", "16k_zh",
            voice_id="vid",
            timestamp=100,
            nonce=100,
            expired=200,
        )
        assert url.startswith("wss://asr.cloud.tencent.com/asr/v2/125?")
        assert "engine_model_type=16k_zh" in url
        assert "signature=" in url
        # 签名原文不含 signature，且已 urlencode
        from urllib.parse import unquote, urlparse, parse_qs
        q = parse_qs(urlparse(url).query)
        sig = unquote(q["signature"][0])
        sign_url = (
            "asr.cloud.tencent.com/asr/v2/125?"
            "engine_model_type=16k_zh&expired=200&needvad=1&nonce=100"
            "&secretid=SID&timestamp=100&voice_format=1&voice_id=vid"
        )
        assert sig == realtime_signature("SKEY", sign_url)

    def test_apply_realtime_message_collects_stable(self):
        c = StableSentenceCollector()
        assert apply_realtime_message(c, {
            "code": 0,
            "result": {
                "slice_type": 1,
                "voice_text_str": "中间结果",
                "start_time": 0,
                "end_time": 500,
            },
        }) is False
        assert apply_realtime_message(c, {
            "code": 0,
            "result": {
                "slice_type": 2,
                "voice_text_str": "最终一句",
                "start_time": 0,
                "end_time": 1200,
            },
        }) is False
        assert apply_realtime_message(c, {"code": 0, "final": 1}) is True
        text, segs, dur = c.finalize()
        assert text == "最终一句"
        assert dur == pytest.approx(1.2)
        assert len(segs) == 1

    def test_realtime_ws_flow(self, monkeypatch):
        import types
        import asr_proxy.providers.tencent as tencent_mod

        messages = [
            json.dumps({"code": 0, "message": "success", "voice_id": "v1"}),
            json.dumps({
                "code": 0,
                "result": {
                    "slice_type": 2,
                    "voice_text_str": "实时测试",
                    "start_time": 0,
                    "end_time": 800,
                },
            }),
            json.dumps({"code": 0, "final": 1}),
        ]
        state = {"i": 0, "sent_bin": 0, "sent_end": False}

        class FakeWS:
            def send(self, data, opcode=None):
                # text end
                if isinstance(data, str) and "end" in data:
                    state["sent_end"] = True
                elif isinstance(data, (bytes, bytearray)):
                    state["sent_bin"] += 1

            def recv(self):
                if state["i"] >= len(messages):
                    import websocket
                    raise websocket.WebSocketTimeoutException("done")
                m = messages[state["i"]]
                state["i"] += 1
                return m

            def settimeout(self, t):
                return None

            def close(self):
                return None

        fake_ws_mod = types.ModuleType("websocket")

        class _Timeout(Exception):
            pass

        class _Closed(Exception):
            pass

        class _ABNF:
            OPCODE_BINARY = 2
            OPCODE_TEXT = 1

        fake_ws_mod.WebSocketTimeoutException = _Timeout
        fake_ws_mod.WebSocketConnectionClosedException = _Closed
        fake_ws_mod.ABNF = _ABNF
        fake_ws_mod.create_connection = lambda *a, **k: FakeWS()
        monkeypatch.setitem(__import__("sys").modules, "websocket", fake_ws_mod)

        monkeypatch.setattr(
            tencent_mod, "to_wav_mono",
            lambda src, sample_rate=16000, ffmpeg="ffmpeg": src,
        )
        monkeypatch.setattr(
            tencent_mod, "wav_to_pcm_s16le",
            lambda path: (b"\x00" * 6400, 16000),
        )
        monkeypatch.setattr(
            tencent_mod, "write_temp_audio",
            lambda data, suffix=".wav": "tmp.wav",
        )
        monkeypatch.setattr(tencent_mod, "cleanup", lambda *a, **k: None)

        p = TencentProvider(
            ProviderConfig(
                name="tencent",
                secret_id="SID",
                secret_key="SKEY",
                app_id="125",
                models=["realtime-16k_zh"],
            ),
            pace_realtime=False,
            sleep_fn=lambda _: None,
        )
        result = p.recognize(b"fake", filename="a.wav", model="realtime-16k_zh")
        assert result.text == "实时测试"
        assert result.model == "realtime-16k_zh"
        assert state["sent_bin"] >= 1
        assert state["sent_end"] is True


class TestBaiduProvider:
    def test_success(self):
        client = MagicMock()
        client.get.return_value = _mock_response(
            200, {"access_token": "tok", "expires_in": 2592000},
        )
        client.post.return_value = _mock_response(
            200, {"err_no": 0, "err_msg": "success.", "result": ["你好百度"]},
        )
        p = BaiduProvider(
            ProviderConfig(
                name="baidu",
                api_key="ak",
                secret_key="sk",
                app_id="123",
                models=["1537"],
            ),
            client=client,
        )
        result = p.recognize(b"wavbytes", filename="a.wav", model="1537")
        assert result.text == "你好百度"
        assert result.model == "1537"

    def test_permission_3302(self):
        client = MagicMock()
        client.get.return_value = _mock_response(
            200, {"access_token": "tok", "expires_in": 1000},
        )
        client.post.return_value = _mock_response(
            200,
            {"err_no": 3302, "err_msg": "6: No permission to access data"},
        )
        p = BaiduProvider(
            ProviderConfig(
                name="baidu",
                api_key="ak",
                secret_key="sk",
                app_id="123",
                models=["80001"],
            ),
            client=client,
        )
        with pytest.raises(AsrProviderError) as ei:
            p.recognize(b"wav", model="80001")
        assert ei.value.code == "permission"
        assert ei.value.model == "80001"

    def test_oauth_failure(self):
        client = MagicMock()
        client.get.return_value = _mock_response(
            200, {"error": "invalid_client", "error_description": "unknown client id"},
        )
        p = BaiduProvider(
            ProviderConfig(
                name="baidu",
                api_key="bad",
                secret_key="bad",
                models=["1537"],
            ),
            client=client,
        )
        with pytest.raises(AsrProviderError) as ei:
            p.recognize(b"wav")
        assert ei.value.code == "auth"


class TestProviderEnabled:
    def test_tencent_needs_secrets(self):
        assert not ProviderConfig(name="tencent", api_key="x").enabled
        assert ProviderConfig(
            name="tencent", secret_id="a", secret_key="b",
        ).enabled

    def test_baidu_needs_ak_sk(self):
        assert not ProviderConfig(name="baidu", api_key="ak").enabled
        assert ProviderConfig(
            name="baidu", api_key="ak", secret_key="sk",
        ).enabled

    def test_stepfun_api_key(self):
        assert ProviderConfig(name="stepfun", api_key="sk").enabled
        assert not ProviderConfig(name="stepfun").enabled
