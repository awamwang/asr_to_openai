# -*- coding: utf-8 -*-
"""配置加载：YAML + 环境变量。密钥完备即视为启用该服务商。"""
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

_DEFAULT_AUTO_ORDER = [
    "aliyun", "doubao", "siliconflow", "groq",
    "stepfun", "tencent", "baidu",
]


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
    # 豆包旧版 / 腾讯 / 百度 AppID
    app_id: str = ""
    # 腾讯云 CAM：SecretId / SecretKey
    secret_id: str = ""
    secret_key: str = ""
    base_url: str = ""
    proxy: str = ""
    timeout: float = 300.0
    # 该服务商下可切换的模型列表（顺序即 provider/auto 尝试顺序）
    models: List[str] = field(default_factory=list)
    # 阿里云流式 ASR 断句：False=VAD（默认），True=语义断句
    semantic_punctuation_enabled: bool = False
    # 阿里云 VAD 静音阈值（ms），范围 [200, 6000]，默认 1300
    # 腾讯 Flash 复用：本地合并碎句的句间静音阈值
    max_sentence_silence: int = 1300
    # 腾讯 Flash filter_punc：0不过滤 / 1句末 / 2全部
    # 有效值由全局 text.clean_punctuation 映射，不单独作为主开关
    filter_punc: int = 0
    # 豆包 enable_punc：是否让上游打标点（由全局 text.clean_punctuation 映射）
    enable_punc: bool = True
    # 连接预热：识别成功后后台保持 1 个已连接未使用的 WS，供下次请求复用
    # （把建连移出关键路径；仅 WS 型服务商消费该字段）
    warm: bool = True
    # 预热连接空闲超过此时长视为失效（秒）。注意这只是「客户端愿意复用多久」，
    # 服务端仍可能提前断开空闲连接；取用时若已断开会自动重建（见各 Provider）。
    warm_ttl: float = 20.0
    # 音频分片大小（毫秒）：WS 型服务商按此切 PCM 后逐片发送。
    # 分片数 ≈ 网络往返次数，实测豆包 nostream 200ms→1000ms 端到端省约 32%。
    # 0 表示用该 Provider 内置默认（豆包/腾讯实时目前为 200ms）。
    segment_ms: int = 0
    # 按模型覆盖分片大小：{模型名或资源 ID: 毫秒}，键大小写不敏感。
    # 未命中则用 segment_ms，再未配置则用 Provider 内置默认。
    segment_ms_by_model: Dict[str, int] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        """密钥完备则启用：腾讯看 secret_id+secret_key，百度看 api_key+secret_key，其余看 api_key。"""
        name = (self.name or "").strip().lower()
        if name == "tencent":
            return bool(
                (self.secret_id or "").strip() and (self.secret_key or "").strip()
            )
        if name == "baidu":
            return bool(
                (self.api_key or "").strip() and (self.secret_key or "").strip()
            )
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
    # debug | info | warning | error；debug 含逐步尝试链
    log_level: str = "info"
    # 按天滚动后保留的备份天数（TimedRotatingFileHandler.backupCount）
    log_backup_days: int = 7
    ffmpeg_path: str = "ffmpeg"
    # 兼容字段：不再改写 auto。真正策略请请求模型 ID strategy-cost 等。
    strategy: str = "auto"
    auto_order: List[str] = field(
        default_factory=lambda: list(_DEFAULT_AUTO_ORDER),
    )
    # 各策略专用服务商顺序；非空时为白名单（未列入则该策略不使用）。
    # 对无自身排序的策略（如 free）同时作为默认尝试顺序。
    free_order: List[str] = field(default_factory=list)
    cost_order: List[str] = field(default_factory=list)
    speed_order: List[str] = field(default_factory=list)
    accuracy_order: List[str] = field(default_factory=list)
    providers: Dict[str, ProviderConfig] = field(default_factory=dict)
    # 容灾：auto / strategy-* 最多尝试几个候选（0=不限制）
    max_failover_candidates: int = 4
    # 硬失败（quota/auth/permission/not_activated）短时跳过秒数
    failover_cooldown_sec: float = 300.0
    # 软失败（upstream/empty 等）短时跳过秒数
    failover_soft_cooldown_sec: float = 60.0
    # 识别前统一转 16k 单声道；失败则回退原音频
    audio_preprocess: bool = True
    # 预处理时裁首尾静音（依赖 ffmpeg silenceremove）
    audio_trim_silence: bool = True
    # 本地清洗停顿/碎句标点（主配置）；同时映射到各厂商上游标点参数
    clean_punctuation: bool = False
    # 智能断句：只保留原生支持语义断句的模型（见 supports_smart_sentence）
    smart_sentence: bool = False

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

    def usable_model_ids(self) -> List[str]:
        """返回当前配置下真实可用、有序的模型 ID 列表。

        排序规则：
          1. 最上边：策略模型（auto + strategy-cost/speed/accuracy/free/free+speed/free+accuracy）
          2. 随后按 auto_order 的服务商顺序，每家依次列：
             - 裸服务商名 <服务商>：定点调用该服务商「优先模型」（上次成功 > 配置首项），只试一个
             - <服务商>/auto：该服务商 models 列表内切换（上次成功开始轮转）
             - 具体模型 <服务商>/<模型>
        只包含已启用（密钥完备）的服务商；smart_sentence=true 时进一步
        过滤为仅剩原生支持语义断句的模型（与路由行为一致）。
        """
        from asr_proxy.strategy import STRATEGY_PUBLIC_IDS  # 局部导入避免循环依赖
        from asr_proxy.strategy.metadata import supports_smart_sentence

        ids: List[str] = ["auto"] + list(STRATEGY_PUBLIC_IDS)
        for p in self.enabled_providers():
            models = list(p.models)
            if self.smart_sentence:
                models = [
                    m for m in models if supports_smart_sentence(p.name, m)
                ]
                if not models:
                    # smart_sentence 开启且无幸存模型：裸名/provider/auto 也会被过滤为空，跳过
                    continue
            ids.append(p.name)  # 裸服务商名：定点调用该服务商优先模型
            ids.append(f"{p.name}/auto")
            ids.extend(f"{p.name}/{m}" for m in models)
        return ids

    def order_for_strategy(self, strategy_name: str) -> List[str]:
        """返回策略专用服务商顺序。

        组合策略（如 free+speed）取前缀对应列表。
        空列表表示未配置：不白名单限制，沿用 enabled / auto_order。
        """
        base = (strategy_name or "").split("+", 1)[0].strip().lower()
        mapping = {
            "free": self.free_order,
            "cost": self.cost_order,
            "speed": self.speed_order,
            "accuracy": self.accuracy_order,
        }
        return list(mapping.get(base) or [])


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


def _clamp_filter_punc(value: Any, default: int = 0) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(0, min(2, n))


def derive_provider_text_params(
    clean_punctuation: bool,
    smart_sentence: bool,
) -> Dict[str, Dict[str, Any]]:
    """全局 text.* / ASR_* 主配置 → 各厂商接口参数。

    clean_punctuation=False（保留标点）：腾讯不过滤、豆包打标点。
    clean_punctuation=True（清洗标点）：腾讯滤全部、豆包关上游标点。
    smart_sentence=True：强制阿里流式 semantic_punctuation_enabled。
    """
    derived: Dict[str, Dict[str, Any]] = {
        "tencent": {"filter_punc": 2 if clean_punctuation else 0},
        "doubao": {"enable_punc": not clean_punctuation},
    }
    if smart_sentence:
        derived["aliyun"] = {"semantic_punctuation_enabled": True}
    return derived


def apply_global_text_policy(
    providers: Dict[str, ProviderConfig],
    *,
    clean_punctuation: bool,
    smart_sentence: bool,
) -> None:
    """用全局主配置覆盖已加载的厂商细节项（YAML/厂商环境变量在此后不再独立生效）。"""
    for name, fields in derive_provider_text_params(
        clean_punctuation, smart_sentence,
    ).items():
        p = providers.get(name)
        if not p:
            continue
        for attr, value in fields.items():
            setattr(p, attr, value)


def _int_env(*keys: str, default: int = 1300) -> int:
    raw = _first_env(*keys)
    if not raw:
        return default
    return _clamp_sentence_silence(raw, default=default)


def _int_env_raw(*keys: str, default: int = 0) -> int:
    """读取整数环境变量（不钳制范围），未设置则用 default。"""
    raw = _first_env(*keys)
    if not raw:
        return default
    return _int_yaml(raw, default)


def _int_yaml(raw: Any, default: int = 0) -> int:
    """解析整数配置（不做范围钳制，与 VAD 静音阈值区分开）。"""
    if raw is None or raw == "":
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _parse_int_map(raw: Any) -> Dict[str, int]:
    """解析 {键: 毫秒} 映射，键小写化；值非法则跳过。"""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, int] = {}
    for key, value in raw.items():
        try:
            ivalue = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if ivalue <= 0:
            continue
        out[str(key).strip().lower()] = ivalue
    return out


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
            warm=bool(raw.get("warm", True)),
            warm_ttl=float(
                raw.get("warm_ttl") if raw.get("warm_ttl") is not None else 20.0
            ),
            segment_ms=_int_env_raw(
                "DOUBAO_SEGMENT_MS", "VOLC_SEGMENT_MS",
                default=_int_yaml(raw.get("segment_ms"), 0),
            ),
            segment_ms_by_model=_parse_int_map(raw.get("segment_ms_by_model")),
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
        ) or yaml_models or ["XingChenASR-V3.2-Ultra"]
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
    if name in ("stepfun", "step"):
        env_models = _first_env("STEPFUN_ASR_MODELS", "STEP_ASR_MODELS")
        env_model = _first_env("STEPFUN_ASR_MODEL", "STEP_ASR_MODEL")
        models = _normalize_models(
            models=env_models or None,
            model=env_model or None,
        ) or yaml_models or ["stepaudio-2.5-asr"]
        return ProviderConfig(
            name="stepfun",
            api_key=_first_env(
                "STEPFUN_API_KEY", "STEP_API_KEY",
                default=str(raw.get("api_key") or "").strip(),
            ),
            base_url=_first_env(
                "STEPFUN_BASE_URL", "STEP_BASE_URL",
                default=str(
                    raw.get("base_url") or "https://api.stepfun.com/v1"
                ).strip(),
            ),
            proxy=normalize_proxy(_first_env(
                "STEPFUN_PROXY", "STEP_PROXY",
                default=str(raw.get("proxy") or "").strip(),
            )),
            timeout=_float_env(
                "STEPFUN_TIMEOUT", "STEP_TIMEOUT", default=default_timeout,
            ),
            models=models,
        )
    if name in ("tencent", "tencentcloud", "qcloud"):
        env_models = _first_env("TENCENT_ASR_MODELS", "QCLOUD_ASR_MODELS")
        env_model = _first_env("TENCENT_ASR_MODEL", "QCLOUD_ASR_MODEL")
        models = _normalize_models(
            models=env_models or None,
            model=env_model or None,
        ) or yaml_models or ["16k_zh"]
        yaml_silence = raw.get("max_sentence_silence")
        yaml_silence_default = (
            _clamp_sentence_silence(yaml_silence)
            if yaml_silence is not None
            else 2500
        )
        # filter_punc 先占位，load_config 末尾由 text.clean_punctuation 覆盖
        yaml_filter_punc = _clamp_filter_punc(raw.get("filter_punc"), default=0)
        env_filter = _first_env("TENCENT_FILTER_PUNC", "QCLOUD_FILTER_PUNC")
        filter_punc = (
            _clamp_filter_punc(env_filter, default=yaml_filter_punc)
            if env_filter
            else yaml_filter_punc
        )
        return ProviderConfig(
            name="tencent",
            secret_id=_first_env(
                "TENCENT_SECRET_ID", "QCLOUD_SECRET_ID",
                default=str(
                    raw.get("secret_id") or raw.get("secretId") or ""
                ).strip(),
            ),
            secret_key=_first_env(
                "TENCENT_SECRET_KEY", "QCLOUD_SECRET_KEY",
                default=str(
                    raw.get("secret_key") or raw.get("secretKey") or ""
                ).strip(),
            ),
            app_id=_first_env(
                "TENCENT_APP_ID", "QCLOUD_APP_ID",
                default=str(raw.get("app_id") or raw.get("appid") or "").strip(),
            ),
            timeout=_float_env(
                "TENCENT_TIMEOUT", "QCLOUD_TIMEOUT", default=default_timeout,
            ),
            models=models,
            max_sentence_silence=_int_env(
                "TENCENT_MAX_SENTENCE_SILENCE",
                "QCLOUD_MAX_SENTENCE_SILENCE",
                default=yaml_silence_default,
            ),
            filter_punc=filter_punc,
        )
    if name == "baidu":
        env_models = _first_env("BAIDU_ASR_MODELS")
        env_model = _first_env("BAIDU_ASR_MODEL")
        models = _normalize_models(
            models=env_models or None,
            model=env_model or None,
        ) or yaml_models or ["1537"]
        return ProviderConfig(
            name="baidu",
            api_key=_first_env(
                "BAIDU_API_KEY", "BAIDU_AK",
                default=str(raw.get("api_key") or raw.get("apiKey") or "").strip(),
            ),
            secret_key=_first_env(
                "BAIDU_SECRET_KEY", "BAIDU_SK",
                default=str(
                    raw.get("secret_key") or raw.get("secretKey") or ""
                ).strip(),
            ),
            app_id=_first_env(
                "BAIDU_APP_ID",
                default=str(raw.get("app_id") or raw.get("appId") or "").strip(),
            ),
            timeout=_float_env("BAIDU_TIMEOUT", default=default_timeout),
            models=models,
        )
    return ProviderConfig(
        name=name,
        api_key=str(raw.get("api_key") or "").strip(),
        workspace_id=str(raw.get("workspace_id") or "").strip(),
        app_id=str(raw.get("app_id") or raw.get("app_key") or "").strip(),
        secret_id=str(raw.get("secret_id") or "").strip(),
        secret_key=str(raw.get("secret_key") or "").strip(),
        base_url=str(raw.get("base_url") or "").strip(),
        proxy=normalize_proxy(str(raw.get("proxy") or "").strip()),
        timeout=default_timeout,
        models=yaml_models,
    )


def canonical_provider_name(name: str) -> str:
    """历史别名归一：grok → groq；volcengine/volc → doubao；silicon/sf → siliconflow 等。"""
    n = (name or "").strip().lower()
    if n == "grok":
        return "groq"
    if n in ("volcengine", "volc", "bytedance"):
        return "doubao"
    if n in ("silicon", "sf"):
        return "siliconflow"
    if n in ("step",):
        return "stepfun"
    if n in ("tencentcloud", "qcloud"):
        return "tencent"
    return n


def _parse_provider_order(
    raw: Any,
    *,
    default: Optional[List[str]] = None,
) -> List[str]:
    """解析服务商顺序列表：规范化别名、去重保序。"""
    if raw is None:
        return list(default or [])
    if not isinstance(raw, list):
        return list(default or [])
    names = [canonical_provider_name(str(x)) for x in raw]
    seen = set()
    return [x for x in names if x and not (x in seen or seen.add(x))]


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
    if "stepfun" not in providers_raw and "step" in providers_raw:
        providers_raw = dict(providers_raw)
        providers_raw["stepfun"] = providers_raw.pop("step")
    if "tencent" not in providers_raw:
        for alias in ("tencentcloud", "qcloud"):
            if alias in providers_raw:
                providers_raw = dict(providers_raw)
                providers_raw["tencent"] = providers_raw.pop(alias)
                break

    providers: Dict[str, ProviderConfig] = {}
    for name in (
        "aliyun", "groq", "doubao", "siliconflow",
        "stepfun", "tencent", "baidu",
    ):
        providers[name] = _merge_provider(name, providers_raw.get(name) or {})
    for name, raw in providers_raw.items():
        canon = canonical_provider_name(name)
        if canon not in providers:
            providers[canon] = _merge_provider(canon, raw or {})

    auto_order = _parse_provider_order(
        data.get("auto_order"),
        default=list(_DEFAULT_AUTO_ORDER),
    )
    # 策略专用 order：未配置或空列表 = 不限制；非空 = 白名单 + 默认顺序
    free_order = _parse_provider_order(data.get("free_order"))
    cost_order = _parse_provider_order(data.get("cost_order"))
    speed_order = _parse_provider_order(data.get("speed_order"))
    accuracy_order = _parse_provider_order(data.get("accuracy_order"))

    port_env = _env("ASR_PORT") or _env("DASHSCOPE_PORT")
    log_env = _env("ASR_LOG") or _env("DASHSCOPE_LOG")
    from asr_proxy.logutil import DEFAULT_BACKUP_DAYS, normalize_log_level
    log_level = normalize_log_level(
        _env("ASR_LOG_LEVEL") or str(server.get("log_level") or "info"),
    )
    backup_raw = _env("ASR_LOG_BACKUP_DAYS") or str(
        server.get("log_backup_days") or DEFAULT_BACKUP_DAYS
    )
    try:
        log_backup_days = max(1, int(backup_raw))
    except (TypeError, ValueError):
        log_backup_days = DEFAULT_BACKUP_DAYS

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

    routing = data.get("routing") if isinstance(data.get("routing"), dict) else {}
    max_fail = _env("ASR_MAX_FAILOVER_CANDIDATES") or str(
        routing.get("max_failover_candidates", data.get("max_failover_candidates", 4))
    )
    try:
        max_failover_candidates = max(0, int(max_fail))
    except (TypeError, ValueError):
        max_failover_candidates = 4
    cool = _env("ASR_FAILOVER_COOLDOWN_SEC") or str(
        routing.get("failover_cooldown_sec", data.get("failover_cooldown_sec", 300))
    )
    soft = _env("ASR_FAILOVER_SOFT_COOLDOWN_SEC") or str(
        routing.get(
            "failover_soft_cooldown_sec",
            data.get("failover_soft_cooldown_sec", 60),
        )
    )
    try:
        failover_cooldown_sec = max(0.0, float(cool))
    except (TypeError, ValueError):
        failover_cooldown_sec = 300.0
    try:
        failover_soft_cooldown_sec = max(0.0, float(soft))
    except (TypeError, ValueError):
        failover_soft_cooldown_sec = 60.0
    audio = data.get("audio") if isinstance(data.get("audio"), dict) else {}
    audio_preprocess = _bool_env(
        "ASR_AUDIO_PREPROCESS",
        default=_as_bool(
            audio.get("preprocess", data.get("audio_preprocess", True)),
            default=True,
        ),
    )
    audio_trim_silence = _bool_env(
        "ASR_AUDIO_TRIM_SILENCE",
        default=_as_bool(
            audio.get("trim_silence", data.get("audio_trim_silence", True)),
            default=True,
        ),
    )
    text_cfg = data.get("text") if isinstance(data.get("text"), dict) else {}
    clean_punctuation = _bool_env(
        "ASR_CLEAN_PUNCTUATION",
        default=_as_bool(
            text_cfg.get(
                "clean_punctuation", data.get("clean_punctuation", False),
            ),
            default=False,
        ),
    )
    smart_sentence = _bool_env(
        "ASR_SMART_SENTENCE",
        default=_as_bool(
            text_cfg.get("smart_sentence", data.get("smart_sentence", False)),
            default=False,
        ),
    )
    apply_global_text_policy(
        providers,
        clean_punctuation=clean_punctuation,
        smart_sentence=smart_sentence,
    )

    return AppConfig(
        host=str(server.get("host") or "127.0.0.1"),
        port=int(port_env or server.get("port") or 8004),
        log_file=log_env or str(server.get("log_file") or "asr_proxy.log"),
        log_level=log_level,
        log_backup_days=log_backup_days,
        ffmpeg_path=_first_env("FFMPEG_PATH", default=str(data.get("ffmpeg_path") or "ffmpeg")),
        strategy=strategy,
        auto_order=auto_order,
        free_order=free_order,
        cost_order=cost_order,
        speed_order=speed_order,
        accuracy_order=accuracy_order,
        providers=providers,
        max_failover_candidates=max_failover_candidates,
        failover_cooldown_sec=failover_cooldown_sec,
        failover_soft_cooldown_sec=failover_soft_cooldown_sec,
        audio_preprocess=audio_preprocess,
        audio_trim_silence=audio_trim_silence,
        clean_punctuation=clean_punctuation,
        smart_sentence=smart_sentence,
    )
