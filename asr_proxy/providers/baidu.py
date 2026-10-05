# -*- coding: utf-8 -*-
"""百度智能云短语音识别：OAuth access_token + server_api。

文档：https://cloud.baidu.com/doc/SPEECH/s/Jlbxdezuf
模型：dev_pid 1537（标准版）/ 80001（极速版）；单次硬限约 60s。
"""
from __future__ import annotations

import base64
import time
from typing import Any, Dict, Optional

import httpx

from asr_proxy.config import ProviderConfig
from asr_proxy.providers.base import AsrProvider, AsrResult
from asr_proxy.providers.errors import AsrProviderError

DEFAULT_MODEL = "1537"
DEFAULT_TIMEOUT = 120.0
TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
ASR_URL = "https://vop.baidu.com/server_api"

# 常见 err_no → 统一 code（检测不到则 upstream）
_ERR_NO_MAP = {
    1: "upstream",       # server error
    2: "upstream",
    3: "upstream",
    4: "quota",          # 集群超限额
    5: "permission",     # 无权限 / 未开通某产品
    6: "permission",
    17: "quota",
    18: "quota",
    19: "quota",
    100: "auth",         # invalid parameter / token
    110: "auth",         # Access token invalid or no longer valid
    111: "auth",         # Access token expired
    3300: "upstream",
    3301: "upstream",
    3302: "permission",  # 无权限访问数据（如极速版未开通）
    3307: "upstream",
    3308: "upstream",
}


def _map_err_no(err_no: int) -> str:
    return _ERR_NO_MAP.get(int(err_no), "upstream")


def _parse_dev_pid(model: str) -> int:
    raw = (model or "").strip() or DEFAULT_MODEL
    try:
        return int(raw)
    except ValueError as e:
        raise AsrProviderError(
            f"无效的百度模型/dev_pid: {raw!r}",
            code="upstream",
            provider="baidu",
            model=raw,
        ) from e


class BaiduProvider(AsrProvider):
    """百度短语音识别。

    配置：app_id / api_key / secret_key / models（1537、80001）
    """

    name = "baidu"

    def __init__(
        self,
        config: ProviderConfig,
        ffmpeg_path: str = "ffmpeg",
        client: Optional[httpx.Client] = None,
    ):
        super().__init__(config, ffmpeg_path)
        self.timeout = float(config.timeout or DEFAULT_TIMEOUT)
        self._client = client
        self._token: str = ""
        self._token_expire_at: float = 0.0

    @property
    def default_model(self) -> str:
        return self.config.model or DEFAULT_MODEL

    def _make_client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout)

    def _get_token(self, client: httpx.Client) -> str:
        now = time.time()
        if self._token and now < self._token_expire_at - 60:
            return self._token

        api_key = (self.config.api_key or "").strip()
        secret_key = (self.config.secret_key or "").strip()
        try:
            resp = client.get(
                TOKEN_URL,
                params={
                    "grant_type": "client_credentials",
                    "client_id": api_key,
                    "client_secret": secret_key,
                },
            )
        except httpx.RequestError as e:
            raise AsrProviderError(
                f"OAuth 请求失败: {type(e).__name__}: {e}",
                code="upstream",
                provider=self.name,
            ) from e

        try:
            data = resp.json()
        except ValueError as e:
            raise AsrProviderError(
                f"OAuth 返回非 JSON: {(resp.text or '')[:200]}",
                code="auth",
                provider=self.name,
            ) from e

        token = str((data or {}).get("access_token") or "").strip()
        if not token:
            err = (data or {}).get("error_description") or (data or {}).get("error") or data
            raise AsrProviderError(
                f"换 token 失败: {err}",
                code="auth",
                provider=self.name,
            )

        try:
            expires_in = float((data or {}).get("expires_in") or 2592000)
        except (TypeError, ValueError):
            expires_in = 2592000.0
        self._token = token
        self._token_expire_at = now + expires_in
        return token

    def recognize(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        model: Optional[str] = None,
        language: Optional[str] = None,
    ) -> AsrResult:
        _ = language
        if not self.enabled:
            raise AsrProviderError(
                "百度未启用（缺少 api_key/secret_key）",
                code="auth", provider=self.name,
            )
        if not audio_bytes:
            raise AsrProviderError(
                "empty audio", code="upstream", provider=self.name,
            )

        used_model = (model or "").strip() or self.default_model
        dev_pid = _parse_dev_pid(used_model)
        app_id = (self.config.app_id or "").strip() or "asr-proxy"

        client = self._client
        owns_client = client is None
        if owns_client:
            client = self._make_client()
        assert client is not None

        try:
            token = self._get_token(client)
            body: Dict[str, Any] = {
                "format": "wav",
                "rate": 16000,
                "channel": 1,
                "cuid": f"asr-proxy-{app_id}",
                "token": token,
                "dev_pid": dev_pid,
                "speech": base64.b64encode(audio_bytes).decode("ascii"),
                "len": len(audio_bytes),
            }
            # 非 wav 时尽量按扩展名提示（上游主要认 wav/pcm）
            if filename and not filename.lower().endswith(".wav"):
                ext = filename.rsplit(".", 1)[-1].lower()
                if ext in ("pcm", "amr", "m4a", "mp3"):
                    body["format"] = ext

            try:
                resp = client.post(ASR_URL, json=body)
            except httpx.RequestError as e:
                raise AsrProviderError(
                    f"识别请求失败: {type(e).__name__}: {e}",
                    code="upstream",
                    provider=self.name,
                    model=used_model,
                ) from e
        finally:
            if owns_client:
                client.close()

        try:
            data = resp.json()
        except ValueError as e:
            raise AsrProviderError(
                f"返回非 JSON status={resp.status_code} body={(resp.text or '')[:200]}",
                code="upstream",
                provider=self.name,
                model=used_model,
            ) from e
        if not isinstance(data, dict):
            raise AsrProviderError(
                f"响应格式异常: {type(data).__name__}",
                code="upstream",
                provider=self.name,
                model=used_model,
            )

        err_no = data.get("err_no")
        try:
            err_int = int(err_no) if err_no is not None else -1
        except (TypeError, ValueError):
            err_int = -1

        if err_int != 0:
            err_msg = str(data.get("err_msg") or "").strip() or f"err_no={err_int}"
            # token 失效时清缓存，便于下次重试
            if err_int in (110, 111):
                self._token = ""
                self._token_expire_at = 0.0
            raise AsrProviderError(
                err_msg,
                code=_map_err_no(err_int),
                provider=self.name,
                model=used_model,
            )

        results = data.get("result") or []
        if isinstance(results, list):
            text = " ".join(str(x).strip() for x in results if str(x).strip())
        else:
            text = str(results).strip()
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
            duration=0.0,
            segments=None,
            provider=self.name,
            model=used_model,
        )
