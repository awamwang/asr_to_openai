# -*- coding: utf-8 -*-
"""ASR → 闪电说兼容代理。"""
from asr_proxy.config import load_config
from asr_proxy.providers import ProviderRouter

__all__ = ["load_config", "ProviderRouter"]
