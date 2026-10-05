# -*- coding: utf-8 -*-
"""策略路由测试：config 解析、模型元数据、四种策略排序、路由接入、时长探测。"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from asr_proxy.audio import _wav_duration_from_header, probe_audio_duration
from asr_proxy.config import AppConfig, ProviderConfig, load_config
from asr_proxy.providers import ProviderRouter, build_providers
from asr_proxy.providers.base import AsrProvider, AsrResult
from asr_proxy.strategy import (
    STRATEGY_NAMES,
    STRATEGY_PUBLIC_IDS,
    AutoStrategy,
    build_strategy,
    parse_request_strategy,
)
from asr_proxy.strategy.impl import (
    AccuracyFirstStrategy,
    CostFirstStrategy,
    FreeStrategy,
    RecommendedStrategy,
    SpeedFirstStrategy,
)
from asr_proxy.strategy.metadata import estimate_cost, get_profile, is_free_profile

WAV_PATH = ROOT / "test_asr_16k.wav"


def _provider_cfg(name: str, api_key: str, models, **kw) -> ProviderConfig:
    return ProviderConfig(name=name, api_key=api_key, models=list(models), **kw)


@pytest.fixture
def wav_bytes() -> bytes:
    assert WAV_PATH.is_file(), f"缺少测试音频: {WAV_PATH}"
    return WAV_PATH.read_bytes()


@pytest.fixture
def cfg_all_enabled() -> AppConfig:
    """四个服务商全部启用（假 key）。"""
    return AppConfig(
        host="127.0.0.1",
        port=8004,
        log_file=str(ROOT / "test_asr_proxy.log"),
        ffmpeg_path=os.environ.get("FFMPEG_PATH", "ffmpeg"),
        strategy="auto",
        auto_order=["aliyun", "doubao", "siliconflow", "groq"],
        providers={
            "aliyun": _provider_cfg("aliyun", "sk-t", [
                "qwen-audio-3.1-asr-flash-streaming",
                "qwen-audio-3.0-asr-flash",
            ]),
            "doubao": _provider_cfg("doubao", "k-t", ["asr-2.0", "asr-1.0"]),
            "siliconflow": _provider_cfg(
                "siliconflow", "sk-t", ["SenseVoiceSmall"],
            ),
            "groq": _provider_cfg("groq", "gsk-t", [
                "whisper-large-v3", "whisper-large-v3-turbo",
            ]),
        },
    )


def _pool_labels(candidates):
    return [(c.provider.name, c.model) for c in candidates]


def _pair_labels(ordered):
    return [(p.name, m) for p, m in ordered]


class _FakeProvider(AsrProvider):
    """仅用于策略排序的最小服务商桩。"""

    def __init__(self, name: str, models):
        super().__init__(ProviderConfig(name=name, api_key="k", models=list(models)))
        self.name = name

    def recognize(self, audio_bytes, filename="audio.wav", model=None, language=None):
        raise NotImplementedError("fake provider")


class TestConfigStrategy:
    def test_default_strategy_auto(self):
        cfg = load_config(ROOT / "config.example.yaml")
        assert cfg.strategy == "auto"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("ASR_STRATEGY", "speed")
        cfg = load_config(ROOT / "config.example.yaml")
        assert cfg.strategy == "speed"

    def test_invalid_strategy_raises(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("strategy: bogus\nproviders: {}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="未知 strategy"):
            load_config(p)

    def test_strategy_names_registered(self):
        assert "free" in STRATEGY_NAMES
        assert isinstance(build_strategy("auto"), AutoStrategy)
        assert isinstance(build_strategy(""), AutoStrategy)
        assert isinstance(build_strategy("COST"), CostFirstStrategy)
        assert isinstance(build_strategy("Speed"), SpeedFirstStrategy)
        assert isinstance(build_strategy("accuracy"), AccuracyFirstStrategy)
        assert isinstance(build_strategy("free"), FreeStrategy)
        assert isinstance(build_strategy("recommended"), RecommendedStrategy)
        assert build_strategy("free+speed").name == "free+speed"
        assert parse_request_strategy("auto") is None
        assert parse_request_strategy("cost") is None
        assert parse_request_strategy("strategy-cost") == "cost"
        assert parse_request_strategy("strategy-free+accuracy") == "free+accuracy"
        assert "strategy-cost" in STRATEGY_PUBLIC_IDS


class TestMetadata:
    def test_siliconflow_longterm_free(self):
        p = get_profile("siliconflow", "SenseVoiceSmall")
        assert p.cost.billing == "free"
        assert p.cost.free_tier == "longterm"
        assert p.speed_tier == 2
        assert p.supported is True

    def test_aliases(self):
        assert get_profile("doubao", "seedasr").model == "asr-2.0"
        assert get_profile("doubao", "volc.seedasr.sauc.duration").model == "asr-2.0"
        assert (
            get_profile("siliconflow", "sensevoice").model
            == "FunAudioLLM/SenseVoiceSmall"
        )

    def test_groq_min_bill_and_currency(self):
        p = get_profile("groq", "whisper-large-v3")
        assert p.cost.min_bill_sec == 10.0
        assert p.cost.currency == "USD"
        assert p.cost.free_tier == "quota"
        assert p.cost.max_bytes == 25 * 1024 * 1024

    def test_tencent_flash_hard_limits_and_min_bill(self):
        flash = get_profile("tencent", "16k_zh_en")
        assert flash.cost.min_bill_sec == 1.0
        assert flash.cost.max_duration == 2 * 3600
        assert flash.cost.max_bytes == 100 * 1024 * 1024
        async_m = get_profile("tencent", "16k_zh_en_2.0")
        assert async_m.cost.min_bill_sec == 1.0
        assert async_m.cost.max_duration == 0
        assert async_m.cost.max_bytes == 0

    def test_silicon_5min_is_not_hard_limit(self):
        p = get_profile("siliconflow", "SenseVoiceSmall")
        assert p.cost.max_duration == 0
        assert p.cost.max_bytes == 0

    def test_aliyun_filetrans_marked_unsupported(self):
        p = get_profile("aliyun", "qwen3-asr-flash-filetrans")
        assert p.supported is False

    def test_unknown_fallback(self):
        p = get_profile("nope", "nope-model")
        assert p.cost.billing == "unknown"
        assert p.speed_tier == 5
        assert p.accuracy_tier == 5

    def test_estimate_cost_free(self):
        assert estimate_cost(get_profile("siliconflow", "SenseVoiceSmall"), 100) is None
        assert is_free_profile(get_profile("siliconflow", "SenseVoiceSmall"))

    def test_estimate_cost_per_second(self):
        p = get_profile("aliyun", "qwen-audio-3.0-asr-flash")
        assert estimate_cost(p, 100) == pytest.approx(0.00026 * 100)

    def test_estimate_cost_min_billing(self):
        # Groq 最短按 10s 计：3s 音频也按 10s 收费
        p = get_profile("groq", "whisper-large-v3")
        assert estimate_cost(p, 3) == pytest.approx(0.000031 * 10)

    def test_estimate_cost_per_call_and_limit(self):
        p = get_profile("baidu", "1537")
        assert estimate_cost(p, 30) == 0.0034
        assert estimate_cost(p, 61) is None  # 超出 60s 硬上限

    def test_estimate_cost_unknown(self):
        p = get_profile("nope", "m")
        assert estimate_cost(p, 30) is None

    def test_estimate_cost_tencent_min_one_second(self):
        p = get_profile("tencent", "16k_zh_en")
        assert estimate_cost(p, 0.4) == pytest.approx(0.000861 * 1.0)
        assert estimate_cost(p, 8) == pytest.approx(0.000861 * 8)


class TestCostStrategy:
    def test_paid_list_price_ignores_free(self, cfg_all_enabled):
        providers = build_providers(cfg_all_enabled)
        pool = [
            (p, m)
            for p in providers.values()
            for m in (p.model_names or [p.default_model])
        ]
        ordered = CostFirstStrategy().rank(pool, duration=120)
        labels = _pair_labels(ordered)
        # 硅基无刊例价、Groq 为 USD，均不进入 cost
        assert ("siliconflow", "SenseVoiceSmall") not in labels
        assert all(p != "groq" for p, _ in labels)
        # 人民币刊例：阿里秒价低于豆包
        assert labels[0][0] == "aliyun"
        assert ("doubao", "asr-2.0") in labels

    def test_quota_does_not_beat_cheaper_paid(self, monkeypatch):
        """免费额度不让模型在 cost 中占优。"""
        from asr_proxy.strategy import metadata as md

        paid = _FakeProvider("tprov", ["persec"])
        monkeypatch.setitem(
            md._PROFILES, ("tprov", "persec"),
            md.ModelProfile(
                provider="tprov", model="persec", speed_tier=3, accuracy_tier=3,
                cost=md.CostSpec(
                    billing="per_second", price_per_sec=0.000001, free_tier="",
                ),
            ),
        )
        candidates = [
            (_FakeProvider("siliconflow", ["SenseVoiceSmall"]), "SenseVoiceSmall"),
            (paid, "persec"),
        ]
        ordered = _pair_labels(CostFirstStrategy().rank(candidates, duration=10))
        assert ordered == [("tprov", "persec")]

    def test_per_call_vs_per_second_critical_time(self, monkeypatch):
        """刊例「按次 vs 按秒」的临界时长比较。"""
        from asr_proxy.strategy import metadata as md

        provider = _FakeProvider("tprov", ["percall", "persec"])
        monkeypatch.setitem(
            md._PROFILES, ("tprov", "percall"),
            md.ModelProfile(
                provider="tprov", model="percall", speed_tier=3, accuracy_tier=3,
                cost=md.CostSpec(
                    billing="per_call", price_per_call=0.0034, max_duration=60,
                ),
            ),
        )
        monkeypatch.setitem(
            md._PROFILES, ("tprov", "persec"),
            md.ModelProfile(
                provider="tprov", model="persec", speed_tier=3, accuracy_tier=3,
                cost=md.CostSpec(billing="per_second", price_per_sec=0.0001),
            ),
        )
        candidates = [(provider, m) for m in provider.model_names]

        # 临界 t* = 0.0034 / 0.0001 = 34s
        short = _pair_labels(CostFirstStrategy().rank(candidates, duration=10))
        assert short == [("tprov", "persec"), ("tprov", "percall")]
        long = _pair_labels(CostFirstStrategy().rank(candidates, duration=50))
        assert long == [("tprov", "percall"), ("tprov", "persec")]

    def test_hard_limit_excluded(self, monkeypatch):
        from asr_proxy.strategy import metadata as md

        provider = _FakeProvider("tprov", ["percall", "persec"])
        monkeypatch.setitem(
            md._PROFILES, ("tprov", "percall"),
            md.ModelProfile(
                provider="tprov", model="percall", speed_tier=3, accuracy_tier=3,
                cost=md.CostSpec(
                    billing="per_call", price_per_call=0.0034, max_duration=60,
                ),
            ),
        )
        monkeypatch.setitem(
            md._PROFILES, ("tprov", "persec"),
            md.ModelProfile(
                provider="tprov", model="persec", speed_tier=3, accuracy_tier=3,
                cost=md.CostSpec(billing="per_second", price_per_sec=0.0001),
            ),
        )
        candidates = [(provider, m) for m in provider.model_names]
        ordered = _pair_labels(CostFirstStrategy().rank(candidates, duration=120))
        # 120s 超出按次硬上限 60s → 被排除
        assert ordered == [("tprov", "persec")]

    def test_unknown_cost_goes_last(self):
        fake = _FakeProvider("weird", ["m1"])
        known = _FakeProvider("siliconflow", ["SenseVoiceSmall"])
        ordered = _pair_labels(CostFirstStrategy().rank(
            [(fake, "m1"), (known, "SenseVoiceSmall")], duration=10,
        ))
        assert ordered == [("weird", "m1")]

    def test_stepfun_beats_baidu_within_hard_limit(self):
        """双方都能用时阶跃恒更省（临界 82s 已超出百度 60s 硬限）。"""
        candidates = [
            (_FakeProvider("baidu", ["1537"]), "1537"),
            (_FakeProvider("stepfun", ["stepaudio-2.5-asr"]), "stepaudio-2.5-asr"),
        ]
        short = _pair_labels(CostFirstStrategy().rank(candidates, duration=10))
        mid = _pair_labels(CostFirstStrategy().rank(candidates, duration=50))
        assert short[0] == ("stepfun", "stepaudio-2.5-asr")
        assert mid[0] == ("stepfun", "stepaudio-2.5-asr")
        over = _pair_labels(CostFirstStrategy().rank(candidates, duration=90))
        assert over == [("stepfun", "stepaudio-2.5-asr")]

    def test_duration_unknown_does_not_drop_baidu(self):
        candidates = [
            (_FakeProvider("baidu", ["1537"]), "1537"),
            (_FakeProvider("aliyun", ["qwen-audio-3.0-asr-flash"]), "qwen-audio-3.0-asr-flash"),
        ]
        ordered = _pair_labels(CostFirstStrategy().rank(candidates, duration=None))
        assert ("baidu", "1537") in ordered
        assert ordered[0][0] == "aliyun"


class TestSpeedAndAccuracy:
    def test_speed_tier_order(self, cfg_all_enabled):
        providers = build_providers(cfg_all_enabled)
        pool = [
            (p, m)
            for p in providers.values()
            for m in (p.model_names or [p.default_model])
        ]
        ordered = _pair_labels(SpeedFirstStrategy().rank(pool))
        # S2（硅基、Groq）最前，S4（阿里 3.1 流式）最后
        assert ordered[0] == ("siliconflow", "SenseVoiceSmall")
        assert ordered[-1] == ("aliyun", "qwen-audio-3.1-asr-flash-streaming")
        tiers = [get_profile(p, m).speed_tier for p, m in ordered]
        assert tiers == sorted(tiers)

    def test_accuracy_tier_order(self, cfg_all_enabled):
        providers = build_providers(cfg_all_enabled)
        pool = [
            (p, m)
            for p in providers.values()
            for m in (p.model_names or [p.default_model])
        ]
        ordered = _pair_labels(AccuracyFirstStrategy().rank(pool))
        tiers = [get_profile(p, m).accuracy_tier for p, m in ordered]
        assert tiers == sorted(tiers)
        assert ordered[0] in [
            ("aliyun", "qwen-audio-3.1-asr-flash-streaming"),
            ("doubao", "asr-2.0"),
            ("groq", "whisper-large-v3"),
        ]
        assert ordered[-1] == ("siliconflow", "SenseVoiceSmall")


class TestHardLimits:
    def test_baidu_excluded_over_60s_not_at_unknown_duration(self):
        candidates = [(_FakeProvider("baidu", ["1537"]), "1537")]
        assert _pair_labels(SpeedFirstStrategy().rank(candidates, duration=61)) == []
        assert _pair_labels(SpeedFirstStrategy().rank(candidates, duration=None)) == [
            ("baidu", "1537"),
        ]

    def test_tencent_flash_excluded_over_2h_or_100mb(self):
        flash = [(_FakeProvider("tencent", ["16k_zh_en"]), "16k_zh_en")]
        async_m = [(_FakeProvider("tencent", ["16k_zh_en_2.0"]), "16k_zh_en_2.0")]
        assert _pair_labels(SpeedFirstStrategy().rank(flash, duration=2 * 3600 + 1)) == []
        assert _pair_labels(
            SpeedFirstStrategy().rank(flash, duration=60, file_size=100 * 1024 * 1024 + 1),
        ) == []
        # 异步大模型 2.0 无这两条硬限
        assert _pair_labels(
            SpeedFirstStrategy().rank(async_m, duration=3 * 3600, file_size=200 * 1024 * 1024),
        ) == [("tencent", "16k_zh_en_2.0")]

    def test_groq_excluded_over_25mb_not_by_10s_billing(self):
        groq = [(_FakeProvider("groq", ["whisper-large-v3"]), "whisper-large-v3")]
        assert _pair_labels(
            SpeedFirstStrategy().rank(groq, duration=3, file_size=25 * 1024 * 1024 + 1),
        ) == []
        # 最短 10s 只是计费，不是硬拒
        assert _pair_labels(
            SpeedFirstStrategy().rank(groq, duration=3, file_size=1024),
        ) == [("groq", "whisper-large-v3")]


class TestFreeStrategy:
    def test_keeps_config_order(self, cfg_all_enabled):
        providers = build_providers(cfg_all_enabled)
        pool = [
            (p, m)
            for p in providers.values()
            for m in (p.model_names or [p.default_model])
        ]
        labels = _pair_labels(FreeStrategy().rank(pool, duration=30))
        # 四家均带免费或额度；顺序= auto_order × 配置 models
        assert labels[0][0] == "aliyun"
        assert ("siliconflow", "SenseVoiceSmall") in labels
        assert ("groq", "whisper-large-v3") in labels

    def test_excludes_paid_only(self, monkeypatch):
        from asr_proxy.strategy import metadata as md

        paid = _FakeProvider("stepfun", ["stepaudio-2.5-asr"])
        monkeypatch.setitem(
            md._PROFILES, ("stepfun", "stepaudio-2.5-asr"),
            md.ModelProfile(
                provider="stepfun", model="stepaudio-2.5-asr",
                speed_tier=3, accuracy_tier=2,
                cost=md.CostSpec(billing="per_second", price_per_sec=0.000042),
            ),
        )
        free_p = _FakeProvider("siliconflow", ["SenseVoiceSmall"])
        labels = _pair_labels(FreeStrategy().rank(
            [(paid, "stepaudio-2.5-asr"), (free_p, "SenseVoiceSmall")],
            duration=10,
        ))
        assert labels == [("siliconflow", "SenseVoiceSmall")]

    def test_free_plus_speed(self, cfg_all_enabled):
        providers = build_providers(cfg_all_enabled)
        pool = [
            (p, m)
            for p in providers.values()
            for m in (p.model_names or [p.default_model])
        ]
        combo = build_strategy("free+speed")
        ordered = _pair_labels(combo.rank(pool, duration=30))
        # 免费集合内 S2（硅基、Groq）应在 S4（阿里 3.1 流式）之前
        assert ordered[0] == ("siliconflow", "SenseVoiceSmall")
        assert ordered[-1] == ("aliyun", "qwen-audio-3.1-asr-flash-streaming")


class TestRecommendedStub:
    def test_rank_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="未实现"):
            RecommendedStrategy().rank([])

    def test_router_recommended_raises(self, cfg_all_enabled):
        router = ProviderRouter(cfg_all_enabled)
        with pytest.raises(NotImplementedError, match="未实现"):
            router.resolve("strategy-recommended")


class TestRouterStrategy:
    def test_unprefixed_name_is_not_strategy(self, cfg_all_enabled):
        router = ProviderRouter(cfg_all_enabled)
        labels = _pool_labels(router.resolve("cost", duration=120))
        assert labels == []

    def test_cost_resolve_by_model_id(self, cfg_all_enabled):
        router = ProviderRouter(cfg_all_enabled)
        labels = _pool_labels(router.resolve("strategy-cost", duration=120))
        assert labels[0][0] == "aliyun"
        assert ("siliconflow", "SenseVoiceSmall") not in labels
        assert ("doubao", "asr-2.0") in labels

    def test_config_strategy_does_not_hijack_auto(self, cfg_all_enabled):
        cfg_all_enabled.strategy = "cost"
        router = ProviderRouter(cfg_all_enabled)
        labels = _pool_labels(router.resolve("auto"))
        providers = [l[0] for l in labels]
        assert providers == ["aliyun", "doubao", "siliconflow", "groq"]

    def test_provider_auto_unaffected(self, cfg_all_enabled):
        router = ProviderRouter(cfg_all_enabled)
        labels = _pool_labels(router.resolve("aliyun/auto", duration=60))
        assert labels == [
            ("aliyun", "qwen-audio-3.1-asr-flash-streaming"),
            ("aliyun", "qwen-audio-3.0-asr-flash"),
        ]

    def test_explicit_model_unaffected_by_strategy(self, cfg_all_enabled):
        router = ProviderRouter(cfg_all_enabled)
        labels = _pool_labels(
            router.resolve("aliyun/qwen-audio-3.0-asr-flash", duration=9999)
        )
        assert labels == [("aliyun", "qwen-audio-3.0-asr-flash")]

    def test_auto_strategy_keeps_legacy_provider_level(self, cfg_all_enabled):
        router = ProviderRouter(cfg_all_enabled)
        labels = _pool_labels(router.resolve("auto"))
        providers = [l[0] for l in labels]
        assert providers == ["aliyun", "doubao", "siliconflow", "groq"]
        assert labels[0] == ("aliyun", "qwen-audio-3.1-asr-flash-streaming")

    def test_recognize_probes_duration_for_cost(self, cfg_all_enabled, wav_bytes):
        from asr_proxy.providers.aliyun import AliyunProvider

        router = ProviderRouter(cfg_all_enabled)
        probed = {}

        def fake_probe(audio_bytes, ffmpeg_path):
            probed["bytes"] = audio_bytes
            return 42.0

        def fake_aliyun(self, audio_bytes, filename="audio.wav", model=None, language=None):
            return AsrResult(text="刊例通道成功", provider="aliyun", model=model)

        with patch("asr_proxy.providers.probe_audio_duration", fake_probe):
            with patch.object(AliyunProvider, "recognize", fake_aliyun):
                result, errors = router.recognize(wav_bytes, model="strategy-cost")

        assert probed["bytes"] == wav_bytes
        assert result.provider == "aliyun"

    def test_free_resolve(self, cfg_all_enabled):
        router = ProviderRouter(cfg_all_enabled)
        labels = _pool_labels(router.resolve("strategy-free", duration=20))
        assert labels[0][0] == "aliyun"
        assert ("siliconflow", "SenseVoiceSmall") in labels

    def test_health_exposes_strategy_models(self, cfg_all_enabled):
        from fastapi.testclient import TestClient
        from asr_proxy.app import create_app

        app = create_app(cfg_all_enabled)
        r = TestClient(app).get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["strategy"] == "auto"
        assert "strategy-cost" in body["strategy_models"]
        assert "strategy-free" in body["models"]


class TestProbeDuration:
    def test_wav_header_duration(self, wav_bytes):
        expected = _independently_parse_wav_duration(wav_bytes)
        assert expected > 0
        assert _wav_duration_from_header(wav_bytes) == pytest.approx(expected)
        # WAV 走头部解析，无需 ffmpeg
        assert probe_audio_duration(wav_bytes) == pytest.approx(expected)

    def test_non_wav_falls_back_to_ffprobe(self, wav_bytes):
        not_wav = b"\xff\xfb\x90\x00" + b"\x00" * 64  # 非 RIFF
        with patch(
            "asr_proxy.audio._ffprobe_duration", return_value=12.5,
        ) as m:
            assert probe_audio_duration(not_wav) == 12.5
            m.assert_called_once()

    def test_both_fail_returns_none(self, wav_bytes):
        with patch(
            "asr_proxy.audio._ffprobe_duration", return_value=None,
        ):
            assert probe_audio_duration(b"\x00\x01\x02") is None


def _independently_parse_wav_duration(data: bytes) -> float:
    """独立解析 WAV 头算时长，作为测试基准（与实现不同的写法）。"""
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    offset = 12
    byte_rate = 0
    data_size = None
    while offset + 8 <= len(data):
        cid = data[offset:offset + 4]
        (size,) = struct.unpack("<I", data[offset + 4:offset + 8])
        body = offset + 8
        if cid == b"fmt ":
            fmt = data[body:body + size]
            _, _, sample_rate, br, _, _ = struct.unpack("<HHIIHH", fmt[:16])
            byte_rate = br
        elif cid == b"data":
            data_size = size
            break
        offset = body + size + (size & 1)
    assert byte_rate and data_size is not None
    return data_size / byte_rate
