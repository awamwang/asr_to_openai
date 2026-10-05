# -*- coding: utf-8 -*-
"""各厂商 ASR 文本统一出口清洗（闪电说 / 翻译客户端）。"""
from __future__ import annotations

import re

# SenseVoice 等：<|zh|><|NEUTRAL|><|Speech|>
_SENSEVOICE_TAG_RE = re.compile(r"<\|[^|]*\|>")
# 连续空白（含全角空格）
_WS_RE = re.compile(r"[\s\u3000]+")
# 连续同类句末标点：。。。 / !!! → 。 / !
_MULTI_END_RE = re.compile(r"([。．.？?！!…])\1+")
# 句中碎顿：短停顿句号夹在汉字间 → 去掉（阿里 VAD 碎句常见）
_MID_PERIOD_RE = re.compile(r"(?<=[\u4e00-\u9fff])[。．.](?=[\u4e00-\u9fff])")


def strip_model_tags(text: str) -> str:
    """去掉模型特殊标签（SenseVoice 等）。始终建议执行。"""
    return _SENSEVOICE_TAG_RE.sub("", text or "")


def strip_pause_commas(text: str) -> str:
    """去掉口语停顿逗号/顿号（腾讯等按停顿插「，」）。"""
    if not text:
        return ""
    return text.replace("，", "").replace("、", "").replace(",", "")


def normalize_asr_text(text: str, *, clean_punctuation: bool = False) -> str:
    """统一出口：始终去模型标签；可选清洗停顿/碎句标点。

    clean_punctuation=False（默认）：只去 SenseVoice 等标签，保留厂商原生标点与断句。
    clean_punctuation=True：再去停顿逗号、汉字间碎句号、压缩连续句末标点与空白。
    """
    if not text:
        return ""
    out = strip_model_tags(text)
    if clean_punctuation:
        out = strip_pause_commas(out)
        out = _MID_PERIOD_RE.sub("", out)
        out = _MULTI_END_RE.sub(r"\1", out)
        out = _WS_RE.sub("", out)
    else:
        # 仅压缩多余空白，不动标点
        out = _WS_RE.sub(" ", out)
    return out.strip()
