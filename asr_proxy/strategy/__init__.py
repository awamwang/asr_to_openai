# -*- coding: utf-8 -*-
"""路由策略注册与工厂。

策略通过请求模型 ID 选择（前缀 strategy-），不覆盖 auto / 服务商/auto / 显式服务商/模型：
  - strategy-cost              费用优先（刊例付费价；美元按 1:7 折合）
  - strategy-speed             速度优先
  - strategy-accuracy          准确度优先
  - strategy-free              免费策略（按 free_order；未配则沿用 auto_order）
  - strategy-free+speed        免费候选上再按速度（受 free_order 约束）
  - strategy-free+accuracy     免费候选上再按准确度
  - strategy-recommended       未实现（请求即报错，不对外列出）

config.yaml 的 strategy 仅作兼容记录，不再改写 auto。
各策略可配独立 order（free_order / cost_order / …）：非空时为白名单，
未列入的服务商不参与该策略；无自身排序的策略（free）以此为默认顺序。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Type

from asr_proxy.strategy.base import AutoStrategy, ProviderModel, RoutingStrategy
from asr_proxy.strategy.impl import (
    AccuracyFirstStrategy,
    CompositeStrategy,
    CostFirstStrategy,
    FreeStrategy,
    RecommendedStrategy,
    SpeedFirstStrategy,
)

STRATEGY_NAMES: List[str] = [
    "auto",
    "cost",
    "speed",
    "accuracy",
    "free",
    "recommended",
]

STRATEGY_ID_PREFIX = "strategy-"

# 对外暴露的策略模型 ID（不含 auto、不含会报错的 recommended）
STRATEGY_PUBLIC_IDS: List[str] = [
    f"{STRATEGY_ID_PREFIX}cost",
    f"{STRATEGY_ID_PREFIX}speed",
    f"{STRATEGY_ID_PREFIX}accuracy",
    f"{STRATEGY_ID_PREFIX}free",
    f"{STRATEGY_ID_PREFIX}free+speed",
    f"{STRATEGY_ID_PREFIX}free+accuracy",
]

_REGISTRY: Dict[str, Type[RoutingStrategy]] = {
    "cost": CostFirstStrategy,
    "speed": SpeedFirstStrategy,
    "accuracy": AccuracyFirstStrategy,
    "free": FreeStrategy,
    "recommended": RecommendedStrategy,
}

_RANKERS = {"speed", "accuracy"}


def normalize_strategy_id(name: Optional[str]) -> Optional[str]:
    """把请求/配置字符串规范成策略名；不是策略则返回 None。"""
    key = (name or "").strip().lower().replace(" ", "")
    if not key or "/" in key:
        return None
    if "+" in key:
        parts = [p for p in key.split("+") if p]
        if len(parts) == 2 and "free" in parts:
            other = parts[1] if parts[0] == "free" else parts[0]
            if other in _RANKERS:
                return f"free+{other}"
        return None
    if key in _REGISTRY or key == "auto":
        return key
    return None


def parse_request_strategy(model: str) -> Optional[str]:
    """请求 model 若为 strategy- 前缀的策略 ID，则返回内部策略名（不含 auto）。

    无前缀的 cost / free 等不视为策略，避免与普通模型名冲突。
    """
    key = (model or "").strip().lower().replace(" ", "")
    prefix = STRATEGY_ID_PREFIX
    if not key.startswith(prefix):
        return None
    spec = normalize_strategy_id(key[len(prefix):])
    if spec is None or spec == "auto":
        return None
    return spec


def build_strategy(name: Optional[str]) -> RoutingStrategy:
    """按名称构建策略实例；auto / 空 → AutoStrategy。未知名报错。"""
    key = (name or "").strip().lower().replace(" ", "")
    if not key or key == "auto":
        return AutoStrategy()
    spec = normalize_strategy_id(key)
    if spec is None:
        raise ValueError(
            f"未知 strategy={name!r}，可选：{', '.join(STRATEGY_NAMES)}，"
            "或组合 free+speed / free+accuracy。"
        )
    if spec.startswith("free+") and spec in ("free+speed", "free+accuracy"):
        inner_name = spec.split("+", 1)[1]
        return CompositeStrategy(
            spec, FreeStrategy(), _REGISTRY[inner_name](),
        )
    cls = _REGISTRY.get(spec)
    if cls is None:
        raise ValueError(
            f"未知 strategy={name!r}，可选：{', '.join(STRATEGY_NAMES)}。"
        )
    return cls()


__all__ = [
    "RoutingStrategy",
    "ProviderModel",
    "AutoStrategy",
    "STRATEGY_NAMES",
    "STRATEGY_PUBLIC_IDS",
    "STRATEGY_ID_PREFIX",
    "build_strategy",
    "parse_request_strategy",
    "normalize_strategy_id",
]
