# -*- coding: utf-8 -*-
"""服务商注册与 auto 路由。"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from asr_proxy.config import AppConfig
from asr_proxy.providers.aliyun import AliyunProvider
from asr_proxy.providers.base import AsrProvider, AsrResult
from asr_proxy.providers.grok import GrokProvider

_FACTORY = {
    "aliyun": AliyunProvider,
    "grok": GrokProvider,
}


def build_providers(cfg: AppConfig) -> Dict[str, AsrProvider]:
    """根据配置构建全部服务商实例（含未启用的，便于查状态）。"""
    result: Dict[str, AsrProvider] = {}
    for name, conf in cfg.providers.items():
        cls = _FACTORY.get(name)
        if cls is None:
            continue
        result[name] = cls(conf, ffmpeg_path=cfg.ffmpeg_path)
    return result


class ProviderRouter:
    """按 model 选择服务商；model=auto 时按顺序容错切换。"""

    def __init__(self, cfg: AppConfig, providers: Optional[Dict[str, AsrProvider]] = None):
        self.cfg = cfg
        self.providers = providers or build_providers(cfg)

    def enabled_list(self) -> List[AsrProvider]:
        """按 auto_order 返回已启用服务商。"""
        result: List[AsrProvider] = []
        for conf in self.cfg.enabled_providers():
            p = self.providers.get(conf.name)
            if p and p.enabled:
                result.append(p)
        return result

    def resolve(self, model: str) -> List[AsrProvider]:
        """解析请求 model，返回按优先级排列的候选服务商列表。"""
        m = (model or "").strip().lower()
        if not m or m == "auto":
            return self.enabled_list()

        # 精确匹配服务商名或该服务商配置的模型名
        matched: List[AsrProvider] = []
        for p in self.providers.values():
            if p.enabled and p.matches_model(m):
                matched.append(p)
        if matched:
            return matched

        # 未匹配到任何已启用服务商
        return []

    def recognize(
        self,
        audio_bytes: bytes,
        model: str = "auto",
        filename: str = "audio.wav",
    ) -> Tuple[AsrResult, List[str]]:
        """识别音频。返回 (结果, 失败记录列表)。全部失败则抛出最后一个异常。"""
        candidates = self.resolve(model)
        if not candidates:
            raise RuntimeError(
                f"没有可用的 ASR 服务商（model={model!r}）。"
                "请在 config.yaml 或环境变量中配置至少一个 api_key。"
            )

        errors: List[str] = []
        last_exc: Optional[BaseException] = None
        for p in candidates:
            try:
                result = p.recognize(audio_bytes, filename=filename)
                if result and result.text:
                    return result, errors
                errors.append(f"{p.name}: 返回空文本")
            except Exception as e:
                last_exc = e
                errors.append(f"{p.name}: {type(e).__name__}: {e}")
                continue

        detail = "; ".join(errors) if errors else "unknown"
        if last_exc is not None:
            raise RuntimeError(f"全部服务商识别失败: {detail}") from last_exc
        raise RuntimeError(f"全部服务商识别失败: {detail}")
