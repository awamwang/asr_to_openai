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
from asr_proxy.providers.tencent import (
    TencentProvider,
    build_flash_query,
    flash_authorization,
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
