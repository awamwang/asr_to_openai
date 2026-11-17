# -*- coding: utf-8 -*-
"""dnskeepalive：域名收集、URL 提取、线程启停。"""
from __future__ import annotations

import logging
import time

import pytest

from asr_proxy.config import AppConfig, ProviderConfig
from asr_proxy.dnskeepalive import (
    collect_warm_hosts,
    host_from_url,
    start_dns_keepalive,
    warm_round,
)


class TestHostFromUrl:
    def test_https(self):
        assert host_from_url("https://api.stepfun.com/v1") == "api.stepfun.com"

    def test_wss(self):
        url = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
        assert host_from_url(url) == "openspeech.bytedance.com"

    def test_no_scheme(self):
        assert host_from_url("api.groq.com/openai/v1") == "api.groq.com"

    def test_uppercase_and_port(self):
        assert host_from_url("https://API.StepFun.com:8443/x") == "api.stepfun.com"

    def test_empty_and_invalid(self):
        assert host_from_url("") == ""
        assert host_from_url(None) == ""  # type: ignore[arg-type]

    def test_bare_host(self):
        assert host_from_url("asr.cloud.tencent.com") == "asr.cloud.tencent.com"


def _provider(name: str, **kwargs) -> ProviderConfig:
    return ProviderConfig(name=name, **kwargs)


class TestCollectWarmHosts:
    def _cfg(self, **providers) -> AppConfig:
        return AppConfig(providers=providers)

    def test_base_url_hosts_of_enabled_providers(self):
        cfg = self._cfg(
            stepfun=_provider("stepfun", api_key="k", base_url="https://api.stepfun.com/v1"),
            siliconflow=_provider("siliconflow", api_key="", base_url="https://api.siliconflow.cn/v1"),
        )
        hosts = collect_warm_hosts(cfg)
        assert "api.stepfun.com" in hosts
        assert "api.siliconflow.cn" not in hosts  # 未启用不保活

    def test_doubao_ws_host(self):
        cfg = self._cfg(
            doubao=_provider(
                "doubao",
                api_key="k",
                base_url="wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream",
            ),
        )
        assert collect_warm_hosts(cfg) == ["openspeech.bytedance.com"]

    def test_aliyun_default_and_workspace(self):
        cfg = self._cfg(
            aliyun=_provider("aliyun", api_key="k", workspace_id="ws-abc"),
        )
        hosts = collect_warm_hosts(cfg)
        assert "dashscope.aliyuncs.com" in hosts
        assert "ws-abc.cn-beijing.maas.aliyuncs.com" in hosts

    def test_tencent_and_baidu_enabled(self):
        cfg = self._cfg(
            tencent=_provider("tencent", secret_id="id", secret_key="key"),
            baidu=_provider("baidu", api_key="k", secret_key="s"),
        )
        hosts = collect_warm_hosts(cfg)
        assert "asr.cloud.tencent.com" in hosts
        assert "aip.baidubce.com" in hosts
        assert "vop.baidu.com" in hosts

    def test_ip_literal_skipped(self):
        cfg = self._cfg(
            stepfun=_provider("stepfun", api_key="k", base_url="https://127.0.0.1:8004/v1"),
        )
        assert collect_warm_hosts(cfg) == []

    def test_no_duplicate_hosts(self):
        cfg = self._cfg(
            stepfun=_provider("stepfun", api_key="k", base_url="https://api.stepfun.com/v1"),
            groq=_provider("groq", api_key="k2", base_url="https://api.stepfun.com/other"),
        )
        assert collect_warm_hosts(cfg).count("api.stepfun.com") == 1


class TestWarmRound:
    def test_failure_logged_not_raised(self, monkeypatch):
        import asr_proxy.dnskeepalive as mod

        def boom(host: str) -> float:
            raise OSError(11001, "getaddrinfo failed")

        monkeypatch.setattr(mod, "resolve_host", boom)
        records: list = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger = logging.getLogger("test_dns_warm")
        logger.handlers = [Capture()]
        logger.setLevel(logging.DEBUG)
        warm_round(["bad.example"], logger)  # 不应抛异常
        assert any("resolve failed" in r.getMessage() for r in records)

    def test_ok_debug_summary(self, monkeypatch):
        import asr_proxy.dnskeepalive as mod

        monkeypatch.setattr(mod, "resolve_host", lambda host: 5.0)
        records: list = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger = logging.getLogger("test_dns_warm2")
        logger.handlers = [Capture()]
        logger.setLevel(logging.DEBUG)
        warm_round(["a.example", "b.example"], logger)
        assert any("round ok=2/2" in r.getMessage() for r in records)


class TestStartStop:
    def test_disabled_returns_none(self):
        cfg = AppConfig(dns_keepalive_sec=0)
        assert start_dns_keepalive(cfg) is None

    def test_no_hosts_returns_none(self):
        cfg = AppConfig(dns_keepalive_sec=20)
        assert start_dns_keepalive(cfg) is None

    def test_start_runs_rounds_and_stops(self, monkeypatch):
        calls: list = []

        monkeypatch.setattr(
            "asr_proxy.dnskeepalive.warm_round",
            lambda hosts, logger: calls.append(list(hosts)),
        )
        cfg = AppConfig(
            dns_keepalive_sec=0.05,
            providers={
                "stepfun": _provider(
                    "stepfun", api_key="k", base_url="https://api.stepfun.com/v1",
                ),
            },
        )
        stop = start_dns_keepalive(cfg)
        assert stop is not None
        deadline = time.monotonic() + 5.0
        while len(calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        stop()
        n = len(calls)
        time.sleep(0.2)
        assert len(calls) in (n, n + 1)  # stop 后不再显著增加
        assert calls[0] == ["api.stepfun.com"]
