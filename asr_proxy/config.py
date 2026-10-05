# -*- coding: utf-8 -*-
"""配置加载：YAML + 环境变量。api_key 非空即视为启用该服务商。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

# 项目根目录（本文件在 asr_proxy/ 下）
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config.yaml"
EXAMPLE_CONFIG_PATH = ROOT / "config.example.yaml"

# SOCKS 别名：socks5:// 本地解析 DNS 访问 Groq 常 TLS 失败，需 socks5h://
_SOCKS_ALIASES = {"socks5": "socks5h", "socks4": "socks4a"}


def normalize_proxy(raw: Optional[str]) -> str:
    """规范化代理地址：补 scheme，并把 socks5:// 纠正为 socks5h://。"""
    value = (raw or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = "http://" + value
    scheme, _, rest = value.partition("://")
    scheme = scheme.lower()
    if scheme in _SOCKS_ALIASES:
        return f"{_SOCKS_ALIASES[scheme]}://{rest}"
    return value


def _normalize_models(
    models: Optional[Union[str, List[Any]]] = None,
    model: Optional[str] = None,
) -> List[str]:
    """将 model / models 统一为去重后的有序列表。"""
    result: List[str] = []
    seen = set()

    def _add(item: Any) -> None:
        s = str(item or "").strip()
        if s and s not in seen:
            result.append(s)
            seen.add(s)

    if isinstance(models, list):
        for item in models:
            _add(item)
    elif isinstance(models, str):
        for part in models.split(","):
            _add(part)

    if model:
        _add(model)

    return result


@dataclass
class ProviderConfig:
    """单个 ASR 服务商配置。"""
    name: str
    api_key: str = ""
    workspace_id: str = ""
    # 豆包旧版控制台 APP ID（X-Api-App-Key）；新版只需 api_key
    app_id: str = ""
    base_url: str = ""
    proxy: str = ""
    timeout: float = 300.0
    # 该服务商下可切换的模型列表（顺序即 provider/auto 尝试顺序）
    models: List[str] = field(default_factory=list)
    # 阿里云流式 ASR 断句：False=VAD（默认），True=语义断句
    semantic_punctuation_enabled: bool = False
    # 阿里云 VAD 静音阈值（ms），范围 [200, 6000]，默认 1300
    max_sentence_silence: int = 1300

    @property
    def enabled(self) -> bool:
        """api_key 存在则启用该服务商。"""
        return bool(self.api_key and self.api_key.strip())

    @property
    def model(self) -> str:
        """默认模型（列表首项），兼容旧字段访问。"""
        return self.models[0] if self.models else ""

    @model.setter
    def model(self, value: str) -> None:
        value = (value or "").strip()
        if not value:
            return
        if value in self.models:
            self.models.remove(value)
        self.models.insert(0, value)


@dataclass
class AppConfig:
    """应用总配置。"""
    host: str = "127.0.0.1"
    port: int = 8004
    log_file: str = "asr_proxy.log"
    ffmpeg_path: str = "ffmpeg"
    # 兼容字段：不再改写 auto。真正策略请请求模型 ID strategy-cost 等。
    strategy: str = "auto"
    auto_order: List[str] = field(
        default_factory=lambda: ["aliyun", "doubao", "siliconflow", "groq"],
    )
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


def _float_env(*keys: str, default: float = 300.0) -> float:
    raw = _first_env(*keys)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off", ""):
        return False
    return default


def _bool_env(*keys: str, default: bool = False) -> bool:
    raw = _first_env(*keys)
    if not raw:
        return default
    return _as_bool(raw, default=default)


def _clamp_sentence_silence(value: Any, default: int = 1300) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(200, min(6000, n))


def _int_env(*keys: str, default: int = 1300) -> int:
    raw = _first_env(*keys)
    if not raw:
        return default
    return _clamp_sentence_silence(raw, default=default)


def _merge_provider(name: str, raw: Dict[str, Any]) -> ProviderConfig:
    """合并 YAML 与环境变量，环境变量优先。"""
    raw = raw or {}
    yaml_models = _normalize_models(
        models=raw.get("models"),
        model=str(raw.get("model") or "").strip() or None,
    )
    yaml_timeout = raw.get("timeout")
    try:
        default_timeout = float(yaml_timeout) if yaml_timeout is not None else 300.0
    except (TypeError, ValueError):
        default_timeout = 300.0

    if name == "aliyun":
        env_models = _first_env("DASHSCOPE_ASR_MODELS", "ALIYUN_ASR_MODELS")
        env_model = _first_env("DASHSCOPE_ASR_MODEL", "ALIYUN_ASR_MODEL")
        models = _normalize_models(
            models=env_models or None,
            model=env_model or None,
        ) or yaml_models or ["qwen-audio-3.1-asr-flash-streaming"]
        yaml_silence = raw.get("max_sentence_silence")
        yaml_silence_default = (
            _clamp_sentence_silence(yaml_silence)
            if yaml_silence is not None
            else 1300
        )
        yaml_semantic = _as_bool(
            raw.get("semantic_punctuation_enabled"), default=False,
        )
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
            models=models,
            timeout=_float_env("ALIYUN_TIMEOUT", default=default_timeout),
            semantic_punctuation_enabled=_bool_env(
                "DASHSCOPE_SEMANTIC_PUNCTUATION",
                "ALIYUN_SEMANTIC_PUNCTUATION",
                default=yaml_semantic,
            ),
            max_sentence_silence=_int_env(
                "DASHSCOPE_MAX_SENTENCE_SILENCE",
                "ALIYUN_MAX_SENTENCE_SILENCE",
                default=yaml_silence_default,
            ),
        )
    if name in ("groq", "grok"):
        # grok 为历史误写，统一按 Groq 处理
        env_models = _first_env(
            "GROQ_ASR_MODELS", "GROK_ASR_MODELS", "XAI_ASR_MODELS",
        )
        env_model = _first_env(
            "GROQ_ASR_MODEL", "GROQ_DEFAULT_MODEL",
            "GROK_ASR_MODEL", "XAI_ASR_MODEL",
        )
        models = _normalize_models(
            models=env_models or None,
            model=env_model or None,
        ) or yaml_models or ["whisper-large-v3", "whisper-large-v3-turbo"]
        return ProviderConfig(
            name="groq",
            api_key=_first_env(
                "GROQ_API_KEY", "XAI_API_KEY", "GROK_API_KEY",
                default=str(raw.get("api_key") or "").strip(),
            ),
            base_url=_first_env(
                "GROQ_BASE_URL", "GROK_BASE_URL", "XAI_BASE_URL",
                default=str(
                    raw.get("base_url") or "https://api.groq.com/openai/v1"
                ).strip(),
            ),
            proxy=normalize_proxy(_first_env(
                "GROQ_PROXY", "GROK_PROXY",
                default=str(raw.get("proxy") or "").strip(),
            )),
            timeout=_float_env("GROQ_TIMEOUT", "GROK_TIMEOUT", default=default_timeout),
            models=models,
        )
    if name in ("doubao", "volcengine", "volc"):
        env_models = _first_env(
            "DOUBAO_ASR_MODELS", "VOLC_ASR_MODELS", "VOLCENGINE_ASR_MODELS",
        )
        env_model = _first_env(
            "DOUBAO_ASR_MODEL", "VOLC_ASR_MODEL", "VOLCENGINE_ASR_MODEL",
        )
        models = _normalize_models(
            models=env_models or None,
            model=env_model or None,
        ) or yaml_models or ["asr-2.0", "asr-1.0"]
        return ProviderConfig(
            name="doubao",
            api_key=_first_env(
                "DOUBAO_API_KEY", "VOLC_API_KEY", "VOLC_ACCESS_KEY",
                "VOLCENGINE_API_KEY", "DOUBAO_ACCESS_KEY",
                default=str(raw.get("api_key") or "").strip(),
            ),
            app_id=_first_env(
                "DOUBAO_APP_ID", "VOLC_APP_ID", "VOLC_APP_KEY",
                "VOLCENGINE_APP_ID",
                default=str(
                    raw.get("app_id") or raw.get("app_key") or ""
                ).strip(),
            ),
            workspace_id=str(raw.get("workspace_id") or "").strip(),
            base_url=_first_env(
                "DOUBAO_WS_URL", "VOLC_WS_URL", "DOUBAO_BASE_URL",
                default=str(
                    raw.get("base_url")
                    or "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
                ).strip(),
            ),
            timeout=_float_env(
                "DOUBAO_TIMEOUT", "VOLC_TIMEOUT", default=default_timeout,
            ),
            models=models,
        )
    if name in ("siliconflow", "silicon", "sf"):
        env_models = _first_env(
            "SILICONFLOW_ASR_MODELS", "SF_ASR_MODELS",
        )
        env_model = _first_env(
            "SILICONFLOW_ASR_MODEL", "SF_ASR_MODEL",
        )
        models = _normalize_models(
            models=env_models or None,
            model=env_model or None,
        ) or yaml_models or ["SenseVoiceSmall"]
        return ProviderConfig(
            name="siliconflow",
            api_key=_first_env(
                "SILICONFLOW_API_KEY", "SF_API_KEY",
                default=str(raw.get("api_key") or "").strip(),
            ),
            base_url=_first_env(
                "SILICONFLOW_BASE_URL", "SF_BASE_URL",
                default=str(
                    raw.get("base_url") or "https://api.siliconflow.cn/v1"
                ).strip(),
            ),
            proxy=normalize_proxy(_first_env(
                "SILICONFLOW_PROXY", "SF_PROXY",
                default=str(raw.get("proxy") or "").strip(),
            )),
            timeout=_float_env(
                "SILICONFLOW_TIMEOUT", "SF_TIMEOUT", default=default_timeout,
            ),
            models=models,
        )
    return ProviderConfig(
        name=name,
        api_key=str(raw.get("api_key") or "").strip(),
        workspace_id=str(raw.get("workspace_id") or "").strip(),
        app_id=str(raw.get("app_id") or raw.get("app_key") or "").strip(),
        base_url=str(raw.get("base_url") or "").strip(),
        proxy=normalize_proxy(str(raw.get("proxy") or "").strip()),
        timeout=default_timeout,
        models=yaml_models,
    )


def canonical_provider_name(name: str) -> str:
    """历史别名归一：grok → groq；volcengine/volc → doubao；silicon/sf → siliconflow。"""
    n = (name or "").strip().lower()
    if n == "grok":
        return "groq"
    if n in ("volcengine", "volc", "bytedance"):
        return "doubao"
    if n in ("silicon", "sf"):
        return "siliconflow"
    return n


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

    # 兼容旧配置键 grok → groq；volcengine/volc → doubao；silicon/sf → siliconflow
    if "groq" not in providers_raw and "grok" in providers_raw:
        providers_raw = dict(providers_raw)
        providers_raw["groq"] = providers_raw.pop("grok")
    if "doubao" not in providers_raw:
        for alias in ("volcengine", "volc", "bytedance"):
            if alias in providers_raw:
                providers_raw = dict(providers_raw)
                providers_raw["doubao"] = providers_raw.pop(alias)
                break
    if "siliconflow" not in providers_raw:
        for alias in ("silicon", "sf"):
            if alias in providers_raw:
                providers_raw = dict(providers_raw)
                providers_raw["siliconflow"] = providers_raw.pop(alias)
                break

    providers: Dict[str, ProviderConfig] = {}
    for name in ("aliyun", "groq", "doubao", "siliconflow"):
        providers[name] = _merge_provider(name, providers_raw.get(name) or {})
    for name, raw in providers_raw.items():
        canon = canonical_provider_name(name)
        if canon not in providers:
            providers[canon] = _merge_provider(canon, raw or {})

    auto_order_raw = data.get("auto_order") or [
        "aliyun", "doubao", "siliconflow", "groq",
    ]
    if not isinstance(auto_order_raw, list):
        auto_order_raw = ["aliyun", "doubao", "siliconflow", "groq"]
    auto_order = [canonical_provider_name(str(x)) for x in auto_order_raw]
    # 去重保序
    seen = set()
    auto_order = [x for x in auto_order if not (x in seen or seen.add(x))]

    port_env = _env("ASR_PORT") or _env("DASHSCOPE_PORT")
    log_env = _env("ASR_LOG") or _env("DASHSCOPE_LOG")

    # 路由策略字段仅作兼容记录，不再改写 auto。
    # 局部导入避免 config → strategy → providers.base → config 的循环依赖。
    from asr_proxy.strategy import STRATEGY_NAMES, normalize_strategy_id

    strategy_raw = (str(_env("ASR_STRATEGY") or data.get("strategy") or "auto")).strip().lower()
    strategy_norm = normalize_strategy_id(strategy_raw)
    if strategy_raw in ("", "auto"):
        strategy = "auto"
    elif strategy_norm is None:
        raise ValueError(
            f"未知 strategy={strategy_raw!r}，可选：{', '.join(STRATEGY_NAMES)}，"
            "或组合 free+speed / free+accuracy。"
        )
    else:
        strategy = strategy_norm

    return AppConfig(
        host=str(server.get("host") or "127.0.0.1"),
        port=int(port_env or server.get("port") or 8004),
        log_file=log_env or str(server.get("log_file") or "asr_proxy.log"),
        ffmpeg_path=_first_env("FFMPEG_PATH", default=str(data.get("ffmpeg_path") or "ffmpeg")),
        strategy=strategy,
        auto_order=auto_order,
        providers=providers,
    )
