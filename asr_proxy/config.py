# -*- coding: utf-8 -*-
"""配置加载：YAML + 环境变量。api_key 非空即视为启用该服务商。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# 项目根目录（本文件在 asr_proxy/ 下）
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config.yaml"
EXAMPLE_CONFIG_PATH = ROOT / "config.example.yaml"


@dataclass
class ProviderConfig:
    """单个 ASR 服务商配置。"""
    name: str
    api_key: str = ""
    workspace_id: str = ""
    base_url: str = ""
    model: str = ""

    @property
    def enabled(self) -> bool:
        """api_key 存在则启用该服务商。"""
        return bool(self.api_key and self.api_key.strip())


@dataclass
class AppConfig:
    """应用总配置。"""
    host: str = "127.0.0.1"
    port: int = 8004
    log_file: str = "asr_proxy.log"
    ffmpeg_path: str = "ffmpeg"
    auto_order: List[str] = field(default_factory=lambda: ["aliyun", "grok"])
    providers: Dict[str, ProviderConfig] = field(default_factory=dict)

    def enabled_providers(self) -> List[ProviderConfig]:
        """按 auto_order 返回已启用的服务商列表。"""
        result: List[ProviderConfig] = []
        seen = set()
        for name in self.auto_order:
            p = self.providers.get(name)
            if p and p.enabled and name not in seen:
                result.append(p)
                seen.add(name)
        # 未出现在 auto_order 中但已启用的，追加到末尾
        for name, p in self.providers.items():
            if p.enabled and name not in seen:
                result.append(p)
                seen.add(name)
        return result


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _first_env(*keys: str, default: str = "") -> str:
    for k in keys:
        v = _env(k)
        if v:
            return v
    return default


def _merge_provider(name: str, raw: Dict[str, Any]) -> ProviderConfig:
    """合并 YAML 与环境变量，环境变量优先。"""
    raw = raw or {}
    if name == "aliyun":
        return ProviderConfig(
            name=name,
            api_key=_first_env(
                "DASHSCOPE_API_KEY", "ALIYUN_API_KEY",
                default=str(raw.get("api_key") or "").strip(),
            ),
            workspace_id=_first_env(
                "DASHSCOPE_WORKSPACE_ID", "ALIYUN_WORKSPACE_ID",
                default=str(raw.get("workspace_id") or "").strip(),
            ),
            model=_first_env(
                "DASHSCOPE_ASR_MODEL", "ALIYUN_ASR_MODEL",
                default=str(raw.get("model") or "qwen-audio-3.1-asr-flash-streaming").strip(),
            ),
        )
    if name == "grok":
        return ProviderConfig(
            name=name,
            api_key=_first_env(
                "XAI_API_KEY", "GROK_API_KEY",
                default=str(raw.get("api_key") or "").strip(),
            ),
            base_url=_first_env(
                "GROK_BASE_URL", "XAI_BASE_URL",
                default=str(raw.get("base_url") or "https://api.x.ai/v1").strip(),
            ),
            model=_first_env(
                "GROK_ASR_MODEL", "XAI_ASR_MODEL",
                default=str(raw.get("model") or "").strip(),
            ),
        )
    return ProviderConfig(
        name=name,
        api_key=str(raw.get("api_key") or "").strip(),
        workspace_id=str(raw.get("workspace_id") or "").strip(),
        base_url=str(raw.get("base_url") or "").strip(),
        model=str(raw.get("model") or "").strip(),
    )


def load_config(path: Optional[str | Path] = None) -> AppConfig:
    """从 YAML 加载配置，再用环境变量覆盖。无 config.yaml 时回退到 example。"""
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.is_file():
        cfg_path = EXAMPLE_CONFIG_PATH

    data: Dict[str, Any] = {}
    if cfg_path.is_file():
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    server = data.get("server") or {}
    providers_raw = data.get("providers") or {}

    providers: Dict[str, ProviderConfig] = {}
    for name in ("aliyun", "grok"):
        providers[name] = _merge_provider(name, providers_raw.get(name) or {})
    for name, raw in providers_raw.items():
        if name not in providers:
            providers[name] = _merge_provider(name, raw or {})

    auto_order = data.get("auto_order") or ["aliyun", "grok"]
    if not isinstance(auto_order, list):
        auto_order = ["aliyun", "grok"]

    port_env = _env("ASR_PORT") or _env("DASHSCOPE_PORT")
    log_env = _env("ASR_LOG") or _env("DASHSCOPE_LOG")

    return AppConfig(
        host=str(server.get("host") or "127.0.0.1"),
        port=int(port_env or server.get("port") or 8004),
        log_file=log_env or str(server.get("log_file") or "asr_proxy.log"),
        ffmpeg_path=_first_env("FFMPEG_PATH", default=str(data.get("ffmpeg_path") or "ffmpeg")),
        auto_order=[str(x) for x in auto_order],
        providers=providers,
    )
