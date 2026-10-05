# -*- coding: utf-8 -*-
"""服务商注册与 auto 路由。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from asr_proxy.audio import probe_audio_duration
from asr_proxy.config import AppConfig, canonical_provider_name
from asr_proxy.providers.aliyun import AliyunProvider
from asr_proxy.providers.baidu import BaiduProvider
from asr_proxy.providers.base import AsrProvider, AsrResult
from asr_proxy.providers.doubao import DoubaoProvider
from asr_proxy.providers.groq import GroqProvider
from asr_proxy.providers.siliconflow import SiliconFlowProvider
from asr_proxy.providers.stepfun import StepFunProvider
from asr_proxy.providers.tencent import TencentProvider
from asr_proxy.strategy import (
    STRATEGY_PUBLIC_IDS,
    build_strategy,
    parse_request_strategy,
)

_FACTORY = {
    "aliyun": AliyunProvider,
    "groq": GroqProvider,
    "doubao": DoubaoProvider,
    "siliconflow": SiliconFlowProvider,
    "stepfun": StepFunProvider,
    "tencent": TencentProvider,
    "baidu": BaiduProvider,
}


@dataclass
class RouteCandidate:
    """一次识别尝试的候选：服务商 + 具体模型名。"""
    provider: AsrProvider
    model: str

    @property
    def label(self) -> str:
        return f"{self.provider.name}/{self.model}"


def build_providers(cfg: AppConfig) -> Dict[str, AsrProvider]:
    """根据配置构建全部服务商实例（含未启用的，便于查状态）。"""
    result: Dict[str, AsrProvider] = {}
    for name, conf in cfg.providers.items():
        cls = _FACTORY.get(name)
        if cls is None:
            continue
        result[name] = cls(conf, ffmpeg_path=cfg.ffmpeg_path)
    return result


def _rotate_from(items: List[str], start: Optional[str]) -> List[str]:
    """将 start 移到队首，其余保持相对顺序（环形旋转）。"""
    if not items or not start:
        return list(items)
    key = start.lower()
    idx = next((i for i, x in enumerate(items) if x.lower() == key), -1)
    if idx <= 0:
        return list(items)
    return items[idx:] + items[:idx]


class ProviderRouter:
    """按 model 选择服务商/模型；策略以独立模型 ID 请求，不覆盖 auto。"""

    def __init__(
        self,
        cfg: AppConfig,
        providers: Optional[Dict[str, AsrProvider]] = None,
        log_fn: Optional[Callable[[str], None]] = None,
    ):
        self.cfg = cfg
        self.providers = providers or build_providers(cfg)
        self._log_fn = log_fn
        # 配置里的 strategy 仅作兼容记录；真正策略由请求 model ID 决定
        self.strategy_name = (cfg.strategy or "auto").strip().lower()
        # 上次成功的服务商；下次 auto 默认从此开始
        self._last_provider: Optional[str] = None
        # 各服务商上次成功的模型；下次 auto / 服务商/auto 默认从此开始
        self._last_model_by_provider: Dict[str, str] = {}

    def set_log_fn(self, log_fn: Optional[Callable[[str], None]]) -> None:
        """设置公共日志回调（所有服务商识别结果共用）。"""
        self._log_fn = log_fn

    def _log(self, msg: str) -> None:
        if self._log_fn is None:
            return
        try:
            self._log_fn(msg)
        except Exception:
            pass

    def enabled_list(self) -> List[AsrProvider]:
        """按 auto_order 返回已启用服务商。"""
        result: List[AsrProvider] = []
        for conf in self.cfg.enabled_providers():
            p = self.providers.get(conf.name)
            if p and p.enabled:
                result.append(p)
        return result

    def remember_success(self, provider_name: str, model_name: str) -> None:
        """记录成功的服务商与模型，供下次请求优先使用。"""
        if not provider_name:
            return
        self._last_provider = provider_name
        if model_name:
            self._last_model_by_provider[provider_name] = model_name

    def _preferred_model(self, provider: AsrProvider) -> str:
        """取服务商优先模型：上次成功 > 配置首项。"""
        remembered = self._last_model_by_provider.get(provider.name)
        if remembered and provider.has_model(remembered):
            return remembered
        return provider.default_model

    def _models_for_provider_auto(self, provider: AsrProvider) -> List[str]:
        """服务商内 auto：从上次成功模型开始轮转。"""
        names = provider.model_names
        if not names:
            return [provider.default_model]
        start = self._last_model_by_provider.get(provider.name)
        return _rotate_from(names, start)

    def _find_provider(self, provider_name: str) -> Optional[AsrProvider]:
        """按名称查找已启用服务商（兼容 grok→groq）。"""
        canon = canonical_provider_name(provider_name)
        p = self.providers.get(canon) or self.providers.get(provider_name)
        if p and p.enabled:
            return p
        return next(
            (
                x for x in self.providers.values()
                if x.enabled and x.name.lower() == canon.lower()
            ),
            None,
        )

    def _strategy_for_request(self, model: str):
        """请求若为策略模型 ID 则构建对应策略，否则 None（走 auto / 显式模型）。"""
        spec = parse_request_strategy(model)
        if spec is None:
            return None
        return build_strategy(spec)

    def resolve(
        self,
        model: str,
        duration: Optional[float] = None,
        file_size: Optional[int] = None,
    ) -> List[RouteCandidate]:
        """解析请求 model，返回按优先级排列的候选列表。

        duration：音频时长（秒）；file_size：字节数。策略硬限/比价需要；
        auto 与显式模型可省略。探测失败则不做对应过滤。
        """
        raw = (model or "").strip()
        m = raw.lower()
        strategy = self._strategy_for_request(raw)
        if strategy is not None:
            return self._resolve_strategy_pool(
                self.enabled_list(), duration, strategy, file_size,
            )

        if not m or m == "auto":
            return self._resolve_global_auto()

        if "/" in raw:
            provider_name, _, rest = raw.partition("/")
            provider_name = provider_name.strip()
            rest = rest.strip()
            p = self._find_provider(provider_name)
            if not p or not p.enabled:
                return []
            if not rest or rest.lower() == "auto":
                return [
                    RouteCandidate(provider=p, model=name)
                    for name in self._models_for_provider_auto(p)
                ]
            if p.has_model(rest):
                # 保持配置中的原始大小写
                actual = next(x for x in p.model_names if x.lower() == rest.lower())
                return [RouteCandidate(provider=p, model=actual)]
            # 允许未列入配置但显式指定的模型
            return [RouteCandidate(provider=p, model=rest)]

        # 兼容：仅服务商名 → 该服务商默认/上次成功模型（不切模型）
        for p in self.enabled_list():
            if p.name.lower() == canonical_provider_name(m):
                return [RouteCandidate(provider=p, model=self._preferred_model(p))]

        # 兼容：裸模型名 → 匹配拥有该模型的已启用服务商
        matched: List[RouteCandidate] = []
        for p in self.enabled_list():
            if p.has_model(raw):
                actual = next(x for x in p.model_names if x.lower() == m)
                matched.append(RouteCandidate(provider=p, model=actual))
        return matched

    def _resolve_global_auto(self) -> List[RouteCandidate]:
        """全局 auto：只在服务商之间切换，每家用一个优先模型。"""
        providers = self.enabled_list()
        if not providers:
            return []
        names = [p.name for p in providers]
        ordered_names = _rotate_from(names, self._last_provider)
        by_name = {p.name: p for p in providers}
        return [
            RouteCandidate(provider=by_name[n], model=self._preferred_model(by_name[n]))
            for n in ordered_names
        ]

    def _resolve_strategy_pool(
        self,
        providers: List[AsrProvider],
        duration: Optional[float],
        strategy,
        file_size: Optional[int] = None,
    ) -> List[RouteCandidate]:
        """策略路由：在「服务商 × 配置模型」候选池上按策略排序（稳定）。"""
        pool: List[Tuple[AsrProvider, str]] = []
        for p in providers:
            models = p.model_names or [p.default_model]
            for m in models:
                if (p, m) not in pool:
                    pool.append((p, m))
        if not pool:
            return []
        try:
            ordered = strategy.rank(pool, duration, file_size)
        except NotImplementedError as e:
            self._log(f"[resolve] 策略不可用: {e}")
            raise
        return [RouteCandidate(provider=p, model=m) for p, m in ordered]

    def recognize(
        self,
        audio_bytes: bytes,
        model: str = "auto",
        filename: str = "audio.wav",
        language: Optional[str] = None,
    ) -> Tuple[AsrResult, List[str]]:
        """识别音频。返回 (结果, 失败记录列表)。全部失败则抛出最后一个异常。"""
        # 策略模型 ID 需要音频时长做硬限/刊例比价；只需探测一次。
        # 文件体积直接取字节数，用于 Groq 25MB / 腾讯极速版 100MB 硬限。
        duration: Optional[float] = None
        file_size: Optional[int] = len(audio_bytes) if audio_bytes else None
        req_strategy = self._strategy_for_request(model)
        if req_strategy is not None and req_strategy.needs_duration:
            duration = probe_audio_duration(audio_bytes, self.cfg.ffmpeg_path)
            self._log(
                f"[recognize] 策略={req_strategy.name} "
                f"音频时长={duration if duration is not None else '未知'} "
                f"字节={file_size if file_size is not None else '未知'}"
            )
        candidates = self.resolve(model, duration=duration, file_size=file_size)
        if not candidates:
            msg = (
                f"没有可用的 ASR 服务商（model={model!r}）。"
                "请在 config.yaml 或环境变量中配置至少一个服务商密钥；"
                "模型请填 groq/... / aliyun/... / doubao/... / siliconflow/... / "
                "stepfun/... / tencent/... / baidu/... / auto，"
                f"或策略 ID {', '.join(STRATEGY_PUBLIC_IDS)}。"
            )
            self._log(f"[recognize] 识别失败 请求模型={model!r} 跳过=['无可用候选']")
            raise RuntimeError(msg)

        self._log(
            f"[recognize] 请求模型={model!r} "
            f"候选={[c.label for c in candidates]}"
        )

        errors: List[str] = []
        last_exc: Optional[BaseException] = None
        for c in candidates:
            try:
                result = c.provider.recognize(
                    audio_bytes,
                    filename=filename,
                    model=c.model,
                    language=language,
                )
                if result and result.text:
                    used_model = result.model or c.model
                    result.provider = result.provider or c.provider.name
                    result.model = used_model
                    used = f"{result.provider}/{used_model}"
                    self.remember_success(c.provider.name, used_model)
                    self._log(
                        f"[recognize] 使用模型={used} "
                        f"请求模型={model!r} "
                        f"跳过={errors or '无'}"
                    )
                    return result, errors
                errors.append(f"{c.label}: 返回空文本")
            except Exception as e:
                last_exc = e
                errors.append(f"{c.label}: {type(e).__name__}: {e}")
                continue

        self._log(
            f"[recognize] 识别失败 "
            f"请求模型={model!r} "
            f"跳过={errors or '无'}"
        )
        detail = "; ".join(errors) if errors else "unknown"
        if last_exc is not None:
            raise RuntimeError(f"全部服务商识别失败: {detail}") from last_exc
        raise RuntimeError(f"全部服务商识别失败: {detail}")
