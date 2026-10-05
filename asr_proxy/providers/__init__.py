# -*- coding: utf-8 -*-
"""服务商注册与 auto 路由。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from asr_proxy.audio import probe_audio_duration
from asr_proxy.config import AppConfig, canonical_provider_name
from asr_proxy.logutil import as_logger
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


def _fmt_elapsed(seconds: float) -> str:
    """耗时展示：<10ms 用 ms，否则保留两位秒。"""
    if seconds < 0.01:
        return f"{max(seconds, 0.0) * 1000:.0f}ms"
    return f"{seconds:.2f}s"

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
        log_fn: Optional[object] = None,
    ):
        self.cfg = cfg
        self.providers = providers or build_providers(cfg)
        self._logger = as_logger(log_fn, level=cfg.log_level)
        # 配置里的 strategy 仅作兼容记录；真正策略由请求 model ID 决定
        self.strategy_name = (cfg.strategy or "auto").strip().lower()
        # 上次成功的服务商；下次 auto 默认从此开始
        self._last_provider: Optional[str] = None
        # 各服务商上次成功的模型；下次 auto / 服务商/auto 默认从此开始
        self._last_model_by_provider: Dict[str, str] = {}

    def set_log_fn(self, log_fn: Optional[object] = None) -> None:
        """设置公共日志（Logger 或测试回调）。"""
        self._logger = as_logger(log_fn, level=self.cfg.log_level)

    def _log(self, msg: str, level: str = "info") -> None:
        getattr(self._logger, level if level != "warn" else "warning")(msg)

    def enabled_list(self) -> List[AsrProvider]:
        """按 auto_order 返回已启用服务商。"""
        result: List[AsrProvider] = []
        for conf in self.cfg.enabled_providers():
            p = self.providers.get(conf.name)
            if p and p.enabled:
                result.append(p)
        return result

    def _providers_for_strategy(self, strategy) -> List[AsrProvider]:
        """按策略专用 order 过滤并排序已启用服务商。

        未配置 order（空列表）时与 enabled_list 相同。
        已配置时：未列入的服务商不参与该策略；列表顺序即默认顺序
        （无自身排序的策略如 free 直接沿用；有排序的策略作稳定排序底序）。
        组合策略（free+speed）使用前缀对应的 order（free_order）。
        """
        providers = self.enabled_list()
        order = self.cfg.order_for_strategy(getattr(strategy, "name", "") or "")
        if not order:
            return providers
        rank = {name: i for i, name in enumerate(order)}
        filtered = [p for p in providers if p.name in rank]
        filtered.sort(key=lambda p: rank[p.name])
        return filtered

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
                self._providers_for_strategy(strategy),
                duration, strategy, file_size,
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
            self._log(f"[resolve] 策略不可用: {e}", "error")
            raise
        return [RouteCandidate(provider=p, model=m) for p, m in ordered]

    def _route_mode(self, model: str, strategy) -> str:
        """描述本次解析模式，便于日志阅读。"""
        if strategy is not None:
            return f"strategy-{strategy.name}"
        raw = (model or "").strip()
        m = raw.lower()
        if not m or m == "auto":
            return "auto"
        if "/" in raw:
            _, _, rest = raw.partition("/")
            if not rest.strip() or rest.strip().lower() == "auto":
                return "provider-auto"
            return "explicit"
        return "lookup"

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
        route_mode = self._route_mode(model, req_strategy)
        if req_strategy is not None and req_strategy.needs_duration:
            duration = probe_audio_duration(audio_bytes, self.cfg.ffmpeg_path)
            self._log(
                f"[recognize] 模式={route_mode} "
                f"音频时长={duration if duration is not None else '未知'} "
                f"字节={file_size if file_size is not None else '未知'}",
                "debug",
            )
        candidates = self.resolve(model, duration=duration, file_size=file_size)
        pool = [c.label for c in candidates]
        if not candidates:
            msg = (
                f"没有可用的 ASR 服务商（model={model!r}）。"
                "请在 config.yaml 或环境变量中配置至少一个服务商密钥；"
                "模型请填 groq/... / aliyun/... / doubao/... / siliconflow/... / "
                "stepfun/... / tencent/... / baidu/... / auto，"
                f"或策略 ID {', '.join(STRATEGY_PUBLIC_IDS)}。"
            )
            self._log(
                f"[recognize] 识别失败 请求模型={model!r} 模式={route_mode} "
                f"切换链=[] 原因=无可用候选",
                "error",
            )
            raise RuntimeError(msg)

        # 候选池仅 debug 弱展示；INFO 突出实际切换链与耗时
        self._log(
            f"[recognize] 请求模型={model!r} 模式={route_mode} "
            f"候选池({len(pool)})={pool}",
            "debug",
        )

        errors: List[str] = []
        # 实际切换步骤，如 "groq/x(失败 0.30s: Timeout)" / "aliyun/y(成功 1.20s)"
        switch_steps: List[str] = []
        last_exc: Optional[BaseException] = None
        total = len(candidates)
        t_all = time.perf_counter()
        for i, c in enumerate(candidates, 1):
            self._log(f"[recognize] 尝试[{i}/{total}] {c.label}", "debug")
            t0 = time.perf_counter()
            try:
                result = c.provider.recognize(
                    audio_bytes,
                    filename=filename,
                    model=c.model,
                    language=language,
                )
                elapsed = time.perf_counter() - t0
                cost = _fmt_elapsed(elapsed)
                if result and result.text:
                    used_model = result.model or c.model
                    result.provider = result.provider or c.provider.name
                    result.model = used_model
                    used = f"{result.provider}/{used_model}"
                    self.remember_success(c.provider.name, used_model)
                    switch_steps.append(f"{used}(成功 {cost})")
                    total_cost = _fmt_elapsed(time.perf_counter() - t_all)
                    self._log(
                        f"[recognize] 尝试[{i}/{total}] {c.label} → 成功 {cost}",
                        "debug",
                    )
                    self._log(
                        f"[recognize] 切换链: {' → '.join(switch_steps)} | "
                        f"选用={used} 耗时={cost} 总耗时={total_cost} "
                        f"请求模型={model!r} 模式={route_mode}",
                        "info",
                    )
                    return result, errors
                err = f"{c.label}: 返回空文本"
                errors.append(err)
                switch_steps.append(f"{c.label}(失败 {cost}: 返回空文本)")
                self._log(
                    f"[recognize] 尝试[{i}/{total}] {c.label} → 失败 {cost}: 返回空文本",
                    "debug",
                )
            except Exception as e:
                elapsed = time.perf_counter() - t0
                cost = _fmt_elapsed(elapsed)
                last_exc = e
                reason = f"{type(e).__name__}: {e}"
                err = f"{c.label}: {reason}"
                errors.append(err)
                # 切换链摘要里截断过长原因，完整原因在 debug / errors
                short = reason if len(reason) <= 120 else reason[:117] + "..."
                switch_steps.append(f"{c.label}(失败 {cost}: {short})")
                self._log(
                    f"[recognize] 尝试[{i}/{total}] {c.label} → 失败 {cost}: {reason}",
                    "debug",
                )
                continue

        total_cost = _fmt_elapsed(time.perf_counter() - t_all)
        self._log(
            f"[recognize] 全部失败 切换链: {' → '.join(switch_steps) or '[]'} | "
            f"总耗时={total_cost} 请求模型={model!r} 模式={route_mode}",
            "error",
        )
        detail = "; ".join(errors) if errors else "unknown"
        if last_exc is not None:
            raise RuntimeError(f"全部服务商识别失败: {detail}") from last_exc
        raise RuntimeError(f"全部服务商识别失败: {detail}")
