# -*- coding: utf-8 -*-
"""路由策略抽象基类。

策略负责把「候选池（服务商 × 模型）」按各自优先级稳定排序，
路由层按排序后的顺序依次尝试（沿用现有容灾语义：失败自动切下一个）。

新增策略只需：
  1. 继承 RoutingStrategy，实现 rank()；
  2. 在 strategy/__init__.py 的 _REGISTRY 中注册策略名。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

from asr_proxy.providers.base import AsrProvider

# 候选 = (服务商实例, 模型名)
ProviderModel = Tuple[AsrProvider, str]


class RoutingStrategy(ABC):
    """候选排序策略。rank() 必须稳定（等价候选保持原顺序）。"""

    #: 策略名（请求模型 ID / 兼容配置字段，小写）
    name: str = ""
    #: 是否需要音频时长（如费用优先需要按时长比价）
    needs_duration: bool = False

    @abstractmethod
    def rank(
        self,
        candidates: List[ProviderModel],
        duration: Optional[float] = None,
        file_size: Optional[int] = None,
    ) -> List[ProviderModel]:
        """对候选稳定排序，返回同子集（可丢弃不支持/超限的候选）。

        duration：音频时长（秒）；探测失败则为 None（不做时长硬限过滤）。
        file_size：音频字节数；未知则为 None（不做体积硬限过滤）。
        """
        raise NotImplementedError


class AutoStrategy(RoutingStrategy):
    """默认策略：保持原有「服务商容灾切换」语义（auto_order + 上次成功优先）。"""

    name = "auto"

    def rank(
        self,
        candidates: List[ProviderModel],
        duration: Optional[float] = None,
        file_size: Optional[int] = None,
    ) -> List[ProviderModel]:
        return list(candidates)
