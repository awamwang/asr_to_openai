# -*- coding: utf-8 -*-
"""兼容旧导入路径：GrokProvider 实际为 GroqProvider。"""
from asr_proxy.providers.groq import GroqProvider as GrokProvider

__all__ = ["GrokProvider"]
