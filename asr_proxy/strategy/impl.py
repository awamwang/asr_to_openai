# -*- coding: utf-8 -*-
"""策略实现：费用优先 / 速度优先 / 准确度优先 / 免费 / 组合 / 推荐（暂未实现）。"""
from __future__ import annotations

from typing import List, Optional

from asr_proxy.strategy.base import ProviderModel, RoutingStrategy
from asr_proxy.strategy.metadata import estimate_cost, get_profile, is_free_profile, to_cny


def _usable(
    profile,
    duration: Optional[float],
    file_size: Optional[int] = None,
) -> bool:
    """未实现通道或超出硬限则不可用。时长/体积未知时不做对应过滤。"""
    if not profile.supported:
        return False
    if (
        profile.cost.max_duration > 0
        and duration is not None
        and duration > profile.cost.max_duration
    ):
        return False
    if (
        profile.cost.max_bytes > 0
        and file_size is not None
        and file_size > profile.cost.max_bytes
    ):
        return False
    return True


def _list_cost(profile, duration: Optional[float]) -> float:
    """刊例预估；探测不到时长时按时长模型用 max(最短计费秒, 1s) 比价，避免全部排 inf。"""
    cost = estimate_cost(profile, duration)
    if cost is not None:
        return cost
    spec = profile.cost
    if spec.billing == "per_second" and duration is None:
        billed = max(spec.min_bill_sec, 1.0)
        return to_cny(profile, spec.price_per_sec * billed)
    return float("inf")


class CostFirstStrategy(RoutingStrategy):
    """费用优先：只比较刊例付费价，免费额度不参与排序。

    规则来自 docs/asr_models_guide.md：
      1. 无刊例价（长期免费）不进入本策略；
      2. 美元刊例按固定汇率 1 USD = 7 CNY 折合后比价；
      3. 对「按次价 vs 秒单价 × t」取低；硬限超出则排除；
      4. 无法预估的未知模型排最后（保持配置顺序）。
    """

    name = "cost"
    needs_duration = True

    def rank(
        self,
        candidates: List[ProviderModel],
        duration: Optional[float] = None,
        file_size: Optional[int] = None,
    ) -> List[ProviderModel]:
        scored = []
        for provider, model in candidates:
            profile = get_profile(provider.name, model)
            if not _usable(profile, duration, file_size):
                continue
            if profile.cost.billing == "free":
                continue
            scored.append((_list_cost(profile, duration), provider, model))
        scored.sort(key=lambda item: item[0])
        return [(p, m) for _, p, m in scored]


class SpeedFirstStrategy(RoutingStrategy):
    """速度优先：按模型速度等级 S1→S5（数字小在前）排序。

    等级已用 2026-10-10 本地实测校准（见 metadata._MEASURED_2026_10）；
    未实测模型沿用文档静态等级。同档位时保持路由层传入的底序
    （config.yaml speed_order 决定服务商先后，同服务商按 models 配置顺序）。
    """

    name = "speed"
    needs_duration = True

    def rank(
        self,
        candidates: List[ProviderModel],
        duration: Optional[float] = None,
        file_size: Optional[int] = None,
    ) -> List[ProviderModel]:
        scored = []
        for provider, model in candidates:
            profile = get_profile(provider.name, model)
            if not _usable(profile, duration, file_size):
                continue
            scored.append((profile.speed_tier, provider, model))
        scored.sort(key=lambda item: item[0])
        return [(p, m) for _, p, m in scored]


class AccuracyFirstStrategy(RoutingStrategy):
    """准确度优先：按静态准确度等级（1 最高）排序。

    当前为静态近似（大模型级 > 轻量/蒸馏），未做真实准确率实测；
    后续可替换为实测准确率或线上评估。
    """

    name = "accuracy"
    needs_duration = True

    def rank(
        self,
        candidates: List[ProviderModel],
        duration: Optional[float] = None,
        file_size: Optional[int] = None,
    ) -> List[ProviderModel]:
        scored = []
        for provider, model in candidates:
            profile = get_profile(provider.name, model)
            if not _usable(profile, duration, file_size):
                continue
            scored.append((profile.accuracy_tier, provider, model))
        scored.sort(key=lambda item: item[0])
        return [(p, m) for _, p, m in scored]


class FreeStrategy(RoutingStrategy):
    """免费策略：只保留长期免费或带免费额度的模型。

    候选顺序由路由层按 free_order（未配置则 auto_order）预先排好；
    本策略只做过滤，保持传入顺序。
    """

    name = "free"
    needs_duration = True

    def rank(
        self,
        candidates: List[ProviderModel],
        duration: Optional[float] = None,
        file_size: Optional[int] = None,
    ) -> List[ProviderModel]:
        out: List[ProviderModel] = []
        for provider, model in candidates:
            profile = get_profile(provider.name, model)
            if not _usable(profile, duration, file_size):
                continue
            if not is_free_profile(profile):
                continue
            out.append((provider, model))
        return out


class CompositeStrategy(RoutingStrategy):
    """先按过滤器收窄候选，再交给内层策略排序（如 free+speed）。"""

    def __init__(self, name: str, filt: RoutingStrategy, inner: RoutingStrategy):
        self.name = name
        self._filt = filt
        self._inner = inner
        self.needs_duration = bool(filt.needs_duration or inner.needs_duration)

    def rank(
        self,
        candidates: List[ProviderModel],
        duration: Optional[float] = None,
        file_size: Optional[int] = None,
    ) -> List[ProviderModel]:
        narrowed = self._filt.rank(candidates, duration, file_size)
        return self._inner.rank(narrowed, duration, file_size)


class RecommendedStrategy(RoutingStrategy):
    """推荐策略：已废弃时长经验表，请改用 cost / speed / accuracy / free。"""

    name = "recommended"

    def rank(
        self,
        candidates: List[ProviderModel],
        duration: Optional[float] = None,
        file_size: Optional[int] = None,
    ) -> List[ProviderModel]:
        raise NotImplementedError(
            "推荐策略（recommended）未实现：请请求模型 ID "
            "strategy-cost / strategy-speed / strategy-accuracy / strategy-free "
            "（或 strategy-free+speed / strategy-free+accuracy）"
        )
